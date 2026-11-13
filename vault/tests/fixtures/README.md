# Test fixtures (public attestation data, no secrets)

- `tdx_quote_prime-e-test.hex` and `event_log.json`: measurement E's TDX quote (5,010 bytes, hex) and RTMR event log from the Phala test enclave of 23 Oct 2026.
- `collateral_phala.json`: measurement F's Intel collateral for that quote, fetched 26 Oct 2026 from Phala's PCCS; TCB info and QE identity valid to 25 Nov 2026 (`nextUpdate`). From 25 Nov 2026 00:02 UTC the sandbox test `agent_key`, which runs on the real time, fails with the collateral expired; this is the vault refusing stale collateral, as `expired_collateral_rejected` in `src/attest.rs` checks at a fixed date. Live registrations are unaffected: the agent fetches fresh collateral for each one.
- The enclave no longer exists, so `tests/sandbox.rs` patches a test key onto E's implicit account in the sandbox to call `register_agent`; this is not possible on a real network.
