# Demo feeds (testnet build only)

**Published by the cmdk team for the testnet demo; not a real venue.** These two files are the "demo feeds" of the
design (§4.9 sources): static JSON files in this repository that we publish to, labelled as ours, for the
"warning before the numbers move" scene on the live market. They are never part of a production allowlist.

| File | Kind (§4.9) | Feed id |
|---|---|---|
| `venue-status.json` | security alerts and venue status | `cmdk-testnet-venue-status` |
| `governance.json` | protocol announcements | `cmdk-testnet-governance` |

## Shape

```json
{
  "notice": "Published by the cmdk team for the testnet demo; not a real venue. ...",
  "version": 1,
  "feed": {"id": "...", "kind": "...", "title": "...", "publisher": "cmdk team", "home_page_url": "..."},
  "items": [
    {"id": "venue-status-2026-11-03-001", "published": "2026-11-03T06:00:00Z", "title": "...", "body": "...", "url": "..."}
  ]
}
```

- `notice` is the first key of every file and is shown wherever an item is shown.
- `items` are newest last. `id` is unique and never reused; `published` is UTC, ISO 8601 with `Z`.
- An item is only ever appended. A correction is a new item that names the id it corrects.

## Where the agent reads them

From the public repository's raw GitHub URL, on the `main` branch:

```
https://raw.githubusercontent.com/cmdkfi/app/main/feeds/venue-status.json
https://raw.githubusercontent.com/cmdkfi/app/main/feeds/governance.json
```

General shape: `https://raw.githubusercontent.com/<org>/<repo>/<branch>/feeds/<file>.json`. The two URLs go into the
agent's allowlist (agent row, `docs/03-plan.md`), labelled as demo feeds; the agent records each item's URL, fetch
time and content hash in its transcript. The URLs return 404 while the repository is private (checked 3 Nov).

## Publishing an item

Append an item to the file, commit and push to `main`. raw.githubusercontent.com serves files with
`cache-control: max-age=300` (checked 3 Nov), so allow 5 minutes before a scene starts.
