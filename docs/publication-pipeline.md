# Bounded publication pipeline

Measured on 2026-10-25, with experimental functional revision `9df5757`. This
compares the previous optimized serial broker (`53f092f`) with an eight-publication
pipeline. Compiler, HTTP library, renderer, shared validator and reference images
are unchanged. All performance runs use epoll under ordinary Docker's default
seccomp policy.

## Results

| Workload | Goose serial | Goose pipeline | Rust | Jai |
|---|---:|---:|---:|---:|
| Room reads/s | 379,386 (373,874–383,080) | 382,419 (373,823–390,443) | 18,391 (18,238–18,870) | 314,759 (302,354–315,346) |
| Posts/s, no subscribers | 6,013 (5,643–6,067) | 6,044 (6,032–6,068) | 3,421 (3,305–3,496) | 4,206 (4,155–4,322) |
| Messages/s delivered to all 64 readers | 1,125 (960–1,399) | 1,587 (1,545–1,744) | 2,236 (2,230–2,248) | 3,324 (3,237–3,328) |
| Anonymous MiB after reads | 24.25 | 22.05 | 37.80 | 22.06 |
| Anonymous MiB after posts | 24.55 | 24.41 | 98.09 | 84.96 |
| Anonymous MiB after fan-out drains | 26.75 | 26.68 | 89.05 | 83.98 |
| Server CPU microseconds per complete fan-out message | 1,927 | 1,586 | 1,322 | 929 |

Complete fan-out improves by 41.1%. Jai's fan-out lead narrows from 2.96× to
2.09×; the gap is not closed. Room reads change by +0.8% and posts without
subscribers by +0.5% against the bracketed serial baseline. Goose remains 21.5%
ahead of Jai on room reads and 43.7% ahead on posts without subscribers.

Anonymous memory after fan-out is essentially unchanged: 26.75 to 26.68 MiB.
Server CPU time per complete message falls by 17.7%. Memory is sampled cgroup anonymous memory after
the phase drains, not a peak or a fixed-work allocation comparison. CPU time
includes server user and kernel time; it is not a new perf profile.

## Delivery and bounds

At most eight publications are active. Each owns one JSON-encoded body shared
by its readers. A reusable encoding scratch buffer and the existing prefix/chunk
buffers serve the whole broker. Each allocated connection adds eight 32-bit
subscription masks (32 bytes), rather than a growing delivery queue. Existing
request/body limits, 16 subscriptions per connection, 64 KiB deferred command
buffers, and the 64-writer admission limit remain in force. Active and queued
publications share those 64 permits.

Each reader has an independent publication cursor and receives at most one
8 KiB batch per socket-drain credit. It completes publications in order, with
no interleaved WebSocket data messages. Enrollment snapshots subscriptions as a
publication enters the window. Late subscribers do not inherit active work.
Unsubscribe cancels unsent work, while a started fragmented message retains its
identifier until the final fragment has been sent. Disconnect removes that
reader from every active publication without transferring its completion credits
to a reused connection slot.

A final send still waits for its drain notification. The broker retires
publications and emits HTTP acknowledgements in publication order, after commit
and completion by all remaining enrolled readers. A disconnected HTTP writer's
committed publication still reaches its readers. A full acknowledgement mailbox
holds retirement until it can accept the response or the recipient disconnects.

A slow reader can now be overtaken by fast readers within the fixed window, but
still holds retirement and later HTTP acknowledgements. Admission exhaustion
returns 503 before any database write. This preserves bounded outstanding work
and the existing drain contract; it does not prove that browsers have processed
the bytes. See [delivery semantics and compatibility limits](browser-parity.md).

## Validation

The full packaged build passed setup, backend selection, delivery, cached and
uncached HTTP tests, native unit tests, and all compiler regression reproducers.
Delivery passes automatic selection, epoll, ring emulation and native io_uring;
the latter uses the existing restricted test-only seccomp profile. Native cached
and uncached HTTP checks pass as well. Test and production executables have the
same SHA-256 below.

The pressure regression holds a slow reader, admits 64 writers with mixed-size
HTML-escaped bodies, and confirms two fast readers advance through exactly eight
active publications without early
acknowledgement. A late subscriber receives only subsequently enrolled work.
One writer disconnects after commit. Another 100 writes are rejected without
database mutation or RSS growth exceeding the 16 MiB test allowance. Disconnecting
the slow reader delivers every accepted message exactly once; fast readers agree
on the full sequence across window reuse, and every surviving writer completes.
The serial baseline fails precisely the new fast-reader progress assertion.

All 27 differential rendering cases match each reference, all 315 served asset
hashes and MIME types match, and the unchanged shared Chromium suite passes.
Every accepted benchmark response and persisted message/rich-text/FTS set passes
the shared validators. All fan-out messages reach all 64 clients, with no request
errors, invalid responses, or lost messages in the accepted run set.

## Measurement and provenance

The accepted matrix ran from 19:29:47 to 19:37:24 UTC on 2026-10-25.
Rust and Jai main revisions were checked again after measurement and were unchanged.

Two serial-baseline rounds precede the three-round Goose/Rust/Jai comparison;
two more follow it. Application order rotates each comparison round. The table
shows medians and individual rate ranges, not confidence intervals. The machine
is an AMD Ryzen 9 9950X3D under WSL Linux 6.18.54.1, Docker 29.1.3. Servers use four
physical cores (0,2,4,6) and a 2 GiB limit; load generators use eight separate
physical cores (8,10,12,14,16,18,20,22). HTTP reads use 256 clients, writes 32,
with two seconds of warmup and eight-second samples. Fan-out uses 64 tabs, six
subscriptions per tab, 20 paced messages, four posters, and eight seconds of
saturated work followed by draining. Client CPU utilization and every individual
sample are retained in the machine-readable report.

The monitor required three quiet five-second samples and one minute without
compiler processes before starting. Across 92 measurement samples it recorded
no Windows compiler processes. Activity above 0.3 core included Discord in 16
samples (maximum 0.604 core), Code in six (0.489), sublime_merge in one (0.328),
and lobster_matvoxels in two (0.650). No user processes were stopped. The host was not
perfectly idle; baseline bracketing and sample ranges expose some of that variation.

The initial diagnostic comparison also favored the pipeline in all three rounds
(serial 871–1,152 versus pipeline 1,534–1,589 messages/s). It used a development
image and is retained separately; the table uses only the packaged, audited runs.
An initial launcher error selected an already-existing historical output folder
and exited before sampling. Its logs are separate; no historical results were
overwritten and it contributes no samples to this report. The first full attempt
then stopped when the final Rust server could not bind its port. Its eight
completed comparison rows are preserved separately as an interrupted run. The
harness now checks both server ports below the outgoing ephemeral range.
A second attempt was stopped when unrelated Windows compiler jobs started; its
completed rows and monitor are retained separately. The accepted table comes
from a fresh complete matrix after requiring a minute without compiler processes.

- Functional revision: `9df57574cc62a5ee2f28e6d040adf16ba2db4902`; later report commits do not change the executable.
- Harness revision: `0de36a8a9253674d361c5c7a77796038df807625`.
- Goose image: `sha256:124ccb36b36f63e26d4e717baf3e3f10a15140b674bc3364bbda4b88e30b5b9d`.
- Executable SHA-256: `09df86a630a59c8b9f9b43f1c055d0ba3ebd2b14204a47a4edd07259df0a4649`.
- Serial baseline image: `sha256:eeac7b07cf9ac01fc793fc3b054e67aa8d31c2c396c51cadac6cb87e3d6fcafc`, source `53f092f`.
- Rust image: `ghcr.io/basecamp/once-campfire-rust@sha256:98ba156f688c3fe856afd71655f613a6620bc03754f456ed2ef08fb2e5527e6e`, source `6dae2fd3`.
- Jai image: `ghcr.io/lucasmeijer/once-campfire-jai@sha256:bbbe5c642001c653906dbe4d6f6d584090b82b352724c77d5f134a081506829e`, application `2ecd487`; main `b0952a2`.
- Compiler: `d9573d8139de395cd4549b876c176c25eb878fc1`.
- HTTP: `555a723adc103a455ccc4cdf43bd4a9a96608a70` (v0.5.11), unpatched.
- Compression: `6f0c17ac4ceeca930cc7cd9c6b5d9293c8b09f50` (v0.1.4).
- Verifier: `ec02debb3e44f40b8778a6a42db3c4515b2f9eaf`, unmodified.

The [machine-readable results](publication-pipeline-results.json) contain all
13 accepted application rounds, metadata, checksums, audits, diagnostic samples,
delivery logs, parity receipts and the host-monitor summary. Reproduce with the
[previous report's command](broker-optimization.md#provenance-and-reproduction),
using the harness revision and pinned images above, with fresh output directories. Use the serial
baseline image above for the four bracketing rounds.

Everything remains on `codex/campfire-experiment` in the standalone experimental
repository. No compiler or HTTP-library patch is required. The remaining broker
still sends and receives a cross-thread mailbox/drain exchange for each reader's
batch. Worker-owned delivery or bounded batch handoff is a possible next
experiment; a new profile should establish the remaining limiting costs before
choosing it. A larger publication window alone cannot expose more than the four
simultaneous writers in this shared workload. These results do not establish full
Campfire parity or an intrinsic language-speed advantage.
