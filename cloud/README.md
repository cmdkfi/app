# Relayer and rebalancer timer on Cloudflare Containers

Testnet only (Base Sepolia, NEAR testnet). This directory runs `scripts/relayer.py serve` and the rebalancer timer
(`scripts/rebalance_pool.py`, live then demo, every 10 minutes) on Cloudflare Containers. It replaces the Mac
(`caffeinate -s scripts/rebalance.sh`, `scripts/relayer.sh serve` on `localhost:8787`), which stays a fallback
(`DEPLOY.md` steps 6 and 7d).

| File | What it is |
|---|---|
| `Dockerfile` | python 3.14 slim (pinned by digest), `cast` (Foundry v1.8.6) and `near` (near-cli-rs v0.30.1), both checked by SHA-256, `jq`; copies `scripts/relayer.py`, `scripts/relayer.sh`, `scripts/rebalance_pool.py`, `deployments/testnet.json`, `cloud/front.py`. No key. Build context: the repository root (`Dockerfile.dockerignore` lets only those files in) |
| `front.py` | The container's entrypoint and only listener (`0.0.0.0:8080`). Writes the keys and the state, starts the relayer on `127.0.0.1:8787`, proxies to it, runs rebalancer rounds |
| `src/index.ts` | The Worker: routes, CORS, the admin token, the Durable Object `CmdkBackend` that owns the container and keeps the state, the cron |
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
  `relayer.py` signs with `sign-with-access-key-file <path>` instead of `sign-with-keychain`. Values are never
  printed; the log shows the Base address and the NEAR public key.
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
- **SIGTERM** (a stop or a rollout): a running round finishes first, then the container exits (tested locally:
  SIGTERM at 18:44:50, the round's response at 18:45:11, exit at 18:45:11). Cloudflare waits up to 15 minutes
  before SIGKILL.

## Routes

Base URL `https://relayer.cmdk.fi` (also `https://cmdk-relayer.fffilimonov.workers.dev`). Every relayer route also
answers under `/relayer/...`. CORS: the origins in `ALLOWED_ORIGINS` (`https://cmdk.fi`, `http://localhost:5173`).

| Route | Access | What |
|---|---|---|
| `GET /health`, `GET /vault/<id>`, `POST /owner-call` | open | relayer.py, unchanged (`scripts/RELAYER.md`). An owner call needs the owner's signature |
| `POST /create-vault`, `POST /fund-agent` | admin token | relayer.py. They spend the relayer's NEAR without any owner signature (a vault deposit of up to 10 NEAR; 0.16 NEAR per fresh implicit account), so they are not open on a public URL |
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
| `NEAR_RELAYER_ACCOUNT` | the relayer account id, `relayer.py serve --sender` | e.g. `prime-measure-1792736012.testnet` |
| `ADMIN_TOKEN` | 64 hex characters | generated below |

Plain variables in `wrangler.jsonc`: `NEAR_FACTORY_ACCOUNT` (`relayer.py serve --factory`; empty means
`deployments/testnet.json` `near.factory`, which is `null` until DEPLOY.md step 7c, and then the relayer does not
start and `/health` says so), `ALLOWED_ORIGINS`, `REBALANCE_ARGS`, `BASE_RPC`, `NEAR_RPC`.

### The relayer's NEAR key

Checked on 3 Nov without printing any key:
- `prime-measure-1792736012.testnet` (relayer.py's default `RELAYER`) has its key in the legacy keychain,
  `~/.near-credentials/testnet/prime-measure-1792736012.testnet.json` (fields `public_key`, `private_key`,
  `master_seed_phrase`, `seed_phrase_hd_path`, `implicit_account_id`; public key
  `ed25519:Uyd6WjYJ6YShbMQEGpKsMKNq192dXMKV91GYsWDGBho`). The macOS keychain has no `near-testnet-` entry for this
  account (it has entries for `g1.`, `p2-vault.` subaccounts and the faucet accounts). `sign-with-keychain` falls
  back to the legacy file, which is why the Mac relayer works.
- `near ... sign-with-access-key-file ~/.near-credentials/testnet/prime-measure-1792736012.testnet.json display`
  signed a 1 yoctoNEAR self-transfer and displayed it without sending: the file works as an access-key file. A file
  with only `account_id`, `public_key`, `private_key` is also accepted (tested with a throwaway key).
- Use `jq` to pass only the three fields into `wrangler secret put` (below): the pipe carries the key, the
  terminal shows nothing, and the seed phrase does not leave the Mac.
- `near account export-account <id> using-private-key network-config testnet` exists in near-cli-rs 0.30.1 but
  prints the private key on the terminal, so it is not used. If the relayer is an account whose key is only in the
  macOS keychain, `security find-generic-password -s near-testnet-<id> -w` reads it to stdout; its format was not
  checked (reading it may also raise a keychain prompt), so the safer path is a new key for the cloud:
  `near account add-key <id> grant-full-access autogenerate-new-keypair save-to-legacy-keychain network-config
  testnet sign-with-keychain send` (gas only), then the `jq` line with the new file
  `~/.near-credentials/testnet/<id>/ed25519_<public key>.json`. A separate key can be deleted later without
  touching the Mac's.

## Commands for F (in order; nothing here was run against Cloudflare)

Before Friday. `$RELAYER` is the relayer account; `$FACTORY` the factory from `vault/factory/DEPLOY.md` step 0
(`factory.$ACCOUNT`).

```sh
cd /Users/vf/cmdk/app/cloud
RELAYER=prime-measure-1792736012.testnet; FACTORY=factory.<ACCOUNT>.testnet
pnpm install                                   # pnpm-workspace.yaml allows the esbuild, sharp, workerd builds
pnpm run check                                 # wrangler types + tsc --noEmit, exit 0

# 1. Factory name into wrangler.jsonc (a plain variable, public)
sed -i '' "s/\"NEAR_FACTORY_ACCOUNT\": \"\"/\"NEAR_FACTORY_ACCOUNT\": \"$FACTORY\"/" wrangler.jsonc
grep NEAR_FACTORY_ACCOUNT wrangler.jsonc

# 2. Admin token: generated into two files outside the repository, mode 600, never printed
( umask 077; openssl rand -hex 32 | tr -d '\n' > ~/.config/prime/relayer_admin_token
  printf 'Authorization: Bearer %s\n' "$(cat ~/.config/prime/relayer_admin_token)" > ~/.config/prime/relayer_admin.header )

# 3. Secrets, piped from files; wrangler reads stdin and prints only "Success! Uploaded secret ..."
#    (the first one creates the Worker cmdk-relayer as an empty draft if it does not exist yet: wrangler 4.156.0's
#    createDraftWorker, which answers its own prompt with yes when stdin is not a terminal)
npx wrangler secret put BASE_EOA_KEY_JSON < ~/.config/prime/base_sepolia_eoa.json
jq --arg a "$RELAYER" '{account_id: $a, public_key, private_key}' ~/.near-credentials/testnet/$RELAYER.json \
  | npx wrangler secret put NEAR_RELAYER_KEY_JSON
printf %s "$RELAYER" | npx wrangler secret put NEAR_RELAYER_ACCOUNT
npx wrangler secret put ADMIN_TOKEN < ~/.config/prime/relayer_admin_token
npx wrangler secret list                       # the four names, no values

# 4. Deploy: builds the linux/amd64 image with Docker, pushes it to Cloudflare's registry, deploys the Worker
npx wrangler deploy
```

Expected from `wrangler deploy` (not run; from the `--dry-run` output and the docs): the image build, `Uploaded cmdk-relayer`, the cron `*/10 * * * *`, the routes
`relayer.cmdk.fi (custom domain)` and `cmdk-relayer.fffilimonov.workers.dev`. The container needs a few minutes to
provision; until then a request answers 503 `There is no Container instance available at this time`. The
certificate for `relayer.cmdk.fi` can take a few minutes too; the workers.dev URL works meanwhile.

Health check (read-only; the first request starts the container, and the relayer needs about 5 s to check both
chains, answering 503 `relayer unreachable (starting or restarting)` until then):

```sh
U=https://relayer.cmdk.fi; A=~/.config/prime/relayer_admin.header
curl -s $U/health | jq -c '{ok, dry_run, sender, factory, chain_id, near_network, base_block, sender_balance}'
curl -s -H @$A $U/timer | jq -c '{paused, container: .container.status}'
curl -s -X POST -H @$A "$U/rebalance?status=1" | jq -r '.runs[].line'
```

Expected: `{"ok":true,"dry_run":false,"sender":"$RELAYER","factory":"$FACTORY","chain_id":84532,"near_network":"testnet","base_block":<about 54,0xx,xxx>,"sender_balance":"<N> NEAR"}`,
then `{"paused":true,"container":"healthy"}`, then two `RUN … action=status … exit=0` lines. Leave the timer
paused; DEPLOY.md step 6 uploads the state and resumes it.

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

Real Base Sepolia RPC and NEAR testnet, nothing sent: a Base key file with the real address and a dummy private
key (a dry run reads only the address), a throwaway NEAR key that is on no account, a local admin token, all in an
`--env-file` outside the repository. `npx wrangler dev --port 8799 --test-scheduled --env-file <file>`. Outputs:

- `GET /health`: `{"ok": true, "dry_run": false, "sender": "prime-measure-1792736012.testnet", "factory":
  "factory.prime-measure-1792736012.testnet", …, "chain_id": 84532, "near_network": "testnet", "uptime_s": 8,
  "counters": {}, "agents_funded": 0, "sender_balance_yocto": "179318203579175434999999979", "base_block":
  54011436, "sender_balance": "179.318203579175434999999979 NEAR"}`.
- `GET /vault/v1.factory.prime-measure-1792736012.testnet`: 502, `chain: query …: UNKNOWN_ACCOUNT … account
  v1.factory.prime-measure-1792736012.testnet does not exist while viewing`. `GET /vault/not-a-testnet-id.near`:
  400, `vault 'not-a-testnet-id.near' is not a NEAR testnet account id`.
- `POST /fund-agent` without the token: 401 `admin token required`; with it and `{"implicit_account":"xyz"}`: 400
  `implicit_account must be 64 lower-case hex characters`. `POST /owner-call {"vault":"bad"}`: 400 `request:
  missing ['message', 'signature'], unknown []`.
- CORS: `OPTIONS /owner-call` from `https://cmdk.fi`: 204 with `Access-Control-Allow-Origin: https://cmdk.fi`;
  `GET /health` from another origin: no `Access-Control-Allow-Origin`.
- Cron while paused: `cron: timer paused; no round started`.
- `POST /rebalance?dry=1` (36.7 s): `RUN 2026-11-03T16:43:14Z market=live tick=198993 oracle=2290.20 pool=2281.75
  gap=-0.369% action=dry-run:band repair_quote=0.255905USDC swap_in=0 txs=0 … exit=0` and `RUN … market=demo
  tick=-199157 oracle=2244.68 pool=2244.68 gap=+0.000% action=dry-run:repair+bands … exit=0`.
- Cron with the timer resumed and `REBALANCE_ARGS=--band-usdc 10 --dry-run` (confirmed in the container's
  environment first): the same two `RUN` lines with `source=cron`, then `POST /pause` → `{"ok":true,"paused":true,"idle":true}`.
- `POST /rebalance?status=1&market=live`: `our positions: none in /app/scripts/state/live.json`, `wallet …: 0.487648
  ETH, 0.057910 WETH, 64.341191 USDC, 25026.493470 dUSDC`; a second round meanwhile: 409 `a rebalancer round is
  running`; `tolerance=0.5`: 400.
- State: `PUT /state {"live":{"probe":"restart-test"},"demo":{}}`, `docker kill` of the container, then `GET
  /health` restarted it and `/app/scripts/state/live.json` held `{"probe": "restart-test"}` again; both key files
  mode 600, owner `cmdk`; the relayer and `relayer.sh` processes carry none of the secret variables.

Not tested locally: anything on Cloudflare (provisioning, the custom domain, real secrets, the cron on schedule,
container logs in the dashboard, a rollout's SIGTERM, the 64-character hostname, for which `front.py` skips
`getfqdn`), and every sending path (create-vault, owner-call, fund-agent, a real round).
