# Driver telemetry ingest, contract v1

The HTTP contract between a driver's sender and the relay (#1111). The sender script
`src/relay/racecast-sender.py` implements it; any other client, such as a later native
dashboard app, can implement it from this page. The product design lives in
`driver-telemetry-dashboard.md`.

## Credentials

- **Invite link**: `https://<funnel-host>/console/drive?i=<invite>`. One per event:
  `racecast event start` creates it, `racecast event stop` deletes it, and
  `racecast event invite --new` or the Control Center's **New link** replaces it. The
  invite only opens the registration below.
- **Driver code**: what registration returns, `D1:<payload>.<signature>`. The payload
  names the entry, the stint or qualifying segment (endurance only), the driver's name
  and a random connection id. The relay signs it with a key that lives only in the
  invite file on the producer's machine, never in the link, so the link alone cannot
  mint codes and a new invite revokes every code. The ingest also refuses a code whose
  entry is no longer in the `Configuration` tab. A code never authenticates any other
  `/console` page.

## Registration

`GET /console/drive/data?i=<invite>` returns

```json
{"v": 1, "multi_lobby": false, "entries": ["OVO eSports #111"], "segments": [], "rate_hz": 20}
```

`entries` are the rows of the league's `Configuration` tab. `multi_lobby` is true for
endurance; `segments` then lists the stint and qualifying labels of the `Schedule` and
`Qualifying` tabs, and registration needs one of them.

`POST /console/drive/register?i=<invite>` with
`{"entry": "...", "segment": "...", "name": "..."}` returns `{"ok": true, "v": 1,
"code": "D1:..."}`. The name takes up to 32 ASCII letters, digits, spaces, `_ . -`,
because it ends up in a command line. `GET /console/drive/sender.py?i=<invite>`
serves the sender script of the running relay.

## Ingest

`POST /console/drive/ingest` with

- header `X-Driver-Code: D1:...`
- optional header `X-Sender-Stats`: a JSON object of integer counters the sender
  reports (`received`, `forwarded`, `posted`, `dropped`, `post_failed`,
  `interruptions`, `heartbeats`); other keys are ignored
- body, batch format v1: the four bytes `RCT1`, then for each packet a big-endian
  uint16 length and the packet exactly as the console sent it, still encrypted. At
  most 120 packets of at most 512 bytes each.

A sender posts batches at `rate_hz` (default 20 per second) and keeps every packet
in order. The relay decrypts and filters, so a new field never needs a sender update.
Packets of any GT7 format (`A`, `B`, `~`) are accepted; the sender script requests `A`
so a driver's other receivers keep working.

Reply `200`: `{"ok": true, "v": 1, "rate_hz": 20, "accepted": N}`. A client follows
`rate_hz` from every reply.

| Status | Meaning | Client action |
|---|---|---|
| 400 | malformed batch | drop that batch |
| 403 | invalid or revoked code | register again (the sender script retries once a minute and tells the driver) |
| 409 | the entry already has its live senders | retry rarely; the producer can disconnect the other one |
| 410 | the producer disconnected this sender | register again |
| 413 | body too large | send smaller batches |
| 429 | too many failed attempts or senders | retry after a minute |

## Limits

- One live sender per entry in a single-lobby race, two per team in endurance (the
  driver on track and the one waiting in the next lobby). A connection is live while
  its batches arrive; after 20 s without one its slot is free.
- A second registration for a full entry is refused, not swapped in. A sender that
  restarts with its own code is the same connection.
- The producer sees every connection on the tailnet (`GET /drivers/data`) and
  disconnects one with `POST /drivers/disconnect {"conn": "..."}`.

## Versioning

`v` in every reply is the contract version. A change that an existing client cannot
follow raises it; the relay then states which versions it accepts.
