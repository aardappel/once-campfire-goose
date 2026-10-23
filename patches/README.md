# Pinned HTTP send optimization

HTTP is pinned to upstream `75f0362919dd7140bc672f4cbecfb69033c500fc`, which
includes the merged [drain and fairness PR](https://github.com/kaitk/goose-http/pull/2),
the native io_uring send-allocation fix, and goose-compress v0.1.3.

`goose-http-send-copy.patch` contains the functional changes through
`65bbcf71f1a3f203def7e52ebf909f702a70d81f` against that revision, submitted as
[draft PR #3](https://github.com/kaitk/goose-http/pull/3). The PR also contains
benchmark records; this pin carries its functional changes. Both fixture close
calls pass the new receive tag argument.

The [2026-10-23 review](https://github.com/kaitk/goose-http/pull/3#issuecomment-6371183040)
finds a 13-28% server-bound throughput loss and reproduces the apparent gain only
with a saturated client. The patch is retained under the experiment's existing
pinning instruction, pending a decision to retire it. It is not recommended for
upstream merge. If retained and eventually merged, replace it with the immutable
upstream revision in the dependency lock.
`dependencies.json` pins its SHA-256 and resulting Git tree. Bootstrap accepts
only that exact tree and rejects changes to patched files.

The readiness-based ring emulation attempts a synchronous send of at least 16 KiB
on a writable socket with no preceding queued send. Smaller sends stay queued to
preserve pipelined throughput. It copies only the unsent tail into owned
storage before returning. Completions remain deferred until `ur_wait`, including
fully accepted sends, so the existing drain accounting and scheduling apply.
Native io_uring's 64 KiB threshold and the separate epoll loop are unchanged.

The patch includes deterministic C tests for full and partial sends, EAGAIN,
interrupted and failed sends, caller-buffer reuse, completion overflow, close,
allocation failure, and ordering behind sendfile. GCC address/undefined-behavior
sanitizers and Clang pass. All 60 package tests pass at `-O0` and `-O2` with Goose
v2026.3. Both Unix loops pass the 32 MiB stalled-reader delivery, disconnect reuse,
direct-publisher fairness and active-connection deadline tests on Linux/WSL2.
Native io_uring, macOS and Windows integration remain unmeasured here.

The [review report](../docs/send-copy-pr.md) separates the original measurements,
the maintainer's server-bound findings, and validation of the rebased candidate.
The earlier full Rust/Jai comparison used the all-size prototype and remains
separately identified. Neither comparison measures the current compression update.
