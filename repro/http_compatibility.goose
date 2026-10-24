import std;
import http from "github.com/kaitk/goose-http@555a723adc103a455ccc4cdf43bd4a9a96608a70";

thread_fn worker(cfg: http::Config) {
    let error = http::serve(cfg) { req, body, out =>
        http::respond(out, 200, "text/plain", "OK");
    };
    http::report(error);
}

fn main() { http::run(http::Config { .. }, worker); }
