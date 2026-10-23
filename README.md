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
`CAMPFIRE_HTTP_BACKEND=auto` selects goose-http's native completion loop or its fallback;
`epoll` selects the direct Linux loop. Both backends run the HTTP and WebSocket integration tests.
TLS should terminate at a reverse proxy; application deployment compatibility is still in progress.

## Pinned HTTP patch

The build pins upstream goose-http including the merged
[drain notification PR #2](https://github.com/kaitk/goose-http/pull/2).
The application uses these credits to bound WebSocket output when readers stall.
A local checksum-pinned patch avoids copying bytes already accepted by a writable socket
on the emulated ring, retaining owned partial writes and deferred drain completions.
See the [patch pin](patches/README.md) and [reproducer](docs/websocket-backpressure.md).

## Verification and provenance

`test_server.py`, native unit tests, and the differential rendering tests are preserved from
the experiment. Historical results and limitations are in [docs/experiment.md](docs/experiment.md).
The [current comparison](docs/send-copy.md) uses packaged images, current Rust/Jai references,
the unchanged shared load generator and validators, and audited writes. It records throughput,
memory, exact versions, and workload limits. The old Jai `e56ba63` measurements do not describe
the current `2ecd487` comparison. See [cache validation](docs/read-cache.md) for the read path.

On the recorded four-core server allocation, Goose's three-round medians are 213k room
reads/s and 8.4k posts/s, versus current Jai's 187k and 5.3k, and Rust's 28.7k and 4.1k.
The cached-read medians are 11-16% above Jai and posts 60% above it. Mixed reads with writes
to another room reach 209k/s, versus Jai's 182k/s. Anonymous memory sampled after writes
is 28.5 MiB, versus 108.3 MiB for Jai and 93.1 MiB for Rust. A five-pair optimization A/B
reduced median room-read CPU time by 5.2%; its throughput ranges overlap despite a 9.4%
median increase. These HTTP workloads have no live subscribers and limited generator
headroom; they do not measure full application or broadcast capacity. The report includes
ranges, provenance, host activity and remaining compatibility work.

Application source was extracted from `aardappel/goose` revision
`9bbf9446` (`bench/campfire`) onto the experimental branch. The Goose compiler remains a separate dependency.
Application code retains the Apache 2.0 license. Templates/frontend assets retain their upstream
MIT license and embedded notices; see [THIRD_PARTY.md](THIRD_PARTY.md).
