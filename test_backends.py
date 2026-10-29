#!/usr/bin/env python3
"""Check backend selection against live HTTP and kernel descriptors on Linux."""
import argparse
import http.client
import os
from pathlib import Path
import secrets
import socket
import subprocess
import tempfile
import time

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("binary", type=Path)
parser.add_argument("--require-native", action="store_true")
args = parser.parse_args()


def check(mode, expected=None):
    with tempfile.TemporaryDirectory(prefix="campfire-backend-") as temporary:
        work = Path(temporary)
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        env = dict(os.environ, DATABASE_PATH=str(work / "db.sqlite3"), HTTP_PORT=str(port),
                   SECRET_KEY_BASE=secrets.token_hex(64), CAMPFIRE_HTTP_THREADS="1")
        env.pop("CAMPFIRE_HTTP_BACKEND", None)
        if mode is not None:
            env["CAMPFIRE_HTTP_BACKEND"] = mode
        with (work / "server.log").open("w+") as log:
            server = subprocess.Popen([str(args.binary.resolve())], env=env, stdout=log, stderr=log)
            ready = False
            try:
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline and server.poll() is None:
                    client = http.client.HTTPConnection("127.0.0.1", port, timeout=.5)
                    try:
                        client.request("GET", "/up")
                        response = client.getresponse()
                        response.read()
                        ready = response.status == 200
                        if ready:
                            break
                    except (OSError, http.client.HTTPException):
                        pass
                    finally:
                        client.close()
                    time.sleep(.02)
                log.seek(0)
                output = log.read()
                if not ready:
                    assert mode == "uring" and not args.require_native, output
                    assert server.poll() not in (None, 0) and "io_uring: " in output, output
                    print("PASS: required io_uring refuses unavailable native support")
                    return False
                rings = 0
                for fd in Path(f"/proc/{server.pid}/fd").iterdir():
                    try:
                        rings += os.readlink(fd) == "anon_inode:[io_uring]"
                    except FileNotFoundError:
                        pass
                if mode == "uring":
                    expected = "io_uring"
                assert f"HTTP backend: {expected}\n" in output, output
                assert rings == (1 if expected == "io_uring" else 0), (mode, rings)
                print(f"PASS: {mode or 'default'} selects {expected}, native rings={rings}, HTTP 200")
                return True
            finally:
                server.terminate()
                try:
                    server.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait()


native = check("uring")
check(None, "epoll")
check("epoll", "epoll")
check("emulated", "emulated")
check("auto", "io_uring" if native else "epoll")

for policy, window in (("drained", "32"), ("queued", "0"), ("queued", "7"), ("queued", "9"), ("queued", "1024"), ("queued", "bad")):
    with tempfile.TemporaryDirectory(prefix="campfire-invalid-window-") as temporary:
        database = Path(temporary) / "db.sqlite3"
        environment = dict(os.environ, SECRET_KEY_BASE=secrets.token_hex(64), DATABASE_PATH=str(database),
                           CAMPFIRE_HTTP_BACKEND="epoll", CAMPFIRE_PUBLICATION_ACK=policy, CAMPFIRE_PUBLICATION_WINDOW=window)
        result = subprocess.run([str(args.binary.resolve())], env=environment, capture_output=True, text=True, timeout=5)
        assert result.returncode != 0 and not database.exists(), (policy, window, result.stdout, result.stderr)
        assert "publication windows require queued" in result.stderr or "CAMPFIRE_PUBLICATION_WINDOW must" in result.stderr
print("PASS: publication window rejects invalid capacities and requires explicit queued acknowledgement")

for batch in ("8192", "16383", "49152", "2097152", "bad"):
    with tempfile.TemporaryDirectory(prefix="campfire-invalid-batch-") as temporary:
        database = Path(temporary) / "db.sqlite3"
        environment = dict(os.environ, SECRET_KEY_BASE=secrets.token_hex(64), DATABASE_PATH=str(database),
                           CAMPFIRE_HTTP_BACKEND="epoll", CAMPFIRE_CABLE_BATCH=batch)
        result = subprocess.run([str(args.binary.resolve())], env=environment, capture_output=True, text=True, timeout=5)
        assert result.returncode != 0 and not database.exists(), (batch, result.stdout, result.stderr)
        assert "CAMPFIRE_CABLE_BATCH must" in result.stderr
print("PASS: cable batch rejects invalid sizes")
