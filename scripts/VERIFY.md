# Verification scripts: `reproduce-hash.sh` and `ltv.sh`

Two read-only checks behind steps 2 and 4 of the README's "Verify it". Neither needs a key or sends a
transaction; both work on testnet only (NEAR testnet, Base Sepolia, chain 84532).

| Script | Needs |
|---|---|
| `scripts/reproduce-hash.sh` | Rust (rustup), `cargo near` (cargo-near), `python3`; `curl` and `jq` for `--vault`; Docker for `--reproducible` |
| `scripts/ltv.sh` | `cast` (Foundry), `curl`, `jq`, `python3`; bash 3.2 or later |

Run both from the repository root. Public RPCs are the defaults: `https://rpc.testnet.near.org` (`--near-rpc` or
`$NEAR_RPC`) and `https://base-sepolia-rpc.publicnode.com` (`--rpc` or `$BASE_RPC`, `ltv.sh` only).

## 1. `reproduce-hash.sh`: the vault's code hash, built from this checkout

```sh
./scripts/reproduce-hash.sh                                   # host build
./scripts/reproduce-hash.sh --vault <vault>.testnet           # host build, then compare with the published hash
./scripts/reproduce-hash.sh --reproducible --vault <vault>.testnet   # Docker build (see 1.2)
```

The script builds `vault/` with `cargo near`, then hashes the wasm itself (Python `hashlib`) and checks that the
base58 equals the one `cargo near` printed. The base58 of the SHA-256 is what NEAR shows as the Global Contract
hash. With `--vault` it reads the account with the RPC `view_account` query and compares `global_contract_hash`.
For an account whose contract was deployed directly (not a Global Contract), it compares `code_hash` and says so.

Expected output shape (host build on 3 Nov 2026, macOS arm64; the build log goes to a temporary file named on
stderr):

```text
build            cargo near build non-reproducible-wasm
                 (host toolchain, NOT reproducible: another machine, rustc or cargo-near version may give a
                 different hash; only --reproducible can be expected to match bit for bit)
commit           <12-character commit> (<n> uncommitted or untracked paths in the checkout)
toolchain        rustc 1.95.0 (59807616e 2026-04-14); cargo-near-near 0.22.0
wasm             <repo>/vault/target/near/cmdk_vault.wasm
size             782345 bytes
sha256 (hex)     92b2746ccc4ad0cbb263b7de1eefdc7b61cff5dad45287075b4ab902956ab0b4
sha256 (base58)  AseMK3E3saWeqmRt92xihZeb5a5UiChaPyvp6UhktYBm   <- NEAR's Global Contract hash
vault            <vault>.testnet (view_account, NEAR testnet block <height>)
published        <base58 hash>   <- global_contract_hash (Global Contract by hash)
result           MATCH
```

The last three lines appear only with `--vault`. Exit codes: 0 built (and `MATCH`), 1 error, 2 usage, 3 `MISMATCH`.
The hash above belongs to the checkout it was built from; the published hash is in `deployments/testnet.json`
(`near.vault_code_hash`) once the vault is deployed.

### 1.1 Caveat: the host build is not reproducible

`cargo near build non-reproducible-wasm` uses the local Rust toolchain. Another rustc or cargo-near version,
another OS or a different checkout path can change the bytes, so a different hash from the host build on your
machine is not by itself evidence of different source. Only the reproducible build (1.2) is expected to give the
published hash bit for bit on any machine. The host build is still useful on the machine that built the deployed
wasm, and it shows the exact build command.

### 1.2 `--reproducible`

`cargo near build reproducible-wasm` builds a clean clone of the last commit inside a Docker image pinned by
digest. Before it starts, the script checks and lists what is missing:

- a running Docker daemon (`docker info`);
- `[package.metadata.near.reproducible_build]` in `vault/Cargo.toml` with `image`, `image_digest` and
  `container_build_command` (`passed_env` optional), as in the template `cargo near new` generates;
- `package.repository` in `vault/Cargo.toml`: an `https` URL of a repository that can be cloned (cargo-near
  rejects other schemes and an empty value);
- a clean git working tree: no uncommitted or untracked paths.

The image published for this (`sourcescan/cargo-near`) is `linux/amd64` only; on an arm64 machine Docker runs it
under emulation, which works but is slower.

## 2. `ltv.sh`: a vault's collateral, debt and LTV on Morpho

```sh
./scripts/ltv.sh <vault>.testnet                  # live market (default)
./scripts/ltv.sh <vault>.testnet --market backup  # live | backup | demo | 0x<market id>
./scripts/ltv.sh --address 0x<Base address> --market live   # skip the NEAR lookup
```

Steps:

1. NEAR testnet: the vault's `get_state` view (RPC `call_function`, finality `final`, no keychain) gives
   `base_address`, `morpho` and `markets`; the market must be in the vault's `markets`, and its `executor` is the
   one asked for `ltvOf`. With `--address`, Morpho and the executor come from `deployments/testnet.json`.
2. Base Sepolia, every read at one block: Morpho `idToMarketParams`, `market`, `position`; the market's oracle
   `price()` (the oracle is read from Morpho's market parameters, not from our file); token `decimals` and
   `symbol`; the IRM's `borrowRateView`; the executor's `ltvOf(address, id)`.
3. Arithmetic in Python integers (Morpho Blue SharesMathLib and MathLib; the executor's CorridorMath):

   - debt = ceil(borrowShares × (totalBorrowAssets + 1) / (totalBorrowShares + 1e6))
   - collateral value = floor(collateral × price / 1e36), in loan-token units
   - LTV = ceil(debt × 1e18 / collateral value) in WAD, printed as a percentage to 4 decimals; 0 without debt
   - liquidatable when debt > floor(collateral value × LLTV / 1e18), Morpho's health rule

Expected output (test wallet's position on the backup market, 3 Nov 2026):

```text
cmdk ltv.sh (read-only) - Base Sepolia (84532) block 53819084, 2026-11-03 06:01:25 UTC
source           --address (no vault lookup; Morpho and executor from deployments/testnet.json)
base address     0x0B7Ed66f46fe83252a208563A88201BA60490d25
market           backup 0x99d43c0f2a0eb6172774598a4e34e7eee09caa502692c2b2df0f40e1e3c5ea75
  loan token     USDC 0x036CbD53842c5426634e7929541eC2318f3dCF7e (6 decimals)
  collateral     WETH 0x4200000000000000000000000000000000000006 (18 decimals)
  oracle         0x20Ff664b99676a45A13bD77ACB2AFECeA10Cd538 price 2244675801807553502812018763 (1e36 scale; 2,244.67 USDC per WETH)
  irm / lltv     0x46415998764C29aB2a25CbeA6254146D50D22687 / 86.00 %
  morpho         0xBBBBBbbBBb9cC5e90e3b3Af64bdAF62C37EEFFCb
  totals         totalBorrowAssets 6744896, totalBorrowShares 6744896000000, lastUpdate 1793685307 (2026-11-03 05:55:07 UTC, 378 s before this block)
position         supplyShares 7089983105264, borrowShares 6744896000000, collateral 6000000000000000
collateral       0.006000000000000000 WETH
collateral value 13.468054 USDC  (= collateral x price / 1e36, rounded down; raw 13468054)

                 at lastUpdate                accrued to block 53819084 (+378 s, rate 1302065709/s)
borrow assets    6744896                      6744899  (market totalBorrowAssets)
debt (raw)       6744896                      6744899
debt             6.744896 USDC                6.744899 USDC
LTV              50.0807 %                    50.0807 %
LTV (WAD, up)    500807020821270839           500807243570600475
liquidatable     no                           no
  (Morpho: liquidatable when debt > collateral value x LLTV = 11.582526 USDC)

executor ltvOf   500807020821270839 (50.0807 %) from 0xB23e0d7e1D0182Db7f6f2637EC4Ac51c10290f40
  cross-check    MATCH with the "at lastUpdate" LTV (WAD)
```

With a vault, `source` reads `<vault> (get_state, NEAR testnet block <height>)` and `base address` is the vault's
`base_address`. A vault without a position prints zeros and `(no position on this market)`.

Exit codes: 0 printed; 1 error, with one line saying which: vault account not found, account without a contract or
without `get_state`, market not in the vault's allowlist, market not found on Morpho, unknown market name, RPC not
reachable or not chain 84532; 2 usage.

### 2.1 Caveat: `lastUpdate` and accrual

Morpho's `market()` totals are as of the market's `lastUpdate`, the last transaction that touched the market. The
"at lastUpdate" column uses them as stored; the executor's `ltvOf` does the same, so the two must be equal
(`MATCH`). The "accrued to block" column adds the interest since `lastUpdate` the way Morpho's `_accrueInterest`
does: totalBorrowAssets += totalBorrowAssets × wTaylorCompounded(borrowRateView, seconds elapsed) / 1e18. This is
the debt Morpho would use in a transaction in that block, which is the figure a liquidation checks. On a local fork
of Base Sepolia, 86,605 s after `lastUpdate` on the backup market, the column gave totalBorrowAssets 6745684 and
Morpho's own `accrueInterest` at that timestamp stored 6745684.
