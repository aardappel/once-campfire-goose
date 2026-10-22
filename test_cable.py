#!/usr/bin/env python3
"""Exercise real ActionCable framing, authorization, fan-out and presence cleanup."""
import concurrent.futures
import html
import http.client
import json
import os
from pathlib import Path
import re
import secrets
import socket
import sqlite3
import struct
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlencode


class WebSocket:
    def __init__(self, port, cookie):
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=15)
        self.sock.sendall((f"GET /cable HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
                           "Upgrade: websocket\r\nConnection: Upgrade\r\n"
                           "Sec-WebSocket-Version: 13\r\nSec-WebSocket-Protocol: actioncable-v1-json\r\n"
                           f"Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\nCookie: {cookie}\r\n\r\n").encode())
        head = b""
        while not head.endswith(b"\r\n\r\n"):
            head += self.take(1)
        assert head.startswith(b"HTTP/1.1 101"), head
        assert self.receive()["type"] == "welcome"

    def take(self, size):
        data = bytearray()
        while len(data) < size:
            part = self.sock.recv(size - len(data))
            assert part, "unexpected WebSocket EOF"
            data.extend(part)
        return data

    def send(self, value):
        data = json.dumps(value).encode()
        mask = secrets.token_bytes(4)
        size = bytes([0x80 | len(data)]) if len(data) < 126 else b"\xfe" + struct.pack("!H", len(data))
        self.sock.sendall(b"\x81" + size + mask + bytes(c ^ mask[i % 4] for i, c in enumerate(data)))

    def subscribe(self, identifier):
        self.send({"command": "subscribe", "identifier": identifier})
        while True:
            message = self.receive()
            if message.get("identifier") == identifier and "type" in message:
                return message["type"]

    def receive(self, on_fragment=None):
        result = bytearray()
        started = False
        while True:
            op, size = self.take(2)
            assert not size & 0x80, "server frame is masked"
            if size == 126:
                size = struct.unpack("!H", self.take(2))[0]
            elif size == 127:
                size = struct.unpack("!Q", self.take(8))[0]
            payload = self.take(size)
            assert op & 15 in (0, 1), (op, payload[:100])
            assert (op & 15) == (0 if started else 1), "interleaved data frame"
            started = True
            result.extend(payload)
            if op & 0x80:
                return json.loads(result)
            if on_fragment:
                on_fragment()
                on_fragment = None

    def publication(self, on_fragment=None):
        while True:
            message = self.receive(on_fragment)
            if isinstance(message.get("message"), str):
                return message["message"]

    def close(self):
        self.sock.close()


with tempfile.TemporaryDirectory(prefix="campfire-cable-") as temporary:
    work = Path(temporary)
    database = work / "production.sqlite3"
    with socket.socket() as available:
        available.bind(("127.0.0.1", 0))
        port = available.getsockname()[1]
    environment = dict(os.environ, DATABASE_PATH=str(database), HTTP_PORT=str(port), SECRET_KEY_BASE=secrets.token_hex(64))
    with (work / "server.log").open("w+") as log:
        server = subprocess.Popen([str(Path(sys.argv[1]).resolve())], env=environment, stdout=log, stderr=log)
        sockets = []
        def request(path, fields=None, cookie=""):
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=15)
            conn.request("GET" if fields is None else "POST", path, None if fields is None else urlencode(fields),
                         {"Cookie": cookie, "Content-Type": "application/x-www-form-urlencoded"})
            response = conn.getresponse()
            result = response.status, dict(response.getheaders()), response.read().decode()
            conn.close()
            return result
        try:
            for _ in range(100):
                try:
                    if request("/up")[0] == 200:
                        break
                except OSError:
                    time.sleep(.05)
            status, headers, _ = request("/first_run", {"user[name]": "Cable test", "user[email_address]": "cable@example.test", "user[password]": "test password"})
            assert status == 303
            cookie = headers["Set-Cookie"].split(";", 1)[0]
            page = request("/rooms/1", cookie=cookie)[2]
            tag = next(tag for tag in re.findall(r"<turbo-cable-stream-source[^>]+>", page) if 'channel="RoomMessagesChannel"' in tag)
            signed = html.unescape(re.search(r'signed-stream-name="([^"]+)"', tag)[1])
            identifier = json.dumps({"channel": "RoomMessagesChannel", "signed_stream_name": signed})
            presence = json.dumps({"channel": "PresenceChannel", "room_id": 1})
            first, second = WebSocket(port, cookie), WebSocket(port, cookie)
            sockets.extend([first, second])
            forged = json.dumps({"channel": "RoomMessagesChannel", "signed_stream_name": signed[:-1] + ("a" if signed[-1] != "a" else "b")})
            assert first.subscribe(forged) == "reject_subscription"
            for connection in sockets:
                assert connection.subscribe(identifier) == "confirm_subscription"
                assert connection.subscribe(presence) == "confirm_subscription"
            with sqlite3.connect(database) as db:
                assert db.execute("SELECT connections,unread_at FROM memberships WHERE room_id=1").fetchone() == (2, None)
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                post = pool.submit(request, "/rooms/1/messages", {"message[body]": "&" * 40000, "message[client_message_id]": "large-publication"}, cookie)
                heartbeat = json.dumps({"channel": "HeartbeatChannel"})
                received = first.publication(lambda: first.send({"command": "subscribe", "identifier": heartbeat}))
                assert "&amp;" * 40000 in received
                assert second.publication() == received
                assert post.result()[0] == 200
                # A disconnect during an outstanding publication cannot block the remaining reader.
                post = pool.submit(request, "/rooms/1/messages", {"message[body]": "after disconnect", "message[client_message_id]": "disconnect-publication"}, cookie)
                first.close()
                assert "after disconnect" in second.publication()
                assert post.result()[0] == 200
            second.close()
            for _ in range(100):
                with sqlite3.connect(database) as db:
                    current = db.execute("SELECT connections,connected_at FROM memberships WHERE room_id=1").fetchone()
                if current == (0, None):
                    break
                time.sleep(.02)
            assert current == (0, None), current
            print("PASS: signed subscription rejection, two-reader fan-out, 200 KiB fragmented delivery with concurrent commands, acknowledged writes, disconnect recovery and presence cleanup")
        except BaseException:
            log.seek(0)
            print(log.read(), file=sys.stderr)
            raise
        finally:
            for connection in sockets:
                connection.close()
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait()
