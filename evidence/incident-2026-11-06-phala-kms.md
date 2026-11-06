# Phala Cloud KMS incident, 6 November 2026: what it reached and what we did

## The notice

On 6 November 2026 Phala e-mailed the owner of the CVM `cmdk-agent` (app id `29fc9b0846dc459e21923c5a1b63474d79df6e6b`),
with SPF, DKIM and DMARC passing for `phala.network`: an exploit on the Phala Cloud platform earlier that day; the
app-key of CVMs on the default (off-chain) Phala KMS may have been exposed. Phala's own scope statement: the app-key
"used to encrypt your ENV and the CVM disk, and to derive sub-keys using the dstack getKey() API is leaked"; "we still
don't have any evidence showing the plain text or the cipher text of your ENV or CVM disk is leaked"; CVMs on the
on-chain KMS (Base, Ethereum) are not affected; recommendation: redeploy, either on the on-chain KMS or on an updated
Phala KMS. At the time of writing Phala had published no post; its status page read "all services online" (06:25 UTC).
The Phala CLI's `kms list` already showed a replacement, "Phala KMS 2026-11", whose public key differs from the
`key-provider` event in the old boot's event log.

## What the app-key could reach in this deployment

| Protected by the app-key | Here | Exposure |
|---|---|---|
| Encrypted environment | one variable, `NEARAI_API_KEY` (`allowed_envs` in the measured compose) | a third-party API key with a spend limit; rotated by the owner the same morning |
| CVM disk | the `evidence` volume: measurement files and decision transcripts | all of it is public and served by the evidence endpoint; nothing to protect |
| `getKey()` derivations | none; the agent calls only `Info` and `GetQuote` on the guest agent (`agent/src/tee.rs`) | none |
| Gateway TLS (app certificate) | the evidence endpoint's certificate chains to the KMS | a holder of the app-key who also controlled routing could serve a false endpoint; `agent/verify-transcript.sh` checks transcripts by their recorded signatures and the measurement files by their hashes, not by the endpoint's TLS |

Not reached: the agent's signing key. The ed25519 key is generated inside the CVM at boot (`SigningKey::generate` with
the OS random source, `agent/src/main.rs`), kept in memory and never written or derived. The vault accepts a key only
through a TDX quote whose `report_data` is that key (`vault/src/attest.rs`), verified against Intel's collateral; the
KMS is not in that path. Nobody could sign as the agent or register another one with the leaked key. No wallet: the
agent's implicit account holds registration gas only (about 0.14 testnet NEAR).

## What we did (all times UTC, 6 November)

1. Backed up the old CVM's 75 transcripts and their index before anything else; every SHA-256 matched the index.
   They are archived in `evidence/transcripts/` and verify with `agent/verify-transcript.sh` as before.
2. The owner created a new NEAR AI API key (spend limit kept); the old key is deleted after the switch.
3. 06:54 deployed a new CVM, `cmdk-agent-2`, from the same `docker-compose.live.yml`, pre-launch script, OS image
   `dstack-0.5.12` and instance size, with the new key as its only encrypted variable, on the replacement Phala KMS
   (its `key-provider` event names the "Phala KMS 2026-11" key). App id `49ecae9fcc4a6970956d8c987386315297922a69`,
   node `prod5`, gateway `dstack-pha1-prod5.phala.network`, boot `boot-1793948092-14fda74d`
   (`evidence/boot-1793948092-14fda74d/`). Its six measured values equal those of boot `a405e917`, so the vault's
   approved measurement applied unchanged and no owner signature was needed.
4. 07:05 the relayer funded the new agent's implicit account `14fda74d…0100` with 0.16 NEAR.
5. 07:09 the agent registered on `v1.factory.prime-measure-1792736012.testnet`: transaction
   `CjN1nLHFUDiVqMyp1ZVKQ88SeDkhosjs6Do6GyxgFNU5`, 130.6 Tgas, 12.6 s; key
   `ed25519:2QwVDKFgp3AT6XMURgWr5ZkKUAS6dYt5nqeHwc85SHio`, expires 2026-11-13T07:09:27Z
   (`evidence/boot-1793948092-14fda74d/registration-v1.json`). The vault deleted the old key in the same receipt;
   the old agent's own status page then read "revoked (another key registered)".
6. 07:15 stopped the old CVM (not deleted: Phala keeps its disk and announced a migration option). Pointed the
   relayer Worker's `AGENT_EVIDENCE_URL` at the new endpoint and redeployed it; updated `deployments/testnet.json`,
   the README (step 8) and `agent/MEASUREMENT.md`.

Not taken: the on-chain KMS (option recommended by Phala as an alternative). Deploying it registers a contract on
Base mainnet from the deployer's wallet; this build is testnet only, and the agent's security does not rest on the
KMS (above), so the replacement Phala KMS was chosen.

## What changed for a verifier

- Evidence endpoint: `https://49ecae9fcc4a6970956d8c987386315297922a69-8080.dstack-pha1-prod5.phala.network`
  (was `…29fc9b08…-8080.dstack-pha-prod9…`). Its `/transcripts` lists the new CVM's boots only; the first CVM's
  transcripts, including the 5 November scenes of README step 10, are in `evidence/transcripts/`.
- Registered key and expiry on `v1` (`get_state`): the values in step 5.
- Unchanged: the six approved values, the vault, the executor, the markets, the verification commands.
