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
# from the repository root
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
- **The venue stress scene (live market, README step 10's last sentence) is not part of `scene.py`;** it is `scripts/venue_stress.py`, below.

# Venue stress on the live market (README step 10, last sentence)

`scripts/venue_stress.py` makes the live market's utilization go above the agent's 95% screen and records what the
live agent does with vault v1's live position. Testnet only (Base Sepolia, chain id 84532; any other chain is
refused). The test wallet signs every transaction through `rebalance_pool.send`, under `rebalance_pool.lock()`.
The output is `evidence/scenes/venue-stress.json` and `venue-stress.md`.

| Position | Address | Role |
|---|---|---|
| `v1` | vault v1's Base address `0xb3ccd0ad…e6fb` (`near.vault_base_address`) | borrower on live; the live enclave agent watches it, the script only reads it |
| `wallet` | the test wallet `0x0B7Ed66f…0d25` | lender on live and backup; the scene's borrower on live |

## What the agent's code does (agent/src/logic.rs, executor/src/Executor.sol)

1. The screen `utilization_above_95pct` fires on a market whose utilization (borrowed / supplied, both accrued to
   the block) is above 0.95. A new screen starts a judge call.
2. Only `leave_market` naming `live` leads to a migrate. The code then picks the destination: same loan and collateral
   token, same executor in the vault's state, corridor in force with migrate allowed, no fired screen on it, its feeds
   fresh, free liquidity above 0, and LTV after the move at or below its act line. `fraction` is 1e18 when the
   destination's free liquidity covers the debt plus 1% (`migrate_fraction`), so the run requires that. The vault's
   corridor must allow migrate and `movesInWindow(v1)` must be below 2.
3. With no qualifying destination and exit allowed on live (it is), `leave_market` becomes `exit` on live, a sale
   of collateral. `reduce_light` or `reduce_to_target` lead to a `Reduced` event; `hold` leads to nothing. The
   script records whichever happens.
4. `Executor.migrate` checks the destination's feeds (`_requireFresh`: ETH/USD at most 1,200 s old, USDC/USD at most
   86,400 s), moves the shares and the collateral with a Morpho flash loan, and reverts unless the destination's
   collateral rose by exactly the collateral moved and its debt by at most the debt moved + 1 unit. It emits
   `Migrated(caller, from, to, fraction, debtMoved, collateralMoved)`.

## Commands

```sh
# from the repository root
python3 -I scripts/test_venue_stress.py                                 # unit tests, no network
python3 -I scripts/venue_stress.py status                               # reads only
python3 -I scripts/venue_stress.py run --dry-run                        # reads only: plan, sizing, preconditions, the `before` reading
python3 -I scripts/venue_stress.py run 2>&1 | tee -a ~/.local/state/cmdk/scene-venue-stress.log
python3 -I scripts/venue_stress.py unwind --dry-run                     # reads only
python3 -I scripts/venue_stress.py unwind                               # after FAILED: repay the wallet's borrow, withdraw its collateral
```

Options of `run`: `--borrow-keep-units N` (default 100000, 0.10 USDC left free), `--collateral-ltv F` (default 0.5),
`--timeout-s S` (default 1800), `--poll-s P` (default 15), `--out-dir` (default `evidence/scenes`). `status` takes the
first two, to print the borrow a run would make. Exit codes: 0 done, 1 failed after something was sent (the files
hold what was recorded; the last line names the `unwind` command), 2 refused before anything was sent, 4 the
rebalancer lock is held (a crash scene or a rebalancer run is sending).

**Before a run:** no crash scene may be running, and the container's timer must be paused as in "Timer pause and
rebalancer state" above: the timer, `scene.py` and this script share the wallet's nonce. The lock covers the Mac
only. `status` must print `preconditions: all hold`.

## What `run` does

1. **Preconditions** (a refusal sends nothing): Base Sepolia; v1 has debt on live; backup free liquidity at least
   v1's debt plus 1%; v1's corridors on live and backup set, not closed for the agent, migrate allowed;
   `movesInWindow(v1)` below 2; backup's feeds fresh; backup's utilization not above 0.95; v1's LTV after the move
   at or below backup's act line; the borrow positive; the wallet's free WETH at least the collateral; the wallet has
   no live position yet (else `unwind` first); no `venue-stress.json` in the output directory.
2. **Sizing.** borrow = live free liquidity (supply − borrowed) − `--borrow-keep-units`; collateral = the borrow's value
   at the live oracle ÷ `--collateral-ltv`, rounded up to the wei. With 5.270284 USDC free on 5 Nov: borrow 5.170284
   USDC, collateral 0.004554570 WETH at 2,270.37 USD, utilization 0.5946 → 0.9923.
3. **`before`** at the latest block, after the lock; `GET https://relayer.cmdk.fi/agent/transcripts` (the index).
4. **Wallet transactions:** WETH `approve(Morpho, collateral)`, Morpho `supplyCollateral(live, collateral, wallet, 0x)`,
   then free liquidity is read again and Morpho `borrow(live, borrow, 0, wallet, wallet)` (the borrow is lowered if
   the free liquidity fell; a note says so). `after_borrow` is read at the borrow's block.
5. **Wait for the agent.** Every `--poll-s`: the block, live utilization and borrow APR, v1 on live and backup, and
   the executor's `Migrated`, `Reduced` and `Exited` events with caller v1 since the start block, one line per poll.
   The wait ends at a `Migrated`, at v1's live borrow shares 0, or at `--timeout-s`. A failed poll is printed and
   the next poll reads again.
6. **`after`** at the block of the agent's migrate (or exit on live), else the latest reading at timeout. The
   comparison sums v1's collateral and debt over live and backup. For an action in block N, "before" is block N−1
   with interest accrued to block N's time, so only the action differs; the `Migrated` event's debt and collateral
   are recorded next to it. Collateral must be identical; debt equal up to 1 unit. The observed difference is
   recorded either way. Without an action, "before" is the `after_borrow` reading and the difference includes interest.
7. **Transcripts.** The index again; each name not in the first index is downloaded from
   `https://relayer.cmdk.fi/agent/transcripts/<name>` (urllib, 20 s timeout). A failed fetch is a note.
8. **Unwind** (also `unwind` on its own): USDC `approve(Morpho, debt × 1.01 + 1)`, Morpho
   `repay(live, 0, all borrow shares, wallet, 0x)` (by shares, so the debt clears exactly), Morpho
   `withdrawCollateral(live, all collateral, wallet, wallet)`. `run` unwinds whether or not the agent acted. After a
   failure it does not, and prints the `unwind` command. A separate `unwind` adds a note to an existing record.
9. **Files** are written after `before`, after the borrow, after the wait and at the end.

Borrow APR = `IRM.borrowRateView(MarketParams, Market)` (the per-second WAD rate Morpho would accrue at next) ×
31,536,000 / 1e18, simple, not compounded; the agent's transcripts use the same formula (`borrow_apr`).

## The files

`evidence/scenes/venue-stress.json`:

- **Header.** `scene`, `market` (`live`), `destination` (`backup`), `markets` (ids), `executor`, `started_block`,
  `started_utc`, `addresses` (`v1`, `wallet`), `sizing` (`borrow_units`, `collateral_wei`, `keep_units`,
  `collateral_ltv`, `wallet_ltv`, `utilization_after`, `free_liquidity_units`), `corridors` (v1 on live and backup),
  `backup_feeds` (feed, `age_s`, `max_age_s`, `fresh`).
- **`phases`.** `before`, `after_borrow`, `after` and, for an action, `before_action` (block N−1). Each has `block`,
  `ts`, `utc`; `markets.<live|backup>` with `supply_units`, `borrow_units`, `utilization`, `free_liquidity_units`,
  `borrow_rate_per_sec`, `borrow_apr`, `oracle_price`; `positions.v1.<live|backup>` and `positions.wallet.live`
  with `coll`, `debt`, `ltv`, `shares`, `cash` (USDC at the address); `moves_in_window`; `wallet_weth_wei`. Raw
  units are strings.
- **`comparison`.** `block_before`, `block_after`, `basis`, `collateral_before_wei`, `collateral_after_wei`,
  `collateral_diff_wei`, `debt_before_units`, `debt_after_units`, `debt_diff_units`, `collateral_identical`,
  `debt_equal`, `debt_equal_within_units` (1) and, for a migrate, `event` (`fraction`, `debt_moved_units`,
  `collateral_moved_wei`).
- **`actions`.** From the chain's events, in chain order: `block`, `kind` (`migrate`, `reduce`, `exit`), `tx`, `by`
  (`agent`). A migrate has `from`, `to`, `fraction`, `debt_moved_units`, `collateral_moved_wei`. A reduce has
  `market`, `repaid_units`, `sold_wei`, `protective`, `ltv_before`, `ltv_after`. An exit has `market`, `repaid_units`,
  `from_idle_units`, `sold_wei`, `collateral_out_wei`.
- **`transcripts`.** Per new transcript: `name`, `sha256` (of the bytes served), `sha256_index`, `vault`, `loop`,
  `time_utc`, `block`, `trigger` (`kind`, `new_screens`), `screens` (`market:screen`), `judge` (`outcome`,
  `accepted`, `ignored`, `trigger`, `judgment_market`, `answer`: `risk`, `action`, `market`, `reasons`), `decisions`
  (`market`, `action`, `to`, `fraction`, `amount`, `protective`, `basis`), `chosen`, `proposal` (`method`, `args`,
  `sent`, `why_not_proposed`, `why_not_sent`, `near_tx`, `base_tx`, `base_status`, `base_block`, `error`).
- **Footer.** `transactions` (`label`, `tx`, `block`, `gas`, `fee_wei`, `l1_fee_wei`), `outcome` (`stopped`,
  `agent_actions`; empty until the run ends) and `notes`.

`venue-stress.md` is rendered from the JSON. It contains the three-phase table (before the borrow, after the borrow,
after the agent's action), the comparison, the actions, the transcripts with the judge's reasons, every transaction
hash and the notes.

## Limits of this scene

- **The judge decides.** The judge may answer `hold`, `reduce_light` or `reduce_to_target`, or may name another
  market. A judgment is applied only while every screen it was asked about is still on (`relevant`). On 5 Nov one
  judge call took 93 s.
- **Fresh feeds at the moment of the decision.** If ETH/USD on Base Sepolia is more than 1,200 s old when the
  judgment is applied, `feed_stale` fires on live and backup. The backup then fails the destination filter, and
  `leave_market` becomes an exit on live. In v1's transcripts of 4 and 5 Nov (to 16:19 UTC), 15 of 57 fire
  `feed_stale` on both markets, for example `ETH/USD age 1203 s > 1200 s`. The precondition checks the feeds only
  at the start.
- **Other markets of v1.** v1 also holds a demo position. If it is in a protective state, `pick` proposes the demo
  reduce first; with a screen on demo, the judge may name demo instead of live. Run the scene with no crash scene
  in progress.
- **One run per 24 h of moves.** The executor allows 2 moves per rolling 24 h per caller. A migrate leaves v1 on
  backup; moving it back to live is the owner's or a later scene's.

# Exit on a live-only vault (README step 10; `evidence/scenes/exit-v4.md`)

Run on 6 Nov on a second team vault, `v4`, whose policy lists only the live market: a `leave_market` judgment then
has no destination and the agent's code turns it into `exit` on live, a sale of collateral through the executor
(`agent/src/logic.rs`). The same script serves, with four options added that day:

```sh
# from the repository root; the vault's Base address from `GET $U/vault/v4.FACTORY` (base.address)
python3 -I scripts/venue_stress.py status --vault 0x<v4 base address> --tag v4 --expect exit
python3 -I scripts/venue_stress.py run --vault 0x<v4 base address> --tag v4 --scene venue-stress-exit --expect exit
python3 -I scripts/venue_stress.py unwind --vault 0x<v4 base address> --tag v4 --scene venue-stress-exit
```

`--vault` names the watched vault (default v1's address from the deployments), `--tag` how it is called in the
output, `--scene` the output file name, `--expect exit` replaces the destination preconditions (backup liquidity,
corridor and utilization) with: the live corridor in force with exit allowed, and no corridor set on backup. The
wait also ends at an `Exited` event by the vault.

How v4 was prepared (all through the public relayer and owner messages, `evidence/scenes/exit-v4.json`
`setup_steps`): `POST /create-vault` with one market; 0.004 WETH to its Base address; owner `setup` with the live
corridor and the agent measurement; owner `open` at 50 % LTV; owner `withdraw` of the borrowed USDC to the wallet,
so that an exit has to sell collateral rather than repay from idle USDC; the agent found the vault in its factory
scan and registered on it (it waits for 0.16 NEAR before a registration; `POST /fund-agent` once).

What happened: the venue stress alone (utilization 0.99, APR 2.49 %) got `hold` from the judge, recorded in
`venue-stress-exit.md`. The exits (two runs, the second clean) came from the text path: two items appended to the demo feeds (labelled as ours,
`feeds/README.md`) announced a drain of the live market; the agent fetched them within about 7 minutes (raw GitHub
caches 300 s, the agent polls every 60 s), the judge answered `leave_market` with risk critical, the agreement
rule counted two independent feeds, and the agent called `exit`. The vault signed the Base transaction through
Chain Signatures; the agent's NEAR RPC `send_tx` answered `HANDLER_ERROR`/`PENDING` although the call was included,
so in the first run the agent recorded `near_error` and did not broadcast; the operator broadcast the signed transaction
from the NEAR outcome (`cast publish <raw>`), which anyone can do and nobody can alter. In the second run, an hour
later with new items, the agent sent and broadcast the exit itself, mined seven minutes after the push. Details and
every transaction: `evidence/scenes/exit-v4.md`.

Limits: the judge decides (attempt 1 shows a `hold`); a feed item is seen only after the raw GitHub cache expires;
the scene needs a vault with a single approved market, or the judgment becomes a migrate (the venue-stress scene
above).

