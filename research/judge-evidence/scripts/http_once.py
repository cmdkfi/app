#!/usr/bin/env python3
"""One isolated HTTP request; parent enforces a hard subprocess deadline.

Only the response goes to stdout. No exception text, headers or secrets logged.
"""
import json
import sys
import urllib.request
from pathlib import Path

assert sys.flags.isolated
try:
    key=(Path.home()/'.config/prime/nearai_key').read_text().strip()
    body=sys.stdin.buffer.read()
    request=urllib.request.Request('https://cloud-api.near.ai/v1/chat/completions',data=body,
        headers={'Authorization':'Bearer '+key,'Accept-Encoding':'identity',
                 'Content-Type':'application/json','x-no-aliasing':'true'},method='POST')
    with urllib.request.urlopen(request,timeout=540) as response:
        sys.stdout.buffer.write(response.read())
except Exception as e:
    sys.stderr.write(json.dumps(dict(error_type=type(e).__name__,http_status=getattr(e,'code',None))))
    sys.exit(1)
