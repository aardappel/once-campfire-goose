import std;
import http from "github.com/kaitk/goose-http@555a723adc103a455ccc4cdf43bd4a9a96608a70";

fn main() {
    let box, error = http::mail_open(2, 64, 0, 4, 0);
    http::exit_on_error(error);
    let first = http::address(box, 1); let second = http::address(box, 2);
    http::mail_mark(box, 1, true); http::mail_mark(box, 2, true);
    var recipients = [first, second];
    assert(http::mail_send(http::address(box, 0), 7, "sample") == http::STALE);
    assert(http::mail_send_many(recipients, 7, "sample") == http::SENT);
    assert(http::mail_send_many(recipients, 7, "sample") == http::SENT);
    assert(http::mail_send_many(recipients, 7, "sample") == http::FULL);
    http::mail_mark(box, 2, false);
    var delivered: i64 = 0; var skipped: i64 = 0;
    let batches = http::mail_take(box) { key, tag, bytes =>
        assert(key == 0);
        http::mail_dispatch(key, tag, bytes) { recipient, event, data =>
            assert(event == 7 && data == "sample");
            if http::mail_live(http::address(box, recipient)) { assert(recipient == 1); delivered++; }
            else { assert(recipient == 2); skipped++; }
        };
    };
    assert(batches == 2);
    assert(delivered == 2 && skipped == 2);
    var oversized: u8[47] = [0;47];
    assert(http::mail_send_many(recipients, 7, oversized) == http::TOO_LONG);
    assert(http::mail_send_many(recipients[..0], 7, "") == http::TOO_LONG);
    var too_many: http::MailAddress[65] = [first;65];
    assert(http::mail_send_many(too_many, 7, "") == http::TOO_LONG);
    assert(http::mail_send_many(recipients, 9, oversized[..46]) == http::SENT);
    var exact: i64 = 0;
    http::mail_take(box) { key, tag, bytes =>
        http::mail_dispatch(key, tag, bytes) { recipient, event, data =>
            assert(event == 9 && data.len == 46); exact++;
        };
    };
    assert(exact == 2);
    let other, error2 = http::mail_open(2, 64); http::exit_on_error(error2);
    recipients[1] = http::address(other, 2);
    assert(http::mail_send_many(recipients, 7, "") == http::STALE);
    http::mail_close(other);
    var malformed: u8[3] = [64,0,1];
    http::mail_dispatch(0, 7, malformed) { key, tag, bytes => assert(false); };
    assert(http::mail_send(first, 8, "single") == http::SENT);
    let singles = http::mail_take(box) { key, tag, bytes =>
        http::mail_dispatch(key, tag, bytes) { recipient, event, data =>
            assert(recipient == 1 && event == 8 && data == "single"); delivered++;
        };
    };
    assert(singles == 1);
    assert(delivered == 3);
    http::mail_stop(box);
    assert(http::mail_send_many([first], 7, "") == http::STALE);
    http::mail_close(box);
    let reopened, error3 = http::mail_open(2, 64); http::exit_on_error(error3);
    assert(http::mail_send_many([first], 7, "") == http::STALE);
    http::mail_close(reopened);
    print("PASS: bounded worker mail, stale recipients, generation reuse, shutdown and ordinary delivery");
}
