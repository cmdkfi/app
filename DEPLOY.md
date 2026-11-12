# Public testnet deployment: the record (4 to 9 November 2026)

What was deployed on Base Sepolia (chain 84532) and NEAR testnet, in what order, with the commands that did it and what they returned. Nothing here touches mainnet. The section numbers are the steps other files cite ("DEPLOY.md step 6"). Logs and relayer responses are in `evidence/deploy/`; the live values are in `deployments/testnet.json`.

## Result

| Item | Value |
|---|---|
| Executor | `0xA9bbabE6d75C5BA96CA095586284774c9D09D18b`, block 54354973, `COOLDOWN` 60 s, three markets (live, backup, demo); the prototype executor `0xB23e0d7e1D0182Db7f6f2637EC4Ac51c10290f40` retired and its grants revoked |
| Vault Global Contract | reproducible build of commit `975b98d`, 783,124 bytes, `G1Q5ksFhGbgPTN4A2DuvgraYmsbRpfwb78B547gAKXWq` (hex `def9671e…77e1ba`); published for 78.31 NEAR, tx `8PsReqwVUKeWqFbgN2DXTYtQM5PvRD9mZP2BYbRoFJng` |
| Factory | `factory.prime-measure-1792736012.testnet`, keys deleted, vault hash fixed at `init` (`vault/factory/DEPLOY.md`) |
| Team vault | `v1.factory.prime-measure-1792736012.testnet`, Base address `0xb3ccd0ad7e5fca9d4c18cf59d19fab4703eee6fb`; `setup` (7 receipts) and `open` on live at LTV 499999892050639305 |
| Lender supply | 13 USDC on live, 13 USDC on backup, 5,000 dUSDC on demo |
| Pool bands | live band 83707 after a 1.659 USDC repair; demo anchor 83708, sell 83709, buy 83710 |
| Timer | on Cloudflare Containers, resumed 4 Nov 11:50 UTC, every round `exit=0` |
| Agent | four images over the week: `@sha256:984f0c0b…` (commit `db6aaf8`, 4 Nov), `@sha256:54405980…` (`407847d`, 5 Nov), `@sha256:479f98f3…` (`fd0272f`, 5 Nov), `@sha256:e72564a8…` (`4171c10`, 9 Nov, current: boot `2286af92`, compose `db4f5085…`, approved on v1 by NEAR tx `CoHoSrYiNfhJ9KduGpvqFGmn1TzxcopbDgWRLuHZwQdk` and on v4 by `EpbnCDh9Jrh8bmsNQLdLDT4UmSYUHz5pS2LHHxjhphE6`) |
| Live scene | owner act line 45 %, the agent's protective `reduce` in the next tick repaid 3.849781 USDC and sold 0.001664 WETH, LTV 0.49967 → 0.40015 (`evidence/protect-live.txt`) |

The 9 Nov image moved the agent's NEAR reads to FastNear, dRPC and Intear first, because `rpc.testnet.near.org` answered the enclave `-429 … DEPRECATED` on about 95 % of calls. Image updates kept their CVM and its gateway URL (`phala deploy --cvm-id … --wait`); the CVM itself was replaced on 6 Nov after Phala's KMS incident (`evidence/incident-2026-11-06-phala-kms.md`). Each new compose hash was recomputed from the boot's `app-compose.json` and approved by each vault's owner.

## Budget (Base Sepolia test wallet)

The team's test wallet `0x0B7Ed66f46fe83252a208563A88201BA60490d25` paid every Base step. After the 3 Nov close-out (`evidence/venue/2026-11-03-close-out.md`) and a top-up of 40 USDC and 0.2 ETH it held 0.487648 ETH, 0.057910 WETH, 64.341191 USDC and 25,026 dUSDC (our mintable test token). The steps took 26 USDC of supply, about 10 USDC of band, and keep a 14 USDC floor for the timer (8 reserve + 6 maximum repair); 0.0144 WETH to the bands and 0.01 WETH plus 0.01 ETH to the vault. Sizes: 13 USDC on live covers the vault's open of 0.01 WETH at 50 % up to an ETH price of $2,600; 13 USDC on backup takes a full migrate of that debt; the scene's sale from 50 % to a 40 % target is value / 6, about 3.81 USDC (`CorridorMath.repayToLtv`). NEAR: 85 on the publishing account, 5 on the relayer.

## 0. Prerequisites and shell setup

Before the run: the executor's test suite green; the vault built reproducibly (`vault/factory/DEPLOY.md` step 1, Docker with disk room); the agent image pinned by digest in `agent/docker-compose.live.yml`; the relayer and timer container deployed (`cloud/README.md`), answering `/health` with `"ok":true` and `/timer` with `"paused":true`; nothing else running on the wallet.

The commands below share one setup file outside the repository: addresses and two helpers, no keys. `tx` sends one transaction from the test wallet through `scripts/rebalance_pool.py` (which reads the wallet key itself and refuses any chain but Base Sepolia); `owner` signs an owner message with `scripts/p2_owner.py` and submits it through the relayer.

```sh
set -o pipefail
export PATH="$HOME/.foundry/bin:$PATH" RPC=https://base-sepolia-rpc.publicnode.com
U=https://relayer.cmdk.fi; A=<file holding the relayer's admin header>   # cloud/README.md
LOG=evidence/deploy/2026-11-04.log
W=0x0B7Ed66f46fe83252a208563A88201BA60490d25; OLD=0xB23e0d7e1D0182Db7f6f2637EC4Ac51c10290f40
MORPHO=0xBBBBBbbBBb9cC5e90e3b3Af64bdAF62C37EEFFCb; IRM=0x46415998764C29aB2a25CbeA6254146D50D22687; LLTV=860000000000000000
USDC=0x036CbD53842c5426634e7929541eC2318f3dCF7e; WETH=0x4200000000000000000000000000000000000006; DUSDC=0xeb68Fd5bAAAfD4e46b43bAa7fEAb3216B87b10d5
LIVE=0xa25ac5171b7e500b82653d3750b91bb44e2409c32ca3c9e80b0db077e8a4535c; LIVE_ORACLE=0x7D0ebc71EBBEC6F215C2Ab818F9dB538A52f6a3E
BACKUP=0x99d43c0f2a0eb6172774598a4e34e7eee09caa502692c2b2df0f40e1e3c5ea75; BACKUP_ORACLE=0x20Ff664b99676a45A13bD77ACB2AFECeA10Cd538
DEMO=0x55872b53dba6147b6badf9d4277bc166dfb7d5f8ac8d491da093960dbef6d585; MOCK=0x91Ec8d7177b876017773cf787B2caB0Fe6Ac6b2f
MAX=115792089237316195423570985008687907853269984665640564039457584007913129639935
EXEC=$(jq -r .base.executor deployments/testnet.json)
V=$(jq -r '.near.vault // empty' deployments/testnet.json); VB=$(jq -r '.near.vault_base_address // empty' deployments/testnet.json)
tx() {  # tx LABEL TO SIG [ARGS...]; VALUE=<wei> for ETH; prints "LABEL: tx 0x… status 1 gas N, fee N wei"
  python3 -I -c '
import sys; sys.path.insert(0, "scripts"); import rebalance_pool as rp
assert int(rp.subprocess.run([rp.CAST, "chain-id", "--rpc-url", rp.RPC], capture_output=True, text=True).stdout) == rp.CHAIN_ID, "not Base Sepolia"
fd = rp.lock(); v = rp.os.environ.get("VALUE"); rp.send(*sys.argv[1:], value=int(v) if v else None)' "$@" 2>&1 | tee -a "$LOG"
}
owner() {  # owner METHOD ARGS_JSON NAME: sign as the vault owner and submit through the relayer, synchronously
  python3 -I scripts/p2_owner.py --vault "$V" --method "$1" --args "$2" --out "$HOME/.local/state/cmdk/$3.signed.json" >> "$LOG" &&
  jq -c --arg v "$V" '{vault: $v, message, signature, wait: true}' "$HOME/.local/state/cmdk/$3.signed.json" |
    curl -s $U/owner-call -H 'Content-Type: application/json' -d @- > "evidence/deploy/2026-11-04-$3.json"
  jq -c '{ok, near_tx, error, receipts: [.receipts[]? | {hash, status}]}' "evidence/deploy/2026-11-04-$3.json" | tee -a "$LOG"
}
```

`tx` sends nothing when `cast estimate` fails. Steps 2 to 5 were first run with these exact commands on an anvil fork of Base Sepolia (3 Nov), and every output matched. The timer and `tx` share the wallet's nonce, so wallet transactions from outside the container go between `POST /pause` and `POST /resume` (step 7e).

## 1. Balance check

`python3 -I scripts/rebalance_pool.py --market live --status | grep '^wallet'` → `wallet 0x0B7Ed66f46fe83252a208563A88201BA60490d25: 0.487648 ETH, 0.057910 WETH, 64.341191 USDC, 25026.493470 dUSDC`; the timer reported `"paused":true`.

## 2. Executor redeploy

```sh
(cd executor && forge test 2>&1 | tail -1)          # 64 tests passed, 0 failed
(cd executor && BACKUP_ORACLE=$BACKUP_ORACLE COOLDOWN=60 DEPLOY_OUT=deployments-2026-11-04.json ./script/deploy.sh)
```

Two transactions: `MockOracle.setPrice` (the demo oracle moves to the live oracle's price) and the `Executor` creation, at `0xA9bbabE6d75C5BA96CA095586284774c9D09D18b`, block 54354973; the address and block were written into `deployments/testnet.json`. Read-only checks: `COOLDOWN()` 60, `marketCount()` 3, the live and backup configs carry the public Uniswap USDC/WETH 0.01 % pool `0x57183717A087d2fe3Ad890873877244c3B96156c` (every enabled fee tier already has a public pool; backup's migrate uses a flash loan and no swap), demo carries `0x722F350ec250c1697063A080B0196506D7Fe21c1`. The agent and the relayer read the executor from the vault's policy (step 7f), not from the file.

## 3. Old executor's grants revoked, new ones set

Eleven transactions from the test wallet, all `status 1`: allowance 0 on USDC, WETH and dUSDC and Morpho authorization off for `$OLD`; unlimited allowances and Morpho authorization for the new executor; corridors 50/70/30, sell budget 20 %, migrate and exit allowed on live, backup and demo (`setCorridor` with a deadline 600 s ahead). Read back: allowances `old 0 new 1157…9935`, `Morpho old false new true`, corridors `5e17 7e17 3e17 2e17 true true true` on each market.

## 4. Lender supply

```sh
S='supply((address,address,address,address,uint256),uint256,uint256,address,bytes)'
tx 'approve Morpho 26 USDC' $USDC 'approve(address,uint256)' $MORPHO 26000000
tx 'supply 13 USDC to live'   $MORPHO "$S" "($USDC,$WETH,$LIVE_ORACLE,$IRM,$LLTV)" 13000000 0 $W 0x
tx 'supply 13 USDC to backup' $MORPHO "$S" "($USDC,$WETH,$BACKUP_ORACLE,$IRM,$LLTV)" 13000000 0 $W 0x
tx 'approve Morpho 5,000 dUSDC' $DUSDC 'approve(address,uint256)' $MORPHO 5000000000
tx 'supply 5,000 dUSDC to demo' $MORPHO "$S" "($DUSDC,$WETH,$MOCK,$IRM,$LLTV)" 5000000000 0 $W 0x
```

Five `status 1` lines; `totalSupplyAssets` read back `13000001`, `13000001` and `5000000001` (each market kept a one-unit remainder from the close-out).

## 5. Live band and demo layout

```sh
python3 -I scripts/rebalance_pool.py --market live --band-usdc 10 --tolerance 0.0005
python3 -I scripts/rebalance_pool.py --market demo
```

`--band-usdc 10` caps the live band at 10 USDC plus WETH of equal value; the tight tolerance forces a repair first, so the band is centred on the oracle (fork measurements of 3 Nov: a band minted 0.155 % off centre took only 7.21 of the 10 USDC and a 5 USDC sale then moved the pool 0.536 %; centred, 3.82 USDC moved it 0.358 %). Result: live band position 83707 after a 1.659 USDC repair (the pool had drifted 2.36 % since the close-out); demo anchor 83708, sell band 83709 (20,000 dUSDC, minted), buy band 83710.

## 6. Timer

The rebalancer runs in the container `cmdk-relayer` on Cloudflare (`cloud/README.md`): a cron every 10 minutes runs one round, live then demo, with `--band-usdc 10`. It started paused; the state files of step 5 were uploaded and the timer resumed:

```sh
jq -n --slurpfile l scripts/state/live.json --slurpfile d scripts/state/demo.json '{live: $l[0], demo: $d[0]}' |
  curl -s -X PUT -H @$A $U/state -H 'Content-Type: application/json' --data-binary @- | jq -c .state
curl -s -X POST -H @$A $U/resume
```

From the next cron on, `GET /runs` showed one `RUN … market=live … exit=0 source=cron` and one `market=demo` line every 10 minutes. `POST /pause` lets a round in progress finish and then stops the timer.

## 7. Vault, relayer, `setup` and `open`

**7a. Publish and factory.** `vault/factory/DEPLOY.md` steps 0 to 5: reproducible build from commit `975b98d`, publish by hash, factory `init`, the factory's keys deleted. Values in the Result table above.

**7b. Vault v1.** `vault/factory/DEPLOY.md` step 6 with `OWNER=0x0B7Ed66f46fe83252a208563A88201BA60490d25` (the test wallet, so `scripts/p2_owner.py` can sign its owner messages) and the step 2 executor; then that file's step 7 checks.

**7c. NEAR side recorded.** The factory, relayer, vault, its Base address and the code hash (base58 and hex) were written into `deployments/testnet.json` (`near.*`).

**7d. Relayer.** In the same container; it signs with the relayer account's access key (`NEAR_RELAYER_KEY_JSON`, a container secret). Each vault created through `/create-vault` gets 0.005 ETH from the test wallet for its first Base gas; v1 was created by the factory directly, so 7e funded it by hand. `curl -s https://relayer.cmdk.fi/health` → `"ok":true`, `"dry_run":false`, the relayer account, the factory, `"factory_exists":true`, `"chain_id":84532`.

**7e. Gas money and collateral for the vault's Base address.**

```sh
curl -s -X POST -H @$A $U/pause
VALUE=10000000000000000 tx '0.01 ETH to the vault' $VB 0x
tx '0.01 WETH to the vault' $WETH 'transfer(address,uint256)' $VB 10000000000000000
curl -s -X POST -H @$A $U/resume
```

**7f. `setup` on all three markets** (one owner signature, 7 Base transactions paid by the vault's ETH): live approves both tokens and authorizes Morpho; backup shares tokens and executor with live, so it sets only its corridor; demo approves dUSDC. Corridors 50/70/30, sell budget 20 %, migrate and exit allowed (`scripts/RELAYER.md` has the message shape).

```sh
C='{"target":"500000000000000000","act":"700000000000000000","floor":"300000000000000000","sell_budget":"200000000000000000","migrate_allowed":true,"exit_allowed":true}'
owner setup '{"markets":[{"market":"'$LIVE'","approve":true,"authorize":true,"corridor":'$C'},{"market":"'$BACKUP'","approve":false,"authorize":false,"corridor":'$C'},{"market":"'$DEMO'","approve":true,"authorize":false,"corridor":'$C'}]}' setup
```

Response `"ok":true` with 7 receipts, each `"status":1` (`evidence/deploy/2026-11-04-setup.json`).

**7g. `open` on live** with the vault's 0.01 WETH at 50 %: `owner open '{"market":"'$LIVE'","collateral_in":"10000000000000000","target_ltv":"500000000000000000"}' open-live` → one receipt, `status 1`; `ltvOf` 499999892050639305 (`evidence/deploy/2026-11-04-open-live.json`).

## 8. Agent

Operator `cmdk`, image `ghcr.io/cmdkfi/agent` pinned by digest in `agent/docker-compose.live.yml`, `--mode live`, Phala's pre-launch script `agent/pre-launch.sh`, deployed with the command in `agent/MEASUREMENT.md` §2. The CVM's port-8080 gateway URL is `AGENT_EVIDENCE_URL` in `cloud/wrangler.jsonc`; from then on the app reads the agent through `https://relayer.cmdk.fi/agent/vaults`. The measurement from the boot's `RESULT measurement={…}` line was checked against `agent/MEASUREMENT.md` (MRTD, RTMR0 to 2, OS image hash; compose hash recomputed) and approved with `owner approve_measurement '<the JSON>' approve-measurement` (`"ok":true`, no receipts). The compose names the real factory account before the image is measured, since the compose hash is part of the measurement.

Funding is automatic: the agent prints `WAITING implicit_account=<64 hex> …` while its implicit account is short, and the Worker's cron makes the `/fund-agent` call for the account in the agent's quote (`cloud/README.md`, "Agent funding"), topping up to 0.16 NEAR, capped at 2 NEAR per 24 h in all; by hand: `curl -s -H @$A $U/fund-agent -d '{"implicit_account":"<64 hex>"}'`. Then `register_agent` lands (`agent/MEASUREMENT.md` §7) and `get_state` shows the agent key with `expires_at` 7 days ahead.

## 9. Live reduce scene

The owner lowers the act line under the position's LTV; on its next tick the agent finds a protective session and sells to the new target through the executor.

```sh
curl -s -X POST -H @$A "$U/rebalance?market=live&tolerance=0.0005"     # centre the pool first
owner set_corridor '{"market":"'$LIVE'","target":"400000000000000000","act":"450000000000000000","floor":"300000000000000000","sell_budget":"200000000000000000","migrate_allowed":true,"exit_allowed":true}' set-corridor-act45
cast logs --from-block <block before the change> --address $EXEC 'Reduced(address indexed caller, bytes32 indexed market, uint256 repaid, uint256 collateralSold, uint256 ltvBefore, uint256 ltvAfter, bool protective)' $VB $LIVE --rpc-url $RPC
```

The agent's `Reduced` event: repaid 3849781 (3.849781 USDC), collateralSold about 1.66e15, ltvBefore 0.49967, ltvAfter 0.40015, `protective: true`. The corridor was then set back to 50/70/30 (`set-corridor-restore`).

## 10. Evidence

`evidence/protect-live.txt` (the owner's change, the agent's `Reduced` event, the LTV after, the restore), `evidence/deploy/2026-11-04-*.json` (the relayer's responses), `evidence/deploy/2026-11-04.log` (every step's output), `evidence/deploy/2026-11-04-timer-rebalance.log` (the timer's `RUN` lines), and later `evidence/deploy/2026-11-05*` and `2026-11-09*` for the image updates. None of them holds a key; the signed owner messages stayed outside the repository.
