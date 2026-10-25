# Publication broker optimization

Measured on 2026-10-25. This report compares the original epoll application with
experimental revision `53f092f` and the same pinned Rust/Jai images. The HTTP
library, compression package, compiler, verifier, renderer and delivery limits
are unchanged. All uninstrumented performance runs use ordinary Docker's default
seccomp policy.

## Retained changes

The broker assembles each 8 KiB batch with bounded slice copies and reuses its
prefix and batch buffers. It handles at most 128 queued control commands before
a delivery pass. Subscription metadata occupies 16 contiguous slots per
connection, separate from the compact connection state. A recipient therefore
scans only its own slots. Ready recipients rely on the generation check already
performed by `mail_send`; waiting recipients still receive liveness checks.

There is still one active encoded publication, one batch per reader per drain
credit, and 64 admission permits. A successful POST still waits for commit and
every enrolled reader's socket drain or disconnect. The broker still serializes
publications globally. No HTTP-library patch or special container policy is used.

## Profiling evidence

Linux perf sampled user cycles at 499 Hz with DWARF call stacks on the shared
64-client fan-out workload. Reports were resolved against the exact executables.
The original broker accounted for 58.24% of sampled cycles. Disassembly places
53.91 percentage points in the per-byte branch/store loop that assembles each
chunk. In the final profile the broker accounts for 9.12%; the largest remaining
entries include memory copying (13.48%), mailbox liveness checks (6.32%), mutex
unlocking (6.07%) and mailbox sends (4.34%). These are sampled shares, not isolated
function benchmarks or a decomposition of wall-clock latency.

Profiled throughput varied substantially from uninstrumented runs. It is not
used for the performance claims below. The raw diagnostic samples and symbol
reports are retained in the [machine-readable results](broker-optimization-results.json).

A larger inline subscription array increased the connection state scanned on
each command. Separating subscription storage performed better. A further
single-entry cache for identical encoded batches also failed to improve the
rotated diagnostic comparison: 921 messages/s median versus 995 for the compact
layout without it. The cache is not part of the committed implementation.

## Repeated same-machine comparison

| Workload | Goose before | Goose after | Rust | Jai |
|---|---:|---:|---:|---:|
| Room reads/s | 398,373 (393,047–402,498) | 387,516 (379,457–388,365) | 19,248 (19,150–19,570) | 313,107 (300,123–330,084) |
| Posts/s, no subscribers | 6,138 (5,998–6,314) | 6,158 (6,137–6,251) | 3,508 (3,503–3,544) | 4,293 (4,183–4,478) |
| Messages/s delivered to all 64 subscribers | 648 (641–668) | 984 (965–1,188) | 2,312 (2,062–2,329) | 3,326 (3,305–3,437) |
| Anonymous MiB after reads | 23.34 | 23.69 | 38.07 | 21.90 |
| Anonymous MiB after posts | 25.69 | 24.27 | 97.27 | 88.41 |
| Anonymous MiB after fan-out drains | 28.55 | 26.61 | 91.49 | 84.18 |
| Server CPU microseconds per delivered message | 3,797 | 1,984 | 1,278 | 920 |

Complete fan-out improves by 51.8%, with
47.8% less sampled server CPU per delivered message and 6.8% less anonymous
memory after fan-out. Jai's fan-out lead narrows from 5.13× over the baseline to
3.38× over the new version. The gap is not closed.

Room reads are 2.7% lower than the bracketed baseline; this is a measured read
regression in this run set, whose cause has not been isolated. Read memory is
0.36 MiB higher. Posts without subscribers are essentially unchanged (+0.3%).
Goose still leads Jai by 23.8% on room reads and 43.4% on posts without subscribers.
These measurements support a fan-out improvement, not an across-the-board win.

## Correctness and memory bounds

The full application suite passes automatic selection, direct epoll and ring
emulation, with HTTP caches enabled and disabled. The restricted native io_uring
profile also passes backend selection, the delivery suite, and HTTP tests with
caches enabled and disabled. Required native mode still refuses unavailable
support. The production-image smoke test passes.

All 27 differential rendering cases match each reference, and all 315 assets
match their pinned hashes. The unchanged shared Chromium suite passes.

The expanded delivery suite checks escaped identifiers, multiple and duplicate
subscriptions, unsubscribe/slot reuse, publication ordering, 200 KiB fragmented
messages with concurrent incoming commands, and disconnect/presence cleanup.
A deliberately stalled reader fills all 64 publication permits. Committed writes
remain unacknowledged, 100 excess writes return 503 without database mutations,
and retained RSS grows by less than the test's 16 MiB allowance. Disconnecting
the stalled reader completes every admitted write and delivers every message
exactly once to the remaining reader. This test passes the original implementation
as well as the optimized one. The finite RSS check supplements the unchanged
explicit queue/body/chunk limits; it is not a proof of peak process memory.

Every accepted HTTP response passes the unmodified shared contracts. All fan-out
messages reach all 64 clients; exact message, rich-text and FTS sets pass the
persistence audit. There are no invalid responses, request errors, lost messages
or fan-out POST errors in the accepted comparison.

| Three-round reference comparison | Goose | Rust | Jai |
|---|---:|---:|---:|
| Valid measured HTTP responses | 9,410,330 | 549,471 | 7,669,370 |
| Audited HTTP writes, including warmup | 185,014 | 101,009 | 128,526 |
| Audited fan-out writes | 25,166 | 53,697 | 80,631 |

## Method and limits

Two baseline rounds run before the three-round Goose/Rust/Jai comparison, and
two baseline rounds follow it. The three-application order rotates each round.
Rates are medians; ranges show individual samples, not confidence intervals.
The machine is an AMD Ryzen 9 9950X3D under WSL Linux 6.18.54.1 and Docker 29.1.3.
Servers use physical cores 0,2,4,6 and a 2 GiB limit. Clients use separate physical
cores 8,10,12,14,16,18,20,22. HTTP reads use 256 clients, writes 32, and samples last
eight seconds after two seconds of warmup. Fan-out uses 64 tabs of one fixture
user, six subscriptions per tab, 20 paced messages, four posters and eight
seconds of saturated delivery followed by draining.

Read clients consumed roughly 7.0–7.2 cores of their eight-core allocation.
These measurements describe this bounded configuration, not an unrestricted
throughput ceiling.

Memory figures are sampled cgroup anonymous memory, not total/peak memory.
Equal-duration write phases do different amounts of work; they do not establish
fixed-work allocation efficiency. Fan-out memory is sampled after the saturated
phase has drained. The 64-writer admission limit is unchanged; the 32-writer
throughput workload does not establish support for unlimited concurrent writers.

The host monitor waits for three quiet five-second samples before starting.
Across 92 measurement samples it observed no compiler process. Desktop activity
remained: Discord in nine samples (up to 0.602 core), ChatGPT in 14 (0.942),
Fork in three (0.647), Claude in eight (0.640), and Chrome in one (0.315).
No other user work was interrupted. The host was not perfectly idle, and the
sample ranges should not be treated as confidence intervals.

The renderer matches the tested reference cases, but this remains an experimental
Campfire port with the [documented compatibility gaps](browser-parity.md). No
broad language-superiority claim follows from these workloads.

## Provenance and reproduction

Measurement ran from 07:45:39 to 07:53:19 UTC on 2026-10-25. The experimental
functional revision and benchmark harness are `53f092f039c2292a89af4cad9e23dd4fb174af33`.
Later documentation commits do not change the measured executable.

- Goose image: `sha256:eeac7b07cf9ac01fc793fc3b054e67aa8d31c2c396c51cadac6cb87e3d6fcafc`.
- Executable SHA-256: `d8bbc2908fa2cbe0fd347759633c9ed4368f98b87f3da3e8cb7d088292546796` (identical in the test image).
- Baseline image: `sha256:23e7ec908667a96af24c2816718a3434ea081cd9c4293d568e8f4d5bda655857`, functional revision `1e178a8`.
- Rust image: `ghcr.io/basecamp/once-campfire-rust@sha256:98ba156f688c3fe856afd71655f613a6620bc03754f456ed2ef08fb2e5527e6e`, revision `6dae2fd3`.
- Jai image: `ghcr.io/lucasmeijer/once-campfire-jai@sha256:bbbe5c642001c653906dbe4d6f6d584090b82b352724c77d5f134a081506829e`, application tag `2ecd487`.
- Rust main remains `6dae2fd3`; Jai main remains documentation revision `b0952a2`, checked again after measurement.
- Compiler: `d9573d8139de395cd4549b876c176c25eb878fc1`.
- HTTP: `555a723adc103a455ccc4cdf43bd4a9a96608a70` (v0.5.11), unpatched.
- Compression: `6f0c17ac4ceeca930cc7cd9c6b5d9293c8b09f50` (v0.1.4).
- Verification: `ec02debb3e44f40b8778a6a42db3c4515b2f9eaf`, unmodified.

No work was landed on Goose master, no compiler/library code was changed, and
the retired send-copy patch remains absent.

The [machine-readable report](broker-optimization-results.json) retains every accepted run and its image identities,
checksums, validated response counts, persistence audits, fan-out completion
counts, memory samples, browser/rendering receipts and host-monitor summary.

From a clean checkout with the shared verifier and its Ruby dependencies/load
generator installed, run the following with pinned reference image digests:

```sh
python3 benchmark_shared.py --verification "$VERIFY" --seed "$SEED" \
  --runtime-root "$RUNTIME" --output build/broker-comparison \
  --apps goose,rust,jai --goose-backend epoll --rounds 3 --duration 8 \
  --clients 256 --write-clients 32 --client-cores 8 --fanout-clients 64 \
  --profiles baseline --routes room_show,post_message \
  --rust-image "$RUST_DIGEST" --jai-image "$JAI_DIGEST"
```

Bracket it with two rounds of `--apps goose --goose-image "$BASELINE_IMAGE"`
using distinct output directories and otherwise identical arguments. For CPU
profiling, use `perf record -F 499 -e cycles:u --call-graph dwarf,8192 -p PID`
with the shared load generator's `cable` workload. Record onto a native Linux
filesystem and resolve the report against the measured binary; the mounted
Windows filesystem failed perf recording on this host. Profiling permissions
are confined to the diagnostic helper, not the benchmark servers.

## Remaining fan-out work

The byte-copy bottleneck is removed, but the global publication barrier and
per-reader cross-thread send/drain exchange remain. A bounded window of active
publications or delivery owned by each HTTP worker would be the next architectural
experiments. They require explicit per-publication completion tracking, ordered
per-reader cursors, disconnect handling and a fixed memory budget. Early HTTP
acknowledgements or extra unbounded socket queues would not preserve this contract.
