# HTTP send-copy experiment

Measured on 2026-10-23 UTC. The experimental application uses merged upstream HTTP
drain support plus a local optimization that avoids copying bytes accepted immediately
by a writable socket. It retains owned unsent bytes and deferred completions.

Goose leads the current published Jai image by 11-16% on the four cached-read medians,
60% on posts, and 15% on mixed reads/writes in this comparison. This is a fixed-workload
application comparison, not a language-wide or maximum-capacity claim. The separate A/B
supports retaining the optimization for lower user-mode CPU cost, with variable throughput.

## Controlled before/after result

Five pairs alternate the preserved `af65824` image and new `38099a8` image, reversing order
each pair. Each sample warms up for two seconds, then validates exactly three million room
reads on fresh fixture storage. Thirty million measured responses pass in total.
The first, shorter three-pair probe was inconclusive: median throughput fell 2.5% and total
CPU cost rose 4.1%, despite user cycles falling 3.0%. It is retained in the local artifacts.
The longer probe reduces the influence of startup and short timing windows.

| Median metric | Before | After | Change |
|---|---:|---:|---:|
| User-mode instructions / response | 7,826 | 7,785 | -0.5% |
| User-mode cycles / response | 8,665 | 7,736 | -10.7% |
| Total server CPU microseconds / response | 7.95 | 7.54 | -5.2% |
| Valid responses / second | 228,703 | 250,237 | +9.4% |

User-cycle cost falls in every pair: 10.7%, 10.6%, 8.5%, 3.4%, and 17.2%.
Throughput changes by +10.0%, +7.0%, +3.6%, -2.6%, and +17.0%, respectively.
Before throughput ranges from 205,714 to 253,901; after ranges from 213,136 to 267,629.
Those ranges overlap. The repeated user-cycle reduction is the clearest evidence for the
change; a fixed 9.4% throughput improvement is not established for arbitrary runs.

The unchanged shared load generator uses 16 connections, gzip, the `room_show` contract,
`--requests 3000000 --duration 30`, and client CPUs `8,10,12,14`. Both images use four
workers on server CPUs `0,2,4,6`, with a 2 GiB memory limit. The fixed request batch runs
entirely within `perf stat -x, -e instructions:u,cycles:u,task-clock -p 1 -- sleep 25`;
task-clock includes the surrounding idle interval. Perf comes from Linux tools 6.8.0-147.

## Current Rust/Jai comparison

Valid requests/second, median of three rounds; parentheses are observed minima/maxima.
The complete baseline and mixed run spans 05:30:19-05:40:30 UTC.

| Route | Goose | Rust | Jai |
|---|---:|---:|---:|
| Room | 213,472 (207,083-218,656) | 28,680 (28,257-29,012) | 187,055 (181,086-189,230) |
| Older messages | 218,950 (209,820-220,508) | 28,082 (27,341-28,481) | 196,088 (190,676-198,301) |
| Sidebar | 229,946 (216,581-235,253) | 30,607 (30,517-30,615) | 198,693 (198,211-200,622) |
| Search | 221,255 (221,024-243,459) | 30,676 (30,330-30,752) | 197,436 (195,637-203,250) |
| Post message | 8,419 (8,232-8,441) | 4,117 (3,987-4,143) | 5,272 (5,267-5,325) |
| Mixed room reads | 208,696 (203,453-217,780) | 27,314 (27,271-27,360) | 182,016 (181,514-185,113) |

Against Rust, Goose's room-read median is 7.44x and its post median 2.05x.
Room p99 medians are 0.171 ms for Goose, 1.173 ms for Rust, and 0.191 ms for Jai;
post p99 medians are 6.411, 8.983, and 7.415 ms, respectively.

Median cgroup anonymous-memory samples after each workload, in MiB:

| Sample | Goose | Rust | Jai |
|---|---:|---:|---:|
| Room reads | 24.78 | 22.38 | 45.62 |
| Message posts | 28.51 | 93.13 | 108.34 |
| Mixed reads/writes | 22.14 | 74.23 | 87.56 |

These are neither peak RSS nor equal-stored-message measurements; file-backed cache is
excluded. Goose stays near Rust on room reads and below both references after writes.
The new transport optimization does not establish a memory reduction against the prior Goose
build: its send-record capacity and reuse policy are unchanged.

All eight shared contracts pass before every application/profile/round, including decoded
avatars and a persisted post. Every timed response validates, with zero errors. All
acknowledged writes, including warmup writes, pass row, rich-text, FTS and integrity audits.

| Application | Valid timed responses, including mixed writes | Audited acknowledged writes |
|---|---:|---:|
| Goose | 26,491,296 | 245,590 |
| Rust | 3,581,042 | 118,457 |
| Jai | 23,209,016 | 158,979 |

## Behavior and validation

The ring emulation first reserves an owned send record. If the socket is writable and no
send precedes it, it attempts `send` using the caller's bytes during the extern call. It
copies only the unsent tail before returning. A fully accepted send retains its record
until `ur_wait` reports the usual completion; queued sends and sendfile retain their order.
There is no retained pointer into a Goose buffer and no new public API. The application
renderers, SQL transactions and subscriber delivery protocol are unchanged.

The change applies to the readiness-based ring emulation used here. Native io_uring retains
its 64 KiB inline-send threshold; the separate direct epoll loop is unchanged. Kernel socket
copies and the application's response assembly still exist: this is not end-to-end zero-copy.

Validation includes:

- Deterministic C checks for full/partial writes, EAGAIN, EINTR and errors, caller-buffer
  reuse, deferred/overflow completions, close, allocation failure and sendfile ordering.
  GCC address/undefined-behavior sanitizers and Clang pass.
- All 60 HTTP package tests at `-O0` and `-O2` with Goose v2026.3. HTTP integration passes
  pipelines, large responses, WebSockets, file sends, slow/hostile clients, 10,000 connections,
  load, HTTP/2 cleartext, restart and descriptor exhaustion on the emulated ring.
- Both Unix loops pass 32 MiB stalled-reader ordered delivery and disconnected-slot reuse,
  with no measured RSS growth during the stall, plus direct-publisher fairness/deadlines.
- The full application test image passes both backends, cache on/off, authentication expiry
  and revocation, WAL restart, rollback, concurrent writes, 200 KiB fragmented fan-out and
  disconnect cleanup. The production image passes the unchanged shared Chromium suite.
- All 27 differential rendering cases against each reference pass, as do all 315 asset
  hash/MIME checks. No rendering concessions were made for timing.

Native io_uring, macOS and Windows integration remain unmeasured here. The existing
[application compatibility gaps](browser-parity.md) remain; throughput tests have no live
WebSocket subscribers and do not measure fan-out capacity.

## Environment, provenance and reproduction

AMD Ryzen 9 9950X3D, Docker on WSL2 Linux 5.15.167.4, glibc 2.39. The app is built with
Clang 18.1.3 and the pinned Goose compiler. The shared comparison uses four server cores,
four separate client cores, 16 clients, two-second warmups and eight-second samples.
Application order rotates each round. The mixed profile posts ten messages/second to HQ
while reading Watercooler. Each instance gets fresh native-Linux fixture storage.

Both timing phases start after three consecutive five-second quiet-host samples. No compiler
jobs occur during measurement. Host logs record occasional browser/system activity: the
comparison's largest five-second process average is 0.955 cores; the longer A/B's is 0.49.
No competing processes are interrupted. These are quiet desktop measurements, not an
isolated laboratory machine, and all samples are retained. The long A/B spans roughly
05:41:26-05:46:18 UTC.

The room-read generator consumes about 3.76 of its four allocated cores for Goose and 3.74
for Jai; servers consume 1.66 and 1.94 cores. Limited generator headroom remains. These
rates must not be extrapolated to maximum server capacity or a concurrent-user count.

| Input | Revision |
|---|---|
| Goose application | `38099a82ca7b1cb138012307161ff4c2292fe6d9` |
| A/B baseline application | `af65824e896a496b4b685d7a692e298a7abc1106` |
| Goose compiler | `d9573d8139de395cd4549b876c176c25eb878fc1` |
| HTTP upstream | `ebb4238728179e82140550af50e5bab1a7e0dab2` |
| HTTP local patch commit | `6f6e7d80e58d689b5f33a08af26c428739f96642` |
| HTTP patched tree | `21ac01a388175e61614d624107f5f870b635f23f` |
| Rust application | `6dae2fd3c1a2dce61ca11beb731bcce2b42b67ad` |
| Jai published image tag | `2ecd487` |
| Shared verifier | `ec02debb3e44f40b8778a6a42db3c4515b2f9eaf` |

Current Rust and Jai source heads and published image digests were rechecked. Jai main
`b0952a20541fadda3ac0eb7991e9d75acca25799` is documentation-only after the published tag.

```text
Goose image: sha256:6f601aff4d5d42fb05abfb7f3ff5b523e72de740f702a64e6bb0415e461d10f2
Goose executable: ab199b3ddb96c7dd0a260b9d07d50313ca7754beb5fc1680b55685c0a316d79a
Baseline image: sha256:81c9d6cfa78a20df7b0bcec8327bcd4c70fcf6d4faf4a17b3fb9e6a2b4841a45
Baseline executable: 4272e14810ef82104b617acc90fb6e41dca9487312820c26c6dcf6d0d4b104a3
Rust image: sha256:98ba156f688c3fe856afd71655f613a6620bc03754f456ed2ef08fb2e5527e6e
Jai image: sha256:bbbe5c642001c653906dbe4d6f6d584090b82b352724c77d5f134a081506829e
Load generator: f4e5801a7b5ff6ba1a1b8919a007f7d409e79c2f88433299c944c424aa24aecb
Fixture database: 378b2275a197092b31aec7295b0a5c2fbfc24af94d3001ddd095c3b08832c251
HTTP patch: 440c300443c0b2a5c88a15e05aea3769c1188e846252f8e2a5a7f50b7cdf2303
```

Use the [shared reproduction commands](benchmarks.md#reproduce-on-linux) with the source revision
above. The existing `benchmark_shared.py` command runs both baseline and mixed profiles.
Raw receipts, local A/B harnesses, host logs and browser artifacts remain ignored under
`build/send-copy-comparison`, `build/send-copy-cost`, `build/send-copy-cost-long`,
`build/browser-send-copy`, and `build/send-copy-render-*`. No fixture credentials are published.
