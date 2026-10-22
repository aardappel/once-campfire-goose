import std;
import http from "github.com/kaitk/goose-http@v0.4.3";
import hash from "github.com/kaitk/goose-hash@v0.0.4";
import http.websocket;
struct Connection {
    body: http::Gather = http::Gather { .. },
    ws: http::WebSocket = http::WebSocket { .. },
    upgraded: bool = false,
}
thread_fn worker(cfg: http::Config) {
    let error = http::serve(cfg, Connection { .. }) { ev, state, out =>
        if ev.kind == http::REQUEST && http::path_of(ev.req.target) == "/cable" {
            state.upgraded = http::ws_upgrade(ev.req, out, "actioncable-v1-json");
            if state.upgraded { http::ws_send(out, http::WS_TEXT, "{\"type\":\"welcome\"}"); }
        } else if state.upgraded {
            http::ws_read(ev, state.ws, out) { op, data, reply => http::ws_send(reply, op, data); };
        } else {
            http::gather(ev, state.body, out, 1024 * 1024) { req, body, reply =>
                http::respond(reply, 200, "text/plain", body);
            };
        }
    };
    http::report(error);
}
fn main() { http::run(http::Config { port: 27891, workers: 1, max_conns: 64, .. }, worker); }
