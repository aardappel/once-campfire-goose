import std;
import http from "github.com/kaitk/goose-http@d10a3a3a63e0205bf1520319fb23926bdbb147cc";

thread_fn worker(cfg: http::Config) {
    let error = http::serve(cfg) { req, body, out =>
        http::respond(out, 200, "text/plain", "OK");
    };
    http::report(error);
}

fn main() { http::run(http::Config { .. }, worker); }
