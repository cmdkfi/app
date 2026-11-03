// cmdk relayer and rebalancer timer on Cloudflare Containers. Testnet only (Base Sepolia, NEAR testnet).
//
// One Durable Object (CmdkBackend, name "cmdk") owns one always-on container (cloud/Dockerfile, cloud/front.py):
// relayer.py serve on the container's 127.0.0.1:8787 behind front.py on :8080. The Durable Object's storage
// keeps the state files (the container's disk is ephemeral) and passes them to the container at every start.
//
// Public routes (CORS for ALLOWED_ORIGINS), also under a /relayer prefix:
//   GET /health, GET /vault/<id>, POST /owner-call            open (an owner call needs the owner's signature)
//   POST /create-vault, POST /fund-agent                      Authorization: Bearer ADMIN_TOKEN: they spend the
//                                                             relayer's NEAR without any owner signature
// Admin routes (Authorization: Bearer ADMIN_TOKEN):
//   POST /rebalance[?market=live|demo|both][&dry=1|&status=1][&tolerance=X]   one round now, paused or not
//   POST /pause   the cron stops starting rounds; returns once a running round has finished
//   POST /resume  the cron starts a round every 10 minutes again
//   GET  /timer   paused flag, container status, the last RUN lines
//   GET  /runs[?n=N]   the stored RUN lines, oldest first (N default 200)
//   GET  /state   the stored state;  PUT /state {"live": {...}, "demo": {...}} replaces the rebalancer's state
// Cron "*/10 * * * *": one round (live, then demo), unless paused. A fresh Durable Object starts paused.

import { Container } from '@cloudflare/containers';

interface Secrets {
	BASE_EOA_KEY_JSON?: string;
	NEAR_RELAYER_KEY_JSON?: string;
	NEAR_RELAYER_ACCOUNT?: string;
	ADMIN_TOKEN?: string;
}
type Bindings = Env & Secrets;

const PORT = 8080;
const NAME = 'cmdk';
const OPEN_GET = /^\/(health|vault\/[^/]+)$/;
const OPEN_POST = new Set(['/owner-call']);
const GATED_POST = new Set(['/create-vault', '/fund-agent']);
const ADMIN = new Set(['/rebalance', '/pause', '/resume', '/timer', '/runs', '/state']);
const RUNS_KEPT = 5000;
const STATE_NAMES = ['live', 'demo', 'relayer'] as const;

type RoundResult = { ok: boolean; error?: string; runs?: { market: string; exit: number; line: string }[]; state?: Record<string, unknown> };

function json(status: number, body: unknown, headers: Record<string, string> = {}): Response {
	return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json', ...headers } });
}

export class CmdkBackend extends Container<Bindings> {
	defaultPort = PORT;
	sleepAfter = '24h';
	enableInternet = true;

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
		// relayer routes
		const init: RequestInit = { method, headers: { 'Content-Type': 'application/json' } };
		if (method === 'POST') init.body = await request.text();
		const r = await this.call(path, init);
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
	return (env.BACKEND as DurableObjectNamespace<CmdkBackend>).getByName(NAME);
}

export default {
	async fetch(request: Request, env: Bindings): Promise<Response> {
		const url = new URL(request.url);
		const path = url.pathname.replace(/^\/relayer(?=\/)/, '');
		const method = request.method;
		const cors = corsHeaders(request, env);
		const isRelayer = (method === 'GET' && OPEN_GET.test(path)) || (method === 'POST' && (OPEN_POST.has(path) || GATED_POST.has(path)));
		if (method === 'OPTIONS' && (OPEN_GET.test(path) || OPEN_POST.has(path) || GATED_POST.has(path))) {
			return new Response(null, { status: 204, headers: cors });
		}
		if (!isRelayer && !ADMIN.has(path)) return json(404, { ok: false, error: 'no such endpoint' }, cors);
		if ((ADMIN.has(path) || GATED_POST.has(path)) && !authorized(request, env)) {
			return json(env.ADMIN_TOKEN ? 401 : 503, { ok: false, error: env.ADMIN_TOKEN ? 'admin token required' : 'ADMIN_TOKEN is not set' }, cors);
		}
		const forward = new Request(`http://do${path}${url.search}`, { method, headers: { 'Content-Type': 'application/json' }, body: method === 'GET' ? undefined : await request.text() });
		const r = await backend(env).fetch(forward);
		const headers = new Headers(r.headers);
		for (const [k, v] of Object.entries(cors)) headers.set(k, v);
		return new Response(r.body, { status: r.status, headers });
	},

	async scheduled(controller: ScheduledController, env: Bindings, ctx: ExecutionContext): Promise<void> {
		const out = await backend(env).cron();
		console.log(`cron ${controller.cron}: ${out}`);
	},
} satisfies ExportedHandler<Bindings>;
