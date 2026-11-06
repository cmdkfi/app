# Scene `exit-v4`: the agent exits a live-only vault (Base Sepolia, 6 Nov 2026)

A second team vault (v4) lists only the live market, so a leave_market judgment has no destination and the agent's code turns it into exit. Two demo feed items announce a drain of the live market; the judge answers leave_market with risk critical; the agent calls exit on the vault, the vault signs the Base transaction through Chain Signatures and the agent broadcasts it: 0.002006 WETH sold, 4.442487 USDC repaid, the rest of the collateral back at the vault's Base address, six minutes after the items were pushed (seven after their own timestamp). Two earlier runs the same day are kept: venue stress alone, where the judge held, and a first text-path run where the agent's NEAR RPC call answered with a pending error after the vault had signed, so the operator broadcast the signed transaction by hand. Data: `exit-v4.json`; the judge transcripts are archived next to it (`exit-v4-transcript-0006.json`, `-0045.json`, `-0046.json`) with the SHA-256 the agent's index served.

## The vault

`v4.factory.prime-measure-1792736012.testnet`, Base address `0xd3983a65120d1c17f26e6dd6151c5ff3a0701db0`, owner the test wallet, one market (live), corridor target 0.50, act 0.70, floor 0.30, sell budget 0.20, migrate and exit allowed. Because no other market is approved, the agent's code has no destination for a `leave_market` judgment and turns it into `exit` (`agent/src/logic.rs`). Setup, in order: create through the relayer (NEAR `9vJTp4GveQaLb9RyjVG9taZEeS14Cb1GFfC3KzechQGE`), 0.004 WETH to the Base address (`0xb9aa5bb8a08925c7b59450f504ff1a56f2872c2ed9891c8567bbf499fbc0c6c2`), owner `setup` with the agent measurement (NEAR `H9FyHwiEmnBBKya8kCiABjtt7PbyZ4gxn2fjLBz5nr5j`, 4 Base transactions), owner `open` at 50 % (NEAR `C2RhNXCG13g7RAjc8vinsJFVmhWwoBLeVoKdXTAXo1SG`), owner `withdraw` of the borrowed USDC to the wallet so an exit has to sell collateral (NEAR `896Pijh4QdWwqM2yzb5PepnzGnsS2RBixwCjUFhSfUAm`), and the agent's own registration (NEAR `ELBi3Bh29pap7ZVH6k7C6w4CoxtLx8q5zQzTWH4newig`, key valid to 2026-11-13T15:06:21Z). The agent waits for 0.16 NEAR in its account before each registration and held 0.148, so the operator called the relayer's `/fund-agent` once (NEAR `Hvm8jeVYffqTwsH9cDDSVSvmc7e8EvWmdpeXXGgBEgXB`).

## The exit (third run, 16:22 to 16:29 UTC)

The position was reopened the same way (0.002 WETH more to the address, `open` at 50 %: 4.442487 USDC of debt, `withdraw` of that USDC; NEAR `6a9ygwm3vXrtZt6wdthK6wFkq5h6mwXZsJQ2cTR85wqd`, `GpMnWTwv1qZvK19mGPprvvmJd6BWqWuci8NYKthxtJNH`). At 2026-11-06T16:22:08Z two items were appended to the demo feeds (commit `89ab92c`, pushed 16:23:10 UTC, both labelled as ours): `venue-status-2026-11-06-002` and `governance-2026-11-06-002`, announcing a drain of the live market. The agent fetched them on a later poll (raw GitHub caches 300 s) and called the judge; the answer took 161.36 s. Transcript `boot-1793948092-14fda74d-v4-0046.json` at 2026-11-06T16:28:45Z: risk **critical**, action **leave_market**, market live; the agreement rule's text path counted items from two independent feeds; no approved market could take the position, so the decision was `exit` on live.

The agent called `exit` on the vault (NEAR `rEynPrjrfttWbgnu4NVztQH3LC9i9J5ieLmx4VanGER`, final). The vault checked the key and the policy, built the executor call and had it signed by Chain Signatures (`v1.signer-prod.testnet`, path `cmdk-base`); the agent broadcast the signed transaction.

| | |
|---|---|
| Base transaction | `0x014aebcd2cb811e7109ac3df131650d2ff78a32cf43f5a02d2a58ab11299a556` (block 55303401, 2026-11-06T16:29:09Z, status 1, gas 329,202 at 6,000,000 wei + L1 fee 90 wei; broadcast by the agent, `proposal.base` in the transcript) |
| `Exited` event | repaid 4.442487 USDC (0.000000 from idle USDC), sold 0.002005782 WETH, collateral out 0.001991909 WETH |
| v4 after (block 55303593) | no collateral and no debt on Morpho; the vault's Base address holds 0.001991909 WETH and 0 USDC |
| Live pool after the sale | -0.704 % (pool below the oracle after the sale; the timer repairs within 10 minutes) |
| Elapsed | items pushed 16:23:10, judge answered 16:28:45, exit mined 16:29:09 UTC |

## Earlier the same day

**Run 1, venue stress alone (15:07:57 to 15:48:43 UTC).** `scripts/venue_stress.py run --vault 0xd398… --tag v4 --scene venue-stress-exit --expect exit`: the test wallet borrowed 8.449306 USDC, utilization 0.9923, borrow APR 2.49 %. The utilization screen fired and the judge answered at 2026-11-06T15:12:07Z (`boot-1793948092-14fda74d-v4-0006.json`): risk elevated, **hold** ("borrow APR is low at 2.49 % and repaying or deleveraging does not depend on this market's lend-side liquidity, so migration is not warranted; monitor instead"). No action in 40 minutes; the borrow was repaid. Record: `venue-stress-exit.md`. The same stress made the judge answer `leave_market` for v1 on 5 Nov (`venue-stress.md`); the judge is not reproducible, which is why every decision carries its transcript.

**Run 2, the text path with a broadcast by hand (15:51 to 16:01 UTC).** Items `venue-status-2026-11-06-001` and `governance-2026-11-06-001` (commit `11bb6e8`). Transcript `boot-1793948092-14fda74d-v4-0045.json` at 2026-11-06T15:58:57Z: risk critical, leave_market, decision exit. The agent called `exit` (NEAR `3hxs2UC25j3F42K41EryQVCjG8u1TSwyLwUjL5NASQ32`, final; the vault signed the Base transaction, nonce 6). The agent's RPC `send_tx` answered `HANDLER_ERROR` with cause `PENDING` although the transaction was already included (`final_execution_status: INCLUDED_FINAL`), so the agent recorded `near_error` and did not broadcast. The operator read the signed transaction from the NEAR outcome and broadcast it with `cast publish` at about 16:01 UTC; no key was involved and nothing in the transaction could be changed. Base `0xd90f46ed88ace72d77bab2157ff07d054ce1518f0f8b23259f7e76a2bdd793d0` (block 55295076, 2026-11-06T16:01:24Z, status 1): repaid 4.450866 USDC, sold 0.002002309 WETH, collateral out 0.001997691 WETH.

## What this shows

- the agent's key can call only the vault's agent methods; the vault built the Base transaction and had it signed by Chain Signatures.
- the signed transaction is public in the NEAR outcome: anyone can broadcast it, nobody can alter it, and a failed broadcast is recoverable without any key.
- a leave-market judgment on a vault with no approved destination becomes an exit, a sale of collateral at the oracle-checked price.

## Hardening item

the agent treats a NEAR send_tx answer of HANDLER_ERROR/PENDING as a failure although the transaction was included; it should poll the transaction and broadcast (agent/src/near.rs), or the relayer should broadcast signed transactions found in NEAR outcomes.
