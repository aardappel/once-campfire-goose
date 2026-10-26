# Bounded publication queue sizes

Larger windows are opt-in and require `CAMPFIRE_PUBLICATION_ACK=queued`.
`CAMPFIRE_PUBLICATION_WINDOW` accepts powers of two from 8 through 512. Without
these settings the application retains final-drain acknowledgement, eight active
publications and 64 delivery permits.

| Active publication window | Total retained delivery permits |
|---:|---:|
| 8 | 64 |
| 32 | 88 |
| 128 | 184 |
| 512 | 568 |

Every size leaves a fixed 56-publication allowance beyond the active window.
Acknowledgement never releases a delivery permit: final delivery retirement does.
When the active window is full, queued acknowledgements wait for space. When all
permits are occupied, new writes receive 503 before database mutation. A stalled
reader cannot make either queue grow beyond these counts.

The broker allocates its delivery array to the configured capacity and retains
one shared encoded body per active publication. Per-connection target masks take
four bytes per window entry (32 bytes at eight, 2 KiB at 512). The existing request,
message, subscription, connection and HTTP mailbox limits remain in force. Memory
is bounded by those counts and payload limits; this setting is not a fixed MiB
budget. Larger windows intentionally permit more retained message data.

Publication ordering, fragmentation, subscriber snapshots, unsubscribe handling,
disconnect recovery and final-drain retirement are unchanged. The HTTP response
still promises committed and queued delivery rather than final socket delivery.

```sh
CAMPFIRE_PUBLICATION_ACK=queued CAMPFIRE_PUBLICATION_WINDOW=32 /usr/local/bin/campfire
```

The comparison uses the same executable for all Goose capacities. The benchmark
option `--goose-queued-windows 8,32,128,512 --goose-publication-ack queued` rotates
the four settings and references across rounds. It records the effective window
and permit count from server startup, and preserves individual samples. Memory
includes anonymous memory after delivery and the cgroup lifetime peak through
that phase; the latter also includes file cache, kernel memory and earlier HTTP
work, so it is not a fan-out-only or anonymous-memory peak.
