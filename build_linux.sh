#!/bin/sh
set -eu
cd "$(dirname "$0")"
export GOOSE_HOME="${GOOSE_HOME:-$PWD/build/home}"
python3 scripts/bootstrap.py
python3 prepare.py
python3 compile_views.py
python3 compile_assets.py
compiler="$PWD/build/toolchain/build/goose"
"$compiler" check --offline -O2 repro/http_compatibility.goose
"$compiler" build --offline --cc clang -o build/campfire campfire.goose
if [ "${1:-}" != "--build-only" ]; then
    python3 test_setup.py build/campfire
    python3 test_server.py build/campfire
    python3 test_server.py build/campfire --no-cache
    for test in cache auth render; do
        "$compiler" build --offline --cc clang -o "build/${test}_test" "${test}_test.goose"
        "build/${test}_test"
    done
    python3 repro/check.py "$compiler"
fi
