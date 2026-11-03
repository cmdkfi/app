"""Compare a TDX v4 quote's measurement registers with dstack-mr output and an event-log replay.
Usage: python3 -I verify_measurements.py quote.hex dstack-mr.json event_log.json
Prints each register (full hex) from the quote, the rebuilt value, and MATCH/MISMATCH.
Offsets: quote header 48 bytes (dcap-qvl Header), then TDReport10: mr_td at 136, rt_mr0..3 at 328/376/424/472, report_data at 520.
RTMR3 replay: digest = sha384(event_type LE u32 || ":" || event || ":" || payload); mr = sha384(mr || digest) from 48 zero bytes (dstack cc-eventlog).
mr_aggregated (dstack-attest): sha256(mrtd||rtmr0||rtmr1||rtmr2||rtmr3 [|| mr_config_id||mr_owner||mr_owner_config if any is non-zero]).
"""
import sys, json, hashlib, struct
q = bytes.fromhex(open(sys.argv[1]).read().strip())
body = q[48:]
reg = {"mrtd": body[136:184], "rtmr0": body[328:376], "rtmr1": body[376:424], "rtmr2": body[424:472], "rtmr3": body[472:520]}
mr_config_id, mr_owner, mr_owner_config = body[184:232], body[232:280], body[280:328]
built = json.load(open(sys.argv[2]))
events = json.load(open(sys.argv[3]))
ok = True
for name in ("mrtd", "rtmr0", "rtmr1", "rtmr2"):
    m = built[name].lower() == reg[name].hex()
    ok &= m
    print(f"{name:6} quote    {reg[name].hex()}\n{name:6} dstack-mr {built[name]}\n{name:6} {'MATCH' if m else 'MISMATCH'}")
mr = bytes(48)
for e in events:
    if e["imr"] != 3:
        continue
    d = bytes.fromhex(e["digest"]) if e["digest"] else hashlib.sha384(struct.pack("<I", e["event_type"]) + b":" + e["event"].encode() + b":" + bytes.fromhex(e["event_payload"])).digest()
    mr = hashlib.sha384(mr + d).digest()
m = mr == reg["rtmr3"]
ok &= m
print(f"rtmr3  quote    {reg['rtmr3'].hex()}\nrtmr3  replay   {mr.hex()}\nrtmr3  {'MATCH' if m else 'MISMATCH'}")
h = hashlib.sha256()
for d in (reg["mrtd"], reg["rtmr0"], reg["rtmr1"], reg["rtmr2"], mr):
    h.update(d)
if any(x != bytes(48) for x in (mr_config_id, mr_owner, mr_owner_config)):
    h.update(mr_config_id); h.update(mr_owner); h.update(mr_owner_config)
print("mr_aggregated", h.hexdigest())
print("mr_config_id ", mr_config_id.hex(), "(0x01 || compose_hash: dstack MrConfig::V1)")
for e in events:
    if e["imr"] == 3 and e["event"] in ("compose-hash", "os-image-hash", "app-id", "key-provider"):
        print(f"event {e['event']:14} {e['event_payload'] if e['event'] != 'key-provider' else bytes.fromhex(e['event_payload']).decode()}")
print("ALL MATCH" if ok else "SOME MISMATCH")
sys.exit(0 if ok else 1)
