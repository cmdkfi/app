#!/usr/bin/env python3 -I
"""cmdk container front (Cloudflare Containers; testnet only). The container's entrypoint and its only listener.

At start-up, before anything else runs, it
  1. writes the two keys from the environment into files with mode 600 and drops them from its environment:
       BASE_EOA_KEY_JSON      -> ~/.config/prime/base_sepolia_eoa.json  (read by rebalance_pool.py, handed to cast)
       NEAR_RELAYER_KEY_JSON  -> ~/.config/prime/near_relayer_key.json  (near-cli-rs sign-with-access-key-file)
  2. writes the state the Durable Object passed in CMDK_STATE_JSON ({"live", "demo", "relayer"}) into
     scripts/state/ (the container's disk is ephemeral; the Durable Object's storage is the copy that survives),
  3. starts `scripts/relayer.sh serve` on 127.0.0.1:8787 (relayer.py binds 127.0.0.1 only) with
     NEAR_ACCESS_KEY_FILE set, so relayer.py signs with the key file instead of a keychain.

Then it serves on 0.0.0.0:8080, for the Worker only (the Worker checks CORS and the admin token):
  GET /health, GET /vault/<id>, POST /create-vault, POST /owner-call, POST /fund-agent   proxied to the relayer
  POST /rebalance[?market=live|demo|both][&dry=1|&status=1][&tolerance=X]
        runs scripts/rebalance_pool.py --market live, then --market demo (as scripts/rebalance.sh does, one round,
        no sleep), with $REBALANCE_ARGS (default --band-usdc 10); returns each run's RUN line and exit code, the
        output, and the state files afterwards. One round at a time (409 while one runs); the script's own lock
        is taken too.
  GET /state       the state files {"live", "demo", "relayer"}
  PUT /state       {"live": {...}, "demo": {...}}: replaces the rebalancer's state files (relayer's is not accepted)
  POST /idle       waits until no round is running (up to 15 min), then returns: the pause handshake
  GET /ping        200
SIGTERM (stop, rollout): a round in progress finishes first (a burn without its re-mint would leave the pool bare).
"""
import json, os, re, signal, subprocess, sys, threading, time, urllib.error, urllib.parse, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import socketserver

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(APP, 'scripts')
STATE_DIR = os.path.join(SCRIPTS, 'state')
HOME = os.path.expanduser('~')
KEY_DIR = os.path.join(HOME, '.config', 'prime')
BASE_KEY = os.path.join(KEY_DIR, 'base_sepolia_eoa.json')     # the path rebalance_pool.py reads
NEAR_KEY = os.path.join(KEY_DIR, 'near_relayer_key.json')
PORT = int(os.environ.get('FRONT_PORT', '8080'))
RELAYER_PORT = 8787
RELAYER_GET = re.compile(r'^/(health|vault/[^/?#]+)$')
RELAYER_POST = ('/create-vault', '/owner-call', '/fund-agent')
ROUND_TIMEOUT = 900                  # seconds per market run
NOTES = {0: '', 3: ' (refused by the budget guard)', 4: ' (another run holds the lock)'}

ROUND = threading.Lock()             # one rebalancer round at a time
STOPPING = threading.Event()
RELAYER = {'proc': None, 'why': None}


def log(line):
    print('%s front: %s' % (time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), line), flush=True)


def write_private(path, text):
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as f:
        f.write(text)
    os.chmod(path, 0o600)


def write_keys():
    """Both keys from the environment into files (mode 600); the variables are removed from this process, so no
    child inherits them. Only the shape is checked; the values are never printed."""
    base = os.environ.pop('BASE_EOA_KEY_JSON', '')
    near = os.environ.pop('NEAR_RELAYER_KEY_JSON', '')
    if base:
        d = json.loads(base)['data']
        d = d[0] if isinstance(d, list) else d
        assert re.fullmatch(r'0x[0-9a-fA-F]{40}', d['address']) and d['private_key'], 'BASE_EOA_KEY_JSON: no data.address/private_key'
        write_private(BASE_KEY, base)
        log('Base key file written for %s' % d['address'])
    else:
        log('BASE_EOA_KEY_JSON not set: the rebalancer cannot run')
    if near:
        k = json.loads(near)
        assert str(k.get('public_key', '')).startswith('ed25519:') and k.get('private_key'), 'NEAR_RELAYER_KEY_JSON: no public_key/private_key'
        write_private(NEAR_KEY, json.dumps({'account_id': k.get('account_id') or os.environ.get('NEAR_RELAYER_ACCOUNT'),
                                            'public_key': k['public_key'], 'private_key': k['private_key']}))
        log('NEAR key file written (public key %s)' % k['public_key'])
    else:
        log('NEAR_RELAYER_KEY_JSON not set: the relayer cannot sign')


def state_files():
    out = {}
    for name in ('live', 'demo', 'relayer'):
        p = os.path.join(STATE_DIR, name + '.json')
        try:
            with open(p) as f:
                out[name] = json.load(f)
        except FileNotFoundError:
            out[name] = None
    return out


def write_state(st, names):
    os.makedirs(STATE_DIR, exist_ok=True)
    for name in names:
        v = st.get(name)
        if v is None:
            continue
        if not isinstance(v, dict):
            raise ValueError('state %s: expected an object' % name)
        p = os.path.join(STATE_DIR, name + '.json')
        tmp = p + '.tmp'
        with open(tmp, 'w') as f:
            json.dump(v, f, indent=1)
        os.replace(tmp, p)


def restore_state():
    raw = os.environ.pop('CMDK_STATE_JSON', '') or '{}'
    st = json.loads(raw)
    write_state(st, ('live', 'demo', 'relayer'))
    log('state restored: %s' % ', '.join('%s %s' % (k, 'set' if st.get(k) is not None else 'empty') for k in ('live', 'demo', 'relayer')))


def factory_account():
    f = os.environ.get('NEAR_FACTORY_ACCOUNT', '').strip()
    if f:
        return f
    try:
        with open(os.path.join(APP, 'deployments', 'testnet.json')) as fh:
            return json.load(fh)['near'].get('factory') or ''
    except (OSError, ValueError, KeyError):
        return ''


def start_relayer():
    sender, factory = os.environ.get('NEAR_RELAYER_ACCOUNT', '').strip(), factory_account()
    if not sender or not factory or not os.path.exists(NEAR_KEY):
        RELAYER['why'] = 'relayer not started: %s' % ', '.join(w for w, ok in (
            ('NEAR_RELAYER_ACCOUNT not set', sender), ('no factory (NEAR_FACTORY_ACCOUNT or deployments near.factory)', factory),
            ('no NEAR key (NEAR_RELAYER_KEY_JSON)', os.path.exists(NEAR_KEY))) if not ok)
        log(RELAYER['why'])
        return
    env = dict(os.environ, NEAR_ACCESS_KEY_FILE=NEAR_KEY)
    cmd = ['sh', os.path.join(SCRIPTS, 'relayer.sh'), 'serve', '--port', str(RELAYER_PORT), '--sender', sender, '--factory', factory]
    for flag, var in (('--near-rpc', 'NEAR_RPC'), ('--base-rpc', 'RPC')):
        if os.environ.get(var):
            cmd += [flag, os.environ[var]]
    log('starting: %s' % ' '.join(cmd))
    RELAYER['proc'] = subprocess.Popen(cmd, env=env, stdout=sys.stdout, stderr=sys.stderr)


def run_market(market, extra):
    cmd = [sys.executable, '-I', os.path.join(SCRIPTS, 'rebalance_pool.py'), '--market', market] + extra
    t0 = time.time()
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=ROUND_TIMEOUT)
        code, out = p.returncode, p.stdout + p.stderr
    except subprocess.TimeoutExpired as e:
        text = lambda b: b.decode(errors='replace') if isinstance(b, bytes) else (b or '')
        code, out = 124, text(e.stdout) + text(e.stderr) + '\nfront: killed after %d s' % ROUND_TIMEOUT
    print('==== %s market %s exit %d (%.1f s): %s\n%s' % (time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), market, code,
          time.time() - t0, ' '.join(cmd[2:]), out.rstrip()), flush=True)
    runs = [l for l in out.splitlines() if l.startswith('RUN ')]
    note = NOTES.get(code)
    if note is None:
        rest = [l for l in out.splitlines() if l.strip() and not l.startswith('RUN ')]
        note = ' (crashed: %s)' % (rest[-1] if rest else 'no output')
    line = runs[-1] if runs else 'RUN %s market=%s action=crash' % (time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), market)
    return {'market': market, 'exit': code, 'line': '%s exit=%d%s' % (line, code, note), 'seconds': round(time.time() - t0, 1),
            'output': out}


def rebalance(q):
    markets = {'live': ['live'], 'demo': ['demo'], 'both': ['live', 'demo']}.get(q.get('market', 'both'))
    if markets is None:
        return 400, {'ok': False, 'error': 'market: live, demo or both'}
    extra = os.environ.get('REBALANCE_ARGS', '--band-usdc 10').split()
    if q.get('status') == '1':
        extra.append('--status')
    elif q.get('dry') == '1':
        extra.append('--dry-run')
    if 'tolerance' in q:
        try:
            tol = float(q['tolerance'])
        except ValueError:
            tol = -1
        if not 0 < tol < 0.05:
            return 400, {'ok': False, 'error': 'tolerance: a number above 0 and below 0.05'}
        extra += ['--tolerance', repr(tol)]
    if STOPPING.is_set():
        return 503, {'ok': False, 'error': 'stopping'}
    if not ROUND.acquire(blocking=False):
        return 409, {'ok': False, 'error': 'a rebalancer round is running'}
    try:
        runs = [run_market(m, extra) for m in markets]
    finally:
        ROUND.release()
    return 200, {'ok': all(r['exit'] == 0 for r in runs), 'args': extra, 'runs': runs, 'state': state_files()}


def proxy(handler, method, body):
    if RELAYER['proc'] is None:
        return handler.send_json(503, {'ok': False, 'error': RELAYER['why'] or 'relayer not started'})
    req = urllib.request.Request('http://127.0.0.1:%d%s' % (RELAYER_PORT, handler.path), data=body, method=method,
                                 headers={'Content-Type': 'application/json'} if body is not None else {})
    try:
        r = urllib.request.urlopen(req, timeout=900)
        code, data = r.status, r.read()
    except urllib.error.HTTPError as e:
        code, data = e.code, e.read()
    except (urllib.error.URLError, OSError) as e:
        alive = RELAYER['proc'].poll() is None
        return handler.send_json(503, {'ok': False, 'error': 'relayer unreachable (%s): %s' % ('starting or restarting' if alive else 'exited', e)})
    handler.send_raw(code, data)


class Handler(BaseHTTPRequestHandler):
    server_version = 'cmdk-front/1'
    MAX_BODY = 1 << 20

    def body(self):
        n = int(self.headers.get('Content-Length') or 0)
        if not 0 <= n <= self.MAX_BODY:
            raise ValueError('body above 1 MB')
        return self.rfile.read(n) if n else b''

    def do_GET(self):
        path = urllib.parse.urlsplit(self.path).path
        if path == '/ping':
            return self.send_json(200, {'ok': True})
        if path == '/state':
            return self.send_json(200, {'ok': True, 'state': state_files()})
        if RELAYER_GET.match(path):
            return proxy(self, 'GET', None)
        self.send_json(404, {'ok': False, 'error': 'no such endpoint'})

    def do_POST(self):
        u = urllib.parse.urlsplit(self.path)
        try:
            body = self.body()
        except ValueError as e:
            return self.send_json(400, {'ok': False, 'error': str(e)})
        if u.path in RELAYER_POST:
            return proxy(self, 'POST', body)
        if u.path == '/rebalance':
            q = dict(urllib.parse.parse_qsl(u.query))
            return self.send_json(*rebalance(q))
        if u.path == '/idle':
            ok = ROUND.acquire(timeout=ROUND_TIMEOUT)
            if ok:
                ROUND.release()
            return self.send_json(200 if ok else 504, {'ok': ok, 'idle': ok})
        self.send_json(404, {'ok': False, 'error': 'no such endpoint'})

    def do_PUT(self):
        if urllib.parse.urlsplit(self.path).path != '/state':
            return self.send_json(404, {'ok': False, 'error': 'no such endpoint'})
        try:
            st = json.loads(self.body() or b'{}')
            if not isinstance(st, dict) or set(st) - {'live', 'demo'}:
                raise ValueError('PUT /state takes {"live": {...}, "demo": {...}} only')
            if not ROUND.acquire(timeout=ROUND_TIMEOUT):
                return self.send_json(409, {'ok': False, 'error': 'a rebalancer round is running'})
            try:
                write_state(st, ('live', 'demo'))
            finally:
                ROUND.release()
        except ValueError as e:
            return self.send_json(400, {'ok': False, 'error': str(e)})
        log('state replaced: %s' % ', '.join(sorted(st)))
        self.send_json(200, {'ok': True, 'state': state_files()})

    def send_json(self, code, out):
        self.send_raw(code, json.dumps(out).encode())

    def send_raw(self, code, data):
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):
        log('%s %s' % (self.address_string(), fmt % args))


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def server_bind(self):
        # HTTPServer.server_bind calls socket.getfqdn(), which fails on the 64-character hostname of a deployed
        # container (Cloudflare Containers local-dev notes); the name is not used.
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = 'cmdk-front', self.server_address[1]


def main():
    write_keys()
    restore_state()
    start_relayer()
    srv = Server(('0.0.0.0', PORT), Handler)

    def stop(signum, frame):
        STOPPING.set()
        log('signal %d: waiting for a running round to finish' % signum)

        def finish():
            with ROUND:
                pass
            p = RELAYER['proc']
            if p is not None and p.poll() is None:
                p.terminate()
                try:
                    p.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    p.kill()
            log('stopped')
            srv.shutdown()
        threading.Thread(target=finish, daemon=True).start()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    log('listening on 0.0.0.0:%d' % PORT)
    srv.serve_forever()


if __name__ == '__main__':
    main()
