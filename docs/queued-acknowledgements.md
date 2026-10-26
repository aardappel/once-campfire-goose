# Queued acknowledgement experiment

`CAMPFIRE_PUBLICATION_ACK=queued` releases the HTTP response after the database
commit and after the broker retains the publication payload and snapshots its
matching subscribers. The default, `drained`, waits for those readers' final
socket drains. Both modes acknowledge in publication order.

Queued success promises committed, queued delivery, not completed socket delivery.
Disconnects cancel only the disconnected reader's work. Disconnecting the HTTP
writer does not cancel a committed publication. An acknowledgement does not
guarantee survival of a subsequent server crash; this experiment adds no durable
replay queue.

Only acknowledgement timing changes. Both modes retain the same eight active
publications, 64 total delivery permits, per-connection drain credits, frame
fragmentation, subscription limits and fixed HTTP mailbox capacity. A successful
queued acknowledgement does **not** release its permit: retirement after final
drain does. The broker cannot enroll a ninth active publication behind a stalled
reader. Once all 64 permits are occupied, new writes receive 503 before mutation.

This isolates early acknowledgement within Goose's existing delivery window. It
does not reproduce Jai's much larger per-reader queue thresholds or write batching.
Results therefore measure the effect of acknowledgement timing in this Goose
implementation, not every architectural difference between Goose and Jai.

Run either policy with the same executable and dependency pins:

```sh
CAMPFIRE_PUBLICATION_ACK=drained /usr/local/bin/campfire
CAMPFIRE_PUBLICATION_ACK=queued /usr/local/bin/campfire
```

The delivery test exercises both policies under a stalled reader. Queued mode must
acknowledge its eight retained publications, block later acknowledgements until
window space exists, retain its admission permits, reject excess writes without
mutation or growing retained memory, and eventually deliver each accepted message
exactly once and in order to the remaining readers. The multi-subscription variant
also forces one HTTP worker and six matching subscriptions per reader.

The benchmark accepts `--goose-publication-ack drained|queued` and records the
selection in its metadata. Comparisons use the same image for both settings,
separate server/client CPU cores, the shared validators, persisted-write audits
and complete delivery to all 64 readers. HTTP response speed alone is not counted
as fan-out throughput.
