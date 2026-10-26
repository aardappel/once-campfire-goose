# Larger bounded publication queues

This opt-in experiment compares queued acknowledgements with active publication windows of 8, 32, 128 and 512. All Goose cases use the same executable (functional revision `373e348`) and unpatched goose-http. The default remains final-drain acknowledgement with an eight-entry window and 64 delivery permits.

Measured on October 26, 2026. The 128-entry window has the highest median complete fan-out rate: **2,434 messages/s, 19.9% above queued mode at eight**. Jai still delivers **3,448 messages/s (1.42× Goose)** with much lower saturated delivery latency. A 512-entry window provides no further measured throughput benefit and carries a much larger latency and stalled-reader memory cost. All larger windows remain opt-in.

## Results

| Setting | Complete messages/s (range) | Gain over queued 8 | Delivery p99, ms | Fan-out POST p99, ms | Anonymous MiB after delivery |
|---|---:|---:|---:|---:|---:|
| Goose queued 8 | 2,031 (1,901–2,120) | — | 13.103 | 9.855 | 27.09 |
| Goose queued 32 | 2,266 (2,174–2,394) | 11.6% | 27.439 | 12.503 | 28.52 |
| Goose queued 128 | 2,434 (2,293–2,543) | 19.9% | 66.431 | 11.391 | 28.11 |
| Goose queued 512 | 2,322 (2,294–2,327) | 14.4% | 192.639 | 15.527 | 32.27 |
| Jai | 3,448 (3,388–3,457) | — | 2.707 | 2.477 | 83.70 |

Every larger-window sample exceeds every eight-entry sample. The larger variants' ranges overlap, so 128 is the highest observed median, not a statistically established optimum. Its gain is 403.6 messages/s, closing 28.5% of the 1,417.8 messages/s gap between queued-eight and Jai in this comparison. This is a fresh comparison of queue capacities; it should not be combined numerically with the earlier final-drain-versus-queued experiment.

At 128 entries, saturated delivery p99 is 5.1× the eight-entry control. At 512 it is 14.7×. The median delivery tail after posting stops is 0.02 seconds at 128 and 0.07 at 512, and is included in the throughput denominator. The 20-message paced checks have median per-run delivery p99 values of 3.97, 3.41, 2.98, 3.88 and 2.87 ms respectively; this small sample only shows that the large saturated delays arise under load, not a reliable low-load ranking.

| Setting | Room reads/s | Posts/s without subscribers | CPU µs per complete fan-out message | Container lifetime peak MiB through fan-out |
|---|---:|---:|---:|---:|
| Goose queued 8 | 383,935 | 6,174 | 1,446 | 99.36 |
| Goose queued 32 | 406,619 | 6,161 | 1,359 | 103.55 |
| Goose queued 128 | 391,419 | 6,137 | 1,339 | 103.95 |
| Goose queued 512 | 387,647 | 6,248 | 1,373 | 107.33 |
| Jai | 316,581 | 4,408 | 887 | 163.06 |

With the 128-entry setting, Goose remains 1.24× as fast on cached room reads and 1.39× on posts without subscribers. It uses about one-third of Jai's sampled anonymous memory after fan-out. Queue capacity does not establish a meaningful HTTP-only improvement: those routes are controls, with no fan-out backlog. There is no single overall winner independent of workload mix.

The separate epoll stalled-reader tests expose the cost of retaining large messages:

| Queued window | Total delivery permits | Large-message stalled RSS, MiB | Six-matching-subscription stalled RSS, MiB |
|---|---:|---:|---:|
| 8 | 64 | 33.49 | 16.19 |
| 32 | 88 | 39.27 | 16.51 |
| 128 | 184 | 56.36 | 18.09 |
| 512 | 568 | 134.55 | 22.90 |

These are individual process-RSS samples at the deliberately stalled state, not worst-case memory guarantees or Jai comparisons. The large-message case uses 40,000 ampersands per prefill message to exercise escaping and fragmentation; the subscription case uses smaller messages with six matching identifiers and one HTTP worker. Both reject excess writes while retaining the configured delivery permits.

The evidence supports preserving 32 and 128 as selectable throughput/latency trade-offs. It does not support choosing 512 as an optimization: its median throughput is lower than 128, its delivery p99 is nearly three times larger, and its large-message stalled RSS is 2.4× higher. The default stays final-drain acknowledgement at eight, and the existing `:app` image tag is unchanged.

## Method

Three samples per setting and three Jai samples run on the same machine, with order rotated across rounds. This is 15 application rounds, not a fully balanced permutation of all five positions. Every round starts with fresh seeded storage. The experiment uses the unchanged shared HTTP contracts, load generator, ActionCable protocol checks and persisted-write audit. Fan-out runs for eight seconds with four closed-loop publishers, 64 readers and six subscriptions per reader. A message counts only when all 64 readers have received it, and the rate denominator includes the delivery tail after posting stops. Latency is measured from request start to the last reader, with POST response latency recorded separately.

Goose uses epoll in ordinary Docker, as does Jai. Four separate physical CPU cores serve the application and eight others run the client, with a 2 GiB container limit. HTTP controls use 256 read clients and 32 write clients. The tables report medians of three samples; sample ranges are descriptive, not confidence intervals. This comparison measures application behavior under this workload, not general language speed.

The host guard requires 60 seconds without active compilers and three quiet five-second samples below 0.3 core per process. It interrupts only our runner if a compiler starts or the same unrelated process exceeds 0.8 core in two consecutive samples. Any interrupted attempt is excluded in full and preserved.

## Queue and memory contract

`CAMPFIRE_PUBLICATION_WINDOW` accepts powers of two from 8 through 512. A value above eight requires explicit `CAMPFIRE_PUBLICATION_ACK=queued`; invalid settings fail before database initialization. Total delivery permits equal the active window plus 56: 64, 88, 184 or 568 for the measured sizes. Active and pending work share this permit budget. When the active window is full, another 56 publications can remain admitted and pending; before enrollment, more of the total budget can be pending.

A queued HTTP success means the database commit completed and the broker retained the payload and subscriber snapshot. Acknowledgement never releases the delivery permit. Final-drain retirement does. A full active window delays further acknowledgements; a full permit budget rejects new writes with 503 before database mutation. Disconnecting an HTTP writer does not cancel its committed publication. There is no durable replay guarantee across a server crash.

The broker stores one shared encoded body per active publication and four bytes of target mask per window entry per connection: 32 bytes at eight versus 2 KiB at 512. Existing payload, connection, subscription and HTTP mailbox limits remain. The result is bounded by counts and payload sizes, not a new fixed MiB budget. Larger windows deliberately retain more data under pressure.

Sampled anonymous memory after fan-out, cgroup lifetime memory peak, and stalled-reader process RSS describe different things. The cgroup peak includes file cache, kernel memory and earlier HTTP phases; it is neither an isolated fan-out peak nor an anonymous-memory peak. Stalled-reader RSS comes from the separate adversarial test with large escaped messages.

## Validation

The full Docker suite passes, including both acknowledgement policies at eight on automatic selection, epoll and emulation; both pressure cases for 32, 128 and 512 on epoll; and the 512-entry multi-subscription case on emulation. Pressure cases verify ordered exactly-once delivery to continuing readers, late subscription snapshots, abandonment of an HTTP writer, retained permits after early acknowledgement, 100 excess writes rejected without mutation, and less than 16 MiB RSS growth from those rejections. Disconnecting the stalled reader releases the held work.

Native io_uring's initial 512-entry mixed-size test exhausted its TCP-prefill attempt budget without reaching the expected stall. A diagnostic run and the unmodified repeat passed; the latter also passed the multi-subscription and HTTP suites. The cause of the initial failure remains unconfirmed, and all three receipts are preserved. No timeout, test limit or production delivery rule was weakened. This benchmark uses epoll; the native test inconsistency remains a validation caveat.

At window 512, all 315 packaged assets match their pinned hashes, all 27 differential rendering cases match each of Rust and Jai, and the unmodified shared Chromium smoke suite passes. Test, packaged and queued-parity images contain the same binary hash.

## Jai policy context

The verified Jai source normally acknowledges after enqueueing; its request wrapper waits for connections noted as behind. Default per-connection queue thresholds are 4,096 frames soft and 65,536 hard, with up to 64 frames coalesced per write. Goose still uses a global publication window and one outstanding drain credit per connection. Increasing that window measures the effect of more permitted overlap in Goose; it does not reproduce Jai's queue layout or write batching, or assign the remaining performance gap to any one of those differences.

## Provenance and evidence

The accepted attempt is `queue-window-quiet1`, from 22:54:36 to 23:03:22 UTC. All 15 application rounds passed HTTP contracts, write audits, complete fan-out and persistence checks. The host monitor collected 106 measurement samples with no compiler activity or interruption. Brief background activity remained below the interruption threshold (maximum 0.682 core); this was a guarded quiet workstation, not an otherwise empty host.

The machine is an AMD Ryzen 9 9950X3D under WSL Linux 6.18.54.1. Server CPUs are 0,2,4,6; client CPUs are 8,10,12,14,16,18,20,22. The compiler pin is `d9573d8139de395cd4549b876c176c25eb878fc1`, goose-http v0.5.11 is `555a723adc103a455ccc4cdf43bd4a9a96608a70`, and the shared verifier is `ec02debb3e44f40b8778a6a42db3c4515b2f9eaf`.

Jai main was rechecked after measurement at `b0952a20541fadda3ac0eb7991e9d75acca25799`. Only documentation differs from the measured application revision `2ecd487`; its image digest is `sha256:bbbe5c642001c653906dbe4d6f6d584090b82b352724c77d5f134a081506829e`. Rust's rendering reference remains `6dae2fd3c1a2dce61ca11beb731bcce2b42b67ad`; Rust throughput was not remeasured in this queue-capacity experiment.

The Goose executable SHA-256 is `5afd6ba2f4560a1128e6bb01af99f28b2ef0694cd9662f432ddc6f8db6e73b79`. The [complete evidence](publication-queue-sizes-results.json) preserves raw samples, individual latency distributions, memory observations, host monitoring, validation receipts (including the initial native failure), immutable references and reproduction scripts. The [earlier policy-only comparison](queued-acknowledgements.md) remains separate historical evidence.

## Reproduction

```sh
CAMPFIRE_PUBLICATION_ACK=queued CAMPFIRE_PUBLICATION_WINDOW=32 /usr/local/bin/campfire
```

The harness accepts `--goose-publication-ack queued --goose-queued-windows 8,32,128,512`. The evidence includes exact container IDs, dependencies, checksums, every measured row, host monitoring, validation logs, and executable benchmark/summary recipes.
