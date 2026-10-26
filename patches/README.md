# HTTP dependency history

The application uses unpatched goose-http at
`555a723adc103a455ccc4cdf43bd4a9a96608a70` (v0.5.11), including the merged
[drain and fairness PR #2](https://github.com/kaitk/goose-http/pull/2), the native
io_uring send-allocation fix, lazy WebSocket reservations, and goose-compress v0.1.4. Bootstrap verifies the
immutable revision and rejects tracked dependency modifications.

The worker-dispatch experiment in commit `0081410` added one shared mailbox payload
for up to 64 readers on the same worker. Its tests passed, but its median complete
fan-out rate was 1,756 messages/s versus the existing pipeline's 1,767 in the matched
comparison. The application no longer carries that extension. Its pinned patch,
mailbox tests and source remain in Git history; the [measurement report](../docs/worker-dispatch.md)
records the experiment and validation. The expanded socket-pressure tests remain
in the normal suite and run against unpatched upstream goose-http.

The send-copy patch from [PR #3](https://github.com/kaitk/goose-http/pull/3) was
retired on 2026-10-23. The maintainer measured a 13-28% server-bound throughput
loss and reproduced the apparent gain only with a saturated client. Copying the
baseline response twice had no measurable cost. The application no longer applies
that patch; its source, tests and measurements remain in Git history and the PR.

The [review report](../docs/send-copy-pr.md) records the evidence, historical
validation, and native io_uring requirements. Earlier Rust/Jai comparisons describe
their pinned experimental builds, not the current unpatched application.
