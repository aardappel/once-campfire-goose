# HTTP dependency history

The application pins goose-http at
`555a723adc103a455ccc4cdf43bd4a9a96608a70` (v0.5.11), including the merged
[drain and fairness PR #2](https://github.com/kaitk/goose-http/pull/2), the native
io_uring send-allocation fix, lazy WebSocket reservations, and goose-compress v0.1.4. Bootstrap verifies the
immutable revision, patch SHA-256 and resulting Git tree, rejecting any other tracked
dependency modifications.

`goose-http-worker-dispatch.patch` adds a bounded multi-recipient mailbox envelope
and dispatches it on the owning HTTP worker in both epoll and ring loops. One mailbox
slot holds one copy of the chunk plus up to 64 generation-bearing recipient keys.
The box generation and shutdown checks protect enqueueing; the existing loop checks
each exchange before delivery. A successful enqueue does not assert every recipient
is still live. The application retains per-reader drain credits, liveness checks and
ordered publication retirement. The patch is local to this experimental repository
and is not an upstream release. `worker_mail_test.goose` covers capacity, full queues,
stale keys, mixed boxes, generation reuse, shutdown and ordinary delivery. The socket
tests cover multi-subscription delivery and stalled readers in all supported backends.

The send-copy patch from [PR #3](https://github.com/kaitk/goose-http/pull/3) was
retired on 2026-10-23. The maintainer measured a 13-28% server-bound throughput
loss and reproduced the apparent gain only with a saturated client. Copying the
baseline response twice had no measurable cost. The application no longer applies
that patch; its source, tests and measurements remain in Git history and the PR.

The [review report](../docs/send-copy-pr.md) records the evidence, historical
validation, and native io_uring requirements. Earlier Rust/Jai comparisons describe
their pinned experimental builds, not this worker-dispatch experiment.
