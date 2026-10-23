import std;
import http from "github.com/kaitk/goose-http@75f0362919dd7140bc672f4cbecfb69033c500fc";

thread_fn worker(cfg: http::Config) {
    let error = http::serve(cfg) { req, body, out =>
        http::respond(out, 200, "text/plain", "OK");
    };
    http::report(error);
}

fn main() { http::run(http::Config { .. }, worker); }
