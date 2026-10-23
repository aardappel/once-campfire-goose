import std;
import http from "github.com/kaitk/goose-http@df12a627c9f4df51465f1ac7c324c17e86dc78b3";

thread_fn worker(cfg: http::Config) {
    let error = http::serve(cfg) { req, body, out =>
        http::respond(out, 200, "text/plain", "OK");
    };
    http::report(error);
}

fn main() { http::run(http::Config { .. }, worker); }
