# Relayer service (`relayer.py serve`)

**What it does.** It carries owner messages to the vault on NEAR and the vault's signed transactions to Base
Sepolia; it creates vaults through the factory and funds a new agent's implicit account. It pays NEAR gas from
`--sender` (signed only through near-cli-rs: the keychain, `sign-with-keychain`, or in the container an access-key
file, `sign-with-access-key-file`) and broadcasts on Base.

**What it does not do.** It holds no authority. It cannot make the vault sign anything: the vault checks the
owner's signature and its allowlist and builds every Base transaction itself. The relayer holds one EVM key,
the service wallet's, and uses it for one thing: the ETH float it sends to a new vault (below); that key has no
role in the vault. Because the vault cannot read Base, the relayer reads Base before it composes a call (design §4.1). These
checks only stop a call that would fail; they grant nothing.

## Run it

```sh
cd <repo>
scripts/relayer.sh serve --port 8787 --sender RELAYER.testnet --factory FACTORY.testnet    # restarts after a crash
python3 -I scripts/relayer.py serve ... --dry-run                 # sends nothing, reads nothing
python3 -I scripts/relayer.py serve ... --dry-run --allow-reads   # reads Base and NEAR, sends nothing
```

| Flag | Default | Meaning |
|---|---|---|
| `--eth-float ETH` | `0.005` | ETH sent from the service wallet to each new vault's Base address at `/create-vault`; `0` disables it |
| `--eth-key-file FILE` | `~/.config/prime/base_sepolia_eoa.json` | the service wallet's key file, `{"data": {"private_key", "address"}}` (or a one-element list); read only when the float is on |
| `--cors-origin ORIGIN` | none | an allowed browser origin, `scheme://host[:port]`; repeatable; none means no CORS headers |

With the float on, `serve` refuses to start (exit 2, so `relayer.sh` does not restart it) when the key file
cannot be read, when its `address` is not the address of its key, or when the wallet holds less than 20 floats
(0.1 ETH at the default). A dry run without `--allow-reads` reads the key file but not the balance.

`relayer.sh` restarts the service 5 s after a crash, with a log line; exit 0 (Ctrl-C) or 2 (refused to start)
stops it. To keep it running after logout: `nohup scripts/relayer.sh serve ... >> relayer.log 2>&1 &`; or a
launchd agent (macOS) or a systemd unit (Linux) whose command is the same `relayer.sh serve ...` line, with
`KeepAlive`/`Restart=on-failure` left off since the script restarts itself. The service binds 127.0.0.1 only;
a reverse proxy in front of it serves HTTPS. The proxy exposes `GET /health`, `GET /vault/<id>`,
`GET /job/<id>`, `POST /create-vault` and `POST /owner-call`; `POST /fund-agent` is operator plumbing and stays
private.

**On Cloudflare (from 6 Nov, `cloud/README.md`).** The container runs the same `relayer.sh serve`, still on
127.0.0.1, with the float on and without `--cors-origin`; `cloud/front.py` proxies to it, and the Worker at
`https://relayer.cmdk.fi` is the proxy and the only place that does CORS (it replaces any `Access-Control-*`
header from behind). `GET /health`, `GET /vault/<id>`, `GET /job/<id>` and `POST /owner-call` are open.
`POST /create-vault` is open with limits: 2 per client IP per hour and 20 per 24 hours in all, 429 with
`Retry-After` beyond, counted only for an accepted creation; it answers 503 until the factory account exists, and
`front.py` adds `factory_exists` to `/health`. `POST /fund-agent` needs the admin token, since it spends the
relayer's NEAR without any signature; the Worker's cron makes the call itself for the agent's pending registrations
once `AGENT_EVIDENCE_URL` is set (`cloud/README.md`, "Agent funding"). When
`NEAR_ACCESS_KEY_FILE` names a near-cli-rs access-key file, every near command signs with
`sign-with-access-key-file <path>` instead of `sign-with-keychain`; the Mac leaves it unset.

Tests (no network): `python3 -I -m unittest discover -s scripts -p test_relayer.py` from the repository root,
or `python3 -I scripts/test_relayer.py`. (`-m unittest scripts/test_relayer.py` fails under `-I`, which keeps
the working directory off `sys.path`.)

## Endpoints

Every response is JSON with a boolean `ok`: `{"ok": true, ...}` or `{"ok": false, "error": "..."}`.

| HTTP | Body | Meaning |
|---|---|---|
| 200 | `{"ok": true, ...}` | success (a synchronous call, a found job in any stage, a read) |
| 200 | `{"ok": false, "error": "...", "near_tx": "...", ...}` | a synchronous call sent a NEAR transaction that failed, a Base transaction reverted, or the float failed; also a job in stage `failed`. Check `ok`, never only the status |
| 202 | `{"ok": true, "job": "<16 hex>"}` | an asynchronous call was accepted; poll `GET /job/<id>` |
| 400 | `{"ok": false, "error": "<refusal>"}` | refused before anything was sent |
| 404 | `{"ok": false, "error": "no such endpoint"}` or `"no such job"` | wrong path, or a job id that is unknown or already dropped |
| 502 | `{"ok": false, "error": "chain: <detail>"}` | an RPC or CLI call failed, possibly after the NEAR transaction was sent |
| 500 | `{"ok": false, "error": "<ExceptionName>: <detail>"}` | a bug |

| Request | Response |
|---|---|
| `POST /create-vault {"owner":"0x..","policy":{"cooldown_secs":120,"markets":[{"id","loan_token","collateral_token","executor","slice_max":"1000000000"}]},"deposit_near":"2"}` | 202 `{ok, job}`; with `"wait": true`, `{ok, near_tx, vault:"v1.FACTORY", base_address:"0x..", float}` from the factory's `creating vault:` log (`float` only with the float on) |
| `GET /vault/v1.FACTORY` | `{ok, vault, state: get_state, base: {address, eth_wei, pending_nonce, tokens: {token: balance}, markets: [{id, executor, morpho_authorized, loan_allowance, collateral_allowance, corridor: {..., set}, position: {collateral, borrow_shares, debt}}]}, notes}` |
| `POST /owner-call {"vault":"v1.FACTORY","message":"{\"protocol\":\"cmdk-vault/1\",...}","signature":"0x.."}` | 202 `{ok, job}`; with `"wait": true`, `{ok, near_tx, signed: [{raw, hash, from, to, nonce, value}], receipts: [{hash, status, block, gasUsed, effectiveGasPrice, l1Fee}]}`; on a revert `ok: false`, the receipts so far, `held` (not broadcast) |
| `GET /job/<id>` | the job (below); 404 for an unknown id |
| `POST /fund-agent {"implicit_account":"<64 hex>"}` | synchronous only: `{ok, status:"funded", near_tx, amount_yocto}` or `{ok, status:"already funded", balance_yocto}`; when the account did not exist yet (the first funding of a boot) `near_tx` is `null` and `created: true, balance_after_yocto` are added (below) |
| `GET /health` | `{ok, dry_run, sender_balance, base_block, counters, agents_funded, uptime_s, jobs: {kept, max, running, stages: {stage: count}}, eth_float_wei, wallet: {address, balance_wei, balance, floats_left} or null}` |

Paths are exact: no query string, no trailing slash.

## Asynchronous calls (jobs)

`POST /create-vault` and `POST /owner-call` answer at once with HTTP 202 and run in a thread. The request
fields are as above; an optional `"wait": true` (a JSON boolean) runs the call synchronously instead and
answers as on 3 Nov: one response when the NEAR transaction is final and every Base receipt is in, failures
after sending as HTTP 200 with `ok: false`. `"wait": false` or no `wait` is asynchronous. The CLI subcommands
(`submit`, `broadcast`) do not use the service.

What is checked before the 202: the request's own fields (missing or unknown keys, the vault id, the
create-vault policy and deposit). A refusal there is HTTP 400 at once and starts no job. Everything that needs
a read (the message against `get_state`, the flags against Base, the gas money) runs in the job, so a refusal
found there ends the job in `failed` with `http_status` 400 and nothing sent. One sending job runs at a time
(`send_lock`); a job waiting for it stays in `accepted`, and the message's expiry is checked when it starts.

`GET /job/<id>` answers 200 for a known job in any stage and 404 `{"ok": false, "error": "no such job"}`
otherwise. A job is `{ok, job, kind, stage, times, ...}`: `kind` is `owner_call` or `create_vault`; `times`
holds the unix time (seconds, 3 decimals) at which each stage was first reached, in order; the other fields
are added as they become known and stay.

| Kind | Stages, in order | Added at the stage |
|---|---|---|
| `owner_call` | `accepted` | `vault` |
| | `vault_checked` | the NEAR call succeeded and returned the signed Base transactions: `method`, `near_tx`, `signed` |
| | `base_sent` | `base_sent`: the hashes broadcast so far, in nonce order; `receipts` grows as each arrives (the relayer broadcasts one, waits for its receipt, then the next) |
| | `done` | every receipt, each `status` 1; the fields of the synchronous answer |
| `create_vault` | `accepted` | `owner` |
| | `vault_created` | `near_tx`, `vault`, `base_address` (from the factory's log) |
| | `float_sent` | `float: {from, to, value_wei, tx}` (only with the float on) |
| | `done` | `float.nonce`, `float.receipt` with `status` 1 |
| either | `failed` | `ok: false`, `error`, and the other fields of the synchronous answer (`near_tx`, `signed`, `receipts`, `held`, `note`; for create, `vault` and `base_address` when the vault exists), plus `http_status`: the status the synchronous call would have answered (200, 400, 502 or 500) |

`approve_measurement` and `revoke_agent` produce no Base transaction: `accepted`, `vault_checked`, `done`, with
`signed` and `receipts` empty. `near_tx` is added as soon as the near CLI returns it, before
`vault_checked`, so a job that fails on a later RPC read (`http_status` 502) still names it. A dry run ends in
`done` with the dry-run fields (`dry_run`, `near_command`, or `reads_skipped`).

Jobs are kept in memory only, at most 1,000; beyond that the oldest is dropped. A restart of the service
(`relayer.sh` after a crash, a new container) forgets every job, including one in flight; the chains keep the
truth (the NEAR transaction, the vault's `owner_nonce`, the Base nonce and receipts), and the app reconciles
from them as for a lost response.

Examples: the shapes the service returns (taken from a run against the unit tests' fakes); hashes, times and
receipt numbers are placeholders. Request:

```sh
curl -s localhost:8787/owner-call -H 'content-type: application/json' \
  -d '{"vault":"v1.FACTORY","message":"{\"protocol\":\"cmdk-vault/1\",...,\"method\":\"set_corridor\",...}","signature":"0x.."}'
```

```json
{"ok": true, "job": "e39f0bf6e83ef872"}
```

`GET /job/e39f0bf6e83ef872`, at each stage:

```json
{"ok": true, "job": "e39f0bf6e83ef872", "kind": "owner_call", "stage": "accepted",
 "times": {"accepted": 1793725271.392}, "vault": "v1.FACTORY"}

{"ok": true, "job": "e39f0bf6e83ef872", "kind": "owner_call", "stage": "vault_checked",
 "times": {"accepted": 1793725271.392, "vault_checked": 1793725280.101}, "vault": "v1.FACTORY",
 "near_tx": "<base58>", "method": "set_corridor",
 "signed": [{"raw": "0x02…", "hash": "0x…07", "from": "0x<vault Base address>", "to": "0x<executor>", "value": "0", "nonce": 7}]}

{"ok": true, "job": "e39f0bf6e83ef872", "kind": "owner_call", "stage": "base_sent",
 "times": {"accepted": 1793725271.392, "vault_checked": 1793725280.101, "base_sent": 1793725280.402}, "vault": "v1.FACTORY",
 "near_tx": "<base58>", "method": "set_corridor", "signed": [...], "base_sent": ["0x…07"]}

{"ok": true, "job": "e39f0bf6e83ef872", "kind": "owner_call", "stage": "done",
 "times": {"accepted": 1793725271.392, "vault_checked": 1793725280.101, "base_sent": 1793725280.402, "done": 1793725282.010},
 "vault": "v1.FACTORY", "near_tx": "<base58>", "method": "set_corridor", "signed": [...], "base_sent": ["0x…07"],
 "receipts": [{"hash": "0x…07", "status": 1, "block": 100, "gasUsed": 50000, "effectiveGasPrice": 1000000, "l1Fee": 5,
               "from": "0x<vault Base address>", "to": "0x<executor>"}]}

{"ok": false, "job": "da4b4ffd1e720a43", "kind": "owner_call", "stage": "failed",
 "times": {"accepted": 1793725271.422, "vault_checked": 1793725280.1, "base_sent": 1793725280.4, "failed": 1793725282.0},
 "vault": "v1.FACTORY", "near_tx": "<base58>", "method": "set_corridor", "signed": [...], "base_sent": ["0x…07"],
 "receipts": [{"hash": "0x…07", "status": 0, ...}],
 "error": "Base transaction 0x…07 (nonce 7) reverted; the rest were not broadcast", "held": [],
 "note": "held transactions stay valid until their nonce passes; broadcast them with relayer.py broadcast, or ask the owner to sign cancel",
 "http_status": 200}

{"ok": false, "job": "…", "kind": "owner_call", "stage": "failed", "times": {"accepted": …, "failed": …}, "vault": "v1.FACTORY",
 "error": "setup: authorize is true for executor 0x… but Morpho already has isAuthorized(vault, executor) = true, …",
 "http_status": 400}
```

`POST /create-vault` (same 202), then:

```json
{"ok": true, "job": "ce929e78d9b2988a", "kind": "create_vault", "stage": "vault_created",
 "times": {"accepted": 1793725271.422, "vault_created": 1793725283.0}, "owner": "0x…",
 "near_tx": "<base58>", "vault": "v2.FACTORY", "base_address": "0x<vault Base address>"}

{"ok": true, "job": "ce929e78d9b2988a", "kind": "create_vault", "stage": "float_sent",
 "times": {"accepted": …, "vault_created": …, "float_sent": …}, "owner": "0x…", "near_tx": "<base58>", "vault": "v2.FACTORY",
 "base_address": "0x<vault Base address>",
 "float": {"from": "0x<service wallet>", "to": "0x<vault Base address>", "value_wei": "5000000000000000", "tx": "0x…03"}}

{"ok": true, "job": "ce929e78d9b2988a", "kind": "create_vault", "stage": "done",
 "times": {"accepted": …, "vault_created": …, "float_sent": …, "done": …}, "owner": "0x…", "near_tx": "<base58>",
 "vault": "v2.FACTORY", "base_address": "0x<vault Base address>",
 "float": {"from": "0x<service wallet>", "to": "0x<vault Base address>", "value_wei": "5000000000000000", "tx": "0x…03",
           "nonce": 3, "receipt": {"hash": "0x…03", "status": 1, "block": 100, "gasUsed": 21000, ...}}}

{"ok": false, "job": "481060256446daa6", "kind": "create_vault", "stage": "failed",
 "times": {"accepted": …, "vault_created": …, "failed": …}, "owner": "0x…", "near_tx": "<base58>", "vault": "v2.FACTORY",
 "base_address": "0x<vault Base address>",
 "float": {"from": "0x<service wallet>", "to": "0x<vault Base address>", "value_wei": "5000000000000000"},
 "error": "ETH float of 0.005 ETH to 0x… failed: eth_sendRawTransaction …: {\"code\": -32000, \"message\": \"insufficient funds\"}; the vault exists",
 "http_status": 200}
```

## ETH float at vault creation

With `--eth-float` above 0, `/create-vault` sends that much ETH from the service wallet to the new vault's
Base address once the factory's log names it, so the vault can pay Base gas for its first `setup`. The wallet
reads its pending nonce and the fees (`min(2 x base fee + priority, 2 gwei)`, as for the vault), `cast mktx`
signs a 21,000-gas transfer offline with every field given (chain id 84532, no RPC), the relayer checks the
chain id of the raw transaction and that the RPC's hash equals the signed hash, broadcasts it and waits for
the receipt. The key goes only to the `cast` subprocess; it is never printed, logged or returned, and a `cast`
error has the key removed. One float is sent at a time, under the same `flock` on `scripts/state` (or
`$STATE_DIR`) that `rebalance_pool.py` and DEPLOY.md's `tx` helper take, because they use the same wallet and
nonce; the service waits up to 120 s for it. A failed or reverted float ends the call with `ok: false`, the
`error`, and the vault's `vault`, `base_address` and `near_tx`: the vault exists; send the ETH by hand. `/health` shows the wallet's address, balance and `floats_left` (balance over the float).

## CORS

By default the service sends no CORS headers. With `--cors-origin https://app.example.com` (repeatable), a
request whose `Origin` header is one of them gets `Access-Control-Allow-Origin: <that origin>` on every
response, error answers included, and `OPTIONS` on any path answers 204 with `Access-Control-Allow-Methods:
GET, POST, OPTIONS`, `Access-Control-Allow-Headers: content-type`, `Access-Control-Max-Age: 600`. Every response
then carries `Vary: Origin`. Another origin gets the same answers without the `Access-Control-*` headers, so the
browser blocks it. `OPTIONS` always answers 204 with `Allow: GET, POST, OPTIONS` (before this change it was 501).

Either the service or the reverse proxy does CORS. If the proxy answers `OPTIONS` itself, the service never
sees the preflight. A proxy that adds `Access-Control-Allow-Origin` while the service is started with
`--cors-origin` sends the header twice, and browsers refuse a response with two values: then the proxy must
set (replace) the header rather than add it, or the service runs without `--cors-origin`.

**The first funding creates the account.** An implicit account exists only once it holds NEAR, and near-cli-rs
0.30.1 asks "does not exist, proceed?" before a transfer to it (`src/common.rs`, `handle_validation_warning`),
which no service without a terminal can answer (found 4 Nov: the container's `/fund-agent` failed with `The input
device is not a TTY`). So when the balance reads 0 the service runs that one `send-near` with `--quiet`, which
skips the question but prints nothing, and then reads the balance until it is at least 0.15 NEAR (up to 20 s);
the answer carries `"near_tx": null, "created": true, "balance_after_yocto"`. Every later top-up of an existing
account runs as before and returns the transaction id.

Example: `curl -s localhost:8787/fund-agent -d '{"implicit_account":"'$IMPLICIT'"}'` after the agent prints
`WAITING implicit_account=... pending_vaults=<k> ...`. It tops the account up to 0.15 NEAR plus 0.01 when the
balance is under 0.15, at most 0.5 NEAR per account per 24 hours. The agent registers on each vault that approved
its measurement and each registration burns 0.0116 NEAR, so it is one call per vault (each new `WAITING` line):
0.16 + (N − 1) × 0.0116 NEAR per boot for N vaults; the 24-hour cap covers 30 vaults in one boot.
On Cloudflare (from 6 Nov) nobody needs to watch for `WAITING`: every 10 minutes the Worker's cron reads the agent's
evidence endpoint and, while a watched vault is `registering`, makes this call for the agent's implicit account
(taken from the quote's report_data), within the Worker's own `FUND_NEAR_PER_DAY` (2 NEAR per 24 hours in all);
this service's 0.5 NEAR per account per 24 hours stays the inner bound, and its balance check decides whether
anything is sent (`already funded` otherwise). The call by hand, `curl -s -H @$A $U/fund-agent -d
'{"implicit_account":"<64 hex>"}'` (DEPLOY.md step 8), is the fallback; it is not counted in the Worker's cap.

**Owner call.** The relayer reads `get_state`, counts the Base transactions the method produces (setup: see
below; set_corridor, open, open_from_stables, repay, close, withdraw, cancel, sweep_eth 1; set_executor 0 while another
market still uses the old executor, else 1 for the revoke if flagged plus 1 per distinct token of the markets
that used it; approve_measurement, revoke_agent 0), composes one `TxParams` each (nonce = pending count at the
vault's Base address, consecutive; `max_fee_per_gas = min(2 x base fee + priority, 2 gwei)`; gas limit from the
table in `relayer.py`, because the calldata does not exist before the vault signs), calls `owner_call` with 300
Tgas, and broadcasts every returned transaction at once in nonce order, waiting for each receipt.

**Create vault is one `setup` message.** Its args:
`{"markets": [{"market": "0x..", "approve": true, "authorize": true, "corridor": {"target", "act", "floor",
"sell_budget", "migrate_allowed", "exit_allowed"}}, ...], "measurement": {approve_measurement's fields}}`.
`market`, `approve`, `authorize` are required in every part; `corridor` and `measurement` are optional (absent
or null). Per part, in order, the vault emits approve(executor, max) on the loan and the collateral token if
`approve`, `setAuthorization(executor, true)` if `authorize`, `setCorridor` if `corridor` (and marks the
market's corridor set); a call identical to one already in the message (same token and executor, same
executor) is emitted once. The measurement is stored as `approve_measurement` stores it, with no Base
transaction. Live and backup share tokens and executor, so the Create vault message (live with `approve` and
`authorize`, backup with both false, a corridor on each, the measurement) is 5 Base transactions: approve
USDC, approve WETH, setAuthorization, setCorridor live, setCorridor backup. A setup with no Base transaction
and no measurement, or with a market listed twice, is refused.

## Pre-checks before a message reaches the chain

- Message: JSON with exactly the vault's fields; `protocol` `cmdk-vault/1`, `network` `testnet`, the vault id,
  nonce above `owner_nonce`, at least 30 s before expiry and at most 3600 s ahead (the vault refuses an expiry
  more than an hour after its block time), a known method with exactly its argument fields and
  types; signature 65 bytes of hex. Market, token and destination as the vault checks them.
- Flags against Base, `setup`: per executor of the listed markets, "some part has `authorize`" must equal
  "Morpho `isAuthorized(vault, executor)` is false" (true while already set reverts; false while unset leaves
  the markets unusable). Per (token, executor) of every listed market, an allowance of 0 must be approved by
  some part (`approve` true); `approve` true on an allowance already set is allowed (approve does not revert).
  The app reads both from `GET /vault` (`morpho_authorized`, `loan_allowance`, `collateral_allowance`).
- Flags against Base, `set_executor`: `revoke_authorization` must equal "Morpho still authorizes the old executor" when a revoke is due.
- `close`, `repay` (item 14): loan-token allowance to the executor and loan-token balance at least the debt
  (or the repay amount) plus 1%; `repay` with no debt is refused. `open`: Morpho authorization, collateral
  allowance and collateral balance at the vault's Base address at least `collateral_in` (`collateral_in` 0 is allowed:
  borrow more against what is there). `open_from_stables` (`{"market", "stables_in", "target_ltv"}`; the
  executor pulls `stables_in` of the loan token and swaps it to collateral): Morpho authorization, and
  loan-token allowance to the executor and loan-token balance at the vault's Base address at least `stables_in`;
  `stables_in` 0 is refused (`zero stables_in`, the vault's wording).
- Funds: the vault's Base ETH covers `gas_limit x max_fee + value` for every transaction (L1 fee not included).
- Create vault: the factory's checks (owner and tokens 20 bytes of hex, ids 32 bytes, non-zero executor, at
  least one market, no duplicate ids), plus types and unknown keys; deposit above 0 and at most 10 NEAR.
- Each signed transaction returned: from the vault's Base address, at the nonce given, chain id 84532.

## Testnet-only guards

At start-up the service reads `eth_chainId` (must be 84532) and NEAR `status.chain_id` (must be `testnet`) and
exits with 2 otherwise. Every near command ends `network-config testnet sign-with-keychain send` (or
`sign-with-access-key-file <path> send` in the container); account ids
must end in `.testnet` (an agent's implicit account is 64 hex). Every raw transaction's chain id is decoded and
must be 84532 before `eth_sendRawTransaction`, in the service and in `relayer.py broadcast`. Bind: 127.0.0.1.

## State file

`--state`, default `scripts/state/relayer.json` (git-ignored), JSON written atomically (temp file, fsync,
rename): `funded` (per implicit account: time, yoctoNEAR, NEAR tx), `vaults` (per vault: the last broadcast,
its receipts, and the raw transactions `held` after a failure, for `relayer.py broadcast`), `counters`. A dry
run never writes it.
