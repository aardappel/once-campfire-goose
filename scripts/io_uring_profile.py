#!/usr/bin/env python3
"""Create an opt-in Docker seccomp profile for goose-http's native Linux ring."""
import argparse
import hashlib
import json
from pathlib import Path
from urllib.request import urlopen

REVISION = "4b8214fc4971e06172938a1f1554ccff6647ee80"
URL = f"https://raw.githubusercontent.com/moby/profiles/{REVISION}/seccomp/default.json"
SHA256 = "6416b47770785a41ac59073cdc77d9fe98517df2799dc83ef207e622de3053f6"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("build/seccomp-io-uring.json"))
    args = parser.parse_args()
    with urlopen(URL, timeout=30) as response:
        source = response.read()
    if hashlib.sha256(source).hexdigest() != SHA256:
        raise SystemExit("Moby seccomp profile checksum mismatch")
    profile = json.loads(source)
    profile["syscalls"].append({
        "names": ["io_uring_setup", "io_uring_enter", "io_uring_register"],
        "action": "SCMP_ACT_ALLOW",
    })
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(profile, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {args.output}: Moby {REVISION} plus three io_uring syscalls")


if __name__ == "__main__":
    main()
