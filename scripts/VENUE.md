# Testnet venue: pools, rebalancer timer, liquidator

Testnet only (Base Sepolia, chain 84532). The scripts refuse any other chain id. The test wallet
0x0B7Ed66f46fe83252a208563A88201BA60490d25 signs everything. Its key is read from
`~/.config/prime/base_sepolia_eoa.json` and handed only to `cast`; it is never printed. Addresses are in
`deployments/testnet.json`. `rebalance_pool.py` and `liquidate.py` read that file by default, and also accept the
old flat `executor/deployments.json` through `DEPLOYMENTS=...`. `p1_onchain_run.py` still reads only the old file.

| Market | Oracle | Pool | Kept at the oracle by |
|---|---|---|---|
| live | MorphoChainlinkOracleV2 on Chainlink ETH/USD and USDC/USD | public USDC/WETH 0.01% `0x5718…156c` | `rebalance_pool.py --market live`: our ±1% band, burnt before a repair |
| backup | its own MorphoChainlinkOracleV2 instance on the same two feeds | the same pool as live (prototype executor config) | the live run (same pool, same price to within rounding) |
| demo | MockOracle (the test wallet sets it) | WETH/dUSDC 0.01% `0x722F…21c1` | `rebalance_pool.py --market demo`: anchor, sell band, buy band |

## Status (reads only)

```sh
cd /Users/vf/cmdk/app
python3 -I scripts/rebalance_pool.py --market live --status
python3 -I scripts/rebalance_pool.py --market demo --status
```

The output gives the pool tick and price, the oracle and its tick, the gap in %, each of our positions with what a
burn would return, the wallet's ETH, WETH, USDC and dUSDC, and the L1 data fee from `GasPriceOracle`
(0x4200…000F: l1BaseFee, blobBaseFee, the scalars, and `getL1FeeUpperBound` for a 400-byte transaction). When
the gap is above the tolerance it also prints the repair cost and what the guard would do. Nothing is sent, and the
state files are checked to be byte-identical afterwards. The last line is the same `RUN` line the timer logs.

## Rebalancer on a timer

```sh
scripts/rebalance.sh                                  # every 10 min: live, then demo; defaults --max-cost-usdc 6 --reserve-usdc 8
INTERVAL_MIN=5 scripts/rebalance.sh --max-cost-usdc 4 # extra arguments go to both markets' runs
```

- **Output:** one line per run on stdout:
  `RUN <utc> market=live tick=… oracle=… pool=… gap=…% action=… repair_quote=… swap_in=… txs=… fee_eth=… l1_fee_wei=… l1_bound_wei=… exit=…`.
  `fee_eth` is the sum of L2 and L1 fees from the receipts; `l1_fee_wei` is the L1 part.
- **Detail log:** the full output of every run goes to `$LOG_DIR/rebalance-detail.log` (default
  `~/.local/state/cmdk/`), including every transaction hash.
- **Exit codes:** `exit=3` is the budget guard's refusal and `exit=4` means another run holds the lock. Any other
  non-zero code is a crash: it is logged with its last line, and the next round runs as usual.
- **Stopping:** Ctrl-C or SIGTERM lets a run in progress finish, then stops. A burn without its re-mint would leave
  the pool bare.
- **Keeping it up:**
  - nohup: `nohup caffeinate -i scripts/rebalance.sh >> ~/.local/state/cmdk/rebalance.log 2>&1 &`.
    `caffeinate -i` keeps a Mac awake; drop it on Linux. Stop it with `kill <pid>`.
  - Or a launchd agent or systemd unit with the same command and RunAtLoad / `WantedBy=default.target`.
- **One sending run at a time:** a lock on the state directory makes a manual run during the timer exit 4 instead
  of racing the wallet's nonce. `--status` and `--dry-run` take no lock.

What a live run does (the order is from results.md §4.8):
1. If the gap is above the tolerance (0.25%), compute the repair before sending anything. The cost comes from a
   walk over the pool's initialised ticks with our band taken out, cross-checked with QuoterV2 when our band is not
   on the path.
2. Refuse (exit 3, nothing sent) when the cost is above `--max-cost-usdc` (default 6), or when the wallet's USDC
   plus what the band burn returns, minus the cost, would fall under `--reserve-usdc` (default 8).
3. Burn our band and check the guard again on the exact post-burn pool.
4. Make a limit-priced swap to the oracle, repeated at most 3 times.
5. Mint a new ±1% band at the oracle. It uses only the USDC above reserve plus max cost (14 by default), so the next
   repair can still be paid.

The demo run is the prototype's anchor + 2% sell band + 1% buy band layout (results.md item 41). The guard applies
only when a demo repair spends WETH; dUSDC is minted.

## A demo price move

```sh
python3 -I scripts/rebalance_pool.py --market demo --set-price 1600 --dry-run   # what it would send
python3 -I scripts/rebalance_pool.py --market demo --set-price 1600
python3 -I scripts/rebalance_pool.py --market demo --set-price "$(python3 -I -c 'import sys; print(round(int(sys.argv[1])/1e24, 2))' "$(~/.foundry/bin/cast call 0x7D0ebc71EBBEC6F215C2Ab818F9dB538A52f6a3E 'price()(uint256)' --rpc-url https://base-sepolia-rpc.publicnode.com | cut -d' ' -f1)")"   # back to the live price
```

The pool and the MockOracle move together, in steps of at most 1.9%, so no block shows a gap above the agent's 2%
screen (item 42). The sell and buy bands are burnt first. On the last step they are minted at the new pool price
*before* the oracle is set, so the sale the new price triggers meets the bands and not the anchor alone. A move is
not budget-guarded. On a fork on 3 Nov, $2,340.53 → $2,200 (four steps) swapped 0.000156 WETH through the anchor.

## Liquidator (testnet tooling)

```sh
python3 -I scripts/liquidate.py --market demo --borrower 0x... --dry-run    # reading and computed amounts only
python3 -I scripts/liquidate.py --market demo --borrower 0x...
python3 -I scripts/liquidate.py --market live --borrower 0x... [--reserve-usdc 8]
```

**Reading.** The script reads `idToMarketParams`, `market`, `position` and the oracle `price`. It accrues the
borrow assets to the latest block with the IRM's `borrowRateView`, as Morpho does before it checks. It then applies
Morpho's health rule: liquidatable when debt (shares rounded up) > collateral × price / 1e36 × LLTV.

**Amounts.**
- When the collateral covers the seizure, it repays all borrow shares. The seizure is debt × LIF / price, with
  LIF = min(1.15, 1/(1 − 0.3 × (1 − LLTV))) = 1.0438 at 86%.
- Otherwise it seizes all the collateral, and Morpho books the remainder as bad debt.

**Funding.**
- Demo: any dUSDC shortfall is minted first.
- Live and backup: the wallet keeps `--reserve-usdc` for the rebalancer, and a partial liquidation repays what is
  above it.

**Transactions.** It approves Morpho for the repayment plus 1%, calls
`liquidate(marketParams, borrower, seizedAssets, repaidShares, 0x)`, and sets the allowance back to 0.

**Checked on an anvil fork on 3 Nov** (demo market, two anvil dev accounts, each 0.01 WETH / 18 dUSDC):

| Case | Script | Result |
|---|---|---|
| $2,200 | "healthy", nothing sent | — |
| $2,000 | computed 18.000007 dUSDC repaid and 0.009394575 WETH seized | both exact |
| $1,700 | seize-all mode | repaid 16.286002 dUSDC; 1.714 dUSDC written off as bad debt by Morpho |

Not yet run on Base Sepolia: that is step 2 of the venue row, with a test position opened for it.

## USDC budget

**Measured costs.**

| What | Cost | Source |
|---|---|---|
| Repair through other LPs' liquidity, our band burnt first | about 0.0068 USDC per tick, so about 0.068 USDC per 0.1% of drift (10 ticks) | 3 Nov: 0.774579 USDC for a 1.126% drift (114 ticks), live pool's other in-range L 2.9e12. Fork, 3 Nov: 8.607 USDC for a 12.14% drift. 27 Oct: about 7.1 USDC took the pool 1,047 ticks to U's band |
| Inside a band we do not burn | about 0.23 USDC per tick, or about 2.3 USDC per 0.1% | 27 Oct, U's band L 9.53e13 |
| 10.6% drift on 27 Oct, through U's band | 9.8–13.6 USDC | results.md §4.8 |
| The same drift on 27 Oct with the band between the pool and the oracle | 45.5 USDC against 8.3 USDC once the band was burnt first | results.md item 40 |

The rebalancer always burns first.

**What the guard allows.**
- The live pool is thin outside our band. On the fork, a stranger's 0.006 WETH sale (about $13) moved it 12%.
- With the defaults, the guard therefore repairs drifts up to about 8–9% (6 USDC) on its own.
- A bigger push is refused and waits for a faucet claim, or for a manual run with a higher `--max-cost-usdc`.
- A repair is not a loss: USDC buys WETH at below the oracle price. It does turn the USDC reserve into WETH,
  whenever the pool is pushed below the oracle or ETH rises.

**Gas.** One repair plus re-band was 7 transactions and 0.000005241 ETH on 3 Nov: base fee 0.005 gwei, and an L1
part of 23,718–42,579 wei per transaction. The L1 fee ranged up to 2.44 × 10¹² wei per transaction on 27 Oct, so
it is read every run.

**Reserve plan.**

| Item | USDC |
|---|---|
| Wallet floor (`--reserve-usdc`) | 8 |
| One maximum repair (`--max-cost-usdc`) | 6 |
| Live band, the rest | about 4.3 on 3 Nov |
| Lender supply already in live / backup (not touched) | 3 / 6 |

**Faucet.** Circle's faucet (faucet.circle.com, Base Sepolia) gives 20 USDC per claim, once per 2 hours
(results.md §4.8). It needs a person at the web form. Claim once a day to the test wallet, and at once after any
`exit=3` line. With the wallet at 14.08 USDC after 3 Nov's repair, one claim takes it to 34: room for 4 maximum
repairs (6 each) above the 8 USDC floor before the next claim.

## Operations to 25 Nov (`docs/03-plan.md`, venue and operations rows)

| When | What |
|---|---|
| 3–4 Nov | The timer started by the reviewer and left to run for 24 h unattended (the venue row's done-criterion). Daily: read the RUN lines (`grep -c 'exit=3'`, `grep -v 'exit=0'`), claim the faucet |
| 4–5 Nov (step 2) | On test positions: supply and borrow on all three markets, a `migrate` live → backup, a scripted liquidation (`liquidate.py`). Every live sale follows a live rebalance (§4.8) |
| 6 Nov | The executor is redeployed. Update `deployments/testnet.json` (executor; markets and pools if they change; NEAR `factory`, `vault_code_hash`, `relayer`). If a market gets a new pool, start its state file afresh and add it to the timer |
| 6–14 Nov | The timer runs throughout the build and the evidence scenes; demo price moves only through `--set-price` |
| 14–25 Nov | Operations: the timer on its host with restart; faucet claim daily; check wallet ETH weekly (at about 0.000005 ETH per repair, 0.2878 ETH lasts far beyond 25 Nov); `--status` on both markets before each clean-machine run on 18 and 25 Nov |
