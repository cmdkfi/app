# Relayer service (`relayer.py serve`)

**What it does.** It carries owner messages to the vault on NEAR and the vault's signed transactions to Base
Sepolia; it creates vaults through the factory and funds a new agent's implicit account. It pays NEAR gas from
`--sender` (signed only through near-cli-rs's keychain, `sign-with-keychain`) and broadcasts on Base.

**What it does not do.** It holds no authority. It cannot make the vault sign anything: the vault checks the
owner's signature and its allowlist and builds every Base transaction itself. The relayer never holds an EVM key.
Because the vault cannot read Base, the relayer reads Base before it composes a call (design §4.1). These
checks only stop a call that would fail; they grant nothing.

## Run it

```sh
cd /Users/vf/cmdk/app
scripts/relayer.sh serve --port 8787 --sender RELAYER.testnet --factory FACTORY.testnet    # restarts after a crash
python3 -I scripts/relayer.py serve ... --dry-run                 # sends nothing, reads nothing
python3 -I scripts/relayer.py serve ... --dry-run --allow-reads   # reads Base and NEAR, sends nothing
```

`relayer.sh` restarts the service 5 s after a crash, with a log line; exit 0 (Ctrl-C) or 2 (refused to start)
stops it. To keep it running after logout: `nohup scripts/relayer.sh serve ... >> relayer.log 2>&1 &`; or a
launchd agent (macOS) or a systemd unit (Linux) whose command is the same `relayer.sh serve ...` line, with
`KeepAlive`/`Restart=on-failure` left off since the script restarts itself. The service binds 127.0.0.1 only;
the app's server-side proxy calls it, and it is never exposed to the internet.

Tests (no network): `python3 -I -m unittest discover -s scripts -p test_relayer.py` from the repository root,
or `python3 -I scripts/test_relayer.py`. (`-m unittest scripts/test_relayer.py` fails under `-I`, which keeps
the working directory off `sys.path`.)

## Endpoints

Every response is `{"ok": true, ...}` or `{"ok": false, "error": "..."}` (400 refused, 502 chain error).

| Request | Response |
|---|---|
| `POST /create-vault {"owner":"0x..","policy":{"cooldown_secs":120,"markets":[{"id","loan_token","collateral_token","executor","slice_max":"1000000000"}]},"deposit_near":"2"}` | `{ok, near_tx, vault:"v1.FACTORY", base_address:"0x.."}` from the factory's `creating vault:` log |
| `GET /vault/v1.FACTORY` | `{ok, vault, state: get_state, base: {address, eth_wei, pending_nonce, tokens: {token: balance}, markets: [{id, executor, morpho_authorized, loan_allowance, collateral_allowance, corridor: {..., set}, position: {collateral, borrow_shares, debt}}]}, notes}` |
| `POST /owner-call {"vault":"v1.FACTORY","message":"{\"protocol\":\"cmdk-vault/1\",...}","signature":"0x.."}` | `{ok, near_tx, signed: [{raw, hash, from, to, nonce, value}], receipts: [{hash, status, block, gasUsed, effectiveGasPrice, l1Fee}]}`; on a revert `ok: false`, the receipts so far, `held` (not broadcast) |
| `POST /fund-agent {"implicit_account":"<64 hex>"}` | `{ok, status:"funded", near_tx, amount_yocto}` or `{ok, status:"already funded", balance_yocto}` |
| `GET /health` | `{ok, dry_run, sender_balance, base_block, counters, agents_funded, uptime_s}` |

Example: `curl -s localhost:8787/fund-agent -d '{"implicit_account":"'$IMPLICIT'"}'` after the agent prints
`WAITING implicit_account=...`. It tops the account up to 0.15 NEAR plus 0.01, at most 0.5 NEAR per account
per 24 hours.

**Owner call.** The relayer reads `get_state`, counts the Base transactions the method produces (setup: see
below; set_corridor, open, repay, close, withdraw, cancel, sweep_eth 1; set_executor 0 while another
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
  nonce above `owner_nonce`, at least 30 s before expiry, a known method with exactly its argument fields and
  types; signature 65 bytes of hex. Market, token and destination as the vault checks them.
- Flags against Base, `setup`: per executor of the listed markets, "some part has `authorize`" must equal
  "Morpho `isAuthorized(vault, executor)` is false" (true while already set reverts; false while unset leaves
  the markets unusable). Per (token, executor) of every listed market, an allowance of 0 must be approved by
  some part (`approve` true); `approve` true on an allowance already set is allowed (approve does not revert).
  The app reads both from `GET /vault` (`morpho_authorized`, `loan_allowance`, `collateral_allowance`).
- Flags against Base, `set_executor`: `revoke_authorization` must equal "Morpho still authorizes the old executor" when a revoke is due.
- `close`, `repay` (item 14): loan-token allowance to the executor and loan-token balance at least the debt
  (or the repay amount) plus 1%; `repay` with no debt is refused. `open`: Morpho authorization and collateral
  allowance at least `collateral_in`.
- Funds: the vault's Base ETH covers `gas_limit x max_fee + value` for every transaction (L1 fee not included).
- Create vault: the factory's checks (owner and tokens 20 bytes of hex, ids 32 bytes, non-zero executor, at
  least one market, no duplicate ids), plus types and unknown keys; deposit above 0 and at most 10 NEAR.
- Each signed transaction returned: from the vault's Base address, at the nonce given, chain id 84532.

## Testnet-only guards

At start-up the service reads `eth_chainId` (must be 84532) and NEAR `status.chain_id` (must be `testnet`) and
exits with 2 otherwise. Every near command ends `network-config testnet sign-with-keychain send`; account ids
must end in `.testnet` (an agent's implicit account is 64 hex). Every raw transaction's chain id is decoded and
must be 84532 before `eth_sendRawTransaction`, in the service and in `relayer.py broadcast`. Bind: 127.0.0.1.

## State file

`--state`, default `scripts/state/relayer.json` (git-ignored), JSON written atomically (temp file, fsync,
rename): `funded` (per implicit account: time, yoctoNEAR, NEAR tx), `vaults` (per vault: the last broadcast,
its receipts, and the raw transactions `held` after a failure, for `relayer.py broadcast`), `counters`. A dry
run never writes it.
