namespace campfire;
import std;
import json;
import base64;
import http;
import http.websocket;
import sqlite;
import database;
import view_data;
import sessions;

struct CablePermit { value: bool = true }
struct Publication { room: i64, reply: http::MailAddress, body: u8[] }
struct CableCommand {
    kind: i32, address: http::MailAddress, user: i64 = 0, room: i64 = 0,
    stream: i32 = 0, data: u8[]
}
struct CableSubscriber { live: bool = false, retiring: bool = false, room: i64, stream: i32, identifier: u8[:] }
struct CableConnection {
    live: bool = false, address: http::MailAddress, user: i64,
    ready: bool = true, sequence: i64 = 0, offset: i64 = 0, attempted: i64 = 0
}
struct CableDelivery { reply: http::MailAddress, body: u8[:], remaining: i64 = 0 }
let CABLE_WINDOW: i64 = 8;
let CABLE_ENROLLED: i32 = 65536;
let CABLE_JOIN: i32 = 1;
let CABLE_CREDIT: i32 = 2;
let CABLE_LEAVE: i32 = 3;
let CABLE_UNSUBSCRIBE: i32 = 4;
let CABLE_PUBLISH: i32 = 5;
let CABLE_ACK: i32 = 256;

fn cable_command(kind: i32, address: http::MailAddress) {
    qput(CableCommand { kind: kind, address: address, data: [], .. });
}

fn publish(room: i64, reply: http::MailAddress, body: const u8[:]) {
    // Each admitted HTTP writer owns one permit until its acknowledgement.
    assert(qput(Publication { room: room, reply: reply, body: sql_text(body) }));
    cable_command(CABLE_PUBLISH, reply);
}

thread_fn cable_broker() {
    var connections: CableConnection[>..] = [];
    // Each connection owns 16 contiguous slots; connection scans do not copy their payloads.
    var subscriptions: CableSubscriber[>..] = [];
    // The low 16 bits identify subscriptions; the high bit owns one completion credit.
    var targets: i32[>..] = [];
    reusable[] var storage: u8[>..] = [];
    reusable[] var publications: u8[>..] = [];
    var window: CableDelivery[8] = [CableDelivery { .. }; 8];
    var head: i64 = 0;
    var tail: i64 = 0;
    var body: u8[>..] = [];
    var prefix: u8[>..] = [];
    var chunk: u8[>..] = [];
    var turn: i64 = 0;
    var readers: i64[..64] = [];
    var selected: i64[..64] = [];
    var addresses: http::MailAddress[..64] = [];

    fn drop_connection(index: i64) {
        connections[index].live = false;
        for &target, slot in targets[index*CABLE_WINDOW..(index+1)*CABLE_WINDOW] {
            if target & CABLE_ENROLLED != 0 { window[slot].remaining--; }
            target = 0;
        }
        for &sub in subscriptions[index*16..index*16+16] {
            if sub.live || sub.retiring {
                storage.free_slice(sub.identifier);
                sub.live = false; sub.retiring = false;
            }
        }
    }
    fn matches(sub: const CableSubscriber&, user: i64, room: i64) -> bool {
        return sub.live && (if room==0 { sub.stream==2 } else if room<0 { sub.stream==5 && user == -room } else { sub.stream==1 && sub.room==room });
    }
    fn apply_command(command: const CableCommand&) {
        var index: i64 = -1;
        for &c, i in connections { if c.live && c.address == command.address { index = i; break; } }
        if command.kind == CABLE_JOIN {
            if index < 0 {
                for &c, i in connections { if !c.live { index = i; break; } }
                if index < 0 {
                    index = connections.len;
                    connections.push(CableConnection { address: command.address, user: command.user, .. });
                    for _ in 0..16 { subscriptions.push(CableSubscriber { .. }); }
                    for _ in 0..CABLE_WINDOW { targets.push(0); }
                }
                connections[index] = CableConnection { live: true, address: command.address, user: command.user, sequence: tail, .. };
            }
            var duplicate = false;
            var slot: i64 = -1;
            var count: i64 = 0;
            for sub, i in subscriptions[index*16..index*16+16] {
                if sub.live || sub.retiring { count++; }
                if sub.live && sub.identifier == command.data { duplicate = true; }
                if !sub.live && !sub.retiring { slot = i; }
            }
            if !duplicate && count < 16 {
                let id = storage.alloc_slice(command.data.len); copy_into(id, command.data);
                let sub = CableSubscriber { live: true, room: command.room, stream: command.stream, identifier: id, .. };
                subscriptions[index*16+slot] = sub;
            }
        } else if index >= 0 && command.kind == CABLE_CREDIT {
            connections[index].ready = true;
        } else if index >= 0 && command.kind == CABLE_LEAVE {
            drop_connection(index);
        } else if index >= 0 && command.kind == CABLE_UNSUBSCRIBE {
            for &sub, si in subscriptions[index*16..index*16+16] {
                if sub.live && sub.identifier == command.data {
                    let current = connections[index].sequence % CABLE_WINDOW;
                    let mask = targets[index*CABLE_WINDOW+current];
                    let partial = connections[index].offset != 0 && (mask & (1 << si)) != 0 && (mask & ((1 << si)-1)) == 0;
                    sub.live = false; sub.retiring = partial;
                    // An already-started WebSocket message must finish before its identifier can be reused.
                    if !partial { storage.free_slice(sub.identifier); }
                    for &target, slot in targets[index*CABLE_WINDOW..(index+1)*CABLE_WINDOW] {
                        if !partial || slot != current { target &= ~(1 << si); }
                    }
                }
            }
        }
    }
    loop {
        let first = qget<CableCommand>();
        apply_command(first);
        // Bound control processing so a busy command queue cannot starve delivery.
        for _ in 0..127 {
            let command, exists = qpoll<CableCommand>();
            if !exists { break; }
            apply_command(command);
        }
        loop {
            while tail - head < CABLE_WINDOW {
                let pending, exists = qpoll<Publication>();
                if !exists { break; }
                let slot = tail % CABLE_WINDOW;
                window[slot].reply = pending.reply;
                window[slot].remaining = 0;
                body.clear();
                for &c, ci in connections {
                    var mask: i32 = 0;
                    if c.live {
                        for sub, si in subscriptions[ci*16..ci*16+16] {
                            if matches(sub, c.user, pending.room) { mask |= 1 << si; }
                        }
                    }
                    if mask != 0 { mask |= CABLE_ENROLLED; window[slot].remaining++; }
                    targets[ci*CABLE_WINDOW+slot] = mask;
                }
                if window[slot].remaining != 0 { json::format_string(body, pending.body); }
                let encoded = publications.alloc_slice(body.len); copy_into(encoded, body);
                window[slot].body = encoded;
                tail++;
            }
            turn++;
            // Apply drain credits before grouping readers at their next frame.
            for &c, ci in connections {
                c.sequence = max(c.sequence, head);
                if !c.live || !c.ready { continue; }
                while c.sequence < tail {
                    let slot = c.sequence % CABLE_WINDOW;
                    let mask = targets[ci*CABLE_WINDOW+slot];
                    if mask & 65535 != 0 { break; }
                    if mask & CABLE_ENROLLED != 0 { window[slot].remaining--; targets[ci*CABLE_WINDOW+slot] = 0; }
                    c.sequence++; c.offset = 0;
                }
            }
            for &c, ci in connections {
                if !c.live || c.sequence == tail || c.attempted == turn { continue; }
                if !c.ready {
                    if !http::mail_live(c.address) { drop_connection(ci); }
                    continue;
                }
                while c.sequence < tail {
                    let slot = c.sequence % CABLE_WINDOW;
                    let mask = targets[ci*CABLE_WINDOW+slot];
                    var subscription: i64 = 0;
                    while mask & (1 << subscription) == 0 { subscription++; }
                    let sub .= subscriptions[ci*16+subscription];
                    let payload = window[slot].body;
                    readers.clear(); selected.clear(); addresses.clear();
                    readers.push(ci); selected.push(subscription); addresses.push(c.address); c.attempted = turn;
                    // Bound lookahead as well as payload size; workers share chunks at equal frame positions.
                    for &other, oi in connections[ci+1..min(ci+257, connections.len)] {
                        if readers.len == 64 { break; }
                        if !other.live || !other.ready || other.attempted == turn || other.address.box != c.address.box ||
                            other.sequence != c.sequence || other.offset != c.offset { continue; }
                        let index = ci + 1 + oi;
                        let bits = targets[index*CABLE_WINDOW+slot] & 65535;
                        if bits == 0 { continue; }
                        var si: i64 = 0; while bits & (1 << si) == 0 { si++; }
                        if subscriptions[index*16+si].identifier != sub.identifier { continue; }
                        readers.push(index); selected.push(si); addresses.push(other.address); other.attempted = turn;
                    }
                    prefix.clear(); prefix.append("{\"identifier\":");
                    json::format_string(prefix, sub.identifier); prefix.append(",\"message\":");
                    let length = prefix.len + payload.len + 1;
                    let capacity = if readers.len == 1 { 8192 } else { 8192 - 2 - 8*readers.len };
                    let end = min(c.offset + capacity, length);
                    chunk.clear();
                    if c.offset < prefix.len { chunk.append(prefix[c.offset..min(end, prefix.len)]); }
                    let start = max(c.offset, prefix.len);
                    let stop = min(end, prefix.len + payload.len);
                    if start < stop { chunk.append(payload[start-prefix.len..stop-prefix.len]); }
                    if end == length { chunk.push('}'); }
                    let tag = ((if c.offset == 0 { 1 } else { 0 }) | (if end == length { 128 } else { 0 })) as i32;
                    let sent = if readers.len == 1 { http::mail_send(c.address, tag, chunk) } else { http::mail_send_many(addresses, tag, chunk) };
                    if sent == http::STALE { for index in readers { drop_connection(index); } break; }
                    if sent == http::SENT {
                        for index, ri in readers {
                            connections[index].ready = false; connections[index].offset = end;
                            if end == length {
                                targets[index*CABLE_WINDOW+slot] &= ~(1 << selected[ri]); connections[index].offset = 0;
                                let at = index*16+selected[ri];
                                if subscriptions[at].retiring { storage.free_slice(subscriptions[at].identifier); subscriptions[at].retiring = false; }
                            }
                        }
                    }
                    break;
                }
            }
            let previous = head;
            // Retire in publication order, including writes whose HTTP client disconnected.
            while head < tail {
                let slot = head % CABLE_WINDOW;
                if window[slot].remaining != 0 { break; }
                if http::mail_send(window[slot].reply, CABLE_ACK, "") == http::FULL { break; }
                publications.free_slice(window[slot].body);
                head++;
                assert(qput(CablePermit { .. }));
            }
            if head == previous { break; }
        }
    }
}

fn cable_frame(out: http::Reply, tag: i32, data: const u8[:]) {
    var head: u8[..4] = []; head.push(tag as! u8);
    if data.len < 126 { head.push(data.len as! u8); }
    else { head.push(126); head.push((data.len >> 8) as! u8); head.push(data.len as! u8); }
    http::send(out, head); http::send(out, data);
    http::after_drain(out);
}

fn cable_string(node: json::Node?, name: const u8[:]) -> const u8[:] {
    let value, ok = json::as_string(json::member(node, name)); return value;
}

fn cable_decode(encoded: const u8[:]) -> u8[] {
    var decoded: u8[>..] = [];
    if !base64::decode(decoded, encoded) { return []; }
    return decoded;
}

fn cable_subscribe(db, user: i64, identifier: const u8[:], key: const u8[:]) -> bool, i64, i32 {
    var nodes: json::Node[>..] = []; var strings: u8[>..] = [];
    let params, error = json::parse(identifier, nodes, strings);
    guard error.len == 0 else { return false, 0, 0; }
    let channel = cable_string(params, "channel");
    if channel == "RoomMessagesChannel" || channel == "Turbo::StreamsChannel" {
        let signed = cable_string(params, "signed_stream_name");
        let split = find(signed, "--");
        guard split > 0 else { return false, 0, 0; }
        let decoded = cable_decode(signed[..split]); guard decoded.len > 0 else { return false, 0, 0; }
        var values: json::Node[>..] = []; var text: u8[>..] = [];
        let value, bad = json::parse(decoded, values, text);
        let name, valid = json::as_string(value);
        guard bad.len == 0 && valid && signed_stream(name, key) == signed else { return false, 0, 0; }
        if channel == "Turbo::StreamsChannel" {
            return name == "rooms" || name == str(gid_param("User",user),":rooms"), 0, if name=="rooms" { 2 } else { 5 };
        }
        guard ends_with(name, ":messages") else { return false, 0, 0; }
        let rows = sql_rows(db, "SELECT r.id,r.type FROM rooms r JOIN memberships m ON m.room_id=r.id JOIN users u ON u.id=m.user_id WHERE m.user_id=? AND u.status=0", [sql_text(user)]);
        for row in rows {
            var id: i64 = 0;
            for cell, i in row { if i == 0 { let n, ok = parse_int(cell); id = n; } else if i == 1 && name == str(gid_param(cell,id),":messages") { return true, id, 1; } }
        }
        return false, 0, 0;
    }
    if channel == "PresenceChannel" || channel == "RoomChannel" || channel == "TypingNotificationsChannel" {
        let n, valid = json::as_i64(json::member(params,"room_id"));
        var room = n as i64;
        if !valid { let id, ok = parse_int(cable_string(params,"room_id")); room = id; }
        let member = sql_number(db,"SELECT count(*) FROM memberships WHERE user_id=? AND room_id=?",[sql_text(user),sql_text(room)]) > 0;
        return member, room, if channel == "PresenceChannel" { 3 } else { 4 };
    }
    return channel == "HeartbeatChannel" || channel == "ReadRoomsChannel" || channel == "UnreadRoomsChannel", 0, 4;
}

fn presence_change(db: sqlite::Db, user: i64, room: i64, mode: i32) -> bool {
    var now: u8[>..]=[]; var cutoff: u8[>..]=[]; campfire_sql_now(now); campfire_disconnected_before(cutoff);
    campfire_writer_enter();
    let failed=sqlite::run() {
        if mode==0 {sql_rows(db,"UPDATE memberships SET connections=CASE WHEN connected_at>=? THEN connections+1 ELSE 1 END,connected_at=?,unread_at=NULL,updated_at=? WHERE user_id=? AND room_id=?",[sql_text(cutoff),sql_text(now),sql_text(now),sql_text(user),sql_text(room)]);}
        else if mode==1 {sql_rows(db,"UPDATE memberships SET connections=CASE WHEN connected_at>=? THEN connections ELSE 1 END,connected_at=?,updated_at=? WHERE user_id=? AND room_id=?",[sql_text(cutoff),sql_text(now),sql_text(now),sql_text(user),sql_text(room)]);}
        else {sql_rows(db,"UPDATE memberships SET connected_at=CASE WHEN connections>1 AND connected_at>=? THEN connected_at ELSE NULL END,connections=MAX(connections-1,0),updated_at=? WHERE user_id=? AND room_id=?",[sql_text(cutoff),sql_text(now),sql_text(user),sql_text(room)]);}
    };
    campfire_writer_leave(); return failed.len==0;
}

fn cable_message(db: sqlite::Db, user: i64, key: const u8[:], data: const u8[:], presence: i64[:], out: http::Reply) {
    var nodes: json::Node[>..] = []; var strings: u8[>..] = [];
    let root, error = json::parse(data, nodes, strings);
    if error.len > 0 { return; }
    let command = cable_string(root, "command"); let identifier = cable_string(root, "identifier");
    if identifier.len == 0 || identifier.len > 2048 { return; }
    var params_nodes: json::Node[>..]=[]; var params_strings: u8[>..]=[];
    let params,bad_params=json::parse(identifier,params_nodes,params_strings);
    let presence_channel=cable_string(params,"channel")=="PresenceChannel";
    let presence_room=signed_integer(json::member(params,"room_id"));
    let address = http::later(out, 3000);
    if command == "unsubscribe" {
        if presence_channel {for &room in presence {if room==presence_room {presence_change(db,user,room,2);room=0;}}}
        qput(CableCommand { kind:CABLE_UNSUBSCRIBE,address:address,data:sql_text(identifier),.. }); return;
    }
    if command=="message" && presence_channel {
        var action_nodes: json::Node[>..]=[]; var action_strings: u8[>..]=[];
        let info,error=json::parse(cable_string(root,"data"),action_nodes,action_strings);
        let action=cable_string(info,"action");
        if action!="absent" && action!="present" && action!="refresh" {return;}
        for room in presence {if room==presence_room && room>0 {presence_change(db,user,room,if action=="absent" {2} else if action=="present" {0} else {1});}}
        return;
    }
    if command != "subscribe" { return; }
    var accepted = false; var room: i64 = 0; var stream: i32 = 0;
    let failed = sqlite::run() { let yes, r, s = cable_subscribe(db,user,identifier,key); accepted=yes; room=r; stream=s; };
    accepted = accepted && failed.len == 0;
    if accepted && presence_channel {
        var exists=false; var slot: i64=-1;
        for value,i in presence {if value==room {exists=true;} if value==0 {slot=i;}}
        if !exists {
            accepted=slot>=0 && presence_change(db,user,room,0);
            if accepted {presence[slot]=room;}
        }
    }
    if accepted { accepted = qput(CableCommand { kind:CABLE_JOIN,address:address,user:user,room:room,stream:stream,data:sql_text(identifier) }); }
    var response: u8[>..] = []; response.append("{\"identifier\":"); json::format_string(response,identifier);
    response.append(if accepted { ",\"type\":\"confirm_subscription\"}" } else { ",\"type\":\"reject_subscription\"}" });
    http::ws_send(out,http::WS_TEXT,response); http::after_drain(out);
}
