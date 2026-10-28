import std;
import http from "github.com/kaitk/goose-http@c3179b05ce13936ad2f8c713d1a670eca53e0437";

thread_fn worker(cfg: http::Config) {
    let error = http::serve(cfg) { req, body, out =>
        http::respond(out, 200, "text/plain", "OK");
    };
    http::report(error);
}

fn main() { http::run(http::Config { .. }, worker); }
