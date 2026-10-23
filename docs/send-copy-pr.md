# Send-copy upstream review

[Upstream PR #3](https://github.com/kaitk/goose-http/pull/3) contains the optimization,
deterministic tests, individual benchmark samples and package reproduction commands.
It is a draft because a plaintext throughput difference remains unresolved.

The application continues carrying a checksum/tree-pinned functional patch through
`a12328478b48efd9ea6adb2038e3f185fc130651` on upstream
`df12a627c9f4df51465f1ac7c324c17e86dc78b3`. Keep it until the upstream merge is
incorporated into the dependency lock. The PR's additional benchmark documentation
does not change the functional patch. Nothing has been landed on Goose master.

## Size threshold and remaining concern

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

## Current application A/B

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

| Median metric | Before | Current candidate | Change |
|---|---:|---:|---:|
| User-mode cycles/response | 9,218.6 | 8,351.7 | -9.4% |
| Total server CPU microseconds/response | 8.454 | 8.115 | -4.0% |
| Valid responses/second | 213,817 | 230,004 | +7.6% |

User-cycle cost improves in all five pairs, total CPU time in three, and throughput
in four. Before throughput spans 205,543-236,734; after spans 220,747-251,441. The
ranges overlap, and one pair loses 2.8% throughput. The strongest evidence is the
repeated user-cycle reduction for this workload. The small-response tradeoff remains.

The exact candidate passes the full native application test image, both HTTP backends,
shared Chromium smoke suite, 27 differential rendering cases against each reference,
and all 315 asset checks. It retains tests for rollback, concurrent acknowledged
writes, session/room revocation, WAL restart, fragmented fan-out and disconnect cleanup.
The HTTP package passes sanitizers, all 60 tests at both optimization levels, and
32 MiB stalled-reader delivery, disconnected-slot reuse and publisher fairness.

Current Rust/Jai throughput was not rerun for this candidate. The
[earlier full comparison](send-copy.md) explicitly identifies its all-size prototype.

## Provenance

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
