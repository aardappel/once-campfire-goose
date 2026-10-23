# Pinned HTTP send optimization

HTTP is pinned to upstream `df12a627c9f4df51465f1ac7c324c17e86dc78b3`, which
includes the merged [drain and fairness PR](https://github.com/kaitk/goose-http/pull/2).
The previous drain patch is no longer needed.

`goose-http-send-copy.patch` is local commit `a12328478b48efd9ea6adb2038e3f185fc130651`
against that revision, submitted as [draft PR #3](https://github.com/kaitk/goose-http/pull/3).
The PR also contains benchmark records; this pin carries its functional changes.
Retain the pinned patch until the upstream merge is incorporated into the dependency lock.
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

The application also passes the full native test matrix, shared Chromium suite,
27 rendering comparisons per reference, and all 315 asset checks. The completed
[review report](../docs/send-copy-pr.md) records the thresholded candidate's CPU
reduction and the remaining plaintext throughput concern. The earlier full Rust/Jai
comparison used the all-size prototype and remains separately identified.
