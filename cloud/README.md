# Relayer and rebalancer timer on Cloudflare Containers

Testnet only (Base Sepolia, NEAR testnet). This directory runs `scripts/relayer.py serve` and the rebalancer timer
(`scripts/rebalance_pool.py`, live then demo, every 10 minutes) on Cloudflare Containers. It replaces the Mac
(`caffeinate -s scripts/rebalance.sh`, `scripts/relayer.sh serve` on `localhost:8787`), which stays a fallback
(`DEPLOY.md` steps 6 and 7d).

| File | What it is |
|---|---|
| `Dockerfile` | python 3.14 slim (pinned by digest), `cast` (Foundry v1.8.6) and `near` (near-cli-rs v0.30.1), both checked by SHA-256, `jq`; copies `scripts/relayer.py`, `scripts/relayer.sh`, `scripts/rebalance_pool.py`, `deployments/testnet.json`, `cloud/front.py`. No key. Build context: the repository root (`Dockerfile.dockerignore` lets only those files in) |
| `front.py` | The container's entrypoint and only listener (`0.0.0.0:8080`). Writes the keys and the state, starts the relayer on `127.0.0.1:8787`, proxies to it, adds `factory_exists` to `/health` and answers `/create-vault` with 503 while the factory account does not exist, runs rebalancer rounds |
| `src/index.ts` | The Worker: routes, CORS (here only), the admin token, the `/create-vault` limits, the read-only proxy of the agent's evidence endpoint (`/agent/...`), the Durable Object `CmdkBackend` that owns the container and keeps the state, the creation counts and the funding ledger, the cron (rebalancer round, then the agent's funding) |
| `wrangler.jsonc` | Container `basic` (1/4 vCPU, 1 GiB, 4 GB), `max_instances` 1, cron `*/10 * * * *`, custom domain `relayer.cmdk.fi`, observability on |

## How it works

- **Always on.** One Durable Object (`CmdkBackend`, name `cmdk`) owns one container. `onActivityExpired` is
  overridden to do nothing, so the container is never stopped for inactivity (`sleepAfter` only sets how often the
  library asks). If the container exits anyway (crash, host restart, rollout), the next request or the next cron
  starts it again.
- **Keys.** The four secrets are Worker secrets. The Durable Object passes them to the container as environment
  variables at start; `front.py` writes `BASE_EOA_KEY_JSON` to `~/.config/prime/base_sepolia_eoa.json` (the path
  `rebalance_pool.py` reads) and `NEAR_RELAYER_KEY_JSON` to `~/.config/prime/near_relayer_key.json`, both mode 600,
  and removes the variables before it starts any child process. The relayer gets `NEAR_ACCESS_KEY_FILE`, so
  `relayer.py` signs with `sign-with-access-key-file <path>` instead of `sign-with-keychain`, and `--eth-key-file`
  names the Base key file: the ETH float stays at relayer.py's default, 0.005 ETH from that wallet to each vault
  created through `/create-vault`. relayer.py refuses to start (exit 2) when that wallet holds less than 20 floats
  (0.1 ETH); it held 0.4876 ETH on 3 Nov (97 floats). Values are never printed; the log shows the Base address and
  the NEAR public key.
- **Factory before Friday.** relayer.py does not read the factory account; it starts with a factory that does not
  exist yet. `front.py` reads it (`view_account`; a yes is kept, a no is read again after 30 s): `/health` gains
  `"factory_exists": false` and `/create-vault` answers 503 `factory … does not exist yet …` before the relayer
  sees it, so nothing is sent to a missing account. Both change by themselves once DEPLOY.md 7a creates it.
- **State.** The container's disk is ephemeral. The rebalancer's state files (`live.json`, `demo.json`: the band and
  anchor token ids) and the relayer's (`relayer.json`: funding cap, held transactions) are kept in the Durable
  Object's storage. They are saved after every round and after every relayer POST, and passed back to the container
  at every start (before the relayer starts). A lost state file would make the next round mint a second band.
- **Timer.** The cron calls the Durable Object, which runs one round in the container (`rebalance_pool.py --market
  live`, then `--market demo`, with `REBALANCE_ARGS`, default `--band-usdc 10`), stores the state and the `RUN`
  lines, and logs them. No `sleep 600` loop runs in the container. **A new Durable Object starts paused**: nothing
  is minted until `POST /resume` (DEPLOY.md step 6), so deploying before Friday is safe.
- **One round at a time.** `front.py` refuses a second round while one runs (409), and `rebalance_pool.py` still
  takes its own lock. `POST /pause` returns only after a running round has finished.
- **Agent evidence for the browser.** `GET /agent/vaults` and `GET /agent/` forward to the agent's evidence
  endpoint (`agent/src/serve.rs`, port 8080 of the CVM) at `AGENT_EVIDENCE_URL`, GET only, and answer the agent's
  JSON as it is, with this Worker's CORS (the agent's own headers are not passed on). The Durable Object reads the
  enclave at most once per path per 10 s and answers from memory meanwhile (`Cache-Control: public, max-age=10`,
  `Age`), so a busy dashboard does not reach the enclave more often than that. While `AGENT_EVIDENCE_URL` is empty
  (until Friday's Phala launch, DEPLOY.md step 8) both answer 503 `{"ok": false, "error": "agent evidence url not
  set"}`.
- **Agent funding (cron).** After the rebalancer round, paused or not (the pause is the rebalancer's switch), the
  cron reads the agent's `GET /` once. When a watched vault's `registration` is `"registering"` (the agent's
  `Phase::Pending`: joined, `register_agent` not accepted yet), it calls the container's `POST /fund-agent` for the
  agent's implicit account, the call DEPLOY.md step 8 made by hand. The evidence endpoint does not serve the account
  or the `WAITING` line (that is in the CVM's log only), so the Worker takes the account from the quote the index
  lists: `tdx_quote.hex` (checked against the index's SHA-256), whose report_data is the agent's ed25519 key and 32
  zero bytes (`agent/src/tee.rs`); the account is that key in hex (`agent/src/near.rs`) and must match the boot id's
  8-hex suffix. Read once per boot. The relayer decides whether to send: it tops the account up to 0.16 NEAR only
  when it holds under 0.15 (the agent waits for 0.14992 before each registration), else answers `already funded`,
  so a vault that is registering for another reason costs one read. One registration per refill: N vaults take
  0.16 + (N − 1) × 0.0116 NEAR, one cron (10 min) per vault after the first.
  **Caps.** `FUND_NEAR_PER_DAY` (default 2 NEAR) bounds the sum of what the relayer reports it sent
  (`amount_yocto` of a `funded` answer, and of a dry-run answer, so the cap is testable locally) over a rolling
  24 hours, kept in the Durable Object's storage; before each call 0.16 NEAR (the most one call sends) is reserved,
  so a call is skipped when the sum plus 0.16 would exceed the cap. `"0"` turns the funding off. The relayer's own
  0.5 NEAR per implicit account per 24 hours stays the inner bound. One log line per decision: `fund: pending=<n>
  sent=<NEAR> account=<64 hex> near_tx=<hash> [<tags>]` or `fund: skipped <reason>`. `GET /funding` keeps the last
  50 decisions except the routine ones (`no evidence url`, `funding off`, `no pending registration`, only logged).
  A `/fund-agent` call by hand (the fallback) is not counted in `FUND_NEAR_PER_DAY`; the relayer's cap counts it.
- **SIGTERM** (a stop or a rollout): a running round finishes first, then the container exits (tested locally:
  SIGTERM at 18:44:50, the round's response at 18:45:11, exit at 18:45:11). Cloudflare waits up to 15 minutes
  before SIGKILL.

## Routes

Base URL `https://relayer.cmdk.fi` (also `https://cmdk-relayer.fffilimonov.workers.dev`). Every relayer route also
answers under `/relayer/...`. CORS: the origins in `ALLOWED_ORIGINS` (`https://cmdk.fi`, `http://localhost:5173`).

**CORS is done in the Worker only.** `front.py` starts the relayer without `--cors-origin`, and the Worker drops
every `Access-Control-*` header of the response from behind before it sets its own (replace, never append), so a
browser never sees two `Access-Control-Allow-Origin` values. It also exposes `Retry-After` to the app.

| Route | Access | What |
|---|---|---|
| `GET /health`, `GET /vault/<id>`, `GET /job/<id>`, `POST /owner-call` | open | relayer.py (`scripts/RELAYER.md`): `/owner-call` answers 202 with a job id, `GET /job/<id>` its stages, `"wait": true` the synchronous answer. An owner call needs the owner's signature. `/health` also carries `jobs`, `wallet` (the float wallet) and, from `front.py`, `factory_exists` |
| `POST /create-vault` | open, with limits | relayer.py (202 and a job; 0.005 ETH float). At most `CREATE_PER_IP_PER_HOUR` (2) per client IP (`CF-Connecting-IP`) per rolling hour and `CREATE_PER_DAY` (20) in all per rolling 24 hours (founder, 3 Nov); beyond, 429 `{"ok": false, "error": "at most … ; retry in N s"}` with `Retry-After: N`. Counted in the Durable Object's storage (timestamps and a hash of the IP, entries older than 24 h pruned), only when the relayer accepted the creation: 202, or 200 for `"wait": true`; a 400, 503 or other refusal does not count. 503 while the factory does not exist |
| `POST /fund-agent` | admin token | relayer.py. It spends the relayer's NEAR without any signature (up to 0.16 NEAR per call; one call per vault an agent registers on), so it is not open. The cron makes this call by itself once `AGENT_EVIDENCE_URL` is set (above); by hand it is the fallback |
| `GET /agent/vaults`, `GET /agent/` (also `/agent`) | open, GET only | the agent's `GET /vaults` and `GET /` (index) at `AGENT_EVIDENCE_URL`, as the agent answers them (`agent/MEASUREMENT.md` §4), CORS as above, read at most once per 10 s per path; 503 `{"ok": false, "error": "agent evidence url not set"}` while the URL is empty, 502 `{"ok": false, "error": "agent evidence unreachable: …"}` when the read fails, 405 `GET only` for another method |
| `GET /funding` | admin token | the auto-funding: `{ok, evidence_url_set, cap_near_per_day, sent_last_24h, sent_last_24h_yocto, max_per_call, events: [{time, decision: "sent"\|"skipped", reason, pending, account, boot, amount_yocto, near_tx, dry_run, line}]}`, the last 50 events, oldest first |
| `POST /rebalance[?market=live\|demo\|both][&dry=1\|&status=1][&tolerance=X]` | admin token | one round now, paused or not; returns each run's `RUN` line, exit code and output |
| `POST /pause`, `POST /resume`, `GET /timer` | admin token | the cron's switch; `/timer` shows `paused`, the container status and the last `RUN` lines |
| `GET /runs[?n=N]` | admin token | the stored `RUN` lines (last 5,000 kept), oldest first, as text |
| `GET /state`, `PUT /state {"live": {...}, "demo": {...}}` | admin token | the stored state; replace the rebalancer's state (DEPLOY.md step 6 uploads the Mac's files) |

Admin token: `Authorization: Bearer <ADMIN_TOKEN>`. The commands below keep it in a header file and pass it with
`curl -H @file`, so it never appears on a command line.

## Hostname

The `cmdk.fi` zone is on this account (`b17ed4ce0f9e5a80c98552f6df971062`, zone active; checked 3 Nov through the
API). `relayer.cmdk.fi` had no DNS record and no Worker custom domain, so `wrangler.jsonc` sets it as a custom
domain; `wrangler deploy` creates the record and the certificate. The workers.dev URL stays on as a fallback.

## Secrets

| Name | Value | Source |
|---|---|---|
| `BASE_EOA_KEY_JSON` | the test wallet's key file as it is (`{"data": [{"address", "private_key", "public_key"}]}`; `front.py` checks the address and that a private key is there) | `~/.config/prime/base_sepolia_eoa.json` |
| `NEAR_RELAYER_KEY_JSON` | `{"account_id", "public_key", "private_key"}` of the relayer account (the near-cli-rs access-key-file format; the seed phrase is left out) | see "The relayer's NEAR key" |
| `NEAR_RELAYER_ACCOUNT` | the relayer account id, `relayer.py serve --sender` | `relayer.prime-measure-1792736012.testnet` (founder, 3 Nov) |
| `ADMIN_TOKEN` | 64 hex characters | generated below |

Plain variables in `wrangler.jsonc`: `NEAR_FACTORY_ACCOUNT` (`relayer.py serve --factory`), set to
`factory.prime-measure-1792736012.testnet`, `vault/factory/DEPLOY.md`'s `factory.$ACCOUNT`. The account is created
on Friday (DEPLOY.md 7a, recorded in 7c); until then the relayer runs and `/health` says `"factory_exists": false`.
`CREATE_PER_IP_PER_HOUR` (2) and `CREATE_PER_DAY` (20), `ALLOWED_ORIGINS`, `REBALANCE_ARGS`, `BASE_RPC`,
`NEAR_RPC`. `AGENT_EVIDENCE_URL`: the agent's evidence endpoint, the Phala gateway URL of the CVM's port 8080
(`agent/MEASUREMENT.md` §4), no trailing slash; empty until DEPLOY.md step 8 sets it and redeploys.
`FUND_NEAR_PER_DAY` (2): the cron's funding cap in NEAR per rolling 24 hours, `"0"` off. `RELAYER_DRY_RUN` is for local tests only (an `--env-file` line; `1` adds `--dry-run --allow-reads`);
it is never set on Cloudflare.

### The relayer's NEAR key

`relayer.prime-measure-1792736012.testnet` (founder, 3 Nov; 30 NEAR). Checked on 3 Nov without printing any key:
its key is in the legacy keychain, `~/.near-credentials/testnet/relayer.prime-measure-1792736012.testnet.json`
(fields `public_key`, `private_key`, `master_seed_phrase`, `seed_phrase_hd_path`, `implicit_account_id`; public key
`ed25519:HwdWu9mieUujCEkYbbcCHYTJCxmLYPZhJjykefqkWPhJ`), and `view_access_key_list` shows that key, full access, as
the account's only key. `jq` passes only `account_id`, `public_key` and `private_key` into `wrangler secret put`
(below): the pipe carries the key, the terminal shows nothing, and the seed phrase does not leave the Mac. A file
with only those three fields works as a near-cli-rs access-key file (tested on 3 Nov with a throwaway key).
`near account export-account` is not used: it prints the private key on the terminal.

## Commands for F (in order; nothing here was run against Cloudflare)

Before Friday. The factory name is already in `wrangler.jsonc`.

```sh
cd /Users/vf/cmdk/app/cloud
RELAYER=relayer.prime-measure-1792736012.testnet
pnpm install                                   # pnpm-workspace.yaml allows the esbuild, sharp, workerd builds
pnpm run check                                 # wrangler types + tsc --noEmit, exit 0
grep -E '"(NEAR_FACTORY_ACCOUNT|CREATE_PER_IP_PER_HOUR|CREATE_PER_DAY)"' wrangler.jsonc
#   "NEAR_FACTORY_ACCOUNT": "factory.prime-measure-1792736012.testnet",  "CREATE_PER_IP_PER_HOUR": "2",  "CREATE_PER_DAY": "20",

# 1. Admin token: generated into two files outside the repository, mode 600, never printed
( umask 077; openssl rand -hex 32 | tr -d '\n' > ~/.config/prime/relayer_admin_token
  printf 'Authorization: Bearer %s\n' "$(cat ~/.config/prime/relayer_admin_token)" > ~/.config/prime/relayer_admin.header )

# 2. Secrets, piped from files; wrangler reads stdin and prints only "Success! Uploaded secret ..."
#    (the first one creates the Worker cmdk-relayer as an empty draft if it does not exist yet: wrangler 4.156.0's
#    createDraftWorker, which answers its own prompt with yes when stdin is not a terminal)
npx wrangler secret put BASE_EOA_KEY_JSON < ~/.config/prime/base_sepolia_eoa.json
jq --arg a "$RELAYER" '{account_id: $a, public_key, private_key}' ~/.near-credentials/testnet/$RELAYER.json \
  | npx wrangler secret put NEAR_RELAYER_KEY_JSON
printf %s "$RELAYER" | npx wrangler secret put NEAR_RELAYER_ACCOUNT
npx wrangler secret put ADMIN_TOKEN < ~/.config/prime/relayer_admin_token
npx wrangler secret list                       # the four names, no values

# 3. Deploy: builds the linux/amd64 image with Docker, pushes it to Cloudflare's registry, deploys the Worker
npx wrangler deploy
```

Expected from `wrangler deploy` (not run; from the `--dry-run` output and the docs): the image build, `Uploaded cmdk-relayer`, the cron `*/10 * * * *`, the routes
`relayer.cmdk.fi (custom domain)` and `cmdk-relayer.fffilimonov.workers.dev`. The container needs a few minutes to
provision; until then a request answers 503 `There is no Container instance available at this time`. The
certificate for `relayer.cmdk.fi` can take a few minutes too; the workers.dev URL works meanwhile.

Health checks (read-only; the first request starts the container, and the relayer needs about 17 s to check both
chains and the float wallet, answering 503 `relayer unreachable (starting or restarting)` until then):

```sh
U=https://relayer.cmdk.fi; A=~/.config/prime/relayer_admin.header
curl -s $U/health | jq -c '{ok, dry_run, sender, factory, factory_exists, chain_id, near_network, base_block, sender_balance, eth_float_wei, floats_left: .wallet.floats_left, jobs}'
curl -s -H @$A $U/timer | jq -c '{paused, container: .container.status}'
curl -s -X POST -H @$A "$U/rebalance?status=1" | jq -r '.runs[].line'
curl -s -w ' %{http_code}\n' $U/job/0123456789abcdef
curl -s -w ' %{http_code}\n' -X POST $U/create-vault -H 'Content-Type: application/json' -d '{"owner":"bad"}'
curl -s -w ' %{http_code}\n' -X POST $U/fund-agent -d '{}'
curl -s -i -X OPTIONS $U/create-vault -H 'Origin: https://cmdk.fi' | grep -ic '^access-control-allow-origin'
curl -s -w ' %{http_code}\n' $U/agent/vaults
curl -s -H @$A $U/funding | jq -c '{evidence_url_set, cap_near_per_day, sent_last_24h, events: (.events | length)}'
```

Expected:
- `{"ok":true,"dry_run":false,"sender":"relayer.prime-measure-1792736012.testnet","factory":"factory.prime-measure-1792736012.testnet","factory_exists":false,"chain_id":84532,"near_network":"testnet","base_block":<about 54,02x,xxx>,"sender_balance":"30 NEAR","eth_float_wei":"5000000000000000","floats_left":97,"jobs":{"kept":0,"max":1000,"running":0,"stages":{}}}`
  (`floats_left` is the wallet's balance over 0.005 ETH: 97 at 0.4876 ETH);
- `{"paused":true,"container":"healthy"}`, then two `RUN … action=status … exit=0` lines;
- `{"ok": false, "error": "no such job"} 404`;
- before Friday `{"ok": false, "error": "factory factory.prime-measure-1792736012.testnet does not exist yet on NEAR testnet (created on Friday, DEPLOY.md 7a); /create-vault opens once it does"} 503`;
  after 7a `{"ok": false, "error": "request: missing ['deposit_near', 'policy'], unknown []"} 400`. Neither sends
  anything nor counts toward the limits;
- `{"ok":false,"error":"admin token required"} 401`; then `1` (one `Access-Control-Allow-Origin` line);
- before step 8 `{"ok":false,"error":"agent evidence url not set"} 503`, then
  `{"evidence_url_set":false,"cap_near_per_day":"2","sent_last_24h":"0","events":0}`.

Leave the timer paused; DEPLOY.md step 6 uploads the state and resumes it.

**Rate-limit test.** On Cloudflare a third create from one IP costs two real vaults (2 NEAR deposit and 0.005 ETH
each), and `CF-Connecting-IP` is set by Cloudflare, so the limits are tested locally with `wrangler dev`, the relayer
in dry run (reads both chains, sends nothing), a fake `CF-Connecting-IP`, and `CREATE_PER_DAY=3` to reach the daily
cap too. The env file lives outside the repository, mode 600; the NEAR key in it is a dummy (a dry run signs
nothing), the factory an existing account (the 503 above would come first otherwise):

```sh
cd /Users/vf/cmdk/app/cloud; T=$(mktemp -d); chmod 700 $T
python3 -I - "$T" <<'PY'
import json, os, secrets, sys
d = sys.argv[1]; base = json.dumps(json.load(open(os.path.expanduser('~/.config/prime/base_sepolia_eoa.json'))), separators=(',', ':'))
near = json.dumps({'account_id': 'relayer.prime-measure-1792736012.testnet', 'public_key': 'ed25519:11111111111111111111111111111111', 'private_key': 'ed25519:dummy'})
tok = secrets.token_hex(32)
for name, text in (('dev.env', "BASE_EOA_KEY_JSON='%s'\nNEAR_RELAYER_KEY_JSON='%s'\nNEAR_RELAYER_ACCOUNT=relayer.prime-measure-1792736012.testnet\n"
                   "ADMIN_TOKEN=%s\nRELAYER_DRY_RUN=1\nNEAR_FACTORY_ACCOUNT=prime-measure-1792736012.testnet\nCREATE_PER_DAY=3\n" % (base, near, tok)),
                   ('admin.header', 'Authorization: Bearer %s\n' % tok)):
    fd = os.open(os.path.join(d, name), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600); os.write(fd, text.encode()); os.close(fd)
PY
npx wrangler dev --port 8799 --env-file $T/dev.env > $T/dev.log 2>&1 &
until grep -q 'Ready on' $T/dev.log; do sleep 1; done
U=http://localhost:8799; until curl -sf $U/health > /dev/null; do sleep 2; done
GOOD='{"owner":"0x0B7Ed66f46fe83252a208563A88201BA60490d25","policy":{"cooldown_secs":120,"markets":[{"id":"0xa25ac5171b7e500b82653d3750b91bb44e2409c32ca3c9e80b0db077e8a4535c","loan_token":"0x036CbD53842c5426634e7929541eC2318f3dCF7e","collateral_token":"0x4200000000000000000000000000000000000006","executor":"0x0000000000000000000000000000000000000001","slice_max":"1000000000"}]},"deposit_near":"2"}'
cv() { curl -s -D $T/h -X POST $U/create-vault -H 'Content-Type: application/json' -H "CF-Connecting-IP: $1" -d "$2" -w ' %{http_code}'; grep -i '^retry-after' $T/h | tr -d '\r'; echo; }
cv 203.0.113.7 '{"owner":"bad"}'; cv 203.0.113.7 "$GOOD"; cv 203.0.113.7 "$GOOD"; cv 203.0.113.7 "$GOOD"
cv 198.51.100.9 "$GOOD"; cv 192.0.2.44 "$GOOD"
curl -s $U/health | jq -c '{dry_run, jobs}'
kill %1; rm -rf $T .wrangler
```

Expected (run on 3 Nov): `request: missing ['deposit_near', 'policy'], unknown [] 400`; `{"ok": true, "job": "…"} 202`
twice; `{"ok":false,"error":"at most 2 vault creations per client IP per hour; retry in 3600 s"} 429Retry-After: 3600`;
`202` for the second IP; `{"ok":false,"error":"at most 3 vault creations per 24 hours in all; retry in 86400 s"}
429Retry-After: 86400` for the third; then `{"dry_run":true,"jobs":{…,"stages":{"done":3}}}`: each job ended
`done` with `dry_run: true` and the `near contract call-function as-transaction … create_vault …` command it would
have run. The 400 did not count.

## Operations

- **Logs.** `npx wrangler tail cmdk-relayer` (live: every `RUN` line with `source=cron` or `source=manual`, each
  relayer request with its status, container start and stop). The dashboard keeps them 7 days (Workers Logs,
  observability on); the container's own stdout (each round's full output with every transaction hash, the
  relayer's log) is under the container application's logs. `GET /runs` keeps the last 5,000 `RUN` lines.
- **Redeploy.** `wrangler deploy` replaces the container (SIGTERM; a running round finishes) and resets the
  Durable Object, which can drop a round's result in flight. Once the timer runs: `curl -s -X POST -H @$A $U/pause`
  first, deploy, then `curl -s -X POST -H @$A $U/resume`. The image carries `deployments/testnet.json` as built;
  the rebalancer reads only tokens, pools and oracles from it, which the 6 Nov steps do not change.
- **Wallet transactions from the Mac** (the `tx` helper in DEPLOY.md) take the Mac's rebalancer lock, which the
  container does not see. Pause the timer around them, as DEPLOY.md step 7e does.
- **Rebalancer runs from the Mac stop at step 6**: the Mac's state files are stale after the upload. Every run after
  that is `POST /rebalance` (with `status=1` for the read-only status).
- **Stop everything:** `curl -s -X POST -H @$A $U/pause` stops the timer; the relayer stops only with
  `npx wrangler delete cmdk-relayer` (or by deploying a version without the container).

## Local test (3 Nov, `wrangler dev`, Docker Desktop 27.4.0)

Real Base Sepolia RPC and NEAR testnet, nothing sent. First run (16:43 UTC, before the relayer's jobs and float): a
Base key file with the real address and a dummy private key, a throwaway NEAR key on no account. Second run (17:21
UTC, relayer `ceb4d7d`, this Worker): the real Base key file (the float wallet must match its key and hold 20
floats, or relayer.py exits 2; nothing signs with it in these requests), a dummy NEAR key, a local admin token, all
in an `--env-file` outside the repository, deleted afterwards; the container log holds no key.
`npx wrangler dev --port 8799 --test-scheduled --env-file <file>`. Outputs of the second run:

- Relayer command: `sh /app/scripts/relayer.sh serve --port 8787 --sender relayer.prime-measure-1792736012.testnet
  --factory factory.prime-measure-1792736012.testnet --eth-key-file /home/cmdk/.config/prime/base_sepolia_eoa.json
  --near-rpc https://rpc.testnet.near.org --base-rpc https://base-sepolia-rpc.publicnode.com`; then `Base wallet
  0x0b7e… holds 0.487647873149337873 ETH (97 floats of 0.005 ETH)`, `listening on http://127.0.0.1:8787
  dry_run=False … eth_float=0.005 ETH cors=off`, `front: factory factory.prime-measure-1792736012.testnet does not
  exist yet`.
- `GET /health`: `{"ok":true,"dry_run":false,"sender":"relayer.prime-measure-1792736012.testnet","factory":"factory.prime-measure-1792736012.testnet","factory_exists":false,"chain_id":84532,"near_network":"testnet","base_block":54023196,"sender_balance":"30 NEAR","eth_float_wei":"5000000000000000","wallet":{"address":"0x0b7ed66f46fe83252a208563a88201ba60490d25","balance_wei":"487647873149337873","balance":"0.487647873149337873 ETH","floats_left":97},"jobs":{"kept":0,"max":1000,"running":0,"stages":{}}}`;
  `GET /timer`: `{"paused":true,"container":"healthy"}`.
- `GET /job/0123456789abcdef` and `GET /job/nope`: 404 `no such job`. `GET /vault/v1.factory.prime-measure-1792736012.testnet`: 502 `chain: query … UNKNOWN_ACCOUNT …`.
- `POST /create-vault {"owner":"bad"}` (factory missing): 503 `factory factory.prime-measure-1792736012.testnet does
  not exist yet on NEAR testnet (created on Friday, DEPLOY.md 7a); /create-vault opens once it does`.
  `POST /fund-agent` without the token: 401 `admin token required`. `POST /owner-call {"vault":"bad"}`: 400
  `request: missing ['message', 'signature'], unknown []`.
- CORS: `OPTIONS /create-vault` and `OPTIONS /job/<id>` from `https://cmdk.fi`: 204 with one
  `Access-Control-Allow-Origin: https://cmdk.fi`, `Access-Control-Expose-Headers: Retry-After`; `GET /health` from
  `https://cmdk.fi`: one `Access-Control-Allow-Origin` line; from another origin: `Vary: Origin` only; the 503 of
  `/create-vault` from `https://cmdk.fi` carries the header too.
- The rate-limit test above, in a third run with `RELAYER_DRY_RUN=1`, an existing account as factory
  (`"factory_exists":true`) and `CREATE_PER_DAY=3`: the outputs listed there. Cron while paused: `cron: timer
  paused; no round started`.

From the first run, unchanged since: `POST /rebalance?dry=1` (36.7 s) gave `RUN … market=live … action=dry-run:band
… exit=0` and `RUN … market=demo … action=dry-run:repair+bands … exit=0`; the cron with the timer resumed and
`REBALANCE_ARGS=--band-usdc 10 --dry-run` gave the same two lines with `source=cron`, then `POST /pause` →
`{"ok":true,"paused":true,"idle":true}`; a second round meanwhile: 409; `tolerance=0.5`: 400; `PUT /state`, `docker
kill`, and the next request restored `/app/scripts/state/live.json`; both key files mode 600, owner `cmdk`; the
relayer and `relayer.sh` processes carry none of the secret variables.

**Agent evidence proxy and funding** (fourth run, 3 Nov 17:46 UTC, `--test-scheduled`, `--persist-to` a directory
outside the repository; a stub of the agent's evidence endpoint on `127.0.0.1:8798` with serve.rs's JSON shapes,
vault `v1` `registered` and `v2` `registering`, a fresh random key in a synthetic v4 TDX quote; the relayer in
`RELAYER_DRY_RUN=1`):

- `AGENT_EVIDENCE_URL` empty: `GET /agent/vaults` from `https://cmdk.fi`: 503 `{"ok":false,"error":"agent evidence
  url not set"}` with one `Access-Control-Allow-Origin: https://cmdk.fi`; `GET /agent/` the same; `POST
  /agent/vaults`: 405 `GET only`; the cron: `cron: timer paused; no round started`, `fund: skipped no evidence
  url`; `GET /funding` without the token: 401; with it: `{"ok":true,"evidence_url_set":false,"cap_near_per_day":"2",
  "sent_last_24h":"0",…,"events":[]}`.
- `AGENT_EVIDENCE_URL=http://127.0.0.1:8798`, `FUND_NEAR_PER_DAY=0.2`: `GET /agent/vaults` from `https://cmdk.fi`:
  200, the stub's JSON, exactly one `Access-Control-Allow-Origin: https://cmdk.fi` (the stub sends its own `*`,
  which is not passed on), `Cache-Control: public, max-age=10`, `Age: 0`, then `Age: 2` two seconds later; the stub
  counted one `/vaults` read for both, a second after 11 s. From another origin: `Vary: Origin` only. Cron 1:
  `relayer POST /fund-agent -> 200`, `fund: pending=1 sent=0.16 account=<the stub's key> near_tx=none dry_run=true
  (relayer in dry run: nothing sent) [v2]`; the container log: `dry run, would run: near tokens
  relayer.prime-measure-1792736012.testnet send-near <account> '160000000000000000000000 yoctonear' …`. Cron 2:
  `fund: skipped cap: 0.16 NEAR sent in the last 24 h, a call may send up to 0.16, FUND_NEAR_PER_DAY=0.2 pending=1
  account=…`, no call (the container logged one `/fund-agent` in all). The quote was read once. After a Worker
  reload the cap still held (storage). The stub restarted with every vault registered: `fund: skipped no pending
  registration (pending=0 vaults=2)`.

Not tested locally: anything on Cloudflare (provisioning, the custom domain, real secrets, the real
`CF-Connecting-IP`, the cron on schedule, container logs in the dashboard, a rollout's SIGTERM, the 64-character
hostname, for which `front.py` skips `getfqdn`), and every sending path (a real create-vault and its float,
owner-call, fund-agent, a real round). Not tested: the real agent's evidence endpoint behind Phala's gateway, a
relayer answer `funded` or `already funded` (the dry run answers neither), the relayer's 0.5 NEAR cap refusal.
