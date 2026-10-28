import std;
import http from "github.com/kaitk/goose-http@7e7fd46c66959585125035186ecf11c5e35a429a";

thread_fn worker(cfg: http::Config) {
    let error = http::serve(cfg) { req, body, out =>
        http::respond(out, 200, "text/plain", "OK");
    };
    http::report(error);
}

fn main() { http::run(http::Config { .. }, worker); }
