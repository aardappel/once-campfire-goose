# Campfire in Goose

An experimental Campfire implementation in [Goose](https://github.com/aardappel/goose).
The implemented HTTP routes pass the shared response contracts and persisted-write audits.
Rendering matches 27 differential cases against the pinned Rust and Jai references.
Full application/browser compatibility is in progress; live updates, editing, administration,
and uploads are not implemented yet. This is not ready to replace a production Campfire installation.

## Build and test

Docker builds the public Goose compiler and packages at the immutable revisions in
`dependencies.json`. The 315 frontend files are vendored and SHA-256 checked against
`views/assets.json`; building never starts a reference server or needs a fixture database.

```sh
docker build --target test -t once-campfire-goose:test .
docker build --build-arg GIT_REVISION="$(git rev-parse HEAD)" -t once-campfire-goose:app .
```

For a Linux development build with Clang, CMake, Ninja, Python 3, Git, liburing-dev,
libssl-dev and libcrypt-dev installed, run `sh build_linux.sh`.
Build products and dependency checkouts stay under ignored `build/`.

## Run against disposable existing Campfire storage

```sh
docker run --rm -p 3000:3000 --env-file campfire.env \
  -v /absolute/path/to/storage:/rails/storage once-campfire-goose:app
```

The environment file supplies `SECRET_KEY_BASE` matching that database's Rails signing secret.
The database is `/rails/storage/db/production.sqlite3`; blob files are in `/rails/storage/files`.
Storage must be writable by UID 1000. The current server requires an existing schema and account.
`HTTP_PORT` defaults to 3000 in the image, `CAMPFIRE_HTTP_THREADS` to 4, and `CAMPFIRE_CACHE=0`
disables response caching. `DATABASE_PATH` and `STORAGE_PATH` override storage locations.
TLS should terminate at a reverse proxy; application deployment compatibility is still in progress.

## Verification and provenance

`test_server.py`, native unit tests, and the differential rendering tests are preserved from
the experiment. Historical results and limitations are in [docs/experiment.md](docs/experiment.md).
Those measurements used Jai `e56ba63`, not its newer `2ecd487`; no current superiority claim is made.
The next comparison will use the packaged image and the official shared runner.

Application source was extracted from `aardappel/goose` revision
`9bbf9446` (`bench/campfire`) onto the experimental branch. The Goose compiler remains a separate dependency.
Application code retains the Apache 2.0 license. Templates/frontend assets retain their upstream
MIT license and embedded notices; see [THIRD_PARTY.md](THIRD_PARTY.md).
