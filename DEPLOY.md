# Public testnet deployment, Friday 6 November 2026

The runbook for the plan row "Public testnet deployment" (`docs/03-plan.md` in the planning repo). Testnet only:
Base Sepolia (chain 84532) and NEAR testnet. Nothing here touches mainnet.

- **Who.** F is the founder (web forms, NEAR keychain, Phala account, every irreversible step). C is Claude Code in
  this checkout on the founder's Mac. C sends Base Sepolia transactions from the team's test wallet
  `0x0B7Ed66f46fe83252a208563A88201BA60490d25`. Its key stays in `~/.config/prime/base_sepolia_eoa.json`, is handed
  only to `cast`, and is never printed. C starts each step only after F's go.
- **Order.** Run the steps in order. Every step lists the exact commands, the expected output and the funds it
  uses. If an output differs, stop and report; do not retry with other arguments.
- **What changed on 3 Nov.** Everything on Base Sepolia was closed: no positions, no Morpho supply on any market,
  no Uniswap bands, state files `{}`, timer stopped (`evidence/venue/2026-11-03-close-out.md`). The live market
  keeps the public Uniswap USDC/WETH 0.01% pool `0x5718…156c`: every enabled fee tier already has a public pool,
  so a pool of our own is impossible. Backup keeps the same pool: migrate uses a flash loan and no swap.
  `executor/script/Deploy.s.sol` is unchanged (its `LIVE_POOL` constant stays).

## Budget (Base Sepolia test wallet)

Holdings after the close-out (3 Nov, block 53,979,463; `--status` read the same at 15:09 UTC): 0.287648 ETH,
0.057910 WETH, 24.341191 USDC, 25,026.49 dUSDC. dUSDC is our mintable test token, not value. F adds 40 USDC
from the Circle faucet before Friday (4 claims of 10 USDC).

| Step | USDC | WETH | ETH | USDC in the wallet after |
|---|---|---|---|---|
| Start, after 4 faucet claims | +40 | | | 64.34 |
| 2. Executor redeploy | 0 | 0 | at most 0.000064 (forge estimate: 5,792,699 gas) | 64.34 |
| 3. Revoke 4 grants, set 7 | 0 | 0 | gas of 11 transactions | 64.34 |
| 4. Lender supply: live 13, backup 13 USDC; demo 5,000 dUSDC | −26 | 0 | gas of 5 transactions | 38.34 |
| 5. Live band 10 USDC + about 0.0044 WETH, after a centring repair (0.22 USDC at a −0.32% gap on 3 Nov); demo anchor and buy band (dUSDC minted) | about −10.2 | −0.0144 | gas | about 28.1 |
| 6. Timer: never mints into the band below 14 USDC (8 reserve + 6 maximum repair) | floor 14 | | | about 28.1 |
| 7. Vault's Base address: gas money and collateral | 0 | −0.010 | −0.010 | about 28.1 |
| 9. Reduce scene: the vault sells about 3.8 USDC of WETH into our band; the timer's next round buys it back | about −3.8 (it becomes WETH) | about +0.0017 | gas | about 24.3 |

**Verdict.** 40 USDC is enough. After step 5 the wallet holds about 14.1 USDC above the timer's 14 USDC floor,
which pays two more maximum repairs. The steps need 26 (supply) + 10 (band) + 14 (floor) = 50 USDC.
- **Fewer claims.** With 3 claims (30 USDC) the wallet starts at 54.34 and keeps 4.34 above the floor, less than
  one maximum repair. With 2 claims or fewer, skip the backup supply on Friday (step 4). Backup's USDC is needed
  only by a `migrate`, and skipping it frees 13 USDC.
- **WETH.** WETH is not short: 0.057910 − 0.0144 − 0.010 leaves about 0.0335.
- **ETH.** About 0.277 ETH is left.

The sizes are computed as follows:
- **Live supply.** 13 USDC covers the vault's open of 0.01 WETH at 50%: 0.01 × $2,285.28 × 0.5 = 11.43 USDC on
  3 Nov, and 13 USDC covers it up to an ETH price of $2,600.
- **Backup supply.** 13 USDC takes a full migrate of that debt.
- **Reduce sale.** From 50% to a 40% target the sale is (debt − 0.4 × value) / 0.6 = value / 6 = 22.85 / 6 =
  3.81 USDC (`CorridorMath.repayToLtv`).

NEAR is spent in steps 7 and 8 only: 85 NEAR on the publishing account and 5 NEAR on the relayer (§0).

## 0. Before Friday

| Item | Owner | Check |
|---|---|---|
| Executor freeze read (Wed 4) and vault review | F | findings in the README's Limitations, no open defect |
| Faucet: 4 claims of 10 USDC to `0x0B7E…0d25` (faucet.circle.com, Base Sepolia) | F | step 1 |
| Timer stays off; nothing on Base Sepolia is restarted | F, C | `pgrep -fl rebalance.sh` prints nothing; `cat scripts/state/live.json scripts/state/demo.json` prints `{}{}` |
| NEAR on the publishing account | F | at least 85 NEAR. The publish costs about 78.24 NEAR for the 782,389-byte host build; the reproducible wasm's size is read on Friday. Add the factory's 2 NEAR and gas. `prime-measure-1792736012.testnet` held 179.32 NEAR on 3 Nov |
| NEAR on the relayer account | F | at least 5 NEAR (2 NEAR vault deposit, agent funding 0.16, gas) |
| Vault build prerequisites | F | `vault/factory/DEPLOY.md` step 1: the commit to publish pushed to `origin/main`, clean tree, Docker running with disk room (the agent build stopped on a full Docker disk on 3 Nov) |
| Agent image | C (agent work) | `grep 'image:' agent/docker-compose.live.yml` shows `ghcr.io/cmdkfi/agent@sha256:` and 64 hex characters, not `REPLACE_WITH_THE_DIGEST_BUILD_SH_PRINTS`; the image pulls anonymously |
| Mac for the timer | F | on AC power from Friday until the timer moves to a server (week of 9 Nov) |

**Shell setup (C, every shell).** C's shell does not keep variables between commands, so the setup goes into one
file outside the repository; it holds addresses only. Write it once:

```sh
mkdir -p ~/.local/state/cmdk /Users/vf/cmdk/app/evidence/deploy
cat > ~/.local/state/cmdk/deploy-2026-11-06.sh <<'EOF'
# DEPLOY.md shell setup: addresses and two helpers; no keys
set -o pipefail
cd /Users/vf/cmdk/app
export PATH="$HOME/.foundry/bin:$PATH" RPC=https://base-sepolia-rpc.publicnode.com
unset STATE_DIR DEPLOYMENTS
LOG=/Users/vf/cmdk/app/evidence/deploy/2026-11-06.log
W=0x0B7Ed66f46fe83252a208563A88201BA60490d25; OLD=0xB23e0d7e1D0182Db7f6f2637EC4Ac51c10290f40
MORPHO=0xBBBBBbbBBb9cC5e90e3b3Af64bdAF62C37EEFFCb; IRM=0x46415998764C29aB2a25CbeA6254146D50D22687; LLTV=860000000000000000
USDC=0x036CbD53842c5426634e7929541eC2318f3dCF7e; WETH=0x4200000000000000000000000000000000000006; DUSDC=0xeb68Fd5bAAAfD4e46b43bAa7fEAb3216B87b10d5
LIVE=0xa25ac5171b7e500b82653d3750b91bb44e2409c32ca3c9e80b0db077e8a4535c; LIVE_ORACLE=0x7D0ebc71EBBEC6F215C2Ab818F9dB538A52f6a3E
BACKUP=0x99d43c0f2a0eb6172774598a4e34e7eee09caa502692c2b2df0f40e1e3c5ea75; BACKUP_ORACLE=0x20Ff664b99676a45A13bD77ACB2AFECeA10Cd538
DEMO=0x55872b53dba6147b6badf9d4277bc166dfb7d5f8ac8d491da093960dbef6d585; MOCK=0x91Ec8d7177b876017773cf787B2caB0Fe6Ac6b2f
MAX=115792089237316195423570985008687907853269984665640564039457584007913129639935
R='Reduced(address indexed caller, bytes32 indexed market, uint256 repaid, uint256 collateralSold, uint256 ltvBefore, uint256 ltvAfter, bool protective)'
EXEC=$(jq -r .base.executor deployments/testnet.json)          # the new executor once step 2 has written it
V=$(jq -r '.near.vault // empty' deployments/testnet.json); VB=$(jq -r '.near.vault_base_address // empty' deployments/testnet.json)
FROM=$(cat ~/.local/state/cmdk/scene-from-block 2>/dev/null)   # written in step 9
tx() {  # tx LABEL TO SIG [ARGS...]: one transaction from the test wallet; Base Sepolia only; under the rebalancer's
        # lock, so it never races a timer run on the wallet's nonce (exit 4 while a run holds it: run it again)
  python3 -I -c '
import sys; sys.path.insert(0, "scripts"); import rebalance_pool as rp
assert int(rp.subprocess.run([rp.CAST, "chain-id", "--rpc-url", rp.RPC], capture_output=True, text=True).stdout) == rp.CHAIN_ID, "not Base Sepolia"
fd = rp.lock(); v = rp.os.environ.get("VALUE"); rp.send(*sys.argv[1:], value=int(v) if v else None)' "$@" 2>&1 | tee -a "$LOG"
}
owner() {  # owner METHOD ARGS_JSON NAME: sign as the vault owner (the test wallet), submit through the relayer
  python3 -I scripts/p2_owner.py --vault "$V" --method "$1" --args "$2" --out "$HOME/.local/state/cmdk/$3.signed.json" >> "$LOG" &&
  jq -c --arg v "$V" '{vault: $v, message, signature}' "$HOME/.local/state/cmdk/$3.signed.json" |
    curl -s localhost:8787/owner-call -H 'Content-Type: application/json' -d @- > "evidence/deploy/2026-11-06-$3.json"
  jq -c '{ok, near_tx, error, receipts: [.receipts[]? | {hash, status}]}' "evidence/deploy/2026-11-06-$3.json" | tee -a "$LOG"
}
EOF
source ~/.local/state/cmdk/deploy-2026-11-06.sh
```

Every C command block below starts with `source ~/.local/state/cmdk/deploy-2026-11-06.sh`. A successful `tx` prints
`  LABEL: tx 0x… status 1 gas N, fee N wei (L2 N + L1 N)` and appends it to `$LOG`. It sends nothing when
`cast estimate` fails; then it prints `LABEL: estimate FAILED …` and exits 1. Steps 2–5 were rehearsed on an anvil
fork of Base Sepolia on 3 Nov with these exact commands (`RPC=http://127.0.0.1:8547`, a scratch `STATE_DIR`, the
wallet's USDC set to 64.341191 with `anvil_setStorageAt`), and every output matched what is written below.

## 1. Faucet arrival check (F claims, C reads; no funds)

```sh
source ~/.local/state/cmdk/deploy-2026-11-06.sh
python3 -I scripts/rebalance_pool.py --market live --status | grep '^wallet' | tee -a $LOG
pgrep -fl rebalance.sh
```

Expected: `wallet 0x0B7Ed66f46fe83252a208563A88201BA60490d25: 0.287648 ETH, 0.057910 WETH, 64.341191 USDC,
25026.493470 dUSDC` (USDC at least 54.34 with 3 claims; see the budget for fewer). `pgrep` prints nothing.

## 2. Executor redeploy (C; gas only, at most 0.000064 ETH)

```sh
source ~/.local/state/cmdk/deploy-2026-11-06.sh
(cd executor && forge test 2>&1 | tail -1)
(cd executor && BACKUP_ORACLE=$BACKUP_ORACLE COOLDOWN=60 DEPLOY_OUT=deployments-2026-11-06.json ./script/deploy.sh 2>&1 | tee -a $LOG | grep -E '^  (executor|backup oracle|demo pool)|ONCHAIN')
```

Expected:
- **`forge test`.** `Ran 3 test suites in …: 64 tests passed, 0 failed, 0 skipped (64 total tests)` (64 on 3 Nov;
  if the Wednesday freeze adds tests, all of them must pass).
- **The deploy.** `  executor 0x…`, `  backup oracle 0x20Ff664b99676a45A13bD77ACB2AFECeA10Cd538`,
  `  demo pool 0x722F350ec250c1697063A080B0196506D7Fe21c1`, `ONCHAIN EXECUTION COMPLETE & SUCCESSFUL.`
- **The transactions.** Two: `MockOracle.setPrice` (the demo oracle moves to the live oracle's price) and the
  `Executor` creation.
- **The address.** A simulation on 3 Nov at wallet nonce 518 gave `0xA9bbabE6d75C5BA96CA095586284774c9D09D18b`.
  Any wallet transaction before the deploy changes it.

`DEPLOY_OUT` keeps the prototype's `executor/deployments.json` as it is. Write the new address and the creation
block into `deployments/testnet.json`. Only these values change, and the file's layout stays as it is:

```sh
source ~/.local/state/cmdk/deploy-2026-11-06.sh
NEW=$(jq -r .executor executor/deployments-2026-11-06.json)
H=$(jq -r '.transactions[] | select(.contractName == "Executor") | .hash' executor/broadcast/Deploy.s.sol/84532/run-latest.json)
BLOCK=$(cast receipt $H blockNumber --rpc-url $RPC); echo "executor $NEW block $BLOCK tx $H" | tee -a $LOG
sed -i '' -e "s/\"executor\": \"$OLD\"/\"executor\": \"$NEW\"/" \
  -e "s/\"executorDeployBlock\": 50841606/\"executorDeployBlock\": $BLOCK/" \
  -e "s/Base part from executor\/deployments.json (prototype executor, block 50841606), agent/Base part from executor\/deployments-2026-11-06.json (executor deployed 6 Nov 2026; the prototype executor $OLD is retired), agent/" \
  -e "s/the prototype executor's config gives it the live market's pool/the executor's config gives it the live market's pool/" \
  deployments/testnet.json
git diff --stat deployments/testnet.json
```

Expected: the `echo` line with a 0x address and a block number, then
`1 file changed, 4 insertions(+), 4 deletions(-)` (tested on a copy on 3 Nov).

Checks (read-only):

```sh
source ~/.local/state/cmdk/deploy-2026-11-06.sh          # EXEC is now the new executor
[ "$EXEC" != "$OLD" ] && echo "executor $EXEC"
cast call $EXEC "COOLDOWN()(uint256)" --rpc-url $RPC                           # 60
cast call $EXEC "marketCount()(uint256)" --rpc-url $RPC                        # 3
for M in $LIVE $BACKUP $DEMO; do cast call $EXEC "config(bytes32)(((address,address,address,address,uint256),address,address[2],uint32[2]))" $M --rpc-url $RPC | grep -o '), 0x[0-9a-fA-F]*'; done
#   ), 0x57183717A087d2fe3Ad890873877244c3B96156c   (live: the public pool)
#   ), 0x57183717A087d2fe3Ad890873877244c3B96156c   (backup: the same pool)
#   ), 0x722F350ec250c1697063A080B0196506D7Fe21c1   (demo)
cast call $EXEC "soldInWindow(address,address)(uint256)" $W $WETH --rpc-url $RPC   # 0: the signature the agent calls (agent/src/chain.rs)
cast call $OLD  "soldInWindow(address,address)(uint256)" $W $WETH --rpc-url $RPC   # Error: … execution reverted (the prototype has soldInWindow(address))
cast call $MOCK "price()(uint256)" --rpc-url $RPC; cast call $LIVE_ORACLE "price()(uint256)" --rpc-url $RPC   # equal right after the deploy
python3 -I -c 'import sys; sys.path.insert(0, "scripts"); import rebalance_pool as rp; print(rp.DEP["executor"])'   # the new executor
```

The agent and the relayer read the executor from the vault's policy (step 7b), not from this file.

## 3. Old executor approvals revoked; new ones set (C; gas only, 11 transactions)

The wallet gave the prototype executor unlimited allowances on USDC, WETH and dUSDC and a Morpho authorization. It
also has corridors set there. Revoke the four grants (the corridors grant nothing without them), then give the new
executor the same set. `scripts/attack-corridor.sh` and any test position of the wallet use it. The corridors are
50/70/30, sell budget 20%, migrate and exit allowed, as in the README.

```sh
source ~/.local/state/cmdk/deploy-2026-11-06.sh
tx 'revoke USDC to the old executor'  $USDC  'approve(address,uint256)' $OLD 0
tx 'revoke WETH to the old executor'  $WETH  'approve(address,uint256)' $OLD 0
tx 'revoke dUSDC to the old executor' $DUSDC 'approve(address,uint256)' $OLD 0
tx 'revoke the Morpho authorization of the old executor' $MORPHO 'setAuthorization(address,bool)' $OLD false
tx 'approve USDC to the executor'  $USDC  'approve(address,uint256)' $EXEC $MAX
tx 'approve WETH to the executor'  $WETH  'approve(address,uint256)' $EXEC $MAX
tx 'approve dUSDC to the executor' $DUSDC 'approve(address,uint256)' $EXEC $MAX
tx 'Morpho authorization of the executor' $MORPHO 'setAuthorization(address,bool)' $EXEC true
C='(500000000000000000,700000000000000000,300000000000000000,200000000000000000,true,true,false)'
for M in $LIVE $BACKUP $DEMO; do
  DL=$(( $(cast block latest -f timestamp --rpc-url $RPC) + 600 ))
  tx "setCorridor $M" $EXEC 'setCorridor(bytes32,(uint64,uint64,uint64,uint64,bool,bool,bool),uint256)' $M "$C" $DL
done
```

Expected: eleven `status 1` lines.

Checks (read-only):

```sh
source ~/.local/state/cmdk/deploy-2026-11-06.sh
for T in $USDC $WETH $DUSDC; do echo "$T old $(cast call $T 'allowance(address,address)(uint256)' $W $OLD --rpc-url $RPC) new $(cast call $T 'allowance(address,address)(uint256)' $W $EXEC --rpc-url $RPC | cut -d' ' -f1)"; done
echo "Morpho old $(cast call $MORPHO 'isAuthorized(address,address)(bool)' $W $OLD --rpc-url $RPC) new $(cast call $MORPHO 'isAuthorized(address,address)(bool)' $W $EXEC --rpc-url $RPC)"
for M in $LIVE $BACKUP $DEMO; do cast call $EXEC 'corridors(address,bytes32)(uint64,uint64,uint64,uint64,bool,bool,bool)' $W $M --rpc-url $RPC | awk '{printf "%s ", $1}'; echo; done
```

Expected: three token lines of the form

```
0x036CbD53842c5426634e7929541eC2318f3dCF7e old 0 new 115792089237316195423570985008687907853269984665640564039457584007913129639935
```

then `Morpho old false new true`, then three lines
`500000000000000000 700000000000000000 300000000000000000 200000000000000000 true true true`.

The rebalancer's exact-amount remainders to the NonfungiblePositionManager and the router (close-out, "What is
left") stay as they are. They are dust, and they give no one authority over positions.

## 4. Lender supply (C; 26 USDC and 5,000 dUSDC, 5 transactions)

Lender liquidity is 0 on all three markets. `p1_onchain_run.py --venue-open` is not used. It tops up only live and
backup, it also sets the wallet's corridors, and it opens 0.003 WETH test positions on all three markets.

```sh
source ~/.local/state/cmdk/deploy-2026-11-06.sh
S='supply((address,address,address,address,uint256),uint256,uint256,address,bytes)'
tx 'approve Morpho 26 USDC' $USDC 'approve(address,uint256)' $MORPHO 26000000
tx 'supply 13 USDC to live'   $MORPHO "$S" "($USDC,$WETH,$LIVE_ORACLE,$IRM,$LLTV)" 13000000 0 $W 0x
tx 'supply 13 USDC to backup' $MORPHO "$S" "($USDC,$WETH,$BACKUP_ORACLE,$IRM,$LLTV)" 13000000 0 $W 0x
tx 'approve Morpho 5,000 dUSDC' $DUSDC 'approve(address,uint256)' $MORPHO 5000000000
tx 'supply 5,000 dUSDC to demo' $MORPHO "$S" "($DUSDC,$WETH,$MOCK,$IRM,$LLTV)" 5000000000 0 $W 0x
for M in $LIVE $BACKUP $DEMO; do cast call $MORPHO 'market(bytes32)(uint128,uint128,uint128,uint128,uint128,uint128)' $M --rpc-url $RPC | head -1; done
```

Expected: five `status 1` lines, then `13000001 [1.3e7]`, `13000001 [1.3e7]` and `5000000001 [5e9]`. Each value is
`totalSupplyAssets`, which includes the one-unit remainder each market kept after the close-out. The wallet already
holds 25,026 dUSDC, so nothing is minted here. With 2 faucet claims or fewer, leave out the backup line and approve
13 USDC instead of 26.

## 5. Live band and demo layout (C; 10 USDC + about 0.0044 WETH on live, 0.010 WETH on demo)

`rebalance_pool.py` sizes a new live band from the wallet: all USDC above `--reserve-usdc` + `--max-cost-usdc`
(8 + 6 = 14), matched with WETH of equal value. With 38 USDC in the wallet that would be a 24 USDC band.
`--band-usdc 10` (added 3 Nov) caps the band's USDC, and so its WETH, at the size the founder chose. Fork
measurements on 3 Nov:
- **Founder's measurement.** 10 USDC + 0.0044 WETH at ±1% holds a 5 USDC sale under 0.5% price move;
  30 USDC + 0.0132 WETH gives 0.16%.
- **Centred band.** A band minted with the pool at the oracle moved the pool 0.358% for a 3.82 USDC sale and
  0.469% for 5 USDC.
- **Off-centre band.** Minted while the pool sat 0.155% off the oracle, inside the 0.25% tolerance, so no repair
  ran first. The band took only 7.21 of the 10 USDC, and a 5 USDC sale then moved the pool 0.536%.

So the seeding run forces the repair with a tight tolerance. The pool is moved to the oracle first, and the band
is centred on it:

```sh
source ~/.local/state/cmdk/deploy-2026-11-06.sh
python3 -I scripts/rebalance_pool.py --market live --band-usdc 10 --tolerance 0.0005 --dry-run | grep -E '^repair|^mint|^RUN'
python3 -I scripts/rebalance_pool.py --market live --band-usdc 10 --tolerance 0.0005 2>&1 | tee -a $LOG | grep -E '^guard|^mint|band position|^RUN'
python3 -I scripts/rebalance_pool.py --market demo 2>&1 | tee -a $LOG | grep -E 'position|^RUN'
python3 -I scripts/rebalance_pool.py --market live --status | grep -E '^pool tick|^our band'
python3 -I scripts/rebalance_pool.py --market demo --status | grep -E '^pool tick|^our'
```

Expected:
- **Live.**
  - `guard: cost $0.xx <= 6.00, wallet USDC after … >= reserve 8.00: pass`.
  - `mint +-1% band [lo, hi) with 0.0043xx WETH + 10.000000 USDC (keeps at least 14.00 USDC, …; cap 10.00 USDC)`.
  - `  band position N, ticks [lo, hi)`.
  - A `RUN` line with `action=repair+band`, or `action=band` when the gap is already under 0.05%.
  - Status: gap about 0.000%, and `our band: … in range; a burn returns` about 9.9 USDC + 0.0044 WETH.
- **Demo.** The state file is empty, so the run mints the anchor (0.005 WETH full range plus dUSDC). It swaps the
  pool to the MockOracle that step 2 moved, then mints the sell band (20,000 dUSDC, minted) and the buy band
  (0.005 WETH). The output:
  - `anchor position N`, `sell position N`, `buy position N`.
  - `RUN … action=repair+bands`.
  - Status: gap +0.000%.
- **On the fork on 3 Nov.** Live 10.000000 USDC + 0.004376 WETH after a 0.478578 USDC repair; the burn would return
  9.860010 USDC + 0.004376 WETH. Demo repair 0.101065 dUSDC.

## 6. Timer (C; runs on this Mac with `caffeinate -s` on AC power)

On 3 Nov the timer ran under `caffeinate -i` and paused three times in 8.6 h when the lid closed (`-i` prevents idle
sleep only). `-s` prevents system sleep while the Mac is on AC power, so keep it plugged in. The timer moves to a
server the week of 9 Nov.

```sh
cd /Users/vf/cmdk/app && nohup caffeinate -s scripts/rebalance.sh --band-usdc 10 >> ~/.local/state/cmdk/rebalance.log 2>&1 &
```

About a minute later (read-only):

```sh
tail -n 3 ~/.local/state/cmdk/rebalance.log
pgrep -fl rebalance.sh; pmset -g assertions | grep 'PreventSystemSleep named'; pmset -g batt | head -1
```

Expected:
- **Log.** `rebalance.sh: every 600 s, live then demo, args: --band-usdc 10; detail log …`, then one
  `RUN … market=live … exit=0` and one `RUN … market=demo … exit=0`.
- **Processes.** `pgrep` shows `/bin/sh scripts/rebalance.sh --band-usdc 10` and
  `caffeinate -s scripts/rebalance.sh --band-usdc 10`.
- **Assertion.** `pmset` shows `pid N(caffeinate): … PreventSystemSleep named: "caffeinate command-line tool"`.
- **Power.** `Now drawing from 'AC Power'`.
- **Not measured.** Whether `-s` holds through a lid close on this Mac has not been measured. The next morning,
  `pmset -g log | grep -i 'Clamshell Sleep'` must show nothing after the start time, and the `RUN` lines must be
  10 minutes apart.

Stop it (a run in progress finishes first): `kill -TERM $(pgrep -f '^/bin/sh scripts/rebalance.sh')`. From now on
every wallet transaction goes through `tx`, which takes the timer's lock, so the two never race the wallet's nonce.

## 7. Vault, relayer, `setup` and `open`

**7a. Publish, factory, keys deleted (F; about 78.24 NEAR burnt + 2 NEAR locked).** Follow
`vault/factory/DEPLOY.md` steps 0–5 exactly. Step 1 builds the vault with `cargo near build reproducible-wasm` from
this checkout. `cmdkfi/app` is made public on Friday, at deploy time. Record the wasm's size and `HASH` in that file.

**7b. Vault v1 (F; 2 NEAR deposit).** Use `vault/factory/DEPLOY.md` step 6, with two settings:
- **`OWNER`.** `OWNER=0x0B7Ed66f46fe83252a208563A88201BA60490d25`, the test wallet. `scripts/p2_owner.py` signs
  owner messages only when the test wallet is the owner.
- **`EXEC`.** That file still sets `EXEC` to the prototype executor. Run
  `EXEC=$(jq -r .base.executor /Users/vf/cmdk/app/deployments/testnet.json)` after its `EXEC=` line; `echo $EXEC`
  must print the step 2 address.

Then run its step 7 checks.

**7c. Record the NEAR side (C).** `HASH` is from 7a. `FACTORY` and `RELAYER` are from `vault/factory/DEPLOY.md`
step 0. Fill in the three values on the second line:

```sh
source ~/.local/state/cmdk/deploy-2026-11-06.sh
FACTORY=<factory.account>.testnet; RELAYER=<relayer account>.testnet; HASH=<base58 from 7a>
HEX=$(shasum -a 256 vault/target/near/cmdk_vault.wasm | cut -d' ' -f1)
VB=$(curl -s https://rpc.testnet.near.org -H 'Content-Type: application/json' -d '{"jsonrpc":"2.0","id":"cmdk","method":"query","params":{"request_type":"call_function","finality":"final","account_id":"v1.'$FACTORY'","method_name":"get_state","args_base64":"e30="}}' | jq -r '.result.result | implode | fromjson | .base_address')
echo "vault v1.$FACTORY base $VB hash $HASH hex $HEX" | tee -a $LOG
sed -i '' -e "s/\"factory\": null/\"factory\": \"$FACTORY\"/" -e "s/\"vault_code_hash\": null/\"vault_code_hash\": \"$HASH\"/" \
  -e "s/\"vault_code_hash_hex\": null/\"vault_code_hash_hex\": \"$HEX\"/" -e "s/\"vault\": null/\"vault\": \"v1.$FACTORY\"/" \
  -e "s/\"vault_base_address\": null/\"vault_base_address\": \"$VB\"/" -e "s/\"relayer\": null/\"relayer\": \"$RELAYER\"/" deployments/testnet.json
jq .near deployments/testnet.json
```

Expected: `base` is the address in step 6's `creating vault` log line, and `jq` shows all six fields filled (the
`sed` was tested on a copy on 3 Nov).

**7d. Relayer (F; no funds).** It signs with the relayer's NEAR keychain entry:

```sh
cd /Users/vf/cmdk/app
python3 -I scripts/test_relayer.py 2>&1 | tail -1            # OK (53 tests on 3 Nov)
nohup scripts/relayer.sh serve --port 8787 --sender $RELAYER --factory $FACTORY >> ~/.local/state/cmdk/relayer.log 2>&1 &
```

A few seconds later, `curl -s localhost:8787/health` prints JSON with `"ok": true` and `"dry_run": false`.

**7e. Gas money and collateral for the vault's Base address (C; 0.01 ETH + 0.01 WETH, 2 transactions).**

```sh
source ~/.local/state/cmdk/deploy-2026-11-06.sh               # V and VB now come from deployments/testnet.json
VALUE=10000000000000000 tx '0.01 ETH to the vault' $VB 0x
tx '0.01 WETH to the vault' $WETH 'transfer(address,uint256)' $VB 10000000000000000
cast balance $VB --rpc-url $RPC; cast call $WETH 'balanceOf(address)(uint256)' $VB --rpc-url $RPC
```

Expected: two `status 1` lines, then `10000000000000000` and `10000000000000000 [1e16]`.

**7f. `setup` on all three markets (C; one owner signature, 7 Base transactions paid by the vault's ETH).**
- **Live** approves both tokens and authorizes Morpho.
- **Backup** shares tokens and executor with live, so it sets only its corridor.
- **Demo** approves dUSDC. Its WETH approval is the same call as live's and is emitted once.

Corridors are 50/70/30, sell budget 20%, migrate and exit allowed. `scripts/RELAYER.md` has the message shape.

```sh
source ~/.local/state/cmdk/deploy-2026-11-06.sh
C='{"target":"500000000000000000","act":"700000000000000000","floor":"300000000000000000","sell_budget":"200000000000000000","migrate_allowed":true,"exit_allowed":true}'
owner setup '{"markets":[{"market":"'$LIVE'","approve":true,"authorize":true,"corridor":'$C'},{"market":"'$BACKUP'","approve":false,"authorize":false,"corridor":'$C'},{"market":"'$DEMO'","approve":true,"authorize":false,"corridor":'$C'}]}' setup
curl -s localhost:8787/vault/$V | jq -c '.base.markets[] | {id, executor, morpho_authorized, loan_allowance, collateral_allowance, corridor}'
```

Expected:
- **Response.** `{"ok":true,"near_tx":"…","error":null,"receipts":[…]}` with 7 receipts, each `"status":1`.
- **Vault view.** Three lines with:
  - `executor` equal to `$EXEC`;
  - `morpho_authorized` true;
  - both allowances `115792089237316195423570985008687907853269984665640564039457584007913129639935`;
  - the corridor set to 50/70/30/20%.

**7g. `open` on live (C; the vault's 0.01 WETH; borrows about 11.4 USDC of the 13 supplied).**

```sh
source ~/.local/state/cmdk/deploy-2026-11-06.sh
owner open '{"market":"'$LIVE'","collateral_in":"10000000000000000","target_ltv":"500000000000000000"}' open-live
cast call $EXEC 'ltvOf(address,bytes32)(uint256)' $VB $LIVE --rpc-url $RPC
cast call $MORPHO 'position(bytes32,address)(uint256,uint128,uint128)' $LIVE $VB --rpc-url $RPC
```

Expected:
- **Response.** One receipt, `"status":1`.
- **LTV.** `ltvOf` just under 5e17 (3 Nov's wallet opens read 499999629349637601).
- **Position.** `0`, the borrow shares, `10000000000000000 [1e16]`.

## 8. Agent (F deploys; C approves and funds)

Operator `cmdk`; the image is `ghcr.io/cmdkfi/agent`, pinned by digest in `agent/docker-compose.live.yml`. The mode
is `--mode live`, so the agent sends. Phala's pre-launch script is `agent/pre-launch.sh` as built.
- **F deploys.** Run the deploy command of `agent/MEASUREMENT.md` §2 from `agent/`: `npx phala@1.1.22 deploy -n
  cmdk-agent -c docker-compose.live.yml …`.
- **C approves.** Take the `RESULT measurement={…}` line from the CVM's log. Check MRTD, RTMR0–2 and the OS image
  hash against §1's table, and recompute the compose hash (§3). Then approve that exact JSON:
  `source ~/.local/state/cmdk/deploy-2026-11-06.sh; owner approve_measurement '<the JSON>' approve-measurement`.
  The response is `"ok":true` with no receipts.
- **C funds.** When the log prints `WAITING implicit_account=<64 hex> …`, run
  `curl -s localhost:8787/fund-agent -d '{"implicit_account":"<64 hex>"}'`. The response is `"status": "funded"`.
- **Check.** `register_agent` lands (MEASUREMENT.md §7). Read-only:
  `near contract call-function as-read-only $V get_state json-args '{}' network-config testnet now` shows the
  agent key and an `expires_at` 7 days ahead.

## 9. Live reduce scene (C signs the owner messages; the agent acts)

The owner lowers the act line under the position's LTV. On its next tick the agent finds a protective session and
sells to the new target through the executor. From 50% to a 40% target the sale is about 3.8 USDC of WETH, which
the 10 USDC band absorbs at about 0.36% price move (fork, step 5).

```sh
source ~/.local/state/cmdk/deploy-2026-11-06.sh
python3 -I scripts/rebalance_pool.py --market live --band-usdc 10 --tolerance 0.0005 | grep '^RUN'   # centre the pool; exit 4 = timer mid-run, run again
cast block-number --rpc-url $RPC > ~/.local/state/cmdk/scene-from-block; FROM=$(cat ~/.local/state/cmdk/scene-from-block)
echo "scene from block $FROM; ltvOf $(cast call $EXEC 'ltvOf(address,bytes32)(uint256)' $VB $LIVE --rpc-url $RPC)" | tee -a $LOG
owner set_corridor '{"market":"'$LIVE'","target":"400000000000000000","act":"450000000000000000","floor":"300000000000000000","sell_budget":"200000000000000000","migrate_allowed":true,"exit_allowed":true}' set-corridor-act45
```

Expected: the `RUN` line with a gap about 0.000%, then `ltvOf` just under 5e17, then `"ok":true` with one receipt
`"status":1`. Then check every few minutes (read-only) until the agent's sale appears:

```sh
source ~/.local/state/cmdk/deploy-2026-11-06.sh
cast logs --from-block $FROM --address $EXEC "$R" $VB $LIVE --rpc-url $RPC | grep -E 'transactionHash|repaid|collateralSold|ltv|protective'
```

Expected:
- **The agent's sale.** One `Reduced` event from the vault's Base address with:
  - `repaid` about 3800000 (3.8 USDC);
  - `collateralSold` about 1.7e15 (0.0017 WETH);
  - `ltvBefore` about 5e17 and `ltvAfter` about 4e17;
  - `protective: true`.
- **Afterwards:**
  - `cast call $EXEC 'ltvOf(address,bytes32)(uint256)' $VB $LIVE --rpc-url $RPC` reads about 4e17.
  - `python3 -I scripts/rebalance_pool.py --market live --status | grep '^pool tick'` shows a gap of about
    −0.36%. The timer's next round repairs it (`action=repair+band`).
- **Restore.** Then set the README's corridor back. The agent does nothing at LTV 0.40 under act 0.70:

```sh
source ~/.local/state/cmdk/deploy-2026-11-06.sh
owner set_corridor '{"market":"'$LIVE'","target":"500000000000000000","act":"700000000000000000","floor":"300000000000000000","sell_budget":"200000000000000000","migrate_allowed":true,"exit_allowed":true}' set-corridor-restore
```

## 10. Evidence capture (C)

```sh
source ~/.local/state/cmdk/deploy-2026-11-06.sh
{ echo "# Protect live: owner-lowered act line, the agent's reduce, LTV back at target (Base Sepolia, $(date -u +%F))"
  echo "vault $V, Base address $VB, executor $EXEC, live market $LIVE"
  echo "## owner set_corridor (target 40%, act 45%)"; jq -c '{ok, near_tx, receipts: [.receipts[] | {hash, status, block}]}' evidence/deploy/2026-11-06-set-corridor-act45.json
  echo "## the agent's reduce (executor Reduced event)"; cast logs --from-block $FROM --address $EXEC "$R" $VB $LIVE --rpc-url $RPC
  echo "## LTV now"; cast call $EXEC 'ltvOf(address,bytes32)(uint256)' $VB $LIVE --rpc-url $RPC
  echo "## owner set_corridor restored (50/70/30)"; jq -c '{ok, near_tx, receipts: [.receipts[] | {hash, status, block}]}' evidence/deploy/2026-11-06-set-corridor-restore.json
} > evidence/protect-live.txt
cp ~/.local/state/cmdk/rebalance.log evidence/deploy/2026-11-06-timer-rebalance.log
ls evidence/deploy/; git status --short
```

**The agent's record.** Add the agent's record of that tick by hand: its `RESULT transcript loop=…` line and the
NEAR transaction of its vault call, from the CVM's log (Phala dashboard). This file has no command that reads that
log.

**The other files.**
- `evidence/deploy/2026-11-06-*.json`: the relayer's responses.
- `evidence/deploy/2026-11-06.log`: the output of every step.

None of them holds a key: the signed owner messages stay in `~/.local/state/cmdk/*.signed.json`. Commit only when
asked.

## 11. AI screening rehearsal 1 (C)

Make a fresh clone in an empty directory. Give Claude Code and Codex each the Q-017 wording (planning repo,
`research_notes/Hackathon rules/`) as the prompt, over the contracts and the README's verify steps. The key-attack
step was dropped from the judges' README on 3 Nov. Save both reports under `evidence/screening/2026-11-06-*.md`.
Anything they cannot reproduce goes into the todo.

## Known gaps to close before step 11

- **View names.** The judges' README names `get_owner`, `get_policy`, `get_position` and `get_agent` on the vault.
  The vault exposes `get_state` and `check_transaction` (3 Nov). Either add the views or rewrite the steps to use
  `get_state` fields.
- **Script paths.** The judges' README names `./scripts/reproduce-measurements.sh` and
  `./scripts/verify-transcript.sh`. The files are `scripts/p3_reproduce_measurements.sh` and
  `agent/verify-transcript.sh`.
