# Crash scenes on the demo market (README step 10)

Testnet only (Base Sepolia, chain id 84532; `scene.py` refuses any other chain). The test wallet
0x0B7Ed66f46fe83252a208563A88201BA60490d25 signs every Base transaction. Its key is read from
`~/.config/prime/base_sepolia_eoa.json` only by `rebalance_pool.key()` and handed only to `cast`; it is never
printed. Addresses come from `deployments/testnet.json`.

`scripts/scene.py` runs one scene: it moves the demo market's price along a fixed path and records three twins that
hold the same position. The output is `evidence/scenes/<scene>.json` and `<scene>.md`.

| Twin | Position (demo market) | Who acts |
|---|---|---|
| `ai-agent` | vault v1's Base address `0xb3ccd0ad…e6fb` (`near.vault_base_address`) | the live enclave agent; `scene.py` only reads the executor's `Reduced` events for this address |
| `keeper` | the test wallet's own executor position | `scene.py`: the agent's protective rule, sent as `reduce` through the executor |
| `unprotected` | vault v2's Base address `0xfc856413…67cf` (v2 has approved no measurement since the revoke scene) | nobody |

After every step, `scripts/liquidate.py --market demo --borrower <address>` runs for each twin past Morpho's
liquidation line, at Morpho's own incentive (LIF 1.0438 at LLTV 86%). The addresses are arguments with these
defaults: `--ai`, `--keeper`, `--unprotected`.

## Price paths

`python3 -I scripts/scene.py plan --scene <name> --start-price <usd>` prints the step table. It reads nothing.
Each move goes through `rebalance_pool.run_demo` with `--set-price`: the pool moves first, then the MockOracle,
each step at most 1.9%. The gap scene's single large move uses `max_step 1`; on the command line this is
`rebalance_pool.py --max-step 1`. The step count is the smallest one that passes `move_demo_price`'s own test on
the oracle's integer prices, so each planned step is exactly one move.

| Scene | Path (from the start price) | Moves | Waits in the plan at the default 180 s per step, 600 s per hold |
|---|---|---|---|
| `gradual-crash` | 32 steps of −1.85% to −45%, hold | 32 | 106 min |
| `gap` | 15 steps of −1.90% to −25%, hold, then −25% → −45% in one move (−26.67%), hold | 16 | 68 min |
| `whipsaw` | 21 steps of −1.82% to −32%, hold, 21 steps of +1.85% back to the start, hold | 42 | 146 min |

Add the transactions of each move to these times: 10 to 12 transactions per move (burn two bands, approve, swap,
mint two bands, `setPrice`). That count comes from the code; the time it takes has not been measured. For a position opened at 50%
that nobody touches, `plan` also prints the first step above the act line (0.70) and the first step past LLTV.
For the gradual crash these are steps 19 and 30, that is −28.6% and −41.9%.

## What one step does

1. **Move the price.** The move is skipped when the oracle is already at the step's target, for example on `--resume`.
   - **Gap row only: the liquidator goes first.** Right after the move, with no wait and before the keeper, the script reads all three twins at the first block after the move. It then runs the liquidator for every twin past the line in that reading, because a real liquidator would act in the block after `setPrice`. Each liquidation from this pass keeps `by: liquidator` and gets `"note": "immediately after the gap move"`; a line in the notes names its hash. After this pass, steps 2 to 6 follow as on any other row.
2. **Wait.** `--step-wait-secs` after a move (default 180), `--hold-secs` on a hold row (default 600). The agent ticks during the wait.
3. **Read all three twins at one block.** For each twin: collateral, debt accrued to that block (Morpho's share math and IRM accrual, as in `ltv.sh` and `liquidate.py`), LTV (CorridorMath rounding), and Morpho's test `debt > value × LLTV`. Also the oracle price and the keeper's `sessionStart`.
4. **Apply the keeper rule.** The keeper is re-read, then the rule from `agent/src/logic.rs` decides. `protective` is true when LTV is above act, or when a session younger than 24 h still has LTV more than 0.1 pp above target. If it is true, the keeper sells as `decide_market`'s protective branch does: `amount_to(target)` (CorridorMath `repayToLtv`), clamped to the debt and to v1's slice maximum (1,000 dUSDC). That amount is sent as `reduce(demo, amount, block time + 600)`. A revert, such as `Cooldown` or `Slippage`, is written to the notes and not retried.
5. **Run the liquidator** for each twin past the line at step 3. The subprocess re-reads the position and sends nothing when the position is healthy.
6. **Read again** if anything was sent. Then rebuild every twin's actions from the chain since the start block: executor `Reduced` events by caller, and Morpho `Liquidate` events by borrower. Write both files.

The run ends with the last hold row. Its reading gives the outcome row for each twin.

**Failure and resume.** Each part of a step is tried up to four times: once, then three retries 10 s apart. The
retries cover RPC errors, a failed price move and an error exit of `liquidate.py`. After the last failure, the
script writes what it has, adds a note with the step number and the transactions of that step, prints
`FAILED at step N` and exits 1. Ctrl-C does the same. `--resume` continues from the failed step and keeps the
records and the start price. A file that already exists is never overwritten without `--resume`, and a scene that
is complete is refused.

## Prerequisites

1. **The three positions are open at 50% with 0.002 WETH each.** This was done on 5 Nov; see `evidence/deploy/2026-11-05.log` from 12:07 UTC.
   - The test wallet sends 0.002 WETH to v1's Base address and 0.002 WETH to v2's: `tx` helper, WETH `transfer`.
   - v2: owner `setup` on demo (approve dUSDC, corridor 50/70/30/20, no measurement), then owner `open` `{"market": <demo>, "collateral_in": "2000000000000000", "target_ltv": "500000000000000000"}`.
   - v1: owner `open` with the same arguments. v1's demo corridor was set in DEPLOY.md 7f, and the agent watches v1.
   - The test wallet calls `open(bytes32,uint256,uint256,uint256)` `<demo> 2000000000000000 500000000000000000 <block time + 600>` on the executor. It needs the approvals, the Morpho authorization and a demo corridor. `attack-corridor.sh` set the approvals and the authorization. The corridor must be the AI twin's (50/70/30/20); otherwise set it first with `setCorridor(<demo>, (500000000000000000,700000000000000000,300000000000000000,200000000000000000,true,true,false), <deadline>)`.

   The owner messages use the `owner` helper and the wallet transactions use the `tx` helper, both from the shell setup in DEPLOY.md §0.
2. **Check.** `python3 -I scripts/scene.py status` should print three rows at `50.00 %`, the corridors of `ai-agent` and `keeper` both `target 0.50 act 0.70 floor 0.30 sell budget 0.20 set True`, and the demo pool within a few hundredths of a percent of the oracle. A real run refuses, and sends nothing, when a twin has no position, or when the keeper has no corridor or a different one from the AI twin.
3. **The agent is live on v1.** `curl -s $U/agent/vaults` should show v1 as registered and active.
4. **Gas.** The wallet needs ETH for 10 to 12 transactions per move. WETH for the pool moves is wrapped from ETH when it runs short, and dUSDC is minted.

## Timer pause and rebalancer state

The container's timer and the Mac share the wallet's nonce. The Mac's state files have been stale since DEPLOY.md
step 6. So, as in DEPLOY.md step 7e, pause the timer around the whole scene and bring the state down first:

```sh
source ~/.local/state/cmdk/deploy-2026-11-06.sh      # DEPLOY.md §0: U, A, RPC, tx, owner
curl -s -X POST -H @$A $U/pause                      # {"ok":true,"paused":true,"idle":true}
curl -s -H @$A $U/state | jq .stored.live > scripts/state/live.json
curl -s -H @$A $U/state | jq .stored.demo > scripts/state/demo.json
```

After the scene, or after `reset`, upload the state and resume the timer:

```sh
jq -n --slurpfile l scripts/state/live.json --slurpfile d scripts/state/demo.json '{live: $l[0], demo: $d[0]}' |
  curl -s -X PUT -H @$A $U/state -H 'Content-Type: application/json' --data-binary @- | jq -c .state
curl -s -X POST -H @$A $U/resume                     # {"ok":true,"paused":false}
```

`scene.py run` and `reset` also take the Mac's rebalancer lock (`rebalance_pool.lock`, exit 4 if it is held). The
container does not see that lock, so the pause is still needed. While the timer is paused, the live pool is not
re-centred.

## Commands

```sh
cd /Users/vf/cmdk/app
python3 -I scripts/test_scene.py                                        # unit tests, no network
python3 -I scripts/scene.py status                                      # reads only
python3 -I scripts/scene.py run --scene gap --dry-run                   # reads only: plan, projection, one step of rebalance_pool's dry-run
python3 -I scripts/scene.py run --scene gap 2>&1 | tee -a ~/.local/state/cmdk/scene-gap.log
python3 -I scripts/scene.py run --scene gap --resume 2>&1 | tee -a ~/.local/state/cmdk/scene-gap.log   # after FAILED at step N
```

The same commands work for `gradual-crash` and `whipsaw`. `tee` hides the exit code: read the last line
(`scene gap done: …` or `FAILED at step N`). `--dry-run` sends nothing and writes nothing, not even the
rebalancer state, which `rebalance_pool` checks for itself.

## Between scenes

```sh
P=$(cast call $(jq -r .base.chainlink.ethUsd.address deployments/testnet.json) 'latestRoundData()(uint80,int256,uint256,uint256,uint80)' --rpc-url $RPC | sed -n 2p | awk '{printf "%.2f", $1 / 1e8}')
python3 -I scripts/scene.py reset --to-price $P --dry-run
python3 -I scripts/scene.py reset --to-price $P                         # steps of at most 1.9 %, then the three twins
```

`reset` uses the same move code as the scenes. It does not close or re-open positions; that stays manual.

- **Close.** v1 and v2: owner `close` `{"market": <demo>}`. The relayer refuses unless the vault's address holds dUSDC of at least the debt plus 1%; on 5 Nov the borrowed amount alone was short by the interest. If so, mint the difference to the vault's address: `tx 'dUSDC to the vault' $DUSDC 'mint(address,uint256)' <vault base address> <units>`, since anyone may mint dUSDC. The wallet: `tx 'wallet close demo' $EXEC 'close(bytes32,uint256)' $DEMO <block time + 600>`. A liquidated position has no debt, and `close` only returns what collateral is left.
- **Re-open** at 50% with 0.002 WETH each, as in the prerequisites. First top up each vault's address to 0.002 WETH, since the collateral a scene sold or Morpho seized is gone.
- **Check** with `scene.py status` before the next scene.

## The files

`evidence/scenes/<scene>.json` follows the plan's format (planning repo, `tasks/scenes-plan.md`):

- **Header.** `scene`, `market`, `started_block`, `started_utc`, `start_price_usd`.
- **`price_path`.** Step 0 is the start, then one entry per plan row, holds included. Each entry has `block`, `utc` and `price_usd` from the reading that closed the step (the oracle at that block), and `tx`, the price move's hashes; a hold has none.
- **`twins.<name>`.** `address`, `kind`, `ltv` and `actions`:
  - `ltv` has one row per reading: `block`, `ltv`, `collateral_wei`, `debt_units`, `price_usd`.
  - `actions` lists reduces and liquidations from the chain's events: `block`, `kind`, `tx`, `repaid_units`, `sold_wei` (collateral sold, or seized), and `by` (`agent`, `keeper` or `liquidator`). A reduce also has `protective`; a liquidation also has `bad_debt_units`, and on the gap row's first pass `note: "immediately after the gap move"`.
- **`outcome`.** `collateral_kept_wei`, `debt_units`, `cash_units`, `net_value_usd_at_end` (collateral × the end oracle price + cash − debt, with dUSDC at 1 USD) and `liquidated`.
- **Footer.** `liquidator` (the test wallet) and `notes`. The notes hold each vault's cash at the start, the keeper's cash basis, the corridor, every refused keeper reduce, and any failure or resume.

`<scene>.md` has the twins, the price path, the LTV table (block × twin), the actions, the outcome table, every
transaction hash and the notes. It states numbers only, with no comparative wording (app-screens A2).

## Limits of this setup

- **The keeper is a script with the agent's protective rule, not the agent binary.** It has no judge, no text, no NEAR signing and no tick loop. It acts once per step, after the wait. It skips the agent's other gates, such as `sale_blocked_by_pool` (pool more than 1% below the oracle) and the read-agreement gate, because the pool is put at the oracle every step. The keeper's position is the wallet's own executor position: the executor runs the same code path for any `msg.sender`, but it is not a vault.
- **Order within a step.**
  - Move and hold rows: move, wait, read, keeper, liquidator. The liquidator runs only after the wait and after the keeper's reduce. With the 1.9% steps, LTV crosses the act line well before the liquidation line, so protection has time.
  - Gap row: move, then the liquidator at once, then the same sequence. Any twin past the line in the first block after `setPrice` is liquidated before the agent's wait and before the keeper. The agent can still act in the blocks between `setPrice` and that reading, a few seconds at most.
  - Our liquidator is a script, not a block-builder. It sends in the next blocks after the reading, so a sale that is already in flight can still land first.
- **The gap's pool and oracle moves are not in adjacent blocks.** `move_demo_price` swaps the pool to the new price, then mints the two bands, then sets the oracle. Four to six transactions separate the pool move from the oracle move. The LTV jump (66.7% → 90.9% at 50% start) happens in the single `setPrice` block. Until then the pool sits about 27% under the oracle, so the agent's pool-gap check blocks a sale.
- **The keeper's cash is a ledger value:** the dUSDC its open borrowed, which is its debt at the start. The test wallet's dUSDC balance also funds the pool bands and the liquidator. A reduce sells collateral for exactly the repaid amount and a liquidation does not touch the borrower's balance, so the borrowed cash is unchanged in every scene. For the vault twins, cash is the dUSDC balance at their Base address.
- **The AI twin is observed only.** Its decisions (`RESULT decision` lines) are in the enclave log and its transcripts, served from the evidence endpoint (README step 8); `scene.py` does not read them.
- **Interest accrues during a scene**, so debt rises slightly between readings. Projections in `--dry-run` ignore interest and assume a fill at the oracle; the executor fills at most 1% worse.
- **One liquidator, ours.** It runs only at step boundaries and has no competition.
- **The venue stress scene (live market, README step 10's last sentence) is not part of `scene.py`.**
