// cmdk relayer and rebalancer timer on Cloudflare Containers. Testnet only (Base Sepolia, NEAR testnet).
//
// One Durable Object (CmdkBackend, name "cmdk") owns one always-on container (cloud/Dockerfile, cloud/front.py):
// relayer.py serve on the container's 127.0.0.1:8787 behind front.py on :8080. The Durable Object's storage
// keeps the state files (the container's disk is ephemeral) and passes them to the container at every start.
//
// Relayer routes, also under a /relayer prefix. CORS for ALLOWED_ORIGINS is done here only: the relayer in the
// container runs without --cors-origin, and any Access-Control-* header from behind is dropped, then set once.
//   GET /health, GET /vault/<id>, GET /job/<id>, POST /owner-call   open (an owner call needs the owner's signature)
//   POST /create-vault   open, with limits: CREATE_PER_IP_PER_HOUR per client IP (CF-Connecting-IP) per rolling
//                        hour and CREATE_PER_DAY in all per rolling 24 hours, 429 with Retry-After beyond; counted
//                        in the Durable Object's storage, only for a creation the relayer accepted (202, or 200
//                        for a synchronous "wait": true)
//   POST /fund-agent     Authorization: Bearer ADMIN_TOKEN: it spends the relayer's NEAR without any signature
// Admin routes (Authorization: Bearer ADMIN_TOKEN):
//   POST /rebalance[?market=live|demo|both][&dry=1|&status=1][&tolerance=X]   one round now, paused or not
//   POST /pause   the cron stops starting rounds; returns once a running round has finished
//   POST /resume  the cron starts a round every 10 minutes again
//   GET  /timer   paused flag, container status, the last RUN lines
//   GET  /runs[?n=N]   the stored RUN lines, oldest first (N default 200)
//   GET  /state   the stored state;  PUT /state {"live": {...}, "demo": {...}} replaces the rebalancer's state
//   GET  /funding the auto-funding: cap, NEAR sent in the last 24 h, the last 50 funding events
// Agent evidence, open, GET only (the agent's evidence endpoint, agent/src/serve.rs, at AGENT_EVIDENCE_URL):
//   GET /agent/vaults -> <AGENT_EVIDENCE_URL>/vaults,  GET /agent/ (or /agent) -> <AGENT_EVIDENCE_URL>/,
//   GET /agent/transcripts -> /transcripts (index), GET /agent/transcripts/<name> -> /transcripts/<name> (5 Nov: public
//   transcripts; <name> must match TRANSCRIPT_NAME, anything else is 404 here and never reaches the agent),
//   GET /agent/evidence/<file> -> /evidence/<file> (8 Nov: the boot's measurement evidence the index lists, tdx_quote.hex,
//   event_log.json, app-compose.json, vm_config.json, info.json and the registration files; <file> must match
//   EVIDENCE_NAME, the Worker's copy of the agent's is_public_name; anything else is 404 here)
//        the agent's JSON as it is, with this Worker's CORS; one read of the enclave per path per 10 s (kept in the
//        Durable Object's memory); 503 {"ok": false, "error": "agent evidence url not set"} while the URL is empty
// Cron "*/10 * * * *": one round (live, then demo), unless paused; then, paused or not, the agent's funding: when
// AGENT_EVIDENCE_URL is set and a watched vault is "registering", POST /fund-agent for the agent's implicit account
// (from the quote's report_data), within FUND_NEAR_PER_DAY (rolling 24 h, kept in the Durable Object's storage).
// A fresh Durable Object starts paused.

import { Container } from '@cloudflare/containers';

// The generated Env (wrangler types) plus the Durable Object binding typed here and the secrets.
interface Bindings extends Env {
	BACKEND: DurableObjectNamespace<CmdkBackend>;
	BASE_EOA_KEY_JSON?: string;
	NEAR_RELAYER_KEY_JSON?: string;
	NEAR_RELAYER_ACCOUNT?: string;
	ADMIN_TOKEN?: string;
	// local tests only (wrangler dev --env-file), never set on Cloudflare: "1" starts the relayer with
	// --dry-run --allow-reads (reads both chains, sends nothing)
	RELAYER_DRY_RUN?: string;
}

const PORT = 8080;
const NAME = 'cmdk';
const OPEN_GET = /^\/(health|vault\/[^/]+|job\/[^/]+)$/;
const OPEN_POST = new Set(['/owner-call', '/create-vault']);
const GATED_POST = new Set(['/fund-agent']);
const ADMIN = new Set(['/rebalance', '/pause', '/resume', '/timer', '/runs', '/state', '/funding']);
// the agent's evidence endpoint (agent/src/serve.rs), proxied read-only: Worker path -> the agent's path
const AGENT_GET = new Map([['/agent', '/'], ['/agent/', '/'], ['/agent/vaults', '/vaults'], ['/agent/transcripts', '/transcripts']]);
// A transcript file name as the agent writes it: boot-<unix>-<8 hex>-v<n>[-twin]-<loop 4 digits>.json (agent/src/main.rs)
const TRANSCRIPT_NAME = /^boot-[0-9]{1,12}-[0-9a-f]{8}-(v|vault)[0-9]{1,6}(-twin)?-[0-9]{4,6}\.json$/; // = agent/src/serve.rs transcript_name
// A measurement evidence file as the agent serves it (agent/src/serve.rs is_public_name): one of PUBLIC_FILES,
// registration-<tag>.json or reregistration-<tag>-<digits>.json, <tag> = v<1-6 digits> or vault<1-6 digits> (vaults::valid_tag)
const PUBLIC_FILES = new Set(['app-compose.json', 'vm_config.json', 'tdx_quote.hex', 'event_log.json', 'info.json']);
const REGISTRATION_NAME = /^registration-(?:v|vault)[0-9]{1,6}\.json$/;
const REREGISTRATION_NAME = /^reregistration-(?:v|vault)[0-9]{1,6}-[0-9]{1,6}\.json$/;
function evidenceName(name: string): boolean {
	return PUBLIC_FILES.has(name) || REGISTRATION_NAME.test(name) || REREGISTRATION_NAME.test(name);
}
function agentRoute(path: string): string | undefined {
	const fixed = AGENT_GET.get(path);
	if (fixed !== undefined) return fixed;
	const t = path.match(/^\/agent\/transcripts\/([^/]+)$/);
	if (t) return TRANSCRIPT_NAME.test(t[1]) ? `/transcripts/${t[1]}` : undefined;
	const e = path.match(/^\/agent\/evidence\/([^/]+)$/);
	return e && evidenceName(e[1]) ? `/evidence/${e[1]}` : undefined;
}
const EVIDENCE_TTL_MS = 10_000;
const EVIDENCE_TIMEOUT_MS = 10_000;
const RUNS_KEPT = 5000;
const STATE_NAMES = ['live', 'demo', 'relayer'] as const;
const HOUR = 3600_000;
const DAY = 24 * HOUR;

type Creation = { t: number; ip: string; id: string };

// The auto-funding of the agent's registrations (cron). Amounts in yoctoNEAR, as decimal strings in storage.
const YOCTO = 10n ** 24n;
// the most one /fund-agent call sends: relayer.py tops an implicit account up to AGENT_MIN 0.15 + AGENT_EXTRA
// 0.01 NEAR, so 0.16 NEAR from a zero balance. The cap check reserves this much before each call.
const FUND_MAX_PER_CALL = 16n * 10n ** 22n;
const FUND_EVENTS_KEPT = 50;
// Phase::Pending's label (agent/src/vaults.rs): joined, register_agent not accepted yet
const REGISTERING = 'registering';

type Evidence = { status: number; type: string; body: string; at: number };
type FundSent = { t: number; yocto: string };
type FundEvent = {
	time: string;
	decision: 'sent' | 'skipped';
	reason: string | null;
	pending: number | null;
	account: string | null;
	boot: string | null;
	amount_yocto: string | null;
	near_tx: string | null;
	dry_run: boolean;
	line: string;
};
// the agent's GET / (serve.rs index): boot, files (name, bytes, sha256, path) and the watched vaults (vaults.rs
// board_json rows; main.rs refresh step 6 writes "registration": Phase::label)
type AgentIndex = {
	boot?: string;
	files?: { name?: string; sha256?: string; path?: string }[];
	vaults?: { tag?: string; vault?: string; registration?: string }[];
};

type RoundResult = { ok: boolean; error?: string; runs?: { market: string; exit: number; line: string }[]; state?: Record<string, unknown> };

function json(status: number, body: unknown, headers: Record<string, string> = {}): Response {
	return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json', ...headers } });
}

function positiveInt(v: string | undefined, fallback: number): number {
	const n = Number(v);
	return Number.isInteger(n) && n > 0 ? n : fallback;
}

// A decimal NEAR amount ("2", "0.2") in yoctoNEAR; null when it is not one.
function parseNear(v: string | undefined): bigint | null {
	const m = /^(\d{1,12})(?:\.(\d{1,24}))?$/.exec((v ?? '').trim());
	return m ? BigInt(m[1]) * YOCTO + BigInt((m[2] ?? '').padEnd(24, '0')) : null;
}

function fmtNear(y: bigint): string {
	const f = (y % YOCTO).toString().padStart(24, '0').replace(/0+$/, '');
	return `${y / YOCTO}${f ? '.' + f : ''}`;
}

async function sha256Hex(text: string): Promise<string> {
	const d = new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text)));
	return Array.from(d, (b) => b.toString(16).padStart(2, '0')).join('');
}

// The agent's NEAR implicit account from its TDX quote (tdx_quote.hex, as served): agent/src/tee.rs makes
// report_data = the ed25519 public key + 32 zero bytes, and the implicit account is that key in hex (near.rs).
// Quote v4: a 48-byte header, then the TD report, whose report_data is at bytes 520..584; so hex 1136..1264.
function implicitFromQuote(hex: string): string | { error: string } {
	const h = hex.trim().toLowerCase();
	if (!/^[0-9a-f]+$/.test(h) || h.length < 1264) return { error: 'quote is not hex of at least 632 bytes' };
	if (h.slice(0, 4) !== '0400' || h.slice(8, 16) !== '81000000') return { error: 'not a version 4 TDX quote' };
	if (h.slice(1200, 1264) !== '0'.repeat(64)) return { error: 'report_data is not a key and 32 zero bytes' };
	return h.slice(1136, 1200);
}

// The client IP is stored only as a hash (16 hex of its SHA-256).
async function ipTag(ip: string): Promise<string> {
	const d = new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(ip)));
	return Array.from(d.slice(0, 8), (b) => b.toString(16).padStart(2, '0')).join('');
}

export class CmdkBackend extends Container<Bindings> {
	defaultPort = PORT;
	sleepAfter = '24h';
	enableInternet = true;
	// the agent's evidence, one read per path per EVIDENCE_TTL_MS (memory only: a restart only reads again)
	private evidence = new Map<string, Evidence>();
	private evidenceReads = new Map<string, Promise<Evidence>>();
	private funding = false;

	// Always on: never stop for inactivity (the library then renews the timeout and asks again later).
	override async onActivityExpired(): Promise<void> {}

	override onStart(): void {
		console.log('container started');
	}

	override onStop(params: unknown): void {
		console.log('container stopped', JSON.stringify(params));
	}

	override onError(error: unknown): unknown {
		console.error('container error', error);
		throw error;
	}

	private async startEnv(): Promise<Record<string, string>> {
		const state: Record<string, unknown> = {};
		for (const n of STATE_NAMES) state[n] = (await this.ctx.storage.get(`state:${n}`)) ?? null;
		const e = this.env;
		return {
			BASE_EOA_KEY_JSON: e.BASE_EOA_KEY_JSON ?? '',
			NEAR_RELAYER_KEY_JSON: e.NEAR_RELAYER_KEY_JSON ?? '',
			NEAR_RELAYER_ACCOUNT: e.NEAR_RELAYER_ACCOUNT ?? '',
			NEAR_FACTORY_ACCOUNT: e.NEAR_FACTORY_ACCOUNT ?? '',
			REBALANCE_ARGS: e.REBALANCE_ARGS ?? '--band-usdc 10',
			RPC: e.BASE_RPC,
			NEAR_RPC: e.NEAR_RPC,
			RELAYER_DRY_RUN: e.RELAYER_DRY_RUN ?? '',
			CMDK_STATE_JSON: JSON.stringify(state),
		};
	}

	// Every call to the container goes through here, so a (re)start always carries the secrets and the stored state.
	private async call(path: string, init: RequestInit = {}): Promise<Response> {
		this.envVars = await this.startEnv();
		return this.containerFetch(`http://container${path}`, init, PORT);
	}

	private async saveState(state: Record<string, unknown> | undefined, names: readonly string[]): Promise<void> {
		if (!state) return;
		for (const n of names) if (state[n] !== undefined && state[n] !== null) await this.ctx.storage.put(`state:${n}`, state[n]);
	}

	private async appendRuns(lines: string[]): Promise<void> {
		const runs = ((await this.ctx.storage.get<string[]>('runs')) ?? []).concat(lines);
		await this.ctx.storage.put('runs', runs.slice(-RUNS_KEPT));
	}

	private async paused(): Promise<boolean> {
		return (await this.ctx.storage.get<boolean>('paused')) ?? true;
	}

	// One rebalancer round in the container; the state files it returns are stored before anything else.
	async round(query: string, source: string): Promise<RoundResult & { status: number }> {
		const r = await this.call(`/rebalance${query}`, { method: 'POST' });
		const text = await r.text();
		let out: RoundResult;
		try {
			out = JSON.parse(text);
		} catch {
			out = { ok: false, error: `container answered ${r.status}: ${text.slice(0, 300)}` };
		}
		const dryOrStatus = /[?&](dry|status)=1/.test(query);
		if (!dryOrStatus) await this.saveState(out.state, ['live', 'demo']);
		const lines = (out.runs ?? []).map((x) => `${x.line} source=${source}${dryOrStatus ? ' (dry run or status)' : ''}`);
		if (!out.runs) lines.push(`RUN ${new Date().toISOString()} action=none source=${source} error=${out.error ?? r.status}`);
		await this.appendRuns(lines);
		for (const l of lines) console.log(l);
		return { ...out, status: r.status };
	}

	async cron(): Promise<string> {
		if (await this.paused()) {
			console.log('cron: timer paused; no round started');
			return 'paused';
		}
		const r = await this.round('', 'cron');
		return r.runs ? r.runs.map((x) => x.line).join('\n') : `error: ${r.error}`;
	}

	private evidenceBase(): string {
		const u: string = this.env.AGENT_EVIDENCE_URL ?? '';
		return u.trim().replace(/\/+$/, '');
	}

	// One read of the agent's evidence endpoint; failures are answered as 502 JSON.
	private async readEvidence(path: string): Promise<Evidence> {
		try {
			const r = await fetch(`${this.evidenceBase()}${path}`, { signal: AbortSignal.timeout(EVIDENCE_TIMEOUT_MS) });
			return { status: r.status, type: r.headers.get('Content-Type') ?? 'application/octet-stream', body: await r.text(), at: Date.now() };
		} catch (e) {
			const body = JSON.stringify({ ok: false, error: `agent evidence unreachable: ${String(e).slice(0, 200)}` });
			return { status: 502, type: 'application/json', body, at: Date.now() };
		}
	}

	// GET /agent/... for browsers: the answer of the last EVIDENCE_TTL_MS if there is one, else one read (requests
	// that arrive during it wait for the same read). `age` is the answer's age in seconds.
	async agentEvidence(path: string): Promise<Evidence & { age: number }> {
		const key = `${this.evidenceBase()}${path}`;
		const hit = this.evidence.get(key);
		const now = Date.now();
		if (hit && now - hit.at < EVIDENCE_TTL_MS) return { ...hit, age: Math.floor((now - hit.at) / 1000) };
		let p = this.evidenceReads.get(key);
		if (!p) {
			p = this.readEvidence(path).finally(() => this.evidenceReads.delete(key));
			this.evidenceReads.set(key, p);
		}
		const e = await p;
		if (e.status !== 502) {
			// transcripts are many distinct paths: drop expired answers before the map grows past a few hundred
			if (this.evidence.size >= 256) for (const [k, v] of this.evidence) if (now - v.at >= EVIDENCE_TTL_MS) this.evidence.delete(k);
			this.evidence.set(key, e);
		}
		return { ...e, age: 0 };
	}

	// The agent's implicit account for this boot: read once per boot from the quote the index lists, checked
	// against the index's SHA-256 and the boot id (boot-<unix>-<first 8 hex of the account>, agent/src/main.rs).
	private async agentAccount(index: AgentIndex): Promise<string | { error: string }> {
		const boot = index.boot ?? '';
		const known = await this.ctx.storage.get<{ boot: string; account: string }>('fund:agent');
		if (known && known.boot === boot) return known.account;
		const f = (index.files ?? []).find((x) => x.name === 'tdx_quote.hex');
		if (!f) return { error: 'no tdx_quote.hex in the agent index (no TEE)' };
		const q = await this.readEvidence('/evidence/tdx_quote.hex');
		if (q.status !== 200) return { error: `tdx_quote.hex answered ${q.status}` };
		if ((await sha256Hex(q.body)) !== f.sha256) return { error: 'tdx_quote.hex differs from the SHA-256 in the index' };
		const acct = implicitFromQuote(q.body);
		if (typeof acct !== 'string') return acct;
		if (boot.split('-').pop() !== acct.slice(0, 8)) return { error: `quote key ${acct.slice(0, 8)}… does not match boot ${boot}` };
		await this.ctx.storage.put('fund:agent', { boot, account: acct });
		return acct;
	}

	private async fundEvent(ev: Omit<FundEvent, 'time' | 'line' | 'dry_run'> & { dry_run?: boolean }, line: string): Promise<string> {
		const e: FundEvent = {
			time: new Date().toISOString(), decision: ev.decision, reason: ev.reason, pending: ev.pending, account: ev.account,
			boot: ev.boot, amount_yocto: ev.amount_yocto, near_tx: ev.near_tx, dry_run: ev.dry_run ?? false, line,
		};
		const events = ((await this.ctx.storage.get<FundEvent[]>('fund:events')) ?? []).concat(e);
		await this.ctx.storage.put('fund:events', events.slice(-FUND_EVENTS_KEPT));
		console.log(line);
		return line;
	}

	private async sentLastDay(now: number): Promise<{ kept: FundSent[]; sum: bigint }> {
		const kept = ((await this.ctx.storage.get<FundSent[]>('fund:sent')) ?? []).filter((x) => x.t > now - DAY);
		return { kept, sum: kept.reduce((a, x) => a + BigInt(x.yocto), 0n) };
	}

	private capYocto(): bigint {
		return parseNear(this.env.FUND_NEAR_PER_DAY) ?? 2n * YOCTO;
	}

	// The cron's funding step: one decision, one log line. Routine skips (no URL, nothing pending) are only logged;
	// every other decision is also kept as a funding event (GET /funding).
	async fund(): Promise<string> {
		const quiet = (line: string) => (console.log(line), line);
		if (!this.evidenceBase()) return quiet('fund: skipped no evidence url');
		const cap = this.capYocto();
		if (cap === 0n) return quiet('fund: skipped funding off (FUND_NEAR_PER_DAY=0)');
		if (this.funding) return quiet('fund: skipped a funding step is running');
		this.funding = true;
		try {
			const ix = await this.readEvidence('/');
			let index: AgentIndex;
			try {
				if (ix.status !== 200) throw new Error(`answered ${ix.status}: ${ix.body.slice(0, 200)}`);
				index = JSON.parse(ix.body);
			} catch (e) {
				const why = `agent evidence unreadable: ${e instanceof Error ? e.message : String(e)}`;
				return this.fundEvent({ decision: 'skipped', reason: why, pending: null, account: null, boot: null, amount_yocto: null, near_tx: null }, `fund: skipped ${why}`);
			}
			const vaults = index.vaults ?? [];
			const pending = vaults.filter((v) => v.registration === REGISTERING);
			const boot = index.boot ?? null;
			if (pending.length === 0) return quiet(`fund: skipped no pending registration (pending=0 vaults=${vaults.length})`);
			const tags = pending.map((v) => v.tag ?? v.vault ?? '?').join(',');
			const base = { pending: pending.length, boot, amount_yocto: null, near_tx: null };
			const acct = await this.agentAccount(index);
			if (typeof acct !== 'string') {
				return this.fundEvent({ ...base, decision: 'skipped', reason: acct.error, account: null }, `fund: skipped ${acct.error} pending=${pending.length} [${tags}]`);
			}
			const now = Date.now();
			const { kept, sum } = await this.sentLastDay(now);
			if (sum + FUND_MAX_PER_CALL > cap) {
				const why = `cap: ${fmtNear(sum)} NEAR sent in the last 24 h, a call may send up to ${fmtNear(FUND_MAX_PER_CALL)}, FUND_NEAR_PER_DAY=${fmtNear(cap)}`;
				return this.fundEvent({ ...base, decision: 'skipped', reason: why, account: acct }, `fund: skipped ${why} pending=${pending.length} account=${acct}`);
			}
			const r = await this.relayer('/fund-agent', 'POST', JSON.stringify({ implicit_account: acct }));
			const text = await r.text();
			let out: { ok?: boolean; status?: string; dry_run?: boolean; amount_yocto?: string; near_tx?: string; balance_yocto?: string; error?: string };
			try {
				out = JSON.parse(text);
			} catch {
				out = { ok: false, error: text.slice(0, 200) };
			}
			const sent = out.ok && /^\d+$/.test(out.amount_yocto ?? '') && (out.status === 'funded' || out.dry_run === true);
			if (sent) {
				const y = BigInt(out.amount_yocto as string);
				await this.ctx.storage.put('fund:sent', kept.concat({ t: now, yocto: y.toString() }));
				const dry = out.dry_run === true;
				const line = `fund: pending=${pending.length} sent=${fmtNear(y)} account=${acct} near_tx=${out.near_tx ?? 'none'}${dry ? ' dry_run=true (relayer in dry run: nothing sent)' : ''} [${tags}]`;
				return this.fundEvent({ ...base, decision: 'sent', reason: null, account: acct, amount_yocto: y.toString(), near_tx: out.near_tx ?? null, dry_run: dry }, line);
			}
			const why = out.ok && out.status === 'already funded'
				? `already funded (balance ${fmtNear(BigInt(out.balance_yocto ?? '0'))} NEAR)`
				: `relayer answered ${r.status}: ${out.error ?? text.slice(0, 200)}`;
			return this.fundEvent({ ...base, decision: 'skipped', reason: why, account: acct }, `fund: skipped ${why} pending=${pending.length} account=${acct}`);
		} finally {
			this.funding = false;
		}
	}

	private async fundingView(): Promise<Response> {
		const { sum } = await this.sentLastDay(Date.now());
		const cap = this.capYocto();
		return json(200, {
			ok: true,
			evidence_url_set: this.evidenceBase() !== '',
			cap_near_per_day: fmtNear(cap),
			sent_last_24h: fmtNear(sum),
			sent_last_24h_yocto: sum.toString(),
			max_per_call: fmtNear(FUND_MAX_PER_CALL),
			events: (await this.ctx.storage.get<FundEvent[]>('fund:events')) ?? [],
		});
	}

	override async fetch(request: Request): Promise<Response> {
		const url = new URL(request.url);
		const path = url.pathname;
		const method = request.method;
		if (path === '/rebalance' && method === 'POST') {
			const { status, ...out } = await this.round(url.search, 'manual');
			return json(status, out);
		}
		if (path === '/pause' && method === 'POST') {
			await this.ctx.storage.put('paused', true);
			const idle = await this.call('/idle', { method: 'POST' });
			console.log('timer paused');
			return json(idle.ok ? 200 : 504, { ok: idle.ok, paused: true, idle: idle.ok });
		}
		if (path === '/resume' && method === 'POST') {
			await this.ctx.storage.put('paused', false);
			console.log('timer resumed');
			return json(200, { ok: true, paused: false });
		}
		if (path === '/timer' && method === 'GET') {
			const runs = (await this.ctx.storage.get<string[]>('runs')) ?? [];
			return json(200, { ok: true, paused: await this.paused(), container: await this.getState(), last_runs: runs.slice(-6) });
		}
		if (path === '/runs' && method === 'GET') {
			const n = Math.max(1, Math.min(RUNS_KEPT, Number(url.searchParams.get('n') ?? 200) || 200));
			const runs = (await this.ctx.storage.get<string[]>('runs')) ?? [];
			return new Response(runs.slice(-n).join('\n') + '\n', { headers: { 'Content-Type': 'text/plain' } });
		}
		if (path === '/state' && method === 'GET') {
			const state: Record<string, unknown> = {};
			for (const n of STATE_NAMES) state[n] = (await this.ctx.storage.get(`state:${n}`)) ?? null;
			return json(200, { ok: true, stored: state });
		}
		if (path === '/state' && method === 'PUT') {
			const body = await request.text();
			const r = await this.call('/state', { method: 'PUT', body, headers: { 'Content-Type': 'application/json' } });
			const out = (await r.json()) as { ok: boolean; state?: Record<string, unknown> };
			if (r.ok) await this.saveState(out.state, ['live', 'demo']);
			console.log(`state replaced: ${r.status}`);
			return json(r.status, out);
		}
		if (path === '/funding' && method === 'GET') return this.fundingView();
		if (path === '/create-vault' && method === 'POST') return this.createVault(request);
		return this.relayer(path, method, method === 'POST' ? await request.text() : undefined);
	}

	// /create-vault, open to browsers: a slot is reserved before the relayer is called (so two requests at once
	// cannot both pass the check) and released unless the relayer accepted the creation.
	private async createVault(request: Request): Promise<Response> {
		const perIp = positiveInt(this.env.CREATE_PER_IP_PER_HOUR, 2);
		const perDay = positiveInt(this.env.CREATE_PER_DAY, 20);
		const ip = await ipTag(request.headers.get('X-Client-IP') || 'unknown');
		const now = Date.now();
		const kept = ((await this.ctx.storage.get<Creation[]>('creations')) ?? []).filter((c) => c.t > now - DAY);
		const mine = kept.filter((c) => c.ip === ip && c.t > now - HOUR);
		const refuse = (error: string, until: number) => {
			const s = Math.max(1, Math.ceil((until - now) / 1000));
			console.log(`create-vault limited: ${error}`);
			return json(429, { ok: false, error: `${error}; retry in ${s} s` }, { 'Retry-After': String(s) });
		};
		if (mine.length >= perIp) return refuse(`at most ${perIp} vault creations per client IP per hour`, mine[0].t + HOUR);
		if (kept.length >= perDay) return refuse(`at most ${perDay} vault creations per 24 hours in all`, kept[0].t + DAY);
		const id = crypto.randomUUID();
		await this.ctx.storage.put('creations', kept.concat({ t: now, ip, id }));
		let r: Response | undefined;
		try {
			r = await this.relayer('/create-vault', 'POST', await request.text());
			return r;
		} finally {
			if (!r || (r.status !== 202 && r.status !== 200)) {
				const all = (await this.ctx.storage.get<Creation[]>('creations')) ?? [];
				await this.ctx.storage.put('creations', all.filter((c) => c.id !== id));
			}
		}
	}

	private async relayer(path: string, method: string, body: string | undefined): Promise<Response> {
		const r = await this.call(path, { method, headers: { 'Content-Type': 'application/json' }, body });
		if (method === 'POST') {
			// keep the relayer's state file (funding cap, held transactions) across container restarts
			this.ctx.waitUntil(
				this.call('/state')
					.then((s) => s.json() as Promise<{ state?: Record<string, unknown> }>)
					.then((s) => this.saveState(s.state, ['relayer']))
					.catch((e) => console.error('relayer state not saved', e)),
			);
		}
		console.log(`relayer ${method} ${path} -> ${r.status}`);
		return r;
	}
}

function corsHeaders(request: Request, env: Bindings): Record<string, string> {
	const origin = request.headers.get('Origin');
	const allowed = env.ALLOWED_ORIGINS.split(',').map((s) => s.trim());
	if (!origin || !allowed.includes(origin)) return { Vary: 'Origin' };
	return {
		'Access-Control-Allow-Origin': origin,
		'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
		'Access-Control-Allow-Headers': 'Content-Type, Authorization',
		'Access-Control-Expose-Headers': 'Retry-After',
		'Access-Control-Max-Age': '600',
		Vary: 'Origin',
	};
}

function authorized(request: Request, env: Bindings): boolean {
	const token = env.ADMIN_TOKEN;
	const got = request.headers.get('Authorization') ?? '';
	if (!token) return false;
	const a = new TextEncoder().encode(got);
	const b = new TextEncoder().encode(`Bearer ${token}`);
	return a.byteLength === b.byteLength && crypto.subtle.timingSafeEqual(a, b);
}

function backend(env: Bindings): DurableObjectStub<CmdkBackend> {
	return env.BACKEND.getByName(NAME);
}

export default {
	async fetch(request: Request, env: Bindings): Promise<Response> {
		const url = new URL(request.url);
		const path = url.pathname.replace(/^\/relayer(?=\/)/, '');
		const method = request.method;
		const cors = corsHeaders(request, env);
		const agentPath = agentRoute(path);
		if (agentPath !== undefined) {
			if (method === 'OPTIONS') return new Response(null, { status: 204, headers: cors });
			if (method !== 'GET') return json(405, { ok: false, error: 'GET only' }, { ...cors, Allow: 'GET, OPTIONS' });
			const base: string = env.AGENT_EVIDENCE_URL ?? '';
			if (!base.trim()) return json(503, { ok: false, error: 'agent evidence url not set' }, { ...cors, 'Cache-Control': 'no-store' });
			const e = await backend(env).agentEvidence(agentPath);
			const headers: Record<string, string> = { 'Content-Type': e.type, ...cors };
			if (e.status === 502) headers['Cache-Control'] = 'no-store';
			else Object.assign(headers, { 'Cache-Control': `public, max-age=${EVIDENCE_TTL_MS / 1000}`, Age: String(e.age) });
			return new Response(e.body, { status: e.status, headers });
		}
		const isRelayer = (method === 'GET' && OPEN_GET.test(path)) || (method === 'POST' && (OPEN_POST.has(path) || GATED_POST.has(path)));
		if (method === 'OPTIONS' && (OPEN_GET.test(path) || OPEN_POST.has(path) || GATED_POST.has(path))) {
			return new Response(null, { status: 204, headers: cors });
		}
		if (!isRelayer && !ADMIN.has(path)) return json(404, { ok: false, error: 'no such endpoint' }, cors);
		if ((ADMIN.has(path) || GATED_POST.has(path)) && !authorized(request, env)) {
			return json(env.ADMIN_TOKEN ? 401 : 503, { ok: false, error: env.ADMIN_TOKEN ? 'admin token required' : 'ADMIN_TOKEN is not set' }, cors);
		}
		const forward = new Request(`http://do${path}${url.search}`, {
			method,
			headers: { 'Content-Type': 'application/json', 'X-Client-IP': request.headers.get('CF-Connecting-IP') ?? '' },
			body: method === 'GET' ? undefined : await request.text(),
		});
		const r = await backend(env).fetch(forward);
		// CORS is set here once: drop any Access-Control-* header from behind (a relayer started with --cors-origin
		// would send its own, and a browser refuses two Access-Control-Allow-Origin values), then set ours.
		const headers = new Headers();
		for (const [k, v] of r.headers) if (!k.toLowerCase().startsWith('access-control-')) headers.append(k, v);
		for (const [k, v] of Object.entries(cors)) headers.set(k, v);
		return new Response(r.body, { status: r.status, headers });
	},

	async scheduled(controller: ScheduledController, env: Bindings): Promise<void> {
		const b = backend(env);
		try {
			console.log(`cron ${controller.cron}: ${await b.cron()}`);
		} catch (e) {
			console.error(`cron ${controller.cron}: round failed`, e);
		}
		// after the round, paused or not (the pause is the rebalancer's switch); fund() logs its own line
		try {
			await b.fund();
		} catch (e) {
			console.error(`cron ${controller.cron}: fund: failed`, e);
		}
	},
} satisfies ExportedHandler<Bindings>;
