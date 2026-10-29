import std;
import http from "github.com/kaitk/goose-http@ab762b121024d348d9a5f0ea480ad8145ba702a8";

thread_fn worker(cfg: http::Config) {
    let error = http::serve(cfg) { req, body, out =>
        http::respond(out, 200, "text/plain", "OK");
    };
    http::report(error);
}

fn main() { http::run(http::Config { .. }, worker); }
