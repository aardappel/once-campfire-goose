import std;
import http from "github.com/kaitk/goose-http@v0.4.3";
import hash from "github.com/kaitk/goose-hash@v0.0.4";
import http.websocket;
struct State { ws: http::WebSocket = http::WebSocket { .. }, upgraded: bool = false }
thread_fn publisher(to: http::MailAddress) {
    let data: u8[8192] = ['x';8192];
    for i in 4096 {
        loop {
            let result=http::mail_send(to,1,data);
            if result==http::SENT { break; }
            if result!=http::FULL { return; }
            sleep_ms(1);
        }
    }
    write_stderr("Publisher queued 32 MiB\n");
}
thread_fn worker(cfg: http::Config) {
    let error=http::serve(cfg,State { .. }) { ev,state,out =>
        if ev.kind==http::REQUEST {
            state.upgraded=http::ws_upgrade(ev.req,out);
            if state.upgraded { let address=http::later(out,60000); thread_spawn(publisher,address); }
        } else if ev.kind==http::WAKE && state.upgraded {
            http::ws_send(out,http::WS_TEXT,ev.data);
            http::later(out,60000);
        } else if state.upgraded { http::ws_read(ev,state.ws,out) { op,data,reply => }; }
    };
    http::report(error);
}
fn main() {
    http::run(http::Config { address:"127.0.0.1",port:27893,workers:1,max_conns:8,
                            max_queued:4096,mail_slots:8,mail_size:8192,max_waiting:8,.. },worker);
}
