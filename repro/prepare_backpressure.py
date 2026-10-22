#!/usr/bin/env python3
"""Prepare the same outbound-flow probe for the pinned current HTTP release."""
from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parents[1]
REVISION="e456443241af004fe5784af4d7b3597a54422a62"
package=ROOT/"build/http-v054"
if not (package/".git").exists():
    subprocess.run(["git","clone","--no-checkout","https://github.com/kaitk/goose-http.git",str(package)],check=True)
    subprocess.run(["git","-C",str(package),"checkout","--detach",REVISION],check=True)
revision=subprocess.check_output(["git","-C",str(package),"rev-parse","HEAD"],text=True).strip()
dirty=subprocess.check_output(["git","-C",str(package),"status","--porcelain","--untracked-files=no"],text=True).strip()
assert revision==REVISION and not dirty,"The probe requires the pristine pinned package"
source=(ROOT/"repro/cable_backpressure.goose").read_text()
source=source.replace('"github.com/kaitk/goose-http@v0.4.3"','"./http-v054"')
source=source.replace('http::ws_upgrade(ev.req,out)','http::ws_upgrade(ev.req,state.ws,out)')
(ROOT/"build/cable_backpressure_v054.goose").write_text(source)
(ROOT/"build/cable_backpressure_epoll.goose").write_text(source.replace('http::serve(cfg,','http::serve_epoll(cfg,')) )
print("Prepared v0.5.4 serve() and serve_epoll() probes")
