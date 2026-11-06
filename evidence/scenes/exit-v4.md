# Scene `exit-v4`: the agent exits a live-only vault (Base Sepolia, 6 Nov 2026)

A second team vault (v4) that lists only the live market is told by two demo feed items that the live market is about to be drained; the judge answers leave_market with risk critical; with no approved destination the agent's code turns that into exit; the vault signs the Base transaction through Chain Signatures; the agent's NEAR RPC call reports a pending error, so the operator broadcasts the signed transaction from the NEAR outcome; the exit sells 0.002003 WETH, repays 4.450866 USDC and returns the rest of the collateral to the vault's Base address. Data: `exit-v4.json`; the two judge transcripts are archived next to it (`exit-v4-transcript-0006.json`, `exit-v4-transcript-0045.json`) with the SHA-256 the agent's index served.

## The vault

`v4.factory.prime-measure-1792736012.testnet`, Base address `0xd3983a65120d1c17f26e6dd6151c5ff3a0701db0`, owner the test wallet, one market (live), corridor target 0.50, act 0.70, floor 0.30, sell budget 0.20, migrate and exit allowed. Because no other market is approved, the agent's code has no destination for a `leave_market` judgment and turns it into `exit` (`agent/src/logic.rs`). Setup, in order: create through the relayer (NEAR `9vJTp4GveQaLb9RyjVG9taZEeS14Cb1GFfC3KzechQGE`), 0.004 WETH to the Base address (`0xb9aa5bb8a08925c7b59450f504ff1a56f2872c2ed9891c8567bbf499fbc0c6c2`), owner `setup` with the agent measurement (NEAR `H9FyHwiEmnBBKya8kCiABjtt7PbyZ4gxn2fjLBz5nr5j`, 4 Base transactions), owner `open` at 50 % (NEAR `C2RhNXCG13g7RAjc8vinsJFVmhWwoBLeVoKdXTAXo1SG`, Base `0x7788afbda766c67a6f9a47f26a830dbc6e10ec97fb3f5dbb61a49282b17e924a`: 0.004 WETH collateral, 4.450858 USDC debt), owner `withdraw` of that USDC to the wallet so an exit has to sell collateral (NEAR `896Pijh4QdWwqM2yzb5PepnzGnsS2RBixwCjUFhSfUAm`), and the agent's own registration (NEAR `ELBi3Bh29pap7ZVH6k7C6w4CoxtLx8q5zQzTWH4newig`, key valid to 2026-11-13T15:06:21Z). The agent waits for 0.16 NEAR in its account before each registration and held 0.148, so the operator called the relayer's `/fund-agent` once (NEAR `Hvm8jeVYffqTwsH9cDDSVSvmc7e8EvWmdpeXXGgBEgXB`).

## Attempt 1: venue stress alone (15:07:57 to 15:48:43 UTC)

`scripts/venue_stress.py run --vault 0xd398… --tag v4 --scene venue-stress-exit --expect exit`: the test wallet borrowed 8.449306 USDC, utilization 0.9923, borrow APR 2.49 %. The utilization screen fired and the judge answered at 2026-11-06T15:12:07Z (`boot-1793948092-14fda74d-v4-0006.json`): risk elevated, **hold** ("borrow APR is low at 2.49 % and repaying or deleveraging does not depend on this market's lend-side liquidity, so migration is not warranted; monitor instead"). No action in 40 minutes; the borrow was repaid. Record: `venue-stress-exit.md`. The same stress made the judge answer `leave_market` for v1 on 5 Nov (`venue-stress.md`); the judge is not reproducible, which is why every decision carries its transcript.

## Attempt 2: the text path

At 2026-11-06T15:51:29Z two items were appended to the demo feeds (commit `11bb6e8`, both labelled as ours): `venue-status-2026-11-06-001` ("Live market stress test: free liquidity drained, withdrawals may fail") and `governance-2026-11-06-001` ("Live market: scheduled stress test, positions advised to leave"). The agent fetched them on its next poll and called the judge. Transcript `boot-1793948092-14fda74d-v4-0045.json` at 2026-11-06T15:58:57Z: risk **critical**, action **leave_market**, market live; the agreement rule's text path counted the two items as two independent feeds with recent cited items; no approved market could take the position, so the decision was `exit` on live.

The agent called `exit` on the vault (NEAR `3hxs2UC25j3F42K41EryQVCjG8u1TSwyLwUjL5NASQ32`, 150 Tgas). The vault checked the key and the policy, built the executor call (nonce 6, gas limit 568,953) and had it signed by Chain Signatures (`v1.signer-prod.testnet`, path `cmdk-base`); the signed transaction is the NEAR transaction's return value. The agent's RPC `send_tx` answered `HANDLER_ERROR` with cause `PENDING` although the transaction was already included (`final_execution_status: INCLUDED_FINAL`), so the agent recorded `near_error` and did not broadcast. The operator read the signed transaction from the NEAR outcome and broadcast it with `cast publish` at about 16:01 UTC; no key was involved and nothing in the transaction could be changed.

| | |
|---|---|
| Base transaction | `0xd90f46ed88ace72d77bab2157ff07d054ce1518f0f8b23259f7e76a2bdd793d0` (block 55295076, 2026-11-06T16:01:24Z, status 1, gas 346,214 at 6,000,000 wei + L1 fee 174 wei) |
| `Exited` event | repaid 4.450866 USDC (0.000000 from idle USDC), sold 0.002002309 WETH, collateral out 0.001997691 WETH |
| v4 after (block 55295284) | no collateral and no debt on Morpho; the vault's Base address holds 0.001997691 WETH and 0 USDC |
| Live pool after the sale | -0.361 % (pool below the oracle after the sale; the timer repairs within 10 minutes) |

## What this shows

- the agent's key can call only the vault's agent methods; the vault built the Base transaction and had it signed by Chain Signatures.
- the signed transaction is public in the NEAR outcome: anyone can broadcast it, nobody can alter it, and a failed broadcast is recoverable without any key.
- a leave-market judgment on a vault with no approved destination becomes an exit, a sale of collateral at the oracle-checked price.

## Hardening item

the agent treats a NEAR send_tx answer of HANDLER_ERROR/PENDING as a failure although the transaction was included; it should poll the transaction and broadcast (agent/src/near.rs), or the relayer should broadcast signed transactions found in NEAR outcomes.
