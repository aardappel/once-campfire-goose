# Pinned HTTP flow control

`goose-http-drain.patch` includes commits through `2a268f5b27c4f0aab37fc0decafa006cf6d9ae43`
against upstream `a7ce7d123d921a1f89520f8841f47caba68f1096`.
The upstream review is [kaitk/goose-http#2](https://github.com/kaitk/goose-http/pull/2).
`dependencies.json` pins the base revision, patch SHA-256, and resulting Git tree.
Bootstrap accepts only that exact tree and rejects changes to patched files.

The opt-in `after_drain`/`DRAINED` API lets an upgraded connection accept one
bounded output batch at a time. The producer retains the next batch until the
previous batch reaches the socket. It does not impose a hard cap on arbitrary
`ws_send` calls, and socket acceptance is not an acknowledgement from the client.

Validation on Linux x86-64 in Docker with Goose v2026.3: all 60 package tests
pass at `-O0` and `-O2`. The included mailbox publisher test stalls its reader
for two seconds, then verifies every byte and sequence number of 4,096 8 KiB
frames, and reconnects 16 times to check disconnected slots are reusable.
Both `serve()` (the Docker fallback loop) and `serve_epoll()` pass with no
measurable RSS increase during the stall. Native io_uring, macOS, and Windows
drain integration have not been measured here.

The review follow-up limits epoll to one drain callback per connection turn, using
its existing ready queue. A direct publisher regression confirms other sockets
remain responsive and the active publisher outlives its idle deadline without
`later`. The original epoll patch fails that fairness test; the original ring
already passes the deadline test because its callback accounting restores progress.
These integration tests now run from the package's Unix test runner.

On the Ryzen 9 9950X3D/WSL2 host, `bench/count.sh` with the official Goose v2026.3,
GCC 13.3.0, one worker, 256 pipelined connections, and three alternating runs of
10 million plain HTTP requests measured +0.1 user instructions/request for the
emulated ring and +0.3 for epoll (both below 0.02%). Exact settings and pending
native io_uring/macOS/Windows checks are recorded in the patch's task 063.
