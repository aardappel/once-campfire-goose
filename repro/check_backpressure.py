#!/usr/bin/env python3
"""Measure bounded-volume WebSocket broadcasts against a reader that stops reading."""
import argparse
import json
from pathlib import Path
import socket
import subprocess
import tempfile
import time

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument("binary",type=Path)
parser.add_argument("--read",action="store_true")
args=parser.parse_args()

def rss(pid):
    for line in Path(f"/proc/{pid}/status").read_text().splitlines():
        if line.startswith("VmRSS:"): return int(line.split()[1])*1024
    raise AssertionError("missing process RSS")

with tempfile.TemporaryFile(mode="w+") as log:
    process=subprocess.Popen([str(args.binary.resolve())],stdout=log,stderr=log)
    sock=None
    try:
        for _ in range(100):
            try:
                sock=socket.socket()
                if not args.read: sock.setsockopt(socket.SOL_SOCKET,socket.SO_RCVBUF,1024)
                sock.connect(("127.0.0.1",27893));break
            except ConnectionRefusedError:
                sock.close();sock=None;time.sleep(.05)
        assert sock is not None, "probe did not start"
        sock.settimeout(15)
        baseline=rss(process.pid)
        sock.sendall(b"GET /cable HTTP/1.1\r\nHost: localhost\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Version: 13\r\nSec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n\r\n")
        head=b""
        while not head.endswith(b"\r\n\r\n"): head+=sock.recv(1)
        assert head.startswith(b"HTTP/1.1 101"),head
        peak=baseline;received=0;deadline=time.monotonic()+15
        if args.read: sock.settimeout(.05)
        while time.monotonic()<deadline:
            if args.read:
                try: received+=len(sock.recv(1<<20))
                except socket.timeout: pass
            else: time.sleep(.05)
            peak=max(peak,rss(process.pid))
            log.seek(0);output=log.read()
            if "Publisher queued" in output:
                if not args.read or received>=4096*(8192+4): break
        assert "Publisher queued" in output, output
        print(json.dumps({"reading":args.read,"configured_max_queued_bytes":4096,
            "published_payload_bytes":4096*8192,"received_frame_bytes":received,
            "rss_before":baseline,"rss_peak":peak,"rss_growth":peak-baseline},indent=2))
    finally:
        if sock: sock.close()
        process.terminate()
        try: process.wait(timeout=5)
        except subprocess.TimeoutExpired: process.kill();process.wait()
