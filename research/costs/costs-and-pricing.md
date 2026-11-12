> Copied from the planning repository, `docs/01-design-and-feasibility.md` §11 "Costs and pricing" (version of 5 November 2026); the cost inputs it names are in `costs_and_fees.md` in this folder.

## 11. Costs and pricing

Mainnet estimates from our cost note of 21 October 2026 (ETH about $2,460, NEAR about $5.19). On testnet all chain costs are faucet tokens; the hackathon build pays only Phala (about $44 a month; [internal credit detail removed]), NEAR AI inference (cents) and RPC free tiers.

One agent loop in one enclave serves all vaults; there is no machine per user. The smallest Phala TDX machine is estimated to handle on the order of 1,000 positions at one check every 5 minutes (untested).

| Cost | Amount | How often |
|---|---|---|
| Publish the vault Global Contract | 0.0001 NEAR per byte, burned, per published version (measured, G). The P2 vault prototype is 762 KB (verifier plus omni-transaction, rlp and serde_json), so about 76 NEAR per version. Decided 26 Oct: accept the burn and collect about 200 NEAR on testnet through faucet rounds, enough for two or three publishes; a smaller vault stays a mainnet optimisation | Once per code version |
| Deploy executor, factory, markets | Cents on Base | Once |
| Phala enclave | About $44 a month, shared by all vaults | Monthly |
| RPC providers | Free tiers, then paid plans (not priced) | Monthly |
| Vault account storage | About 0.005–0.012 NEAR ($0.03–0.07, estimate) | Once per user |
| `setup` on Base | A few transactions, about $0.05–0.10 | Once per user |
| ETH float on the position address | Maybe $1–2, used up gradually | Per user |
| Agent registration (on-chain quote check, about 200 Tgas) | About 0.02 NEAR ($0.10) per vault, every 7 days and after each enclave restart: about $0.45 a month | Per user, recurring |
| Model calls when a rule fires | $0.0003–0.0025 per call | Per call |
| One action (NEAR call + Chain Signatures + Base transaction) | About $0.02–0.03; about $0.10 if Base gas spikes 10x | Per action |

Per user per month: about $0.70–1.00. Registration renewal is the largest item; registering for 30 days instead of 7 cuts it to about $0.10, at the cost of a longer-lived registration.

Pricing options (not decided):

- **Subscription, about $20 a month for all features.** Simple to explain and predictable. Expensive for small positions ($240 a year is 2.4% of $10k of collateral) but comparable to an annual fee for typical borrowers (Coinbase's Morpho borrowers average about $30K of debt, so about $60K of collateral at 50% LTV: 0.4% a year). Tiers by collateral size would fix the small-position problem.
- **Annual fee on collateral, 0.25–0.50%.**
- **Per action**, as DeFi Saver does (0.05% per automated action plus a 0.25% swap fee).

Constraint for any fee: money leaves the vault only by the owner's signature, so the fee cannot be taken from the vault silently. A subscription paid from the owner's wallet keeps that claim intact; a fee taken from the vault would need an owner-signed allowance (a capped monthly amount to our address). No fee collection is built for the hackathon.
