# HTTP dependency history

The application uses unpatched goose-http at
`75f0362919dd7140bc672f4cbecfb69033c500fc`, including the merged
[drain and fairness PR #2](https://github.com/kaitk/goose-http/pull/2), the native
io_uring send-allocation fix, and goose-compress v0.1.3. Bootstrap verifies the
immutable revision and rejects tracked dependency modifications.

The send-copy patch from [PR #3](https://github.com/kaitk/goose-http/pull/3) was
retired on 2026-10-23. The maintainer measured a 13-28% server-bound throughput
loss and reproduced the apparent gain only with a saturated client. Copying the
baseline response twice had no measurable cost. The application no longer applies
that patch; its source, tests and measurements remain in Git history and the PR.

The [review report](../docs/send-copy-pr.md) records the evidence, historical
validation, and native io_uring requirements. Earlier Rust/Jai comparisons describe
their pinned experimental builds, not the current unpatched application.
