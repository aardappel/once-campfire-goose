# Shared dispatch on the owning HTTP worker

Measured on 2026-10-25 PDT (2026-10-26 UTC), with functional revision `0081410`. This compares the
eight-publication pipeline (`9df5757`) with a bounded shared-payload handoff to
each HTTP worker. All performance runs use epoll under Docker's default seccomp
policy. Rendering, compiler, compression package and reference images are
unchanged. The candidate uses a small pinned goose-http extension. It is retired from the
default after the comparison below; the prior pipeline remains the default.

## Results

| Workload | Goose pipeline (retained) | Shared worker dispatch (retired) | Rust | Jai |
|---|---:|---:|---:|---:|
| Room reads/s | 399,524 (325,284–405,123) | 407,148 (406,176–408,979) | 19,598 (19,484–19,801) | 322,570 (316,153–325,928) |
| Posts/s, no subscribers | 6,170 (6,109–6,248) | 6,198 (6,179–6,242) | 3,635 (3,486–3,639) | 4,352 (4,230–4,403) |
| Messages/s delivered to all 64 readers | 1,767 (1,720–1,864) | 1,756 (1,749–2,071) | 2,343 (2,331–2,346) | 3,440 (3,434–3,491) |
| Anonymous MiB after reads | 23.23 | 22.13 | 37.25 | 22.58 |
| Anonymous MiB after posts | 25.39 | 24.35 | 96.00 | 82.73 |
| Anonymous MiB after fan-out drains | 28.04 | 26.65 | 91.52 | 82.71 |
| Server CPU microseconds per complete fan-out message | 1,489 | 1,480 | 1,264 | 889 |
| All-readers delivery p99, ms | 5.617 | 5.523 | 3.425 | 2.739 |
| Fan-out POST p99, ms | 6.273 | 6.095 | 3.607 | 2.487 |

Shared dispatch does not produce a reliable fan-out gain. Its median changes by
-0.7%, and server CPU per complete message by -0.6%. Its individual rates are
1,755.6, 2,070.9 and 1,749.1 messages/s; the single faster round is not reproduced
by the other two. Pipeline controls before the comparison are 1,720.5 and 1,745.1,
and after it 1,789.5 and 1,863.6. All samples are retained.

Read throughput changes by +1.9% and posts without subscribers by +0.5%; neither
route uses the new grouped send path. The first baseline read sample is unusually
low (325,284/s), widening its range; it is included, not discarded. Fan-out anonymous
memory is 28.04 MiB for the pipeline and 26.65 for the candidate. That sampled
difference does not establish a lower peak or justify the extension without a
repeatable fan-out gain.

The default is restored to the validated eight-publication pipeline and unpatched
goose-http. The complete candidate, patch and unit tests remain in commit `0081410`,
and its image is preserved as `once-campfire-goose:worker-dispatch-experiment`.
The expanded six-subscription pressure test remains in the normal test suite.

The retained pipeline is 23.9% ahead of Jai on room reads and 41.8% ahead on posts
without subscribers. Jai is still 1.95× faster on complete fan-out; Rust is 1.33×
faster there. The fan-out gap has not closed.

Memory is sampled cgroup anonymous memory after a phase drains, not peak memory
or allocation for an identical amount of work. Server CPU includes user and
kernel time. The table gives medians and individual rate ranges, not confidence
intervals. These are application/workload comparisons, not an intrinsic language
speed comparison.

## Candidate ownership and bounds

Before each delivery pass, the broker applies pending drain credits to all
reader cursors. It groups ready readers only when worker mailbox, publication,
frame offset and subscription identifier agree. A group contains at most 64
recipients, found by inspecting at most 256 subsequent connection slots. The
broker encodes the shared chunk once and places it in that worker's mailbox.
The worker checks each exchange generation and resumes each connection through
the existing HTTP delivery path.

The entire envelope, including recipient keys, fits the existing 8 KiB mailbox
slot. A group uses 2 + 8 * recipients bytes for its header; a single recipient
uses the ordinary send path. Three fixed 64-element scratch arrays hold group
indices, subscription indices and addresses. Each connection adds one 64-bit
delivery-pass marker. No per-reader payload queue or shared mutable Goose
object is introduced.

The broker still owns publication scheduling and individual drain credits.
This is worker-owned dispatch of shared chunks, not a fully worker-local
publication broker. Each reader retains its own sequence, frame offset and
final-drain requirement. At most eight publications are active and active plus
queued publications share 64 admission permits. Mailbox capacity, request/body
limits, 16 subscriptions per connection and 64 KiB deferred command bounds
remain in force.

Slow readers can be overtaken within the fixed window, but hold publication
retirement and HTTP acknowledgement. Late readers do not inherit active work.
Unsubscribe cancels unsent work; a started fragmented message retains its
identifier until completion. Disconnect releases only that connection's work.
A disconnected HTTP writer's committed publication is still delivered.
Admission exhaustion returns 503 before database mutation. Acknowledgements
wait for commit, all remaining readers' final socket drain, and publication
order; this does not assert that a browser has processed the bytes.

## Retired local HTTP extension

The candidate uses goose-http v0.5.11 at
`555a723adc103a455ccc4cdf43bd4a9a96608a70`, with
`patches/goose-http-worker-dispatch.patch`. Its SHA-256 is
`d1a3f32949be124f8f948b3f0813f322ab613f808dd42184e4666072da96854a`;
the resulting dependency tree is `1d2d2bd6a73c7fc540d1ab0d35363e97af2046df`.
Bootstrap verifies both the patch and resulting tree.

`mail_send_many` queues one bounded payload with up to 64 recipient keys from
the same worker. Acceptance means the batch was queued; the worker filters stale
recipient generations during dispatch. Mailbox generation, shutdown and
capacity checks use the existing enqueue implementation. Both epoll and uring
loops dispatch the envelope through their existing per-exchange validation.
The application keeps HTTP/3 disabled. No Goose compiler change is needed.

This extension is distinct from the retired send-copy PR #3. It was not
proposed upstream and is also retired from the default build. Its temporary envelope currently allocates per grouped call;
reusing that scratch is a potential follow-up that requires measurement.

## Profile

### Reference delivery policy

The pinned Jai implementation acknowledges ordinary posts after enqueueing their
frames for subscribers. It waits for queue space only when a connection is more
than 4,096 frames behind; the default hard limit is 65,536 frames, and its socket
loop coalesces up to 64 frames per write. Payload frames are shared by reference
among matching subscribers. See its [request pressure wrapper](https://github.com/lucasmeijer/once-campfire-jai/blob/b0952a20541fadda3ac0eb7991e9d75acca25799/src/app/campfire/app.jai#L128),
[publication dispatch](https://github.com/lucasmeijer/once-campfire-jai/blob/b0952a20541fadda3ac0eb7991e9d75acca25799/src/framework/cable/pubsub.jai#L85),
[queue thresholds](https://github.com/lucasmeijer/once-campfire-jai/blob/b0952a20541fadda3ac0eb7991e9d75acca25799/src/framework/cable/server.jai#L19),
and [socket delivery](https://github.com/lucasmeijer/once-campfire-jai/blob/b0952a20541fadda3ac0eb7991e9d75acca25799/src/framework/cable/connection.jai#L402).
Only documentation changed between the image's application revision and this
reviewed main revision.

Goose retains its stronger per-post final-drain acknowledgement and much smaller
outstanding-delivery window. Both benchmark rates count messages received by all
64 clients, but their acknowledgement policies permit different amounts of
overlap. This source review identifies a relevant architectural difference; it
does not quantify how much of the throughput gap it explains. Early
acknowledgement was not enabled to improve the score.

### Goose cycle attribution

Both profiles use sampled user cycles, with about 22,000 samples and no lost
samples. Their throughput figures are diagnostic only and are excluded from
the performance comparison.

| Sampled work | Pipeline | Shared worker dispatch |
|---|---:|---:|
| Copy/move | 13.18% | 10.29% |
| Mailbox enqueue routines | 4.70% | 2.47% |
| Broker function itself | 7.52% | 6.74% |
| Connection liveness check | 6.95% | 7.76% |
| Mutex unlock | 6.32% | 7.08% |
| Internal allocation | 2.21% | 3.13% |

These percentages are relative shares, not absolute CPU savings. Caller stacks
show substantial queue locking in HTTP drain callbacks (`gs_qput`) and broker
consumption (`gs_qpoll`/`gs_qget`). The broker also still probes busy connections
with `mail_live`. Reducing that per-reader control traffic is the next ownership
boundary to investigate, with generation-safe disconnect handling and bounded
credits preserved.

An initial per-connection batching prototype was abandoned because only one of
the shared benchmark's six subscriptions matches each room publication. It would
not remove the measured cross-reader handoffs. Initial grouped-dispatch diagnostic
runs preceded the final all-reader cursor pass and 256-slot scan bound; they are
retained separately and excluded from the table.

## Candidate validation

The full packaged test target passes setup, backend selection, cached and
uncached HTTP, native units and nine compiler reproducers at O0/O1/O2. Mailbox
unit tests cover capacity, envelope size/count, worker mismatch, ordinary key
zero rejection, malformed envelopes, stale recipients, and stopped or reopened
mailbox generations.

Delivery tests pass automatic selection, epoll, ring emulation and native
io_uring. The native run uses the restricted test-only seccomp profile and
also passes cached/uncached HTTP. The grouped pressure test forces one HTTP
worker and six matching subscriptions per reader, with small messages to exercise
shared chunks. It fills a slow reader's TCP window while two fast readers check
all six identifiers for every publication, without duplicates or interleaving.
The mixed-size pressure test also covers fragmentation and multiple workers.

Both pressure modes preserve the eight-publication limit, 64 admission permits,
late enrollment, abandoned HTTP writer delivery, 100 rejected writes without
mutation or RSS growth exceeding the 16 MiB test allowance, final-drain
acknowledgements and disconnect recovery. All accepted writes eventually reach
the remaining readers exactly once and in order.

All 27 differential rendering cases match each Rust/Jai reference, all 315 asset
hashes and MIME types match, and the unchanged shared Chromium suite passes.
The packaged runtime uses the same executable as the full test image, verified
by SHA-256. Every accepted benchmark response and persisted message/rich-text/FTS
set passes the shared validators; all fan-out messages reach all 64 readers.

## Retained-default validation

All runtime Goose sources and dependency pins match the pre-experiment baseline.
The full Docker test target passes on this restored source, including the added
six-subscription, single-worker pressure case on automatic selection, epoll and
emulation. Native io_uring pressure and HTTP checks also pass. The rebuilt
test executable has SHA-256
`09df86a630a59c8b9f9b43f1c055d0ba3ebd2b14204a47a4edd07259df0a4649`,
identical to the retained production image and measured baseline. Its existing
27-case differential rendering, 315-asset and shared-browser receipts are retained
in the evidence report, and the fresh benchmark's shared response and write audits
pass. The runtime tag `once-campfire-goose:app` points back to that baseline image.

## Measurement and provenance

The accepted matrix ran from 03:04:28 to 03:12:06 UTC on 2026-10-26
(20:04:28–20:12:06 PDT on October 25). Rust and Jai main revisions were checked
again after measurement and were unchanged.

Two pipeline-baseline rounds precede a three-round Goose/Rust/Jai comparison;
two more baseline rounds follow it. Application order rotates each comparison
round. The machine is an AMD Ryzen 9 9950X3D under WSL Linux 6.18.54.1 and
Docker 29.1.3. Servers use four physical cores (0,2,4,6) and a 2 GiB limit;
load generators use eight separate physical cores (8,10,12,14,16,18,20,22).
HTTP reads use 256 clients, writes 32, with two seconds of warmup and
eight-second samples. Fan-out uses 64 tabs, six subscriptions per tab,
20 paced messages, four posters, and eight seconds of saturated work plus drain.

The monitor required one minute without active compiler jobs and three consecutive
quiet five-second samples below 0.3 core per process. An idle resident MSBuild
process was permitted only below 0.01 core. During measurement it would interrupt
only its own runner for any active compiler or the same unrelated process above
0.8 core in two successive samples.

All 92 measurement samples had zero active compiler jobs and no interruption
condition. The host was not perfectly idle: one dllhost sample reached 0.324 core,
three Claude process observations reached up to 1.222 cores, and three ChatGPT
observations reached up to 0.583. The Claude peak appeared in one sample,
not two successive samples. These observations and every timestamp are
preserved; no noisy individual result was removed. The original continuously
busy Claude process and earlier compilation/memory-copy jobs were no longer active before
this matrix started.

No unrelated processes were stopped. The first attempt was interrupted when an
unrelated memory-copy benchmark began; its completed baseline rows and partial
comparison are excluded. Every interrupted attempt and monitor is retained
separately in the machine-readable report.

- Functional revision: `0081410d7345908111163ba76abb6fb1477096d0`.
- Candidate image: `sha256:a8f3e483ff94a560d75f86434c3a300ab72161b8437bff7e5302235da374b146`.
- Candidate executable: `33499cdab2a8437f216422352ff33fd115b70692cce0bc434e2b4f5762f31d72`.
- Pipeline baseline: source `9df57574cc62a5ee2f28e6d040adf16ba2db4902`, image `sha256:124ccb36b36f63e26d4e717baf3e3f10a15140b674bc3364bbda4b88e30b5b9d`.
- Baseline executable: `09df86a630a59c8b9f9b43f1c055d0ba3ebd2b14204a47a4edd07259df0a4649`.
- Compiler: `d9573d8139de395cd4549b876c176c25eb878fc1`.
- Rust main: `6dae2fd3c1a2dce61ca11beb731bcce2b42b67ad`.
- Rust image: `ghcr.io/basecamp/once-campfire-rust@sha256:98ba156f688c3fe856afd71655f613a6620bc03754f456ed2ef08fb2e5527e6e`.
- Jai main: `b0952a20541fadda3ac0eb7991e9d75acca25799`, application `2ecd487`.
- Jai image: `ghcr.io/lucasmeijer/once-campfire-jai@sha256:bbbe5c642001c653906dbe4d6f6d584090b82b352724c77d5f134a081506829e`.
- Verifier: `ec02debb3e44f40b8778a6a42db3c4515b2f9eaf`, unmodified.

The [machine-readable results](worker-dispatch-results.json) retain all 13 accepted
application rounds, metadata, individual samples, audits, dependencies, checksums,
profile attribution, validation logs, parity receipts, host-monitor samples and
excluded attempts. They include the exact benchmark and monitor commands. The
baseline metadata records the current harness checkout; its image label and
separately recorded dependencies identify the older binary actually measured.

Everything remains on `codex/campfire-experiment` in the standalone experimental
repository. Compatibility limits remain documented in [browser parity](browser-parity.md).

## Next ownership boundary

This experiment shares worker handoffs but leaves per-reader scheduling, liveness
probes and drain-credit queues in the broker. Moving the complete reader progress
state onto each HTTP worker, and returning one publication completion per worker,
could remove more of that control traffic. It would need a worker-state hook in
goose-http, bounded publication storage and explicit ordered completion/disconnect
handling. The existing final-drain acknowledgement can remain unchanged. This is
a further design to implement and measure, not a performance result from this trial.
No Goose compiler blocker was encountered in the shared-dispatch experiment.
