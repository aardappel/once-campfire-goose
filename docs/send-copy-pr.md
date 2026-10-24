# Send-copy upstream review

[Upstream PR #3](https://github.com/kaitk/goose-http/pull/3) is retired on 2026-10-23. The
[2026-10-23 review](https://github.com/kaitk/goose-http/pull/3#issuecomment-6371183040)
finds a 13-28% throughput loss on server-bound 21 KiB responses. Its double-copy
control has no measurable cost, and it reproduces the Campfire-style gain only
when the client is saturated. The evidence does not support merging this send path.

At retirement, the application moved to unpatched upstream
`75f0362919dd7140bc672f4cbecfb69033c500fc`. The dependency lock no longer applies
the send-copy patch. That pin retained goose-compress v0.1.3 and the native io_uring
allocation fix. The current HTTP/compression pins and epoll/native measurements
are in the [backend comparison](backend-selection.md). The
retired implementation and its tests remain in Git history and the PR. Nothing
has been landed on Goose master.

## Local native io_uring setup

On 2026-10-23, WSL was updated from 2.4.13.0 to 3.0.3. After an authorized restart,
both the host and Docker report Linux `6.18.54.1-microsoft-standard-WSL2`.
Docker remains 29.1.3. A disposable liburing probe now accepts both a basic ring
and goose-http's required flags under the opt-in profile described below. The
default Docker profile still returns EPERM.

Before the update, the same probe on Linux 5.15.167.4 gave these results:

| Container policy | Basic ring | goose-http setup flags |
|---|---|---|
| Docker default | EPERM | EPERM |
| Seccomp disabled for this probe | Available | EINVAL |

goose-http requires Linux 6.1 or later for multishot receive, provided buffer rings,
and deferred task running. Its flags are `SINGLE_ISSUER`, `DEFER_TASKRUN`, and
`SUBMIT_ALL`. Allowing syscalls cannot supply the missing kernel features.

Generate the optional container policy from the repository root:

```sh
python3 scripts/io_uring_profile.py
docker run --rm --security-opt seccomp=build/seccomp-io-uring.json \
  -p 3000:3000 --env-file campfire.env -e CAMPFIRE_HTTP_BACKEND=uring \
  -v /absolute/path/to/storage:/rails/storage once-campfire-goose:app
```

The generator fetches Moby's official default profile at
`4b8214fc4971e06172938a1f1554ccff6647ee80`, verifies SHA-256
`6416b47770785a41ac59073cdc77d9fe98517df2799dc83ef207e622de3053f6`, and adds
one allow rule for `io_uring_setup`, `io_uring_enter`, and `io_uring_register`.
All other rules and the default deny action come from that pinned Moby profile.
This is a per-container option; it does not alter Docker's daemon configuration.
The unrestricted probe above was diagnostic, not the deployment configuration.

The default is now `CAMPFIRE_HTTP_BACKEND=epoll`. `auto` probes native io_uring and
chooses direct epoll when unavailable. `uring` requires native support, while
`emulated` explicitly requests the ring emulation. The optional profile alone
does not change the application's default backend.

Before timing native io_uring, confirm the startup log selects it and run the
delivery, acknowledged-write and browser tests. Compare against two independent
base builds, increase client capacity until server saturation is demonstrated, and
record total server CPU per validated response. A kernel change requires rerunning
Rust and Jai on the same machine; historical numbers are not comparable to a
new-kernel run. Sources: [HTTP shim requirements](https://github.com/kaitk/goose-http/blob/75f0362919dd7140bc672f4cbecfb69033c500fc/shim/uring.c),
[Docker seccomp](https://docs.docker.com/engine/security/seccomp/), and
[WSL update commands](https://learn.microsoft.com/en-us/windows/wsl/basic-commands#update-wsl).

## Unpatched application validation after the kernel update

This validation used pristine HTTP revision `75f0362919dd7140bc672f4cbecfb69033c500fc`;
the built dependency checkout has no tracked changes. The full test-image matrix
passes under Docker's default policy, exercising the emulated ring and direct epoll.
Under the restricted io_uring profile, setup, authentication, caching on/off,
revocation/WAL recovery, rollback, 32 concurrent acknowledged writes, fragmented
200 KiB WebSocket delivery, two-reader fan-out and disconnect cleanup also pass.

The production image passes smoke checks with both policies. With the io_uring
profile, the unchanged Chromium suite passes, as do all 27 differential rendering
cases against each pinned Rust/Jai reference and all 315 asset checks. A separate
production-process probe confirms four `anon_inode:[io_uring]` descriptors and a
successful HTTP response. Native operation is confirmed, not inferred from `auto`.

Unpatched upstream's native allocation-failure fixture runs successfully rather
than skipping. Native io_uring and direct epoll both pass 32 MiB ordered delivery,
stalled-reader recovery, disconnected-slot reuse, fairness and active deadline tests.
No throughput comparison has been rerun on the new kernel.

```text
Application source: 217ec3a2558f049fa2624417805a46fcb8366fc6
Image: sha256:777866f3758ece0af9b41db39022d2ce7b6b669a4f16c792111d856d74445c26
Executable SHA-256: 18c877f7c9ed3211b791aa46ce982213b445c61d2abb3474dd748faa6a72c729
WSL: 3.0.3.0
Kernel: 6.18.54.1-microsoft-standard-WSL2
Docker: 29.1.3
```

Raw records remain ignored under `build/unpatched-*`, `build/browser-unpatched-native`
and `build/native-http-tests.log`. The browser/rendering checks use a test-only Docker
launcher that supplies the profile for the Goose image; reference images and shared
validators remain unchanged. The earlier patched image is retained under
`once-campfire-goose:retired-send-copy` for provenance.

## Historical patched rebase validation

GCC address/undefined-behavior sanitizers and all 60 HTTP package tests at `-O0` and
`-O2` pass with official Goose v2026.3 and Clang 18.1.3. Both Linux loops pass the
32 MiB stalled-reader test, disconnect reuse, publisher fairness and active deadline
checks. The native io_uring fixture explicitly skips on this host. These checks do
not establish a performance benefit. The application test image passes its full
native matrix: setup/authentication, both HTTP backends with caching on and off,
rollback and 32 concurrent acknowledged writes, revocation/WAL recovery, fragmented
WebSocket delivery and disconnect cleanup, renderer units and compiler reproducers.
The production-image smoke test, unmodified shared Chromium suite, 27 differential
rendering cases against each pinned Rust/Jai reference, and all 315 asset checks
also pass. No performance benchmark is rerun for the new dependency pins.

The validated application source is `4cbb4e61ea4c149488224e3c8c07bff92dc803d4`.
The image is `sha256:13f474fd95899885e41e5cb9f31e5f7cd0defd7e8259ec3855450f7a295319b7`;
its executable SHA-256 is
`857a5def236ee3d61f6be8b190a0efe671852cfcbce7ab888a32f92f22d7932d`.
The functional patch SHA-256 is
`396a9d66f0fa2b1c98951890ff65dee1c4dec7b1348fe4f0dbdbcb0f8652f271`, producing tree
`7f5f5399cddf0752813d7efeb4218de87c03cea2`. Raw logs stay ignored under
`build/send-review-*` and `build/browser-send-review`. The
[PR reply](https://github.com/kaitk/goose-http/pull/3#issuecomment-6373693810)
records the fix, the review outcome and the local blocker.

## Historical size-threshold concern

Pre-submission testing found that the original all-size fast path loses 25.5% median
throughput on heavily pipelined plaintext. The submitted candidate keeps sends below
16 KiB queued. Large sends still try socket acceptance before copying their unsent
tail, and all completions remain deferred through the existing drain accounting.

The 16 KiB version's plaintext instruction cost is within 0.03% of upstream and
user-cycle cost within 0.3%, but its six-sample throughput median remains 8.5% lower.
Ranges overlap; that does not establish equivalent performance. The PR records this
as an open question before landing, rather than claiming a general HTTP speedup.

The package test uses official Goose v2026.3, Clang 18.1.3, one worker on CPU 0,
h2load on CPUs 4-15 with eight threads, 256 connections and 16 outstanding requests
per connection. Each sample checks ten million plaintext responses as 2xx. Three
before/after pairs and three reversed-order pairs run without competing builds or
observed host processes averaging above 0.3 cores. Both versions use the emulated ring.

## Historical thresholded application A/B

Measured on October 22, 2026, around 23:20-23:25 PDT, on the Ryzen 9 9950X3D,
Docker/WSL2 Linux 5.15.167.4 and glibc 2.39. Five pairs alternate the preserved
`af65824` application and the thresholded `aea9508` candidate. Each validates three
million room reads after two seconds of warmup, on fresh fixture storage. All
30 million measured responses pass with zero errors.

Four server cores (`0,2,4,6`), four separate client cores (`8,10,12,14`), four HTTP
workers, 16 connections, gzip and a 2 GiB memory limit match the earlier A/B. The
unchanged shared validator checks the roughly 20.9 kB compressed room response.
`perf stat -e instructions:u,cycles:u,task-clock -p 1 -- sleep 25` covers the whole
fixed request batch, including idle time around it. No compiler jobs run during
timing; brief browser activity is retained in the host logs. The generator remains
near its CPU limit, so this is not a maximum-capacity measurement.

| Median metric | Before | Thresholded candidate | Change |
|---|---:|---:|---:|
| User-mode cycles/response | 9,218.6 | 8,351.7 | -9.4% |
| Total server CPU microseconds/response | 8.454 | 8.115 | -4.0% |
| Valid responses/second | 213,817 | 230,004 | +7.6% |

User-cycle cost improves in all five pairs, total CPU time in three, and throughput
in four. Before throughput spans 205,543-236,734; after spans 220,747-251,441. The
ranges overlap, and one pair loses 2.8% throughput. The user-cycle reduction describes
this client-limited setup; the later server-bound review overturns the case for
treating it as a general transport optimization.

The exact candidate passes the full native application test image, both HTTP backends,
shared Chromium smoke suite, 27 differential rendering cases against each reference,
and all 315 asset checks. It retains tests for rollback, concurrent acknowledged
writes, session/room revocation, WAL restart, fragmented fan-out and disconnect cleanup.
The HTTP package passes sanitizers, all 60 tests at both optimization levels, and
32 MiB stalled-reader delivery, disconnected-slot reuse and publisher fairness.

Rust/Jai throughput was not rerun for this thresholded candidate. The
[earlier full comparison](send-copy.md) explicitly identifies its all-size prototype.

## Historical A/B provenance

```text
Application: aea9508ac3f3eadd5925f22d64e712ff8fa3a757
Image: sha256:a1afe83bc1627fd6e218e84d8f81b023113ff601dcbb0a855415d97e7166a7d5
Executable: 9c42349e82d4023b81345695abe62ca94b40995fd4588a4b319e9ce41ed259e7
HTTP functional commit: a12328478b48efd9ea6adb2038e3f185fc130651
Patched tree: 332f8a5b08194a88c9aecc166a513bbc91f0da31
Patch SHA-256: 77a2998b4ade0ae9f185f0301e929ca8748121007c4bc998d7e31fa20fcc5ac5
```

The compiler, baseline executable, fixture and load-generator hashes are unchanged
from the earlier report. Public numeric samples, methodology, adverse observations
and package commands are in the
[PR's measurement record](https://github.com/aardappel/goose-http/blob/efb2db571e8b3aad426112dce1ffd861f6ebfdb6/bench/results/2026-10-22-campfire-send-copy/summary.md).
Local raw artifacts remain ignored under `build/send-threshold-cost-long`,
`build/browser-send-threshold`, `build/send-threshold-render-*`, and the corresponding
host/test logs. No fixture credentials are published.
