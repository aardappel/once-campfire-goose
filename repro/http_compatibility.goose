import std;
import http from "github.com/kaitk/goose-http@e4029c3fa7fe3c6c073bc05c87be569317deff9c";

thread_fn worker(cfg: http::Config) {
    let error = http::serve(cfg) { req, body, out =>
        http::respond(out, 200, "text/plain", "OK");
    };
    http::report(error);
}

fn main() { http::run(http::Config { .. }, worker); }
