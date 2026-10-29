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
import threading
import time
from urllib.parse import urlencode


class WebSocket:
    def __init__(self, port, cookie, receive_buffer=None):
        self.sock = socket.socket()
        self.sock.settimeout(15)
        if receive_buffer is not None:
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, receive_buffer)
        self.sock.connect(("127.0.0.1", port))
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

    def frame(self, data, opcode):
        mask = secrets.token_bytes(4)
        size = bytes([0x80 | len(data)]) if len(data) < 126 else b"\xfe" + struct.pack("!H", len(data))
        self.sock.sendall(bytes([opcode]) + size + mask + bytes(c ^ mask[i % 4] for i, c in enumerate(data)))

    def send(self, value, fragmented=False):
        data = json.dumps(value).encode()
        if fragmented:
            split = len(data) // 2
            self.frame(data[:split], 0x01)
            self.frame(data[split:], 0x80)
        else:
            self.frame(data, 0x81)

    def subscribe(self, identifier, fragmented=False):
        self.send({"command": "subscribe", "identifier": identifier}, fragmented)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            message = self.receive()
            if message.get("identifier") == identifier and "type" in message:
                return message["type"]
        raise TimeoutError("subscription response did not arrive")

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
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            message = self.receive(on_fragment)
            if isinstance(message.get("message"), str):
                return message["message"]
        raise TimeoutError("publication did not arrive")

    def close(self):
        self.sock.close()


with tempfile.TemporaryDirectory(prefix="campfire-cable-") as temporary:
    work = Path(temporary)
    database = work / "production.sqlite3"
    with socket.socket() as available:
        available.bind(("127.0.0.1", 0))
        port = available.getsockname()[1]
    environment = dict(os.environ, DATABASE_PATH=str(database), HTTP_PORT=str(port), SECRET_KEY_BASE=secrets.token_hex(64))
    queued_ack = environment.get("CAMPFIRE_PUBLICATION_ACK", "drained") == "queued"
    window_size = int(environment.get("CAMPFIRE_PUBLICATION_WINDOW", "8"))
    delivery_permits = window_size + 56
    if "--batch-pressure" in sys.argv[2:]:
        environment["CAMPFIRE_HTTP_THREADS"] = "1"
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
                assert connection.subscribe(identifier, fragmented=True) == "confirm_subscription"
                assert connection.subscribe(presence) == "confirm_subscription"
            with sqlite3.connect(database) as db:
                assert db.execute("SELECT connections,unread_at FROM memberships WHERE room_id=1").fetchone() == (2, None)
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                post = pool.submit(request, "/rooms/1/messages", {"message[body]": "&" * 40000, "message[client_message_id]": "large-publication"}, cookie)
                heartbeat = json.dumps({"channel": "HeartbeatChannel"})
                received = first.publication(lambda: first.send({"command": "subscribe", "identifier": heartbeat}, fragmented=True))
                assert "&amp;" * 40000 in received
                assert second.publication() == received
                assert post.result()[0] == 200
                aliases = [json.dumps({"channel": "RoomMessagesChannel", "signed_stream_name": signed,
                                       "padding": "\n\"\\" * size}) for size in (0, 70, 250)]
                for alias in aliases:
                    assert second.subscribe(alias) == "confirm_subscription"
                assert second.subscribe(identifier) == "confirm_subscription"
                for size in (0, 1000, 4000, 7500, 16000):
                    marker = f"ordered-publication-{size}"
                    post = pool.submit(request, "/rooms/1/messages",
                                       {"message[body]": marker + "x" * size, "message[client_message_id]": marker}, cookie)
                    expected = first.publication()
                    delivered = {}
                    while len(delivered) < 4:
                        event = second.receive()
                        if isinstance(event.get("message"), str):
                            assert event["identifier"] not in delivered, "duplicate subscription delivery"
                            delivered[event["identifier"]] = event["message"]
                    assert set(delivered) == {identifier, *aliases}
                    assert all(value == expected and marker in value for value in delivered.values())
                    assert post.result()[0] == 200
                for alias in aliases:
                    second.send({"command": "unsubscribe", "identifier": alias})
                assert second.subscribe(aliases[0]) == "confirm_subscription"
                post = pool.submit(request, "/rooms/1/messages", {"message[body]": "reused-subscription",
                                                               "message[client_message_id]": "reused-subscription"}, cookie)
                expected = first.publication()
                delivered = []
                while len(delivered) < 2:
                    event = second.receive()
                    if isinstance(event.get("message"), str):
                        assert event["message"] == expected
                        delivered.append(event["identifier"])
                assert set(delivered) == {identifier, aliases[0]}
                assert post.result()[0] == 200
                second.send({"command": "unsubscribe", "identifier": aliases[0]})
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
            slow, fast, peer = WebSocket(port, cookie, receive_buffer=1024), WebSocket(port, cookie), WebSocket(port, cookie)
            sockets.extend([slow, fast, peer])
            assert slow.subscribe(identifier) == fast.subscribe(identifier) == peer.subscribe(identifier) == "confirm_subscription"
            batch_pressure = "--batch-pressure" in sys.argv[2:]
            pressure_ids = [identifier]
            if batch_pressure:
                for alias in range(5):
                    extra = json.dumps({"channel": "RoomMessagesChannel", "signed_stream_name": signed, "alias": alias})
                    pressure_ids.append(extra)
                    for connection in (slow, fast, peer):
                        assert connection.subscribe(extra) == "confirm_subscription"
            received, peer_received, late_received, reader_errors = [], [], [], []
            def read_pressure(connection, destination, identifiers):
                try:
                    while True:
                        delivered = {}
                        while len(delivered) < len(identifiers):
                            event = connection.receive()
                            if isinstance(event.get("message"), str):
                                assert event["identifier"] in identifiers and event["identifier"] not in delivered
                                delivered[event["identifier"]] = event["message"]
                        message = next(iter(delivered.values()))
                        assert all(value == message for value in delivered.values()), "publication frames interleaved"
                        if "pressure-finished" in message:
                            return
                        destination.append(re.search(r"pressure-item-\d+", message)[0])
                except BaseException as error:
                    reader_errors.append(error)
            readers = [threading.Thread(target=read_pressure, args=(connection, destination, pressure_ids), daemon=True)
                       for connection, destination in ((fast, received), (peer, peer_received))]
            for reader in readers:
                reader.start()
            def message_count():
                with sqlite3.connect(database) as db:
                    return db.execute("SELECT count(*) FROM messages").fetchone()[0]
            def rss():
                status = Path(f"/proc/{server.pid}/status").read_text()
                return int(re.search(r"^VmRSS:\s+(\d+)", status, re.M)[1]) * 1024
            start_count = message_count()
            accepted = []
            with concurrent.futures.ThreadPoolExecutor(max_workers=64) as pool:
                try:
                    # Fill the slow reader's TCP window before checking application back-pressure.
                    for i in range(window_size + (1024 if batch_pressure else 64)):
                        marker = f"pressure-item-{i}"
                        post = pool.submit(request, "/rooms/1/messages",
                                           {"message[body]": marker + ("x" * 512 if batch_pressure else "&" * 40000), "message[client_message_id]": marker}, cookie)
                        accepted.append((marker, post))
                        try:
                            status = post.result(timeout=.5)[0]
                        except concurrent.futures.TimeoutError:
                            # A slow host can exceed .5 s without back-pressure; a held write never completes.
                            try:
                                status = post.result(timeout=2)[0]
                            except concurrent.futures.TimeoutError:
                                break
                        assert status == 200
                    else:
                        raise AssertionError("slow reader did not apply back-pressure")
                    blocked = post
                    marker = f"pressure-item-{len(accepted)}"
                    abandoned = http.client.HTTPConnection("127.0.0.1", port, timeout=15)
                    abandoned.request("POST", "/rooms/1/messages",
                                      urlencode({"message[body]": marker, "message[client_message_id]": marker}),
                                      {"Cookie": cookie, "Content-Type": "application/x-www-form-urlencoded"})
                    accepted.append((marker, None))
                    # Queued success retains the entire active window's delivery permits.
                    for _ in range(delivery_permits - (window_size if queued_ack else 0) - 2):
                        marker = f"pressure-item-{len(accepted)}"
                        accepted.append((marker, pool.submit(request, "/rooms/1/messages",
                                                             {"message[body]": marker + ("x" * (128 * (len(accepted) % 4)) if batch_pressure else "&" * (5000 * (len(accepted) % 4))), "message[client_message_id]": marker}, cookie)))
                    deadline = time.monotonic() + 5
                    while message_count() < start_count + len(accepted) and time.monotonic() < deadline:
                        time.sleep(.02)
                    assert message_count() == start_count + len(accepted), "admitted writes did not commit"
                    abandoned.close()
                    assert not blocked.done(), "acknowledged without space in the delivery window"
                    # Active publications progress independently; a stalled reader holds retirement.
                    deadline = time.monotonic() + 3
                    expected_active = i if queued_ack else i + window_size
                    while min(len(received), len(peer_received)) < expected_active and time.monotonic() < deadline:
                        time.sleep(.01)
                    assert len(received) == len(peer_received) == expected_active, "bounded pipeline did not advance fast readers"
                    if queued_ack:
                        assert i >= window_size and all(result.done() and result.result()[0] == 200 for _, result in accepted[i-window_size:i]), "queued acknowledgements waited for final drain"
                    active = received.copy()
                    assert peer_received == active, "readers disagree on publication order"
                    late = WebSocket(port, cookie)
                    sockets.append(late)
                    assert late.subscribe(identifier) == "confirm_subscription"
                    late_reader = threading.Thread(target=read_pressure, args=(late, late_received, [identifier]), daemon=True)
                    readers.append(late_reader)
                    late_reader.start()
                    retained = rss()
                    print("PRESSURE_MEMORY " + json.dumps({"window": window_size, "permits": delivery_permits, "queued": queued_ack, "batch": batch_pressure, "stalled_rss_bytes": retained}), flush=True)
                    for _ in range(100):
                        status, _, body = request("/rooms/1/messages", {"message[body]": "pressure-rejected",
                                                                      "message[client_message_id]": "pressure-rejected"}, cookie)
                        assert status == 503 and "Live delivery is busy" in body, (status, body)
                    assert message_count() == start_count + len(accepted), "rejected write mutated the database"
                    assert rss() - retained < 16 * 1024 * 1024, "rejected writes retained unbounded memory"
                    assert not blocked.done(), "stalled reader lost its drain credit"
                    assert received == peer_received == active and not late_received, "pipeline exceeded its window or enrolled a late reader"
                    slow.close()
                    for _, result in accepted:
                        if result is not None:
                            assert result.result(timeout=10)[0] == 200
                    assert request("/rooms/1/messages", {"message[body]": "pressure-finished",
                                                        "message[client_message_id]": "pressure-finished"}, cookie)[0] == 200
                    for reader in readers:
                        reader.join(timeout=10)
                    assert not any(reader.is_alive() for reader in readers) and not reader_errors, reader_errors
                    assert len(received) == len(accepted) and set(received) == {marker for marker, _ in accepted}
                    assert received[:i+1] == [marker for marker, _ in accepted[:i+1]], "serial publications reordered"
                    assert received == peer_received, "publication order changed across window reuse"
                    assert late_received == received[len(active):], "late subscription inherited active publications"
                finally:
                    slow.close()
                    fast.close()
                    peer.close()
            # Readers spread over the workers by SO_REUSEPORT. Each worker matches and writes its own readers;
            # some hold connections without subscriptions. Every worker's first connection is a stalled reader that
            # disconnects while it holds active publications; later, readers leave by WebSocket Close during a
            # publication.
            for connection in sockets:
                connection.close()
            time.sleep(.5)
            def spread_markers(reader, count):
                seen = []
                while len(seen) < count:
                    seen.append(re.search(r"spread-\d+", reader.publication())[0])
                return seen
            def publish_all(markers, readers_now, leaving=()):
                with concurrent.futures.ThreadPoolExecutor(max_workers=len(markers)) as pool:
                    posts = [pool.submit(request, "/rooms/1/messages", {"message[body]": marker,
                                                                        "message[client_message_id]": marker}, cookie)
                             for marker in markers]
                    # A WebSocket Close ends the exchange without CLOSED.
                    for reader in leaving:
                        reader.frame(b"\x03\xe8", 0x88)
                    for reader in readers_now:
                        assert sorted(spread_markers(reader, len(markers))) == sorted(markers)
                    assert all(post.result(timeout=20)[0] == 200 for post in posts)
            stalled = [WebSocket(port, cookie, receive_buffer=1024) for _ in range(8)]
            spread = [WebSocket(port, cookie) for _ in range(16)]
            sockets.extend(stalled + spread)
            subscribed = spread[:12]
            for reader in stalled + subscribed:
                assert reader.subscribe(identifier) == "confirm_subscription"
            stalled += subscribed[2:4]
            remaining = subscribed[:2] + subscribed[4:]
            spread_received = {reader: [] for reader in remaining}
            spread_errors = []
            def read_spread(reader):
                try:
                    while True:
                        marker = re.search(r"spread-(\d+|end)", reader.publication())[0]
                        if marker == "spread-end":
                            return
                        spread_received[reader].append(marker)
                except BaseException as error:
                    spread_errors.append(error)
            spread_threads = [threading.Thread(target=read_spread, args=(reader,), daemon=True) for reader in remaining]
            for thread in spread_threads:
                thread.start()
            spread_posted = []
            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
                # Until the stalled readers hold back a write; a large queued window admits all of them.
                for n in range(64):
                    marker = f"spread-{n}"
                    post = pool.submit(request, "/rooms/1/messages", {"message[body]": marker + "&" * 40000,
                                                                      "message[client_message_id]": marker}, cookie)
                    spread_posted.append(marker)
                    try:
                        assert post.result(timeout=1)[0] == 200
                    except concurrent.futures.TimeoutError:
                        break
                # Stalled readers, every worker's first connection among them, leave while they hold active publications.
                for reader in stalled:
                    reader.close()
                assert post.result(timeout=20)[0] == 200, "a departed reader held retirement"
            for reader in spread[12:]:
                reader.close()
            assert request("/rooms/1/messages", {"message[body]": "spread-end", "message[client_message_id]": "spread-end"}, cookie)[0] == 200
            for thread in spread_threads:
                thread.join(timeout=20)
            assert not spread_errors and not any(thread.is_alive() for thread in spread_threads), spread_errors
            assert all(received == spread_posted for received in spread_received.values()), "readers on other workers lost or reordered publications"
            publish_all([f"spread-{9000 + n}" for n in range(3)], remaining[1::2], remaining[::2])
            for reader in remaining:
                reader.close()
            time.sleep(.5)
            # Every worker is empty again; a new reader is enrolled afresh.
            fresh = WebSocket(port, cookie)
            sockets.append(fresh)
            assert fresh.subscribe(identifier) == "confirm_subscription"
            publish_all(["spread-9100"], [fresh])
            # More readers than CAMPFIRE_CABLE_PASS_READERS wakes at once: the rest still follow when those it
            # woke leave before their kick arrives, by WebSocket Close or by a dropped connection.
            fresh.close()
            crowd = [WebSocket(port, cookie) for _ in range(48)]
            sockets.extend(crowd)
            for reader in crowd:
                assert reader.subscribe(identifier) == "confirm_subscription"
            markers = [f"spread-{9200 + n}" for n in range(6)]
            with concurrent.futures.ThreadPoolExecutor(max_workers=len(markers)) as pool:
                posts = [pool.submit(request, "/rooms/1/messages", {"message[body]": marker,
                                                                    "message[client_message_id]": marker}, cookie)
                         for marker in markers]
                for n, reader in enumerate(crowd[::2]):
                    if n % 2:
                        reader.frame(b"\x03\xe8", 0x88)
                    else:
                        reader.close()
                for reader in crowd[1::2]:
                    assert sorted(spread_markers(reader, len(markers))) == sorted(markers)
                assert all(post.result(timeout=20)[0] == 200 for post in posts)
            print(f"PASS: signed subscriptions, fragmented and multi-subscription delivery, unsubscribe/reuse, ordered {window_size}-publication pipelining, late enrollment, {delivery_permits} retained delivery permits, bounded rejected-write memory, {'queued' if queued_ack else 'drained'} acknowledgements, disconnect recovery, readers spread over workers with departing stalled readers, more readers than a pass wakes, and presence cleanup")
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
