import std;
import hash from "github.com/kaitk/goose-hash@v0.0.4";
import http from "github.com/kaitk/goose-http@d10a3a3a63e0205bf1520319fb23926bdbb147cc";
import auth;

fn main() {
    let expected: u8[20] = [0xde, 0x7c, 0x9b, 0x85, 0xb8, 0xb7, 0x8a, 0xa6, 0xbc, 0x8a,
                           0x7a, 0x36, 0xf7, 0x0a, 0x90, 0x70, 0x1c, 0x9d, 0xb4, 0xd9];
    assert(campfire::hmac_sha1("key", "The quick brown fox jumps over the lazy dog") == expected);
    print("HMAC-SHA1 passed");
}
