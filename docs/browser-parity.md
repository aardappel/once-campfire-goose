# Browser coverage and live delivery

The production-image browser gate uses the unchanged `browser/smoke.mjs` and npm lockfile
from `basecamp/once-campfire-verification` revision
`ec02debb3e44f40b8778a6a42db3c4515b2f9eaf`. Playwright's image is pinned by digest
in `test_browser.py`. The runner creates fresh Linux storage, starts the production
image, runs Chromium, and writes a receipt with image IDs and validator hashes.

```sh
python3 test_browser.py once-campfire-goose:app
# An existing clean verifier checkout can avoid fetching it again:
python3 test_browser.py once-campfire-goose:app --verification /path/to/once-campfire-verification
```

The shared suite exercises first-run setup, two-tab live messaging, duplicate suppression,
stored-markup safety, permalink copying, editing, search, profile/account updates, QR codes,
live room creation and renaming, bots, custom styles, session transfer, invitations, and
autocomplete-created direct pings with draft preservation. This establishes functional
coverage of those flows. Administrative forms use simpler markup than Rust/Jai; it is not
a screenshot-equivalence claim. The existing 27-case differential suite separately checks
room, paging, sidebar, search and POST rendering against the reference servers, including
all 315 served asset hashes and MIME types.

`test_cable.py` checks signed-stream rejection, two-reader delivery, 200 KiB fragmented
messages with concurrent incoming commands, multiple subscriptions per connection,
duplicate subscriptions, unsubscribe/slot reuse, publication ordering, and presence cleanup.
A stalled-reader case fills all 64 publication permits, checks that committed writes remain
unacknowledged, rejects 100 additional writes without database changes or retained-memory
growth above a 16 MiB allowance, and verifies complete recovery after disconnect.
First-run and HTTP regression tests continue to check authorization,
atomic persistence, write rollback, cache invalidation, and concurrent posting.

## Delivery contract

Each admitted mutating HTTP request reserves one of 64 publication permits before writing.
An asynchronous broker serializes broadcasts; each reader receives at most one 8 KiB batch
per goose-http drain credit. It retains one encoded publication while delivering to its
subscribers. Client commands received between fragments wait in a bounded 64 KiB buffer.
The broker processes at most 128 queued commands before a delivery pass, keeps at most
16 subscriptions in each connection, and assembles batches with bulk slice copies into
one reusable buffer. A generation-checked mailbox send detects stale ready readers;
readers awaiting drain credits also receive explicit liveness checks.
Disconnected readers are removed. A message POST is acknowledged only after its transaction
commits and every enrolled subscriber's batches have reached their socket, or the subscriber
disconnects. Socket acceptance does not prove that a browser has processed the message.

A stalled subscriber can delay unrelated publications, because the broker serializes them.
The HTTP wait expires after 60 seconds, but the committed message remains in the database.
Admission exhaustion returns 503 before a new write starts. This trades availability for
bounded outstanding delivery and avoids silently dropping accepted messages.

## Remaining compatibility work

The experimental server is not a complete Campfire replacement. Upload creation, boost
mutation, push notifications and webhook delivery remain unimplemented. Read/unread and
typing notification channels are accepted but do not yet deliver their full events. Active
WebSocket subscriptions do not yet recheck session or membership revocation; HTTP requests
and initial subscriptions do check access. The broker supports 16 subscriptions per socket;
the protocol needs an explicit rejection response when that limit is exceeded. Room creation
restrictions, full profile editing, reverse-proxy HTTPS handling, and the remaining admin
flows need broader coverage. Refresh currently returns at most 1,000 changed messages.

The HTTP throughput samples have no connected WebSocket subscribers. The separate shared
fan-out workload measures complete delivery to 64 tabs with six subscriptions each and
audits persisted message, rich-text and FTS bodies. Neither workload measures the missing
effects above or establishes full application equivalence or an intrinsic language-speed
advantage.
