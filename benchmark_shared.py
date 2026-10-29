#!/usr/bin/env python3
"""Compare Goose, Rust and published Jai images with the shared validator and write audit.

Run on Linux with Docker access. The repository and runtime directory must have
the same absolute paths inside this runner and on the Docker host. Contracts,
HTTP generation, image decoding and acknowledged-write validation come from an
unmodified checkout of once-campfire-verification.
"""

import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import html
import http.client
import json
import os
from pathlib import Path
import platform
import re
import resource
import secrets
import shutil
import socket
import sqlite3
import statistics
import subprocess
import tempfile
import time
import uuid

from benchmark_cache import affinity


def command(*args, check=True):
    result = subprocess.run([str(arg) for arg in args], capture_output=True, text=True)
    if check and result.returncode:
        raise RuntimeError(f"{args[0]} failed: {result.stderr[-4000:]}")
    return result


def json_command(*args):
    return json.loads(command(*args).stdout)


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n")


def checksum(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def server_port():
    # Host-network servers must not compete with load-generator ephemeral sockets.
    ephemeral_start = int(Path("/proc/sys/net/ipv4/ip_local_port_range").read_text().split()[0])
    if ephemeral_start <= 1025:
        raise RuntimeError("benchmark needs two unprivileged ports below the ephemeral range")
    for _ in range(128):
        port = 1024 + secrets.randbelow(ephemeral_start - 1025)
        with socket.socket() as public, socket.socket() as target:
            try:
                public.bind(("0.0.0.0", port))
                target.bind(("0.0.0.0", port + 1))
            except OSError:
                continue
            return port
    raise RuntimeError("no available server port pair outside the ephemeral range")


def valid(value, label):
    if not (value["ok"] > 0 and value["errors"] == 0 and value["invalid_responses"] == 0
            and value["validation"] == "route-contract-v1" and value["statuses"] == {"200": value["ok"]}):
        raise RuntimeError(f"{label}: failed response validation; no throughput result accepted")


def metrics(container):
    result = {}
    cpu = command("docker", "exec", container, "cat", "/sys/fs/cgroup/cpu.stat", check=False)
    if cpu.returncode == 0:
        values = dict(line.split() for line in cpu.stdout.splitlines())
        if "usage_usec" in values:
            result["cpu_seconds"] = int(values["usage_usec"]) / 1e6
    if "cpu_seconds" not in result:
        cpu = command("docker", "exec", container, "cat", "/sys/fs/cgroup/cpuacct/cpuacct.usage", check=False)
        if cpu.returncode == 0:
            result["cpu_seconds"] = int(cpu.stdout.strip()) / 1e9
    memory = command("docker", "exec", container, "cat", "/sys/fs/cgroup/memory.stat", check=False)
    if memory.returncode != 0:
        memory = command("docker", "exec", container, "cat", "/sys/fs/cgroup/memory/memory.stat", check=False)
    if memory.returncode == 0:
        values = dict(line.split() for line in memory.stdout.splitlines())
        for field in ("anon", "total_rss", "rss"):
            if field in values:
                result["anonymous_bytes"] = int(values[field])
                break
    peak = command("docker", "exec", container, "cat", "/sys/fs/cgroup/memory.peak", check=False)
    if peak.returncode == 0:
        result["container_lifetime_peak_bytes"] = int(peak.stdout.strip())
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verification", type=Path, required=True)
    parser.add_argument("--seed", type=Path, required=True)
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--apps", default="goose,rust,jai")
    parser.add_argument("--goose-image", default="once-campfire-goose:app")
    parser.add_argument("--goose-backend", choices=("auto", "epoll", "uring", "emulated"), help="override the Goose image's event loop")
    parser.add_argument("--goose-publication-ack", choices=("drained", "queued"), default="queued", help="select the HTTP acknowledgement stage for Goose publications")
    parser.add_argument("--goose-publication-window", type=int, choices=(8, 16, 32, 64, 128, 256, 512), default=8)
    parser.add_argument("--goose-queued-windows", help="comma-separated queued windows to compare in rotated order using the same image")
    parser.add_argument("--goose-seccomp", type=Path, help="optional container policy for Goose, recorded by SHA-256")
    parser.add_argument("--rust-image", default="ghcr.io/basecamp/once-campfire-rust:main")
    parser.add_argument("--jai-image", default="ghcr.io/lucasmeijer/once-campfire-jai:2ecd487")
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--duration", type=int, default=8)
    parser.add_argument("--clients", type=int, default=16)
    parser.add_argument("--write-clients", type=int, help="POST concurrency (default: same as --clients)")
    parser.add_argument("--client-cores", type=int, help="separate physical cores for the validator (default: same count as server)")
    parser.add_argument("--fanout-clients", type=int, default=0, help="also measure shared ActionCable delivery to this many tabs of one user")
    parser.add_argument("--profiles", default="baseline,mixed")
    parser.add_argument("--routes", default="room_show,messages_page,sidebar,search,post_message")
    parser.add_argument("--preflight-only", action="store_true")
    parser.add_argument("--profile-writes", action="store_true", help="record Goose write-stage timings; adds instrumentation overhead")
    args = parser.parse_args()
    if platform.system() != "Linux":
        raise SystemExit("Run inside the Linux toolchain container")
    apps, profiles, selected = args.apps.split(","), args.profiles.split(","), args.routes.split(",")
    assert apps and len(set(apps)) == len(apps) and set(apps) <= {"goose", "rust", "jai"}
    assert profiles and set(profiles) <= {"baseline", "mixed"}
    windows = [int(value) for value in args.goose_queued_windows.split(",")] if args.goose_queued_windows else [args.goose_publication_window]
    assert windows and len(windows) == len(set(windows)) and set(windows) <= {8, 16, 32, 64, 128, 256, 512}
    assert args.goose_publication_ack == "queued" or windows == [8]
    assert not args.goose_queued_windows or ("goose" in apps and args.goose_publication_ack == "queued")
    cases = [(app, window) for app in apps for window in (windows if app == "goose" else [None])]
    assert args.duration > 0 and args.clients > 0 and (args.preflight_only or args.rounds >= 2)
    assert args.fanout_clients >= 0
    assert args.write_clients is None or args.write_clients > 0
    assert selected and set(selected) <= {"room_show", "messages_page", "sidebar", "search", "avatar", "static_css", "up", "post_message"}
    repo = Path(__file__).resolve().parent
    verification, seed = args.verification.resolve(), args.seed.resolve()
    output, runtime = args.output.resolve(), args.runtime_root.resolve()
    runtime.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=False)
    loadgen = verification / "loadgen/target/release/loadgen"
    labels = json.loads((seed / "labels.json").read_text())
    fixture_env = dict(line.split("=", 1) for line in (seed / "reference.env").read_text().splitlines()
                       if line and not line.startswith("#"))
    room, write_room = labels["rooms.watercooler"], labels["rooms.hq"]
    server_cpus, client_cpus = affinity(args.client_cores)
    cpu_list = lambda cpus: ",".join(map(str, cpus))
    images = {}
    for app in apps:
        inspected = json_command("docker", "image", "inspect", getattr(args, f"{app}_image"))[0]
        config = inspected["Config"]
        image_env = dict(value.split("=", 1) for value in config.get("Env", []))
        images[app] = {"id": inspected["Id"], "digests": inspected.get("RepoDigests", []),
                       "reference": getattr(args, f"{app}_image"), "labels": config.get("Labels", {}),
                       "revision": (config.get("Labels") or {}).get("org.opencontainers.image.revision", image_env.get("GIT_REVISION"))}
    # The checkout is shared with Windows; Git normalizes CRLF and ignores its synthetic executable bits.
    def git(directory, *rest):
        options = ["git", "-c", f"safe.directory={directory}", "-c", "core.autocrlf=true",
                   "-c", "core.filemode=false", "-C", directory]
        pointer = directory / ".git"
        if pointer.is_file():
            gitdir = pointer.read_text().strip().removeprefix("gitdir: ")
            # Windows-managed worktrees retain a drive-letter path inside WSL.
            if len(gitdir) > 2 and gitdir[1:3] == ":/":
                gitdir = "/mnt/" + gitdir[0].lower() + gitdir[2:]
                options += [f"--git-dir={gitdir}", f"--work-tree={directory}"]
        return command(*options, *rest).stdout.strip()
    metadata = {
        "complete": False, "started_at": datetime.now(timezone.utc).isoformat(),
        "scope": "Shared HTTP contracts and audited plain-text posts; not complete application parity",
        "verification_revision": git(verification, "rev-parse", "HEAD"),
        "verification_dirty": bool(git(verification, "status", "--porcelain", "--untracked-files=no")),
        "goose_revision": git(repo, "rev-parse", "HEAD"), "goose_dirty": bool(git(repo, "status", "--porcelain")),
        "executable_sha256": command("docker", "run", "--rm", "--entrypoint", "sha256sum", images["goose"]["id"], "/usr/local/bin/campfire").stdout.split()[0] if "goose" in images else None,
        "loadgen_sha256": checksum(loadgen),
        "seed_sha256": checksum(seed / "db/production.sqlite3"), "seed_source": json.loads((seed / "source.json").read_text()),
        "images": images, "server_cpus": server_cpus, "client_cpus": client_cpus, "memory_limit_bytes": 2 * 1024**3,
        "platform": platform.platform(), "cpu_model": next(line.split(":", 1)[1].strip() for line in Path("/proc/cpuinfo").read_text().splitlines() if line.startswith("model name")),
        "duration_seconds": args.duration, "clients": args.clients, "rounds": args.rounds,
        "write_clients": args.write_clients or args.clients,
        "fanout_clients": args.fanout_clients,
        "profile_writes": args.profile_writes, "profiles": profiles, "read_routes": selected, "mixed_write_rate": 10, "gzip": True, "preflight_only": args.preflight_only,
        "goose_backend": args.goose_backend or "image default",
        "goose_publication_ack": args.goose_publication_ack,
        "goose_publication_windows": windows,
        "goose_seccomp": {"path": str(args.goose_seccomp.resolve()), "sha256": checksum(args.goose_seccomp)} if args.goose_seccomp else "Docker default",
        "validator_sha256": checksum(verification / "loadgen/src/validation.rs"),
        "contracts_sha256": checksum(verification / "bench/contracts.rb"),
        "write_audit_sha256": checksum(verification / "bench/validate_acks.rb"),
    }
    assert not metadata["verification_dirty"], "verification checkout must be unmodified"
    assert not metadata["goose_dirty"], "commit the experimental source before recording measurements"
    save(output / "metadata.json", metadata)
    results = []
    sequence = 0
    for profile in (["baseline"] if args.preflight_only else profiles):
        for iteration in range(1 if args.preflight_only else args.rounds):
            # Spread queue variants and references across early and late positions.
            offset = (iteration * (2 if args.goose_queued_windows else 1)) % len(cases)
            order = cases[offset:] + cases[:offset]
            for app, publication_window in order:
                variant = f"goose-q{publication_window}" if app == "goose" and args.goose_queued_windows else app
                name = f"goose-campfire-{uuid.uuid4().hex[:10]}-{app}"
                with tempfile.TemporaryDirectory(prefix=f"{app}-{profile}-", dir=runtime) as temporary:
                    data = Path(temporary)
                    for folder in ("db", "logs", "contracts"):
                        (data / folder).mkdir()
                    shutil.copytree(seed / "storage", data / "files")
                    database = data / "db/production.sqlite3"
                    with closing(sqlite3.connect((seed / "db/production.sqlite3").as_uri() + "?mode=ro", uri=True)) as source:
                        with closing(sqlite3.connect(database)) as db:
                            source.backup(db)
                            db.executescript("UPDATE push_subscriptions SET endpoint='https://127.0.0.1:9/push/'||id; "
                                             "UPDATE webhooks SET url='http://127.0.0.1:9/hook/'||id;")
                            initial_count = db.execute("SELECT count(*) FROM messages WHERE room_id=?", (write_room,)).fetchone()[0]
                            initial_max = db.execute("SELECT max(id) FROM messages").fetchone()[0]
                    for folder in ("db", "logs"):
                        os.chmod(data / folder, 0o777)
                    os.chmod(database, 0o666)
                    # TemporaryDirectory is private to the runner; image uid 1000 needs traversal.
                    os.chmod(data, 0o755)
                    port = server_port()
                    config = dict(fixture_env, WEB_CONCURRENCY="3", JOB_CONCURRENCY="3", RAILS_MAX_THREADS="5",
                                  RAILS_LOG_LEVEL="warn", HTTP_PORT=str(port), TARGET_PORT=str(port + 1),
                                  PORT=str(port), DATABASE_PATH="/rails/storage/db/production.sqlite3",
                                  STORAGE_PATH="/rails/storage/files", CAMPFIRE_CACHE="1", CAMPFIRE_CACHE_DIAGNOSTICS="0",
                                  CAMPFIRE_RESPONSE_CACHE_MB="64", CAMPFIRE_PROFILE_WRITES="1" if args.profile_writes else "0")
                    if app=="goose" and args.goose_backend:
                        config["CAMPFIRE_HTTP_BACKEND"]=args.goose_backend
                    if app=="goose":
                        config["CAMPFIRE_PUBLICATION_ACK"]=args.goose_publication_ack
                        config["CAMPFIRE_PUBLICATION_WINDOW"]=str(publication_window)
                    env_file = data / "server.env"
                    env_file.write_text("".join(f"{key}={value}\n" for key, value in config.items()))
                    os.chmod(env_file, 0o600)
                    launch = ["docker", "run", "-d", "--name", name, "--network", "host",
                              "--cpuset-cpus", cpu_list(server_cpus), "--memory", str(metadata["memory_limit_bytes"]),
                              "--env-file", str(env_file)]
                    if app == "goose" and args.goose_seccomp:
                        launch += ["--security-opt", f"seccomp={args.goose_seccomp.resolve()}"]
                    for folder, mount in (("db", "db"), ("files", "files"), ("logs", "logs")):
                        launch += ["-v", f"{data / folder}:/rails/storage/{mount}"]
                    launch += [images[app]["id"]]
                    row = {"app": app, "variant": variant, "publication_window": publication_window,
                           "round": iteration + 1, "profile": profile, "samples": []}
                    label = f"{variant}-{profile}-{iteration + 1}"
                    audits, acknowledged = [], 0
                    try:
                        command(*launch)
                        base = f"http://127.0.0.1:{port}"
                        deadline = time.monotonic() + 90
                        while True:
                            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=1)
                            try:
                                conn.request("GET", "/up")
                                response = conn.getresponse()
                                response.read()
                                if response.status == 200:
                                    break
                            except (OSError, http.client.HTTPException):
                                pass
                            finally:
                                conn.close()
                            if time.monotonic() >= deadline:
                                raise RuntimeError(f"{app}: startup timeout")
                            time.sleep(.1)
                        if app == "goose":
                            log = command("docker", "logs", name)
                            row["backend_log"] = log.stdout + log.stderr
                            if args.goose_queued_windows or args.goose_publication_window != 8:
                                assert f"Publication acknowledgement: queued\n" in row["backend_log"]
                                assert f"Publication window: {publication_window}; delivery permits: {publication_window+56}\n" in row["backend_log"]
                            descriptors = command("docker", "exec", name, "sh", "-c",
                                                  'for fd in /proc/1/fd/*; do readlink "$fd" || true; done').stdout
                            row["native_ring_count"] = descriptors.splitlines().count("anon_inode:[io_uring]")
                            if args.goose_backend == "uring":
                                assert row["native_ring_count"] > 0, "native benchmark must have a kernel ring"
                            elif args.goose_backend in ("epoll", "emulated"):
                                assert row["native_ring_count"] == 0, "unexpected native ring"
                        client = lambda *params: json_command("taskset", "-c", cpu_list(client_cpus), loadgen, *params)
                        auth = client("login", "--base", base, "--email", labels["emails.david"], "--password", labels["passwords.all"])["cookie"]
                        scrape = client("scrape", "--base", base, "--cookie", auth, "--room", room)
                        assert scrape["status"] == 200 and scrape["css"]
                        csrf = scrape.get("csrf") or ""
                        prepared = json_command("ruby", verification / "bench/contracts.rb", base, auth, database,
                                                seed / "labels.json", scrape["css"], data / "contracts")
                        row["preflight"] = prepared["preflight"]
                        contracts = prepared["contracts"]
                        routes = {"room_show": f"/rooms/{room}",
                                  "messages_page": f'/rooms/{room}/messages?before={labels["messages.busy_060"]}',
                                  "sidebar": "/users/me/sidebar", "search": "/searches?q=coffee",
                                  "avatar": f'/users/{labels["avatar_tokens.jason"]}/avatar',
                                  "static_css": scrape["css"], "up": "/up", "post_message": None}

                        def sample(route, duration, clients, phase, mixed=False, requests=None):
                            nonlocal sequence, acknowledged
                            sequence += 1
                            params = ["http", "--base", base, "--cookie", auth, "--gzip", "1",
                                      "--validate", contracts[route], "--duration", str(duration), "--conc", str(clients)]
                            if requests:
                                params += ["--requests", str(requests)]
                            if routes[route] is None:
                                audit = data / f"writes-{sequence}.jsonl"
                                audits.append(audit)
                                params += ["--post-room", str(write_room), "--csrf", csrf, "--audit-writes", str(audit)]
                            else:
                                params += ["--path", routes[route]]
                            if mixed:
                                audit = data / f"mixed-{sequence}.jsonl"
                                audits.append(audit)
                                params += ["--mixed-write-rate", "10", "--mixed-write-room", str(write_room),
                                           "--mixed-write-validate", contracts["post_message"],
                                           "--mixed-write-audit", str(audit), "--csrf", csrf]
                            before = resource.getrusage(resource.RUSAGE_CHILDREN)
                            started = time.monotonic()
                            value = client(*params)
                            elapsed = time.monotonic() - started
                            after = resource.getrusage(resource.RUSAGE_CHILDREN)
                            value.update(app=app, profile=profile, round=iteration + 1, route=route, phase=phase,
                                         generator_cpu_percent=100 * (after.ru_utime + after.ru_stime - before.ru_utime - before.ru_stime) / elapsed)
                            save(output / "raw" / f"{sequence:04}-{label}-{route}-{phase}.json", value)
                            valid(value, f"{label} {route} {phase}")
                            if routes[route] is None:
                                acknowledged += value["ok"]
                            if mixed:
                                valid(value["writer"], f"{label} mixed writer")
                                acknowledged += value["writer"]["ok"]
                            return value

                        for route in routes:
                            sample(route, 3, 1, "preflight", requests=1)
                        print(f"PASS {label}: all eight shared route contracts, including decoded avatar and POST", flush=True)
                        if not args.preflight_only:
                            # The mixed profile uses the shared 10-writes/s writer in a different room.
                            for route in (["room_show"] if profile == "mixed" else selected):
                                sample(route, 2, 4, "warmup", mixed=profile == "mixed")
                                before = metrics(name)
                                started = time.monotonic()
                                clients = (args.write_clients or args.clients) if route == "post_message" else args.clients
                                value = sample(route, args.duration, clients, "measured", mixed=profile == "mixed")
                                after = metrics(name)
                                if "cpu_seconds" in before and "cpu_seconds" in after:
                                    value["server_cpu_cores"] = (after["cpu_seconds"] - before["cpu_seconds"]) / (time.monotonic() - started)
                                value["server_anonymous_bytes_after"] = after.get("anonymous_bytes")
                                value["server_container_lifetime_peak_bytes_after"] = after.get("container_lifetime_peak_bytes")
                                row["samples"].append(value)
                                print(f"{label} {route}: {value['rps']} valid requests/s", flush=True)
                            if args.fanout_clients:
                                with closing(sqlite3.connect(database)) as db:
                                    fanout_start = db.execute("SELECT max(id) FROM messages").fetchone()[0]
                                params = ["taskset", "-c", cpu_list(client_cpus), str(loadgen), "cable",
                                          "--base", base, "--cookie", auth, "--room", str(room), "--csrf", csrf,
                                          "--streams", ",".join(scrape["streams"]), "--clients", str(args.fanout_clients),
                                          "--latency-msgs", "20", "--interval-ms", "50", "--tput-secs", str(args.duration),
                                          "--posters", "4", "--hold-secs", "1"]
                                phases = {}
                                with subprocess.Popen(params, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as process:
                                    for line in process.stderr:
                                        fields = line.split()
                                        if len(fields) == 3 and fields[0] == "PHASE":
                                            if fields[1] in ("idle", "saturated", "saturated_drained"):
                                                phases[fields[1]] = dict(metrics(name), unix_ms=int(fields[2]))
                                    payload = process.stdout.read()
                                    assert process.wait() == 0, "shared fan-out generator failed"
                                cable = json.loads(payload)
                                save(output / "raw" / f"{label}-fanout.json", cable)
                                assert cable["ready"] == args.fanout_clients and cable["failed"] == 0, cable
                                paced, saturated = cable["latency"], cable["throughput"]
                                assert paced["post_errors"] == 0 and paced["complete"] == paced["messages"] == paced["post_successes"], cable
                                assert saturated["post_errors"] == 0 and saturated["complete"] == saturated["posted"] == saturated["post_attempts"] > 0, cable
                                expected = {f"fanout bmk{i}z" for i in range(1, paced["messages"] + saturated["posted"] + 1)}
                                with closing(sqlite3.connect(database)) as db:
                                    bodies = db.execute("SELECT r.body,idx.body FROM messages m "
                                                        "LEFT JOIN action_text_rich_texts r ON r.record_id=m.id AND r.record_type='Message' AND r.name='body' "
                                                        "LEFT JOIN message_search_index idx ON idx.rowid=m.id "
                                                        "WHERE m.room_id=? AND m.id>?", (room, fanout_start)).fetchall()
                                assert len(bodies) == len(expected)
                                assert {html.unescape(re.sub(r"<[^>]*>", "", body)).strip() for body, _ in bodies} == expected
                                assert {body.strip() for _, body in bodies} == expected
                                row["fanout"] = dict(cable, phase_metrics=phases, persisted_messages=len(bodies))
                                print(f"{label} fanout: {saturated['delivered_msgs_per_sec']} messages/s delivered to all {args.fanout_clients} subscribers; persistence verified", flush=True)
                        audited = json_command("ruby", verification / "bench/validate_acks.rb", database, write_room, acknowledged, *audits)
                        with closing(sqlite3.connect(database)) as db:
                            assert db.execute("SELECT count(*) FROM messages WHERE room_id=?", (write_room,)).fetchone()[0] - initial_count == acknowledged
                            assert db.execute("SELECT count(*) FROM messages WHERE id NOT IN (SELECT rowid FROM message_search_index)").fetchone()[0] == 0
                            assert db.execute("SELECT count(*) FROM messages m WHERE room_id=? AND id>? AND NOT EXISTS "
                                              "(SELECT 1 FROM action_text_rich_texts r WHERE r.record_type='Message' AND r.name='body' AND r.record_id=m.id)", (write_room, initial_max)).fetchone()[0] == 0
                            assert db.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
                        row["post_write_audit"] = audited
                        for audit in audits:
                            shutil.copy2(audit, output / "raw" / f"{label}-{audit.name}")
                        results.append(row)
                        save(output / f"{label}.json", row)
                    except BaseException:
                        log = command("docker", "logs", name, check=False)
                        (output / f"{label}-server.log").write_text(log.stdout + log.stderr)
                        raise
                    finally:
                        if args.profile_writes:
                            log = command("docker", "logs", name, check=False)
                            (output / f"{label}-server.log").write_text(log.stdout + log.stderr)
                        command("docker", "rm", "-f", name, check=False)
    assert checksum(seed / "db/production.sqlite3") == metadata["seed_sha256"], "source seed changed"
    if "goose" in images:
        measured = command("docker", "run", "--rm", "--entrypoint", "sha256sum", images["goose"]["id"], "/usr/local/bin/campfire").stdout.split()[0]
        assert measured == metadata["executable_sha256"], "executable changed during measurement"
    metadata["complete"] = True
    metadata["finished_at"] = datetime.now(timezone.utc).isoformat()
    summary = {}
    for profile in profiles:
        summary[profile] = {}
        for variant in dict.fromkeys(row["variant"] for row in results):
            entries = [sample for row in results if row["variant"] == variant and row["profile"] == profile for sample in row["samples"]]
            summary[profile][variant] = {}
            for route in sorted({item["route"] for item in entries}):
                values = [item for item in entries if item["route"] == route]
                summary[profile][variant][route] = {"median_rps": statistics.median(item["rps"] for item in values),
                                               "runs": [item["rps"] for item in values]}
    save(output / "metadata.json", metadata)
    save(output / "summary.json", {"metadata": metadata, "summary": summary})
    print("COMPLETE: every accepted response and acknowledged write validated", flush=True)


if __name__ == "__main__":
    main()
