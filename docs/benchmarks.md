# Current Rust/Jai comparison

Measured on 2026-10-22 using the packaged Goose application and current published
Rust and Jai images. The older Jai `e56ba63` comparison in [the experiment log](experiment.md)
is historical and does not establish a 2x lead over current Jai.

## Results

Goose leads current Jai on the observed medians: 5-9% on the four cached-read routes,
4% on reads alongside a paced writer, and 61% on posts. Against Rust, room reads are
7.26x and posts 2.06x. This is a narrower lead over Jai than the historical comparison.

Valid requests/second, median of three rounds; parentheses show the lowest and highest
round, not a confidence interval. Measurement ran from 23:32:22 to 23:42:33 UTC.

| Route | Goose | Rust | Jai |
|---|---:|---:|---:|
| Room | 208,102 (202,864-218,122) | 28,655 (27,851-28,940) | 190,998 (185,664-193,029) |
| Older messages | 208,953 (207,771-213,554) | 28,171 (27,764-28,179) | 198,163 (194,816-198,910) |
| Sidebar | 217,529 (213,354-232,134) | 30,214 (29,877-30,581) | 201,882 (200,307-203,021) |
| Search | 210,045 (204,979-219,156) | 29,828 (29,796-30,586) | 200,780 (195,612-201,157) |
| Post message | 8,505 (8,198-8,914) | 4,129 (3,819-4,145) | 5,298 (5,265-5,350) |
| Room + 10 posts/s | 196,776 (195,752-214,377) | 27,544 (26,174-27,554) | 189,051 (187,245-189,941) |

Median anonymous-memory samples after each route, in MiB:

| Sample | Goose | Rust | Jai |
|---|---:|---:|---:|
| Room reads | 23.61 | 22.77 | 45.97 |
| Message posts | 28.32 | 92.01 | 104.96 |
| Room reads + paced writes | 24.59 | 73.94 | 87.75 |

Goose is close to Rust's memory use on room reads and substantially lower after writes.
The median of the per-round POST p99 latencies is 6.17 ms for Goose, 9.04 ms for Rust,
and 7.52 ms for Jai. Room-read p99 is approximately 0.190, 1.177, and 0.186 ms,
respectively; higher Goose throughput does not imply lower latency on every route.

The generator uses about 3.70 CPU cores for Goose room reads and 3.75 for Jai, out of
four allocated client cores; the servers use about 1.77 and 1.94 cores. This indicates
limited generator headroom, so these results should not be extrapolated to maximum
server throughput. Rust uses about 3.31 server cores and 1.15 generator cores there.

All timed responses passed their route contracts, with zero errors or invalid responses.
The completed audit totals are:

| Application | Valid timed responses, including mixed writes | Audited acknowledged writes, including warmup |
|---|---:|---:|
| Goose | 25,515,588 | 248,817 |
| Rust | 3,551,190 | 117,157 |
| Jai | 23,574,648 | 159,474 |

## What was validated

The measured production image passes the unchanged shared Chromium smoke suite, including
setup, two-tab live messaging, duplicate suppression, stored-markup safety, editing, search,
profile/account forms, rooms, bots, QR codes, session transfers, invitations, and direct pings.
It also passes 27 differential rendering cases against each reference and verifies all
315 static asset hashes and MIME types. Native tests cover both HTTP backends, cache on/off,
cookie expiry, external session/room revocation, rollback, WAL restart, concurrent writes,
fragmented WebSocket delivery, and disconnect cleanup.

Each benchmark instance first passes all eight shared HTTP contracts, including real avatar
decoding and a persisted POST. Every timed response must be HTTP 200 and satisfy the shared
route contract. Every acknowledged write, including warmup writes, is audited for its
database row, rich-text body, and FTS entry. An invalid response or unverified write fails
the run. The source seed and Goose executable checksums are checked again at completion.

These checks establish the listed flows, not complete Campfire compatibility. Upload
creation, boost mutation, push/webhook delivery, full typing/read events, and active
WebSocket revocation remain among the [documented gaps](browser-parity.md).

## Method and limits

- AMD Ryzen 9 9950X3D, x86-64 Docker on WSL2 Linux `5.15.167.4`, glibc 2.39.
- Four physical server cores (`0,2,4,6`), four separate client cores (`8,10,12,14`),
  2 GiB server memory limit. Goose uses four HTTP workers and the automatic backend.
- Three rounds rotate application order: Goose/Rust/Jai, Rust/Jai/Goose, Jai/Goose/Rust.
  Each route has two seconds of warmup at four clients, then eight seconds at 16 clients.
  Requests accept gzip. No compiler builds run during measurement.
- Every application/profile/round starts with its own database copied from the same
  Rails-generated fixture on native Linux storage. Timed POSTs are plain-text messages.
  Fixture push/webhook endpoints point to loopback port 9, with no external delivery.
- The mixed profile reads Watercooler while posting to HQ at ten messages/second. It
  measures cache invalidation under writes to another room, not same-room delivery latency.
- Repeated requests use the same authenticated session and target, strongly favoring
  response caches. This is a fixed-load HTTP comparison, not a maximum-capacity estimate,
  a concurrent-user count, or a language-wide performance verdict.
- No WebSocket subscribers are connected during throughput measurement. Browser and
  bounded-delivery tests run separately; the numbers do not measure fan-out throughput
  or the cost of all production side effects.
- Memory is the cgroup anonymous-memory sample after each route, not peak memory or
  total RSS. It excludes file-backed cache. Write samples follow the same timed workload,
  not an equal number of stored messages.

`benchmark_shared.py` orchestrates production images using the unchanged shared load
generator, response validator, contracts, and write auditor. Raw receipts, fixtures,
credentials, and browser artifacts remain ignored; this report publishes concise tables.

## Why this differs from the earlier experiment

Current Jai's first complete comparison led Goose on cached reads: 188,601 versus 161,314
room requests/second. That justified more work. Goose now avoids formatted cache-key
allocations and certifies cached snapshots through SQLite's WAL header protocol, while
retaining the ordinary transaction/authentication path whenever the certificate is absent
or changes. Cache tests include external revocation and WAL restart. See
[read-cache validation](read-cache.md) for the protocol and the small C adapter it requires.

Cached gzip bodies use level 6: the isolated room encoding shrank from 28,522 to 20,877 bytes.
The additional encoding time was about 0.15 ms per cache fill in that probe. POST compression
is unchanged. A short automatic-versus-direct-epoll comparison showed no meaningful winner,
so automatic selection remains the default. An exploratory run with eight client cores
changed generator behavior; it is excluded from this report's four-client-core comparison.

The [HTTP patch](../patches/README.md) adds bounded producer flow control and includes the
reviewed epoll fairness fix. Its separate plain-HTTP instruction-count check measured
less than 0.02% overhead on both tested loop paths. Native io_uring, macOS, and Windows
drain integration remain upstream release checks.

## Immutable inputs

| Input | Revision |
|---|---|
| Goose application | `2399a6bd22df33ecae0b751525f89859fd2d023a` |
| Goose compiler | `d9573d8139de395cd4549b876c176c25eb878fc1` |
| Rust application | `6dae2fd3c1a2dce61ca11beb731bcce2b42b67ad` |
| Jai published image tag | `2ecd487` |
| Shared verifier | `ec02debb3e44f40b8778a6a42db3c4515b2f9eaf` |
| Rails fixture source | `90b330024dec3e757c79b6a7e6568f93da8e3148` |

Rust's image revision matches current source main. Jai's current source main is
`b0952a20541fadda3ac0eb7991e9d75acca25799`; its changes after the published `2ecd487`
are documentation only. Jai's image does not carry a revision label, so its association
with that tag comes from the published image and [upstream submission](https://github.com/basecamp/once-campfire-verification/pull/11).
The digest below is the exact artifact measured. References were rechecked on 2026-10-22.

| Artifact | SHA-256 |
|---|---|
| Goose local image ID | `7d279f50d36a1719b088f33edcc80b13abec04ad6736f38a3fbd79253189083c` |
| Goose executable | `27dddc1ea704c640ae0e0d8daaafd1d60699e1a6ae3ef14f6ecce3da90061af0` |
| Rust GHCR image digest | `98ba156f688c3fe856afd71655f613a6620bc03754f456ed2ef08fb2e5527e6e` |
| Jai GHCR image digest | `bbbe5c642001c653906dbe4d6f6d584090b82b352724c77d5f134a081506829e` |
| Shared load generator | `f4e5801a7b5ff6ba1a1b8919a007f7d409e79c2f88433299c944c424aa24aecb` |
| Shared validator | `554f9415178dc067c120ce0f9363ddec93007815334305f72d02290e9ff93c3b` |
| Shared contracts | `9bb1afd6ba16b49cdfcc84d32a3701abb10435721d018b07897a4840c2751755` |
| Shared write auditor | `a1cdf6cf005bf20714ccb7fe14e9cb48b0ae0aa05baed1a48f8bc26b17c91387` |
| Seed database | `378b2275a197092b31aec7295b0a5c2fbfc24af94d3001ddd095c3b08832c251` |

Package revisions, patch checksum/resulting tree, and SQLite source are pinned in
[`dependencies.json`](../dependencies.json). The browser's Playwright image is pinned
in `test_browser.py`. Source and verifier checkouts were clean when measurement started.

## Reproduce on Linux

The app builds with Docker. The measurement host also needs Python 3.11+, Ruby, SQLite CLI,
FFmpeg, Git, `taskset`, and the shared verifier's Rust toolchain (1.98.1 in its README).
Use native Linux storage for the fixture and runtime copies. From this repository:

```sh
git clone https://github.com/basecamp/once-campfire-verification.git build/verification
git -C build/verification checkout ec02debb3e44f40b8778a6a42db3c4515b2f9eaf
cargo build --release --locked --manifest-path build/verification/loadgen/Cargo.toml
build/verification/bin/seed --output /tmp/campfire-shared-seed

docker pull ghcr.io/basecamp/once-campfire-rust@sha256:98ba156f688c3fe856afd71655f613a6620bc03754f456ed2ef08fb2e5527e6e
docker pull ghcr.io/lucasmeijer/once-campfire-jai@sha256:bbbe5c642001c653906dbe4d6f6d584090b82b352724c77d5f134a081506829e
docker build --build-arg GIT_REVISION="$(git rev-parse HEAD)" -t once-campfire-goose:app .
python3 test_image.py once-campfire-goose:app
python3 test_browser.py once-campfire-goose:app --verification "$PWD/build/verification"
python3 test_rendering.py --seed /tmp/campfire-shared-seed --runtime-root /tmp/campfire-bench \
  --output build/render-rust --reference rust --extended
python3 test_rendering.py --seed /tmp/campfire-shared-seed --runtime-root /tmp/campfire-bench \
  --output build/render-jai --reference jai --extended

python3 benchmark_shared.py --verification "$PWD/build/verification" \
  --seed /tmp/campfire-shared-seed --runtime-root /tmp/campfire-bench \
  --output build/comparison --client-cores 4 --goose-backend auto \
  --rust-image ghcr.io/basecamp/once-campfire-rust@sha256:98ba156f688c3fe856afd71655f613a6620bc03754f456ed2ef08fb2e5527e6e \
  --jai-image ghcr.io/lucasmeijer/once-campfire-jai@sha256:bbbe5c642001c653906dbe4d6f6d584090b82b352724c77d5f134a081506829e
```

The fixture builder generates fresh disposable credentials, so a new seed has a different
database hash. The runner records it. Use a new output directory for each run and keep the
tracked source clean. `test_rendering.py` uses the reference digests in
`reference_server.py`; the HTTP comparison above also selects explicit image digests.
