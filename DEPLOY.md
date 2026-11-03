# Public testnet deployment, Friday 6 November 2026

The sequence for the plan row "Public testnet deployment" (`docs/03-plan.md` in the planning repo): executor on
Base Sepolia, vault Global Contract and factory on NEAR testnet, team vault, `setup` on all three markets, an
`open` on the live market, an owner-lowered act line and a manual `reduce`, then AI screening rehearsal 1.
Testnet only. Every step that moves funds says how much. Run the steps in order; each has a read-only check.

## 0. Before Friday

| Item | Owner | Check |
|---|---|---|
| Executor freeze read (Wed 4) and vault review | founder | findings in the README's Limitations, no open defect |
| NEAR on the publishing account | founder | at least 85 NEAR (publish about 78.2 NEAR for the 782,389-byte host build; the reproducible wasm's size is read on Friday + factory 2 NEAR + gas); `prime-measure-1792736012.testnet` held 179.32 NEAR on 3 Nov |
| NEAR on the relayer account | founder | at least 5 NEAR (2 NEAR vault deposit, agent funding, gas) |
| Base Sepolia test wallet | founder | at least 0.1 ETH and about 40 USDC (Circle faucet, 20 USDC per 2 h); the live `reduce` in step 6 sells into our own band, which held 4.35 USDC on 3 Nov |
| Stop the rebalancer timer for the duration | whoever runs this | `pkill -INT -f rebalance.sh`; it finishes its round and exits 0. The deploy uses the same wallet, and two senders clash on the nonce |
| Build both wasms and record the hash | whoever runs this | `vault/factory/DEPLOY.md` step 1 |

## 1. Executor on Base Sepolia (gas only, about 0.002 ETH)

```sh
cd /Users/vf/cmdk/app/executor
forge test 2>&1 | tail -3                      # all green before deploying
BACKUP_ORACLE=0x20Ff664b99676a45A13bD77ACB2AFECeA10Cd538 COOLDOWN=60 ./script/deploy.sh
jq . deployments.json
```

`Deploy.s.sol` reuses the backup oracle (CREATE2 salt cannot repeat), finds the existing backup market and demo
pool, sets the MockOracle to the live price, and deploys a new `Executor` with the three markets. Live and backup
keep the public USDC/WETH 0.01% pool (`0x5718…156c`) unless the founder decides otherwise (open question in the
planning todo). Record the new executor address: every later step uses it.

Because `setPrice` moved the demo oracle, re-band the demo pool once (dust on demo):

```sh
cd /Users/vf/cmdk/app && python3 -I scripts/rebalance_pool.py --market demo --remint
python3 -I scripts/rebalance_pool.py --market live --status      # within tolerance, or run it without --status
```

Check (read-only): `cast call <executor> "COOLDOWN()(uint256)" --rpc-url https://base-sepolia-rpc.publicnode.com` → 60;
`cast call <executor> "config(bytes32)" <liveMarketId> ...` returns the live pool and the two feeds.

Then write the new executor address and the deployment block into `deployments/testnet.json` (`base.executor`).

## 2. Vault Global Contract, factory and vault v1 on NEAR testnet (about 78.2 + 2 + 2 NEAR)

Follow `vault/factory/DEPLOY.md` steps 1–7 exactly, with the new executor address in the policy of step 6.
The publish is one per code version: do it only after the founder's vault review. Record in
`deployments/testnet.json`: `near.factory`, `near.vault_code_hash` (base58 and hex), `near.vault` (`v1.<factory>`),
`near.relayer`, and the vault's Base address from the `creating vault` log line.

## 3. Relayer up (no funds)

```sh
cd /Users/vf/cmdk/app
python3 -I scripts/test_relayer.py 2>&1 | tail -2
nohup scripts/relayer.sh serve --port 8787 --sender <relayer>.testnet --factory <factory>.testnet >> ~/.local/state/cmdk/relayer.log 2>&1 &
curl -s localhost:8787/health
```

## 4. Fund the vault's Base address and `setup` on all three markets (one owner signature)

Send about 0.01 ETH from the test wallet to the vault's Base address (gas for its transactions), then sign one
`setup` message with the owner wallet. `scripts/RELAYER.md` has the message shape: per market `approve`,
`authorize` and the corridor (target 50%, act 70%, floor 30%, sell budget as the README states, migrate and
exit allowed). Five Base transactions for live and backup, seven with demo. Submit through
`POST /owner-call`; the response lists each transaction and receipt.

Check (read-only), for each market: `allowance(vaultBase, executor)` on both tokens → 2^256−1;
`isAuthorized(vaultBase, executor)` on Morpho → true; `corridors(vaultBase, marketId)` on the executor → set.

## 5. `open` on the live market

Send WETH (0.01 is enough) from the test wallet to the vault's Base address, then sign `open` (market live,
`collateral_in` 0.01 WETH, `target_ltv` 50%). Check: Morpho `position(liveId, vaultBase)` shows the collateral
and the debt; `ltvOf(vaultBase, liveId)` on the executor is about 0.50e18.

## 6. Owner-lowered act line and a manual `reduce`

Sign `set_corridor` on live with the act line below the current LTV (for example act 45%, target 40%, floor
30%), so the position is above the act line. Then a `reduce` from the agent path (dry run first):
`agent --mode dry --no-tee` prints the proposal and the vault's `check_transaction` verdict; the live send needs a
registered agent key (step 7) or, for the rehearsal, the owner moves the line back and the evidence records
the dry proposal. Record the transactions into `evidence/protect-live.txt` (README step 10) once the agent is
registered.

## 7. Agent

Deploy the CVM per `agent/MEASUREMENT.md` (pinned OS image, measured compose), approve its measurement from
the owner wallet (`approve_measurement`), fund its implicit account through `POST /fund-agent`, and watch
`register_agent` land. Read-only check: `get_state` on the vault shows the agent key and its expiry.

## 8. AI screening rehearsal 1

A fresh clone in an empty directory; Claude Code and Codex each given the Q-017 wording (planning repo,
`research_notes/Hackathon rules/`) as the prompt over the contracts and README verify steps 1–5. Save both
reports under `evidence/screening/2026-11-06-*.md`. Anything they cannot reproduce goes into the todo.

## 9. Restart the timer

```sh
cd /Users/vf/cmdk/app && nohup caffeinate -i scripts/rebalance.sh >> ~/.local/state/cmdk/rebalance.log 2>&1 &
```

## Known gaps to close before step 8

- The README's verify steps name `get_owner`, `get_policy`, `get_position`, `get_agent` on the vault; the vault
  exposes `get_state` (and `check_transaction`). Either add the views or rewrite the steps to `get_state` fields.
- `scripts/reproduce-hash.sh`, `scripts/ltv.sh`, `scripts/attack-key.sh`, `scripts/attack-corridor.sh`,
  `scripts/verify-transcript.sh` (README steps 2, 4, 7, 8, 9) do not exist yet; the evidence row (Tue 10 – Wed 11)
  builds them, and steps 1–5 of the rehearsal need the first two.
