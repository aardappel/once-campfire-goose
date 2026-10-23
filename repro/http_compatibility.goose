import std;
import http from "github.com/kaitk/goose-http@ebb4238728179e82140550af50e5bab1a7e0dab2";

thread_fn worker(cfg: http::Config) {
    let error = http::serve(cfg) { req, body, out =>
        http::respond(out, 200, "text/plain", "OK");
    };
    http::report(error);
}

fn main() { http::run(http::Config { .. }, worker); }
