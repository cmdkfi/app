# The agent's measurement: what the owner approves, how to reproduce it, and how to run your own agent

Testnet build (NEAR testnet, Base Sepolia). Design: `docs/01-design-and-feasibility.md` §4.4 in the planning
repository. Prototype findings referred to below: P3 in `research_notes/Prototype/results.md` (items 32, 34–38, 43,
49 of its consolidated list); the register reproduction is measurement H.

## 1. The six values the owner approves

`approve_measurement` on the vault takes six values; `register_agent` accepts a quote only when all six match
(`vault/src/attest.rs`, `verify`). RTMR3 is never compared directly (it differs per instance through the
`instance-id` event); the vault replays the imr-3 event log, recomputing every digest, and requires the result to
equal the quote's RTMR3.

| Value | Source | What changes it |
|---|---|---|
| MRTD | the TDX quote (TD report) | the virtual firmware (OVMF) of the OS image; the host's QEMU page-adding mode |
| RTMR0 | the quote | firmware configuration: vCPU count, memory size, ACPI tables, PIC |
| RTMR1 | the quote | the kernel of the OS image |
| RTMR2 | the quote | kernel command line (carries the rootfs hash) and initrd of the OS image |
| compose hash | imr-3 event `compose-hash` (also MRCONFIGID = 0x01 ‖ hash) | any byte of Phala's `app-compose.json`: this directory's compose file, the pre-launch script, `allowed_envs`, Phala's flags |
| OS image hash | imr-3 event `os-image-hash` | the OS image (`--image`); sha256 of its `sha256sum.txt` |

Expected on Phala `tdx.small` (1 vCPU, 2 GB) with `--image dstack-0.5.12 --no-dev-os`, from P3 (CVMs of 26 and
27 Oct; they do not depend on our compose):

| Value | Hex |
|---|---|
| MRTD | `7fe60787222bd394cb516abca2435f22f035ab2cc9c0a4b4b3b148e46297f3d931a237b72f359052c6e657d8c1409173` |
| RTMR0 | `68102e7b524af310f7b7d426ce75481e36c40f5d513a9009c046e9d37e31551f0134d954b496a3357fd61d03f07ffe96` |
| RTMR1 | `4ae609807c765916a31d724786ed74bcb97e168a9d937b04936aeef2015c907eba5ac352c6bcd749c246fd62ffb659ec` |
| RTMR2 | `764489f277a8540189761e72b4238ba36d4d647e11bd958d8ab5522af2a4867b0ff63e06b0c4967f1857236244040a5d` |
| OS image hash | `8db40cb23008d6213668b5816702d100b37b859a8a5bfd18d938ae3a1b3715bd` |
| compose hash | new for this build: read it from the agent's `measurement=` log line and recompute it (§3) |

## 2. What is in the measured compose

`docker-compose.yml` (`--mode dry`) and `docker-compose.live.yml` (`--mode live`) differ only in the mode. Each:

- **pins the image by digest** (`image@sha256:…`); the re-pull in the pre-launch script therefore fetches the same
  bytes. The digest is the registry digest of the image `./build.sh` builds from the committed source (§6); until it
  is pushed, the compose holds `REPLACE_WITH_THE_DIGEST_BUILD_SH_PRINTS`, which makes a deploy fail loudly instead of
  running another image;
- **names the operator on the command line**: `--operator cmdk`. Command-line arguments are part of
  `docker_compose_file` inside `app-compose.json`, so they are in the compose hash; environment variables are not
  (Phala encrypts them and they are outside the measurement). The owner's approval therefore means "this code, run
  by this operator", and a stranger's copy with another operator name has another compose hash, which the vault
  refuses. The prototype did not pass an operator: its compose had only `--mode` (P3 `app-compose.json`, compose
  hashes 448c5504…, 511f4e86…, 5f3820c4…); `--operator` is new in this build (31 Oct decision);
- carries every behaviour flag on the command line; the encrypted environment carries only `NEARAI_API_KEY`
  (in P3, `allowed_envs` in `app-compose.json` held exactly the names in the `-e` file: `["NEARAI_API_KEY"]`);
- publishes port 8080 for the evidence endpoint (§4);
- `restart: unless-stopped` (§5).

**Pre-launch script.** Phala inserts a pre-launch script into `app-compose.json` (17,569 characters, "Phala Cloud
Pre-Launch Script v0.0.20" in P3). It is measured through the compose hash, and Phala's CLI and API can replace it
(the CLI has an "upgrade to latest official" call), which would change the compose hash under us. The build passes it
explicitly: `pre-launch.sh` here is the reviewed v0.0.20 text, byte for byte
(sha256 `982181610f70be9087b1c69b36b719b47b82d37fcef8acc9289ed3bb3095ffe8`, the text in P3's `app-compose.json`
for both CVMs). Reviewed 29 Oct against `dstack-0.5.12`: its root-password and SSH-key setup reach no login path on
the production image (no SSH server, no getty), registry login needs `DSTACK_*` variables that `allowed_envs`
blocks, and its `docker compose pull` re-fetches the pinned digest.

**Deploy command** (Phala CLI 1.1.22 has `--pre-launch-script`; checked in its bundled source, 3 Nov):

```
npx phala@1.1.22 deploy -n cmdk-agent -c docker-compose.live.yml -t tdx.small \
  --no-dev-os --image dstack-0.5.12 --pre-launch-script pre-launch.sh \
  -e <mode-600 file with NEARAI_API_KEY=...> --wait
```

- `--no-dev-os`: the CLI picks the development OS image when it finds an SSH key, and an update did not change it
  (measurement E).
- `--image dstack-0.5.12`: pinned explicitly; Phala also lists `dstack-0.6.1`, and the register reproduction (H, P3)
  is proven only on 0.5.12.

## 3. Reproducing the six values

From the evidence files of a boot (§4): `tdx_quote.hex`, `event_log.json`, `vm_config.json`, `app-compose.json`.

1. **MRTD, RTMR0–2:** from the public OS image `dstack-0.5.12.tar.gz` (meta-dstack release v0.5.12) with `dstack-mr`
   built from dstack tag v0.5.12:
   ```
   dstack-mr measure -c 1 -m 2G --two-pass-add-pages true --pic true --json metadata.json
   ```
   `-c`/`-m` are the vCPU count and memory from `vm_config.json` (`cpu_count`, `memory_size`). The two QEMU flags are
   required although `vm_config.json` says `"qemu_single_pass_add_pages": true` (QEMU 8.2.2): with dstack-mr's
   defaults MRTD and RTMR0 do not match; `--two-pass-add-pages true` fixes MRTD and `--pic true` fixes RTMR0
   (measurement H; reproduced on the production image in P3, `ALL MATCH`). So the verify step uses the flags and does
   not read that field literally. `scripts/p3_reproduce_measurements.sh <work dir> <evidence dir>` (in this
   repository; it uses measurement H's `Dockerfile.dstack-mr` and `verify_measurements.py` from the planning
   repository) does steps 1, 2 and 4 in Docker.
2. **OS image hash:** `sha256(sha256sum.txt)` of the tarball = the `os-image-hash` event payload.
3. **Compose hash:** `sha256sum app-compose.json` = the `compose-hash` event payload = bytes 1–32 of MRCONFIGID; the
   `docker_compose_file` inside it must equal this directory's compose file byte for byte, and its
   `pre_launch_script` must equal `pre-launch.sh`.
4. **RTMR3:** replay the imr-3 events (sha384 over `event_type (LE u32) ":" event ":" payload`, chained from zero)
   and compare with the quote; the agent itself logs `rtmr3_replay_matches_quote=true`.

## 4. Evidence channel

Public (measurement evidence): at boot the agent writes `app-compose.json`, `vm_config.json`, `tdx_quote.hex`,
`event_log.json` and `info.json` (the app certificate removed) into `evidence/<boot>/`, prints each to the log as
`EVIDENCE <name> <sha256> <i>/<n> <base64>` lines, and serves them read-only over HTTP on port 8080:

```
GET /                         -> index: boot, operator, each file's name, bytes, sha256
GET /evidence/<name>          -> the file (only the five names above, registration.json, reregistration-<n>.json)
```

On Phala the port is reached through the CVM's gateway URL for port 8080 (the dashboard lists it; the form is
`<app-id>-8080.<gateway domain>`, not yet observed in our runs). Without it, `scripts/p3_evidence.py <log> <out>`
rebuilds the same files from `phala logs` and checks every SHA-256.

Private (transcripts): one JSON file per decision loop in `evidence/transcripts/`, never printed and never served.
The public log gets one line per transcript: `RESULT transcript loop=<n> <name> sha256=<hex> bytes=<n>`. The team
reads its own transcripts from the volume (for the team's demo vault, where they may be published, with hashes).
Check a transcript's judge call offline with `./verify-transcript.sh <transcript.json>` (request and response
SHA-256, the `provider_tee` signed text, the EIP-191 signer against the attested address, TCB status); on the
prototype's CVM transcript in `testdata/transcripts/` it prints `ALL PASS`.

## 5. Restarts, new CVMs, new images; restart policy

- **Reboot of the same CVM** (`phala cvms restart`): the event log is byte-identical, so RTMR3 and all six values are
  unchanged (P3). The agent makes a new key, so it has a new implicit account that must be funded again
  (`FUNDING` line, §7) and registers again; the vault replaces the old key in one receipt. No owner action.
- **Container restart** (the restart policy below): the same as a reboot from the vault's point of view: new key, new
  account, new registration, measurement unchanged.
- **New CVM, same compose:** new app id and instance id, same six values; registers without the owner.
- **New image** (any source change), or a changed compose or pre-launch script: the compose hash changes, so the
  vault refuses the quote until the owner signs `approve_measurement` with the new values (three such approvals in
  P3 and run 2). New versions are opt-in.

`restart: unless-stopped`: the agent exits non-zero only when it cannot register after 4 attempts (15, 30, 60 s
apart; it never ticks unregistered), or on a fatal startup error. A restart then gives a fresh agent (new key, new
registration). Inside the running agent a failed tick (a provider outage, a failed read, a panic) never stops the
loop (`run_loop`, tested). `unless-stopped` rather than `always`: the two differ only after a manual stop followed
by a Docker daemon restart, which does not occur in a CVM without operator shell access. Cost of a restart loop:
each boot strands what is left in its implicit account (about 0.14 NEAR after one registration); funding is a
call to the relayer's `/fund-agent` after the agent prints `WAITING`, so a loop is bounded by whoever makes that call.

## 6. Run your own agent

The vault checks which code runs, not who runs it. To run the published agent under your own name:

1. **Rebuild the image from source and check it is reproducible.** `./build.sh` builds twice with no cache
   (`docker buildx build --no-cache --platform linux/amd64`, `SOURCE_DATE_EPOCH` = commit time of the last change to
   the image inputs, `rewrite-timestamp=true`) and compares the two image IDs; it ends with `REPRODUCIBLE image
   sha256:<id>` or `DIFFERENT`. What makes the build reproducible:
   - base images by multi-platform index digest (`rust:1.95-bookworm@sha256:6258907a…`,
     `debian:bookworm-slim@sha256:99c0b25c…`); the Rust toolchain is the one in that image;
   - the cross linker and libc from `snapshot.debian.org` at `20261101T000000Z`, not the moving archive;
   - `cargo build --release --locked` (Cargo.lock enforced);
   - `--remap-path-prefix` for `/src`, the Cargo registry and rustup, and `-ffile-prefix-map` for C code
     (aws-lc-sys), so no build path depends on the machine;
   - no package manager in the runtime stage: the CA bundle and the binary are copied from the build stage;
   - file and image timestamps from `SOURCE_DATE_EPOCH`.
   Compare your image ID with the published image's: `docker pull <image>@sha256:<digest>` then
   `docker image inspect -f '{{.Id}}' <image>@sha256:<digest>`. The ID is the config digest, independent of how the
   registry compresses layers. **Status (3 Nov): not yet run to completion.** The first attempt on the founder's Mac
   stopped in `apt-get update` because Docker's disk was full (46 of 47 GB used; apt reports this as "At least one
   invalid signature"); the snapshot's `InRelease` itself verifies with the image's keyring (gpgv, 3 Nov). The result
   goes here once two builds have been compared.
2. **Choose your operator name** and put it in the compose's `--operator` (1–32 of a-z, 0-9, '-').
3. **Push the image** to a registry the CVM can pull from anonymously, put its digest in the compose.
4. **Deploy** with the command in §2, then read the agent's `measurement=` line and the evidence (§4), and reproduce
   the values (§3).
5. **Approve it on your vault:** sign `approve_measurement` with the six values (your compose hash). Your agent then
   prints `FUNDING`, waits for its NEAR, and registers like ours. Our approval does not cover your agent, and yours
   does not cover ours.

## 7. Funding and registration (item 32)

At boot, once, the agent prints the amount its implicit account needs, computed from the gas it attaches:

```
FUNDING implicit_account=<64 hex> needs=0.14992 NEAR = prepaid 130 Tgas x pessimistic gas price 1004000000 yoctoNEAR/gas (0.13052) + storage 0.00200 + margin 0.01740 (1.5 registrations' burn); funded by the relayer's fund-agent endpoint (...)
```

- Prepaid gas 130 Tgas: the vault's receipt is 113.7 Tgas, the transaction 131 Tgas with refunds (P3), not 300.
- NEAR checks prepaid gas × the pessimistic gas price against the balance when it accepts the transaction (run 2:
  `NotEnoughBalance`, cost 0.30118 NEAR for 300 Tgas with 0.1 NEAR held), so the gas part is 130 Tgas × 1.004e9.
- The margin (1.5 × the 0.0116 NEAR a registration burns) keeps the first re-registration above the submission check.
- The relayer's `/fund-agent` tops an account up to 0.15 NEAR plus 0.01 (`scripts/RELAYER.md`).

It then prints `WAITING … balance=… needs=… vault_approves_this_measurement=…` every minute until both hold,
fetches fresh collateral from Phala's PCCS, verifies it locally with dcap-qvl and sends `register_agent` only when the
local status is `UpToDate` and the balance passes the submission check.

**Re-registration (31 Oct).** Every 10 minutes the agent reads `agent.expires_at` from the vault. When the vault
lists its key with under 24 hours left (the vault's registration lasts 7 days, `REGISTRATION_SECS`), it fetches fresh
collateral, verifies it locally and calls `register_agent` again with the same key; the vault's same-key branch
extends the expiry. Log lines: `RESULT reregister due …`, `RESULT reregistered … expires_at <old> -> <new>`; the
record is saved as public evidence `reregistration-<n>.json`. It never re-registers when the vault lists another key
or none (that would displace another agent or undo a revoke). The ticks never wait for it.

## 8. Modes

One flag, `--mode`, four values. "Sends" = calls the vault's agent methods and broadcasts to Base.

| Mode | Judge and text feeds | Sends | Use |
|---|---|---|---|
| `dry` | yes (skipped and logged when no `NEARAI_API_KEY`) | no: builds the proposal, runs `check_transaction` | rehearsal, evidence |
| `live` | yes | yes | the demo agent |
| `twin` | never | no | the deterministic twin: same ticks, screens and protective rule; no discretionary action |
| `twin-live` | never | yes (only protective slices can arise) | a twin that acts |

Twin transcripts are named `<boot>-twin-<n>.json` and carry `"twin": true` and a label. Other flags: `--no-tee`
(local runs: no quote, no registration), `--fork` (an anvil-fork rehearsal; also detected when every provider answers
`anvil_nodeInfo`: `feed_stale` is recorded under `frozen_on_fork` and does not fire), `--evidence-http <addr>|off`.
