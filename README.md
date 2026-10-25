# Campfire in Goose

An experimental Campfire implementation in [Goose](https://github.com/aardappel/goose).
The implemented HTTP routes pass the shared response contracts and persisted-write audits.
Rendering matches 27 differential cases against the pinned Rust and Jai references.
Fresh setup, invitations, live messaging, edits, profile/account forms, rooms, bots, session
transfers, QR codes and direct-ping creation are implemented. The unmodified shared Chromium
smoke test covers these flows. Full application compatibility is still in progress; see
[browser coverage and delivery semantics](docs/browser-parity.md).

## Build and test

Docker builds the public Goose compiler and packages at the immutable revisions in
`dependencies.json`. The 315 frontend files are vendored and SHA-256 checked against
`views/assets.json`; building never starts a reference server or needs a fixture database.

```sh
docker build --target test -t once-campfire-goose:test .
docker build --build-arg GIT_REVISION="$(git rev-parse HEAD)" -t once-campfire-goose:app .
python3 test_image.py once-campfire-goose:app
python3 test_browser.py once-campfire-goose:app
```

For a Linux development build with Clang, CMake, Ninja, Python 3, Git, liburing-dev,
libssl-dev, libcrypt-dev and libqrencode-dev installed, run `sh build_linux.sh`.
Build products and dependency checkouts stay under ignored `build/`.

## Run against disposable Campfire storage

```sh
docker run --rm -p 3000:3000 --env-file campfire.env \
  -v /absolute/path/to/storage:/rails/storage once-campfire-goose:app
```

The environment file supplies `SECRET_KEY_BASE` matching that database's Rails signing secret.
The database is `/rails/storage/db/production.sqlite3`; blob files are in `/rails/storage/files`.
Storage must be writable by UID 1000. An empty database is initialized from the pinned schema;
visit `/first_run` to create the account and administrator. Existing Campfire databases are preserved.
`HTTP_PORT` defaults to 3000 in the image, `CAMPFIRE_HTTP_THREADS` to 4, and `CAMPFIRE_CACHE=0`
disables response caching. `DATABASE_PATH` and `STORAGE_PATH` override storage locations.
`CAMPFIRE_HTTP_BACKEND` defaults to `epoll`, the direct Linux loop usable in ordinary Docker.
`auto` probes for native io_uring and selects epoll when unavailable. `uring` requires native
io_uring and refuses emulation; `emulated` explicitly selects the ring emulation for comparisons.
The selected backend is logged at startup. For `uring` on Linux 6.1 or later, generate and pass the optional
[container seccomp profile](docs/send-copy-pr.md#local-native-io_uring-setup).
TLS should terminate at a reverse proxy; application deployment compatibility is still in progress.

## Pinned HTTP dependency

The build pins upstream goose-http including the merged
[drain notification PR #2](https://github.com/kaitk/goose-http/pull/2).
The application uses these credits to bound WebSocket output when readers stall.
The dependency is unpatched. The send-copy experiment in
[PR #3](https://github.com/kaitk/goose-http/pull/3) was retired after review found a
13-28% server-bound throughput loss. The current pin includes upstream's native-ring
allocation fix, lazy WebSocket reservations, precise timers, and goose-compress v0.1.4.
The immutable HTTP pin is v0.5.11; HTTP/3 is disabled in this application.
See the [dependency history](patches/README.md) and [reproducer](docs/websocket-backpressure.md).

## Verification and provenance

`test_server.py`, native unit tests, and the differential rendering tests are preserved from
the experiment. Historical results and limitations are in [docs/experiment.md](docs/experiment.md).
The [current publication-pipeline comparison](docs/publication-pipeline.md) uses packaged images, current
Rust/Jai references, the unchanged shared load generator and validators, and audited writes.
It records throughput, memory, exact versions, host activity, and workload limits.
See [cache validation](docs/read-cache.md) for the read path and the historical
[backend comparison](docs/backend-selection.md) for epoll versus ring modes.

On 2026-10-25, Goose with epoll reaches medians of 382.4k room reads/s and
6.0k posts/s without subscribers, versus Jai's 314.8k and 4.2k.
Bounded publication pipelining delivers 1,587 messages/s to all 64 readers,
a 41% increase over the serial broker's 1,125. Jai reaches
3,324 and Rust 2,236; the fan-out gap remains.
Anonymous memory after fan-out is 26.68 MiB, versus 26.75 before.
Rendering parity, bounded admission and drain acknowledgement tests pass. The report
preserves individual samples, host activity and compatibility limits; these are
workload-specific application results, not a general language-speed claim.

The [send-copy measurements](docs/send-copy.md) and
[upstream review](docs/send-copy-pr.md) remain as historical evidence for the retired
experiment. Their rates use a different kernel and workload and do not measure
the current application.

Application source was extracted from `aardappel/goose` revision
`9bbf9446` (`bench/campfire`) onto the experimental branch. The Goose compiler remains a separate dependency.
Application code retains the Apache 2.0 license. Templates/frontend assets retain their upstream
MIT license and embedded notices; see [THIRD_PARTY.md](THIRD_PARTY.md).
