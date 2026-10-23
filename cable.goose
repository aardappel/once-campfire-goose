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
struct CableSubscriber { live: bool = false, connection: i64, room: i64, stream: i32, identifier: u8[:] }
struct CableConnection {
    live: bool = false, address: http::MailAddress, user: i64,
    ready: bool = true, subscription: i64 = 0, offset: i64 = 0, enrolled: bool = false
}
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
    var subscriptions: CableSubscriber[>..] = [];
    reusable[] var storage: u8[>..] = [];
    var body: u8[>..] = [];
    var reply = http::MailAddress { .. };
    var room: i64 = 0;
    var active = false;

    fn drop_connection(index: i64) {
        connections[index].live = false;
        connections[index].enrolled = false;
        for &sub in subscriptions {
            if sub.live && sub.connection == index {
                storage.free_slice(sub.identifier);
                sub.live = false;
            }
        }
    }
    fn matches(sub: const CableSubscriber&) -> bool {
        return sub.live && (if room==0 { sub.stream==2 } else if room<0 { sub.stream==5 && connections[sub.connection].user == -room } else { sub.stream==1 && sub.room==room });
    }
    loop {
        let command = qget<CableCommand>();
        var index: i64 = -1;
        for c, i in connections { if c.live && c.address == command.address { index = i; break; } }
        if command.kind == CABLE_JOIN {
            if index < 0 {
                for c, i in connections { if !c.live { index = i; break; } }
                if index < 0 { index = connections.len; connections.push(CableConnection { address: command.address, user: command.user, .. }); }
                connections[index] = CableConnection { live: true, address: command.address, user: command.user, .. };
            }
            var duplicate = false;
            var slot: i64 = -1;
            var count: i64 = 0;
            for sub, i in subscriptions {
                if sub.live && sub.connection == index { count++; if sub.identifier == command.data { duplicate = true; } }
                if !sub.live { slot = i; }
            }
            if !duplicate && count < 16 {
                let id = storage.alloc_slice(command.data.len); copy_into(id, command.data);
                let sub = CableSubscriber { live: true, connection: index, room: command.room, stream: command.stream, identifier: id };
                if slot < 0 { subscriptions.push(sub); } else { subscriptions[slot] = sub; }
            }
        } else if index >= 0 && command.kind == CABLE_CREDIT {
            connections[index].ready = true;
        } else if index >= 0 && command.kind == CABLE_LEAVE {
            drop_connection(index);
        } else if index >= 0 && command.kind == CABLE_UNSUBSCRIBE {
            for &sub in subscriptions {
                if sub.live && sub.connection == index && sub.identifier == command.data {
                    storage.free_slice(sub.identifier); sub.live = false;
                }
            }
        }
        loop {
            if !active {
                let pending, exists = qpoll<Publication>();
                if !exists { break; }
                body.clear();
                room = pending.room; reply = pending.reply; active = true;
                var observed = false;
                for &c in connections { c.enrolled = false; c.subscription = 0; c.offset = 0; }
                for sub in subscriptions {
                    if matches(sub) && connections[sub.connection].live {
                        connections[sub.connection].enrolled = true; observed = true;
                    }
                }
                if observed { json::format_string(body, pending.body); }
            }
            var remaining = false;
            for &c, ci in connections {
                if !c.live || !c.enrolled { continue; }
                if !http::mail_live(c.address) { drop_connection(ci); continue; }
                if !c.ready { remaining = true; continue; }
                while c.subscription < subscriptions.len {
                    let sub .= subscriptions[c.subscription];
                    if !matches(sub) || sub.connection != ci { c.subscription++; c.offset = 0; continue; }
                    var prefix: u8[>..] = []; prefix.append("{\"identifier\":");
                    json::format_string(prefix, sub.identifier); prefix.append(",\"message\":");
                    let length = prefix.len + body.len + 1;
                    let end = min(c.offset + 8192, length);
                    var chunk: u8[>..] = [];
                    for pos in c.offset..end {
                        chunk.push(if pos < prefix.len { prefix[pos] } else if pos < prefix.len + body.len { body[pos-prefix.len] } else { '}' });
                    }
                    let tag = ((if c.offset == 0 { 1 } else { 0 }) | (if end == length { 128 } else { 0 })) as i32;
                    let sent = http::mail_send(c.address, tag, chunk);
                    if sent == http::STALE { drop_connection(ci); break; }
                    if sent == http::SENT {
                        c.ready = false; c.offset = end;
                        if end == length { c.subscription++; c.offset = 0; }
                    }
                    break;
                }
                if c.live && (c.subscription < subscriptions.len || !c.ready) { remaining = true; }
                else { c.enrolled = false; }
            }
            if remaining { break; }
            let sent = http::mail_send(reply, CABLE_ACK, "");
            if sent == http::FULL { break; }
            active = false; body.clear();
            assert(qput(CablePermit { .. }));
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
