# Pinned HTTP send optimization

HTTP is pinned to upstream `ebb4238728179e82140550af50e5bab1a7e0dab2`, which
includes the merged [drain and fairness PR](https://github.com/kaitk/goose-http/pull/2).
The previous drain patch is no longer needed.

`goose-http-send-copy.patch` is a local experiment against that revision.
`dependencies.json` pins its SHA-256 and resulting Git tree. Bootstrap accepts
only that exact tree and rejects changes to patched files.

The readiness-based ring emulation attempts a synchronous send on a writable
socket with no preceding queued send. It copies only the unsent tail into owned
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
