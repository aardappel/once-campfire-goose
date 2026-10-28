import std;
import html;
import rich_html;
import os;

fn main() {
    var out: u8[>..] = [];
    campfire::rich_text(out, "<p onclick='bad()'>Hello <strong>world</strong> &amp; friends</p><img src=x onerror=bad()>");
    assert(out == "<p>Hello <strong>world</strong> &amp; friends</p>");
    out.clear();
    campfire::rich_text(out, "<a href='jav&#x61;script:bad()'>bad</a><a href='https://example.test/x'>good</a>");
    assert(out == "<a>bad</a><a href=\"https://example.test/x\" rel=\"nofollow noopener noreferrer\">good</a>");
    out.clear();
    campfire::rich_text(out, "<DIV><b>nested</DIV> & bare <");
    assert(out == "<div><b>nested</b></div> &amp; bare &lt;");
    out.clear();
    // An entity's ';' is sought only within its 16 bytes, so bare '&'s render in linear time.
    var bare: u8[>..] = [];
    for _ in 200000 { bare.push('&'); }
    let started = clock();
    campfire::rich_text(out, bare);
    let decoded = campfire::html_decode(bare);
    assert(clock() - started < 1.0);
    assert(out.len == 5 * bare.len && decoded == bare);
    out.clear();
    campfire::rich_text(out, "&#x0000000001F600;&#x000000001F600;");
    assert(out == "&amp;#x0000000001F600;&#x000000001F600;");
    assert(campfire::html_decode("&#x000000001F600;") == "\xF0\x9F\x98\x80");
    print("rich text rendering passed");
}
