# Fan-out delivery: coalescing, shared bodies, zero-copy sends and per-worker delivery

Measured on 2026-10-28. Results against Jai come from a Ryzen 7 PRO 6850U laptop (WSL2,
Docker Engine) with the shared verifier, `benchmark_shared.py` and the published Jai image
(`ghcr.io/lucasmeijer/once-campfire-jai@sha256:bbbe5c64…`). Every sample and scenario run
validated, and the full suite and native io_uring tests passed. The per-change numbers come
from same-session A/B runs on a MacBook (M1) under Docker's arm64 VM, where Jai doesn't run.

"Fan-out" is complete messages per second, each received by every reader. Unless stated
otherwise: 64 WebSocket readers with 6 subscriptions each, 4 posters, rotated rounds, medians.

## Against Jai (6850U, same-run ratios)

64 readers (`benchmark_shared.py`), Goose / Jai fan-out on epoll:

| revision | queued-8 | queued-128 | default (drained, 8) |
|---|---:|---:|---:|
| upstream `37189c6` | 0.58–0.63× | 0.60–0.65× | — |
| `d33b4c3` (changes 1–3) | 0.87–0.95× | 0.94–0.95× | — |
| `4b78473` (changes 1–7) | 0.93–0.96× | 0.89–0.94× | 0.63–0.66× |
| **`cf542ab` (all changes)** | **1.34×** | **1.35×** | **1.04×** |

At `cf542ab`, Goose uses 0.63–0.68× Jai's CPU per message. Its delivery p99 is 3.7–4.1 ms
queued and 2.5–2.7 ms with the default, against 4.0–4.1 ms for Jai, and its POST p99 is
lower than Jai's. On native io_uring the ratios are 1.38×, 1.39× and 1.08×. Posts were
already about 1.55× Jai's, and memory about a third.

Jai's own fan-out scenario (`bench/scenarios/fanout`, DEFLATE=0: 30 posts 200 ms apart, then
4 posters for 15 s), `cf542ab` / Jai:

| | 2,000 clients | 10,000 clients |
|---|---:|---:|
| paced delivery p50 / p99 | 0.93× / 0.94× | 0.96× / 0.95× |
| saturated delivery rate | 0.50× (`4b78473`: 0.21×) | 0.40× (0.24×) |
| saturated CPU per delivery | 2.0× | 2.5× |
| paced CPU per delivery | 1.16× | 0.95× |
| peak anonymous memory | 0.07× (20 MiB vs 297) | 0.23× (43 vs 188) |

At this scale Goose now matches Jai's latency and CPU for paced traffic, and uses a small
fraction of its memory. Under saturation Jai still delivers twice as fast, at half the CPU per
delivery. The rate also varies more between rounds for Goose (86 against 125–132 msg/s at 2,000).
Uneven connection counts across workers are a possible cause that hasn't been tested.

## Against the baseline (M1, same session)

Against upstream `37189c6` (the head of `codex/campfire-experiment` and goose-http v0.5.11):

| | baseline | this branch | ratio |
|---|---:|---:|---:|
| 64 readers, default configuration: fan-out msg/s | 1,669 | 3,245 | 1.94× |
| 64 readers, default configuration: delivery p99 | 4.3 ms | 2.6 ms | 0.61× |
| 64 readers, best configuration (baseline queued-128, branch queued-8): fan-out msg/s | 1,940 | 3,816 | 1.97× |
| 64 readers, best configuration: delivery p99 | 101 ms | 4.5 ms | 0.04× |
| 2,000 readers (queued-128 / queued-8): fan-out msg/s | 56.7 | 159.6 | 2.8× |
| 2,000 readers: server CPU per message | 58.6 ms | 15.9 ms | 0.27× |
| 2,000 readers: delivery p99, saturated / paced | 2.4 s / 40.5 ms | 86 ms / 23.5 ms | |

Cached room reads (~230k/s) and posts (~7,400/s) are unchanged. Anonymous memory after
fan-out is unchanged too: 24–31 MiB at 64 readers and 23–26 MiB with 2,000 live WebSockets.
The baseline 64-reader rows are 2 rounds and the 2,000-reader row 1 round, as a final check;
the per-change rows below are 3 rounds each.

On a Ryzen 7 PRO 6850U laptop (WSL2) with the verifier and current Jai, the first three
changes alone (at `d33b4c3`) measured 1.50–1.57× the baseline's fan-out, which is 0.94× Jai at
queued-128, with ~40% less CPU per message.

## Changes, in commit order

1. **Coalesced frames** (`c45867a`). A delivery carries pending frames up to `CABLE_BATCH`
   (32 KiB) instead of one batch per drain credit, so a reader that falls behind catches up
   in fewer, larger sends.
2. **Credit only real deliveries** (`93e1e7c`). Pings and subscription confirmations no
   longer count as drain credits, which used to let the broker over-send.
3. **Shared bodies** (`9542706`). The broker encodes a publication once into a refcounted
   goose-http shared blob, and each delivery mail describes inline bytes and ranges of it
   instead of carrying a copy. Locally: queued-128 fan-out 1,905 → 2,871 msg/s, CPU/msg
   −40%, delivery p99 84 → 15 ms (steps 1–3 together).
4. **Linear entity scan** (`8e627f6`). Rendering sought each `&`'s `;` to the end of the
   message, so a 40 KB `&`-heavy post took ~0.4 s (200,000 `&`: 17 s, now 5 ms). It was a
   real post-path bug, and it caused the flaky window-512 pressure test (`d33b4c3` also
   made that test tolerate slow hosts).
5. **Broker work list** (`3fb6070`). Each delivery pass visits only connections that were
   credited, enrolled, unsubscribed or refused for a full mailbox, and a credit names its
   connection's index, so a credit no longer costs O(connections). Fan-out at 10,000
   readers: 11.7 → 19.5 msg/s, CPU/msg −18%; unchanged at 64 and 1,000.
6. **`CAMPFIRE_HTTP_MAX_CONNS`** (`c5610fd`). Opt-in per-worker connection and waiting
   limits (default unchanged, 1,024), needed above ~4,000 readers.
7. **goose-http pin with zero-copy shared sends and checked blob handles** (`4b78473`, then
   the mutex follow-up). `send_shared` ranges go out from their blobs with `sendmsg` (epoll)
   or an io_uring `sendmsg` (native and emulated rings); only bytes the socket doesn't
   take get copied. TLS and Windows still copy. Blob handles are generation-checked, so a
   stale or forged handle is refused instead of read. queued-8 fan-out +19%, CPU/msg −11%.
8. **Per-worker delivery** (`4ca9f64` and its tests). The broker keeps the global order,
   window, acknowledgements and retirement, but mails each worker every publication once;
   each worker enrolls, writes and credits its own readers, and credits the broker once per
   publication. The broker's serial work per message is O(workers) instead of O(readers).
   Against step 5 on the same goose-http pin: 64 readers +23% fan-out, CPU/msg −30%;
   2,000 readers +51% fan-out, CPU per delivery −46%, paced delivery p50 28 → 18 ms.

Steps 7 and 8 together: 64 readers 4,177 msg/s (3,204 with step 7 alone, 3,796 with step 8
alone); 2,000 readers 7.6 µs CPU per delivery (16.8 and 10.0).

### Delivery semantics after step 8

Ordering, bounded memory, stalled-reader retirement, unsubscribe cancelling unsent
deliveries, finishing an already-started message before its identifier is reused, the
window, permits and 503 on exhaustion are unchanged; see
[browser coverage](browser-parity.md). Two things moved:

- A publication enrolls a worker's subscribers when it reaches that worker, so a
  subscription confirmed between broker enrollment and that moment receives it: a
  slightly later, still causal, cut-off.
- goose-http mail goes only to a waiting exchange, so each worker names one of its cable
  connections as the broker's address for it and names another when that one leaves; the
  broker resends from that worker's position. Local wake-ups of the worker's other readers
  are mail to their own addresses in the same box, which is now 2,048 slots of 128 bytes per
  worker by default (was 128 of 8 KiB). A worker-level address in goose-http would remove
  most of this.

It also fixes a gap: a client's WebSocket Close ended its exchange without CLOSED, so its
presence was never cleaned up. Workers now run the same leave and presence cleanup.

## Tried and dropped

So nobody needs to redo them:

- **Batched drain credits:** a lock-free stack where only the push onto an empty stack
  wakes the broker. Over two A/Bs, throughput was within noise and CPU/msg −1.5 to −3%;
  the profile put all queue wake-ups at ~3.6%. Not merged.
- **`CABLE_BATCH` size:** 8 KiB cost 23% fan-out and +33% CPU/msg; 64 KiB gained nothing.
  It stays 32 KiB.
- **Larger publication windows after step 5:** queued 8, 16, 32, 64 and 128 are within
  noise on throughput (2,878–3,018 msg/s), and queued-8 has the lowest delivery p99 (6.7 vs
  10.1 ms at 128); at 2,000 readers queued-8 matches queued-128 as well. A large window no
  longer buys throughput.
- **Native io_uring as the default backend:** at `d33b4c3` it passed its tests but wasn't ≥
  epoll (memory +20–27%). After step 7 it led epoll by 8–29% at `4b78473`, but after step 8
  the lead is about +3% fan-out, with about 20% more memory. The default stays epoll.
- **The second timer re-arm per delivery** (`http::later` at WAKE and again at DRAINED):
  ~1% in the profile. Unchanged.
- **Zero-copy under TLS:** not attempted. Sealing needs contiguous plaintext, and
  encryption is a copy anyway.

## Method

`perf` profiles, taken under saturated fan-out after step 3, ranked the remaining costs:
the kernel TCP send path ~37% (inflated locally: the load generator and loopback share the
VM), the per-reader body copy ~12%, the broker ~9%, worker-to-broker wake-ups ~3.6%. At
10,000 readers the broker thread was saturated in its per-credit scans (step 5). With 2,000
readers and paced posts, it spent ~20 ms of serial CPU per message (step 8).
