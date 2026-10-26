# Queued acknowledgement experiment

Measured on October 26, 2026, with functional revision `3a7a32f`.
Queued acknowledgements improve complete fan-out throughput by **24.2%** while
keeping the eight-publication window and 64 delivery permits unchanged. This
closes **22.9% of the absolute throughput gap to Jai in this comparison**.
Jai remains 1.66× faster. Queued mode stays opt-in; final-drain acknowledgement
remains the default.

## Results

| Metric | Goose final drain | Goose queued | Rust | Jai |
|---|---:|---:|---:|---:|
| Messages/s delivered to all 64 readers | 1,642 (1,547–1,699) | 2,038 (2,026–2,246) | 2,297 (2,266–2,374) | 3,376 (3,357–3,409) |
| Anonymous MiB after fan-out drains | 27.02 | 26.59 | 89.89 | 83.43 |
| All-readers delivery p99, ms | 5.657 | 12.815 | 3.717 | 2.835 |
| Fan-out POST p99, ms | 6.549 | 8.815 | 3.663 | 2.509 |
| Server CPU microseconds per complete fan-out message | 1,555 | 1,398 | 1,287 | 912 |
| Room reads/s | 394,919 | 398,406 | 19,377 | 321,992 |
| Posts/s without subscribers | 6,141 | 6,121 | 3,506 | 4,323 |

Values are medians; parentheses give the individual fan-out ranges, not confidence
intervals. The four final-drain controls are 1,667.5 and 1,615.8 before the
comparison, and 1,546.6 and 1,698.6 after it. Queued mode reaches 2,245.7, 2,026.0
and 2,038.3. Every queued sample exceeds every final-drain sample in this accepted
attempt, although the rates vary appreciably.

The policy gain is 396.65 complete messages/s. The original absolute gap is
3,376.4 − 1,641.65 = 1,734.75 messages/s; the remaining gap is
3,376.4 − 2,038.3 = 1,338.1. Thus 396.65 / 1,734.75 = 22.9% of that gap closes.
Jai's throughput ratio falls from 2.06× to 1.66×. This is a point estimate for
this workload and queue budget, not a universal attribution of Jai's advantage.

The improvement has a latency cost: median per-run delivery p99 rises from 5.66 to
12.82 ms, and HTTP POST p99 from 6.55 to 8.82 ms under saturated fan-out. Earlier
acknowledgement does not make every HTTP response faster when the bounded window
is full. Server CPU per complete message falls by 10.1%. Room reads change by
+0.9% and posts without subscribers by −0.3%; these control routes show no material
improvement from the policy.

Anonymous memory remains similar. These are cgroup samples after delivery drains,
not peak memory or allocations for an identical amount of work. The memory bound
is supported separately by the admission/slow-reader tests described below.

## Policy and bounds

`CAMPFIRE_PUBLICATION_ACK=queued` releases the HTTP response after the database
commit and after the broker retains the publication payload and snapshots its
matching subscribers. The default, `drained`, waits for those readers' final
socket drains. The broker issues acknowledgements in publication order in both modes.

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

## Validation

The full Docker test target passes with the pinned compiler and unpatched
goose-http. Both acknowledgement modes pass mixed-size and single-worker,
six-subscription pressure tests on automatic selection, epoll, ring emulation and
native io_uring. The tests fill a slow reader's TCP window, verify that early
success retains all eight active delivery permits, fill all 64 admission permits,
then reject 100 excess writes without database mutation or RSS growth above the
16 MiB allowance. They also verify late subscription enrollment, abandoned HTTP
writer delivery, fragmentation, unsubscribe/reuse, disconnect recovery, and
exactly-once ordered delivery to the remaining readers.

All 27 differential rendering cases match each Rust/Jai reference, all 315 asset
hashes and MIME types match, and the unchanged shared Chromium suite passes on
the queued-policy image. All 13 accepted application rounds pass the shared
HTTP validators and persisted message/rich-text/FTS audits. Every acknowledged
fan-out message reaches all 64 readers. No compiler or HTTP-library patch was
needed.

## Measurement and provenance

The accepted attempt ran from 14:18:41 to 14:26:18 PDT on October 26, 2026
(21:18:41–21:26:18 UTC). Two final-drain controls bracket each side of a three-round
queued-Goose/Rust/Jai comparison, whose application order rotates each round.
Both Goose settings use the exact same image and executable, explicitly selecting
the policy through the benchmark option. Only acknowledgement timing changes;
the compiler, dependencies, rendering and delivery-window capacity are identical.

The machine is an AMD Ryzen 9 9950X3D under WSL Linux 6.18.54.1 and Docker 29.1.3.
Servers use four physical cores (0,2,4,6) and a 2 GiB container limit; clients use
eight separate physical cores (8,10,12,14,16,18,20,22). All performance runs use
epoll with ordinary Docker seccomp. Reads use 256 clients, posts 32, with two
seconds of warmup and eight-second samples. Fan-out uses 64 tabs with six
subscriptions each, 20 paced messages, then four closed-loop posters for eight
seconds plus final delivery drain. The shared generator divides completed
all-reader messages by the posting-and-drain duration, not by HTTP response count.

The guard required 60 seconds without active compilers and three consecutive
five-second quiet samples below 0.3 core per process. An idle resident MSBuild
process is allowed only below 0.01 core. During measurement, any active compiler
or the same unrelated process above 0.8 core in two successive samples interrupts
only our runner. These criteria were unchanged from the preceding comparison.

All 92 accepted measurement samples had no active compilers or interruption
condition. Background observations included Chrome (17 observations, at most
0.547 core), Discord (three, at most 0.532) and ChatGPT (one, 0.443). This was not
a perfectly idle host. The first attempt was interrupted by sustained Chrome
activity during the closing controls; all 11 completed rows and its partial final
run are excluded and preserved separately. No unrelated processes were stopped.

Rust and Jai main revisions were verified unchanged before and after measurement.
Jai normally acknowledges queued frames, with much larger per-connection queue
thresholds and different write batching; the [verified source review](worker-dispatch.md#reference-delivery-policy)
describes those differences. This test estimates early acknowledgement at Goose's
existing eight-publication budget. It does not establish what larger bounded
queues would achieve, or assign the remaining gap to particular implementation
costs.

- Functional revision: `3a7a32f3715ca7d42fe8c92fa30da040b9c40cdb`.
- Shared benchmark image: `sha256:affe6a11d5adf9bf6c6ff90aa4643be2e0f1984b7f57493914d3d37a13f68766` (`once-campfire-goose:ack-policy`).
- Queued-default parity image: `sha256:2ebe3d23c31faf9be982a72157cdd7cae6dbd385bbf0b83b657e5e96cf562a1e` (`once-campfire-goose:queued-ack`).
- Test and both runtime executables: SHA-256 `c503432a8313b05dcfb08563cd14cda14b1990a2322ba8c95248a106cdd84ec3`.
- Compiler: `d9573d8139de395cd4549b876c176c25eb878fc1`.
- goose-http: `555a723adc103a455ccc4cdf43bd4a9a96608a70` (v0.5.11), unpatched.
- Rust main: `6dae2fd3c1a2dce61ca11beb731bcce2b42b67ad`.
- Rust image: `ghcr.io/basecamp/once-campfire-rust@sha256:98ba156f688c3fe856afd71655f613a6620bc03754f456ed2ef08fb2e5527e6e`.
- Jai main: `b0952a20541fadda3ac0eb7991e9d75acca25799`; image application revision `2ecd487` (only documentation changed since).
- Jai image: `ghcr.io/lucasmeijer/once-campfire-jai@sha256:bbbe5c642001c653906dbe4d6f6d584090b82b352724c77d5f134a081506829e`.
- Shared verifier: `ec02debb3e44f40b8778a6a42db3c4515b2f9eaf`, unchanged.

The [machine-readable evidence](queued-acknowledgements-results.json) preserves
every accepted row, metadata, audits, individual metric samples, validation logs,
parity receipts, dependency pins, image/checksum checks, reference checks,
host-monitor samples, excluded attempt and reproduction scripts. All work remains
on `codex/campfire-experiment`. Queued mode is a separate opt-in experiment; the
default final-drain policy and the existing `once-campfire-goose:app` tag remain
unchanged.
