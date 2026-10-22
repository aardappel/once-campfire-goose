#!/usr/bin/env python3
"""Fetch immutable toolchain/package revisions; compile the pinned Goose compiler."""
import json
import hashlib
import os
import re
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
LOCK = json.loads((ROOT / "dependencies.json").read_text())

def run(*args):
    subprocess.run([str(arg) for arg in args], check=True)

def checkout(spec, destination):
    if not (destination / ".git").is_dir():
        destination.mkdir(parents=True, exist_ok=True)
        run("git", "init", destination)
        run("git", "-C", destination, "remote", "add", "origin", spec["repository"])
        run("git", "-C", destination, "fetch", "--depth=1", "origin", spec["revision"])
        run("git", "-C", destination, "checkout", "--detach", "FETCH_HEAD")
    actual = subprocess.check_output(["git", "-C", str(destination), "rev-parse", "HEAD"], text=True).strip()
    dirty = subprocess.check_output(["git", "-C", str(destination), "status", "--porcelain", "--untracked-files=no"], text=True)
    if actual != spec["revision"]:
        raise SystemExit(f"Dependency must be pristine at {spec['revision']}: {destination}")
    if patch := spec.get("patch"):
        path = ROOT / patch["file"]
        if hashlib.sha256(path.read_bytes()).hexdigest() != patch["sha256"]:
            raise SystemExit(f"Dependency patch checksum mismatch: {path}")
        if not dirty:
            run("git", "-C", destination, "apply", "--index", path)
        tree = subprocess.check_output(["git", "-C", str(destination), "write-tree"], text=True).strip()
        unstaged = subprocess.run(["git", "-C", str(destination), "diff", "--quiet"]).returncode
        if tree != patch["tree"] or unstaged:
            raise SystemExit(f"Dependency differs from the pinned patched tree: {destination}")
    elif dirty:
        raise SystemExit(f"Dependency must be pristine at {spec['revision']}: {destination}")

compiler = ROOT / "build/toolchain"
checkout(LOCK["compiler"], compiler)
run("cmake", "-S", compiler, "-B", compiler / "build", "-G", "Ninja",
    "-DCMAKE_BUILD_TYPE=Release", "-DGOOSE_BUILTIN_LIBS=none",
    "-DCMAKE_CXX_COMPILER=clang++", "-DCMAKE_C_COMPILER=clang")
run("cmake", "--build", compiler / "build", "--parallel", os.environ.get("BUILD_JOBS", "4"))
home = Path(os.environ.get("GOOSE_HOME", ROOT / "build/home"))
for package in LOCK["packages"]:
    repository = package["repository"].removeprefix("https://").removesuffix(".git")
    ref = package["ref"]
    cache_ref = ref[:12] if re.fullmatch(r"[0-9a-fA-F]{7,40}", ref) else ref.replace("/", "_")
    checkout(package, home / "pkg" / repository / cache_ref)
