# Evidence: what is here and what has been verified where

One page for a reviewer who cannot run everything: which artifact was rebuilt from source and compared, on which machine, and which checks need no Docker at all. "Reproduced" means built again from the named commit on another machine and compared byte for byte; "identity" means the deployed bytes compared with the published hash. The full reports are in `screening/` (rehearsals 1 to 13, 5 to 11 Nov 2026, Claude Code and Codex, each on a fresh clone; rehearsal 13 is the final state).

| Artifact | Published value | Reproduced from source | Identity and provenance, no Docker needed |
|---|---|---|---|
| Vault wasm (NEAR global contract by hash) | SHA-256 `def9671e099fe325a7ac8c43831de0334a3dc75221f6c302b17a893e0377e1ba`, base58 `G1Q5ksFhGbgPTN4A2DuvgraYmsbRpfwb78B547gAKXWq`; source commit `975b98d`; image `sourcescan/cargo-near:0.21.1-rust-1.96.0` pinned by digest | MATCH: rehearsal 5, Claude Code, Mac with Docker, 10 Nov, 7 min 22 s (`screening/2026-11-10-claude-code-5.md`); also rehearsal 4 (26 min, image not cached) | `view_code` bytes hash to the published value; `contract_source_metadata` names the commit and the image: rehearsals 6 and 7 in a Linux container without Docker (`screening/2026-11-1[01]-codex-[67]-container.md`) |
| Factory wasm | SHA-256 `5315458701e4f52c9c033d05d492ec75d18076a128a0261f153434670971c863`, 152,851 bytes; host build, source `vault/factory/` unchanged since `52e0446` | Not reproducible by design (host toolchain); the same hash on macOS arm64 with cargo-near 0.22.0 and Rust 1.95.0 on 5 Nov (team) | No access keys and `get_config` returning the vault hash: every rehearsal |
| Agent image (`ghcr.io/cmdkfi/agent`) | manifest `sha256:e72564a8…`, image id `sha256:d3b51d67…`, binary SHA-256 `0a8f47c3…`, source commit `4171c10` | Two uncached builds identical to the published image id: rehearsal 5, Codex and Claude Code, Mac with Docker, 10 Nov | Registry manifest, image configuration and the binary layer's hash match the manifest: rehearsal 7 |
| Enclave measurements, boot `2286af92` | compose SHA-256 `db4f508517a01b31986dc9ec10eefd7491eb249cc209fb6b395c449e91c1dfc4`; MRTD, RTMR0–3 and OS hash in `boot-1794264608-2286af92/` | `ALL MATCH` with `scripts/p3_reproduce_measurements.sh`: rehearsal 5, Claude Code and Codex (Docker) | OS image hash against the dstack release; compose hash against the event log and the quote's MRCONFIGID; RTMR3 replayed from the event log: rehearsals 6 and 7 |
| Executor, Base Sepolia `0xA9bbabE6d75C5BA96CA095586284774c9D09D18b` | Blockscout-verified, solc 0.8.28, 200 runs, viaIR | Source files identical to the checkout; runtime bytecode (16,459 bytes) and creation bytecode rebuilt and equal: rehearsals 5, 6 and 7 (Foundry, no Docker needed) | — |
| Web app (`cmdkfi/cmdk-app` `main`) | deployed at https://testnet.cmdk.fi | Build of `main` byte-identical to the live bundle and config: rehearsals 5 (c1d28f5's predecessor), 7 (c1d28f5) | — |
| Landing (`cmdkfi/landing`) | https://cmdk.fi | Build of 68e91ec identical to the live HTML: rehearsal 7 | — |

Test suites: executor 64, vault 15 unit and 8 sandbox, agent 65 (1 ignored), web app 162 unit and 88 browser, all passing in rehearsal 5 (Claude Code, Mac); since 11 Nov (cmdk-app #85, #89 and #90: the light design, the desktop-only testnet flow with the sandbox on phones, the Venue stress scene) the web app has 174 unit tests and 110 browser scenarios (desktop, plus the phone notice and the sandbox on mobile), run by its CI on every push. In the container runs without Docker: executor 64, vault 15 unit and 8 sandbox, agent 65, web app 174 unit; in rehearsal 13 the tool also fetched a Chromium and ran the browser suite (108 passed, 14 skipped, two close-flow scenarios timed out under parallel load in the 3 GB container and passed when rerun alone). The sandbox tests sign locally, so a reviewer under a "sign nothing" rule skips them.

"Verify it" steps 1 to 12 of the README: all PASS or corroborated read-only in rehearsals 5 to 7. Step 7 sends transactions; its five reverts are on chain (`attacks.txt`), reproduced by read-only replay of the recorded calldata in every rehearsal, and 5 of 5 on a local anvil fork with a fresh key (`scripts/ATTACKS.md`; team on 10 Nov, Claude Code in rehearsal 5). Transcripts: every entry of the live index matched its hash and size each time (690 on 10 Nov, 747 on 11 Nov).

## Folders

- `attacks.txt`: the five executor reverts of step 7, with transaction hashes.
- `boot-<id>/`: one folder per enclave boot (four boots, 4 to 9 Nov): `app-compose.json`, the quote, the event log, the registration and the measurements, as served by the enclave at the time.
- `deploy/`: deployment logs (executor, factory, vaults), 3 to 6 Nov.
- `incident-2026-11-06-phala-kms.md`: the one infrastructure incident, with what it changed.
- `inject.txt`: step 11, a hostile feed item and the signed transcript of the judge's answer.
- `protect-live.txt`: step 9, the protective reduce on a live position.
- `revoke.txt`: step 12, revocation, close and withdrawal on vault v2.
- `scenes/`: step 10, the price scenes, the venue stress and the exit, each with its data file, transaction table and transcripts.
- `screening/`: the AI screening rehearsals, prompts and reports.
- `transcripts/`: the archived transcript index of the first enclave (4 to 6 Nov) and the transcripts the scenes cite.
- `venue/`: the venue setup and the pool-band rebalancing logs of 3 Nov.
