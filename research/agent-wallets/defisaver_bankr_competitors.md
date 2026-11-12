# DeFi Saver, Bankr, and how other agent products limit their bots — checked 2026-10-21

## DeFi Saver
- Positions sit in the user's Safe (default) or DSProxy; auth via SafeModuleAuth / ProxyAuth. EOA positions on Aave, Spark, Compound can be automated through a Safe operator.
- "Strategies are executed by Defi Saver backend bots, which themselves do not hold permission to users' funds." Only DFS creates strategies; users subscribe with their own parameters. (github.com/defisaver/defisaver-v3-contracts docs/protocol/strategies.md)
- Each execution: caller in BotAuth; subscription hash match; subscription enabled; triggers true. Many strategies check the outcome on-chain (e.g. AaveV3RatioCheck reverts `BadAfterRatio` if the ratio didn't improve or overshoots target + 5%). Bots choose amounts, swap route and gas within caps (gas fee ≤ 20% of amount, DFS fee ≤ 5 bps, allowlisted exchange wrappers).
- Upgrade authority: the user's Safe module delegatecalls the RecipeExecutor the DFSRegistry points to. Registry changes by the owner multisig with a 7-day timelock for core contracts (RecipeExecutor, StrategyExecutor, BotAuth: 604800 s on-chain) and 1 day for action contracts; the owner can revert to the previous address without timelock. On-chain today: AdminVault owner and admin are each 3-of-6 Safes. (docs/protocol/security-and-audits/admin-access-control.md, pause-control.md)
- Chains: app on 6 (Ethereum, Arbitrum, Optimism, Base, Linea, Plasma); automation only on Ethereum, Arbitrum, Optimism, Base. (help.defisaver.com supported-chains, automation/compatibility)
- TVL: $390.3M on DefiLlama (Ethereum $378.3M, Arbitrum $11.8M).
- Fees: 0.05% per automated adjustment + swap fee (0.25% uncorrelated); gas charged to the position. Minimum debt $30K on Ethereum, $500 on L2s.
- Incidents: two disclosed (2020 exchange approval bug, Jan 2021 Compound import bug), "No funds were lost or stolen"; 2024 $55M phishing of one user's DSProxy ownership (not a protocol bug). None involving automation bots.

## DeFi Saver Loan Shifter V2 (checked 2026-11-02)
- Loan Shifter is DeFi Saver's tool for moving a loan between protocols, or changing its collateral or debt asset, without closing and rebuilding it; live since 2020. V2 (announced 2 Nov 2026) adds EOA positions, partial shifts, a metrics preview before the shift (Net APY, liquidation price, safety ratio) and a choice of swap route and slippage. (x.com/defisaver/status/2117302519843942445, quoting x.com/i/article/2117235332181532673 "Loan Shifter V2: A simpler way to shift your DeFi position"; app.defisaver.com/shifter)
- It is user-initiated and one shot: no trigger, no monitoring, no policy. EVM only (the chains the app supports).
- Against our table (`docs/build-scope.md`): the build's `migrate` (same collateral and debt, between two Morpho markets) is a strict subset of Loan Shifter; cross-protocol and collateral or debt swaps are roadmap for us; cross-chain moves (Chain Signatures) are something no EVM wrapper does. What we add today is the trigger (venue stress) and the on-chain policy bound to the enclave, not the move itself.
- Supportable wording: "Loan Shifter moves a loan when you click; ours moves it when the venue shows stress, under a policy set on-chain." Do not claim a wider move set than the column allows.

## Bankr
- 4 May 2026: prompt-injection drain of Grok's Bankr wallet (3B DRB, ≈ $150–180K, mostly returned) after an NFT unlocked transfer permissions. Agent-permission design failure. (Giskard, SlowMist, AMBCrypto)
- 18–20 May 2026: attacker accessed 14 user wallets; estimates $150K–$440K; Bankr pledged reimbursement; no root cause published (possibly session theft or phishing). (Cointelegraph 20 May)
- Supportable wording: two wallet-drain incidents in May 2026; only the first is tied to agent permissions.

## Others that put limits on-chain (counterexamples to "every product gave the bot a wallet key")
- Giza ARMA (Jan 2025, Base): ERC-4337 smart account with session keys limited by target, function and amount; the vendor backend holds the session key.
- Brahma ConsoleKit agents (2025): Safe sub-accounts with an on-chain guard checking an attestation from Brahma's policy manager; products sunset 17 Apr 2026.
- Almanak: Zodiac Roles operators on the user's Safe.
- KPK (formerly karpatkey), vault curator, checked 2026-11-03: its automation dashboard ("Morpho Rebalancing V2" on the KPK USDC Yield vault, Ethereum) shows a Signer EOA with its own gas balance, an Avatar Safe, a Zodiac Roles Modifier and a role key named MORPHO_REBALANCER_AGENT; the bot holds a hot key and the policy is Roles calldata conditions on the Safe. A KPK risk curator's advice after the Binance hack: "Eliminate single-key failure points", automation on scoped permission contracts, automated replenishers, quick monitoring, "Less trust. More determinism." No model in the loop. Ethan DeFi (2 Nov 2026) says KPK's Morpho vaults held RLP during the Resolv exploit (22 Mar 2026) and exited the RLP market as soon as liquidity allowed; secondary, unverified, not checked on chain; a curator exiting on behalf of suppliers, so supplier evidence, not borrower evidence. (x.com/jerrythekid/status/2117324337728381027 with screenshot; x.com/EthanDeFi_/status/2117334140202668529)
- Lit Vincent (Apr 2025): policies enforced by Lit's network.
- Pattern holds for hosted chat-to-trade agents (Bankr, Wayfinder): vendor-held signer credentials, off-chain policy.

## Launch dates for "why now" (checked 2026-10-21)
- Chain Signatures mainnet: 8 Aug 2024 (`v1.signer` code deployed 1 Aug 2024, on-chain) — before the 18-month window.
- Global Contracts (NEP-591): protocol version 77, nearcore 2.6.0; upgrade vote 7 May 2025, expected activation ~8 May 2025 (scheduled; activation not checked on-chain).
- NEAR AI Cloud private TEE inference: 3 Dec 2025 (near.ai blog, BusinessWire).
- Confidential Intents GA: 7 Jul 2026 (near.org blog).
- NEAR Intents: `intents.near` deployed 5 Nov 2024; 1Click SDK first on npm 28 Mar 2025 — both before the window.
- Kamino xStocks: announced 14 Jul 2025 (The Block); Jupiter Lend xStocks 6 May 2026; Morpho Coinbase stock markets on Base 7 Sep 2026; Aave V4 Base equities 25 Sep 2026.
- Phala Cloud public release 1 Apr 2025; dstack v0.5.0 16 May 2025; Shade Agents announced 20 Feb 2025, deprecated as standalone tooling after 19 Apr 2026.
- Morpho PreLiquidationFactory on Base: block 23779056, 16 Dec 2024.
