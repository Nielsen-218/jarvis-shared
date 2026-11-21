"""School subjects for research on the web and for installing software (blueprint areas 5 and 6), against stand-ins on this PC.

Nothing here touches the internet. The harness starts a tiny web server on 127.0.0.1 (a random port) that serves pages it just wrote,
so the right answer is known from how the pages were built. The gate lets a plain GET of a page on this very PC run automatically
(`curl` / `Invoke-RestMethod` / `Invoke-WebRequest` with ONE loopback URL and no output, upload, redirect or proxy flags); every other
address still asks. For installing, the harness builds a zip "release" in the sandbox; unpacking it inside the sandbox is the
install, and the file it contains is the ground truth. Uninstalling stays a person's decision: `Remove-Item` always asks the gate.
"""
from __future__ import annotations

import functools
import http.server
import json
import os
import threading
import zipfile
from typing import List

from .curriculum import Absent, Context, HIGH_SCHOOL, Subject, Task


class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):  # noqa: D401
        return


def _serve(c: Context, folder: str) -> str:
    """Serve `folder` on 127.0.0.1; returns the base URL. The server is stopped by the attempt's cleanup."""
    handler = functools.partial(_Quiet, directory=folder)
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()

    def stop():
        srv.shutdown()
        srv.server_close()
    c.on_cleanup(stop)
    return f"http://127.0.0.1:{srv.server_address[1]}"


def _heading(c: Context) -> Task:
    ask = "what is the main heading of the web page at {url}"
    site = c.folder(c.word(6))
    head = c.word(7) + " " + c.word(6)
    decoys = [c.word(7) + " " + c.word(5) for _ in range(2)]
    for i, other in enumerate(decoys):                    # other pages of the same site: reading the wrong page must fail
        with open(os.path.join(site, f"page{i}.html"), "w", encoding="utf-8", newline="") as f:
            f.write(f"<html><body><h1>{other}</h1></body></html>")
    with open(os.path.join(site, "index.html"), "w", encoding="utf-8", newline="") as f:
        f.write(f"<html><body><h1>{head}</h1></body></html>")
    base = _serve(c, site)
    return Task(ask, {"url": base + "/index.html"}, [head, Absent(tuple(decoys))], None, 0)


def _json_value(c: Context) -> Task:
    ask = "what is the value of the status field in the JSON at {url}"
    site = c.folder(c.word(6))
    status = c.word(8)
    with open(os.path.join(site, "info.json"), "w", encoding="utf-8", newline="") as f:
        json.dump({"name": c.word(6), "status": status, "other": c.word(6)}, f)
    base = _serve(c, site)
    return Task(ask, {"url": base + "/info.json"}, [status], None, 0)


def _install_zip(c: Context) -> Task:
    ask = "install the program in the zip {zip} into the folder {dest} and tell me its version"
    folder = c.folder(c.word(6))
    version = f"{c.rng.randint(1, 9)}.{c.rng.randint(0, 9)}.{c.rng.randint(0, 99)}"
    zpath = os.path.join(folder, "release.zip")
    with zipfile.ZipFile(zpath, "w") as z:
        z.writestr("VERSION", version + "\n")
        z.writestr("readme.txt", c.word(20))
    dest = os.path.join(folder, "installed")

    def check():
        got = ""
        p = os.path.join(dest, "VERSION")
        if os.path.isfile(p):
            with open(p, encoding="utf-8") as f:
                got = f.read().strip()
        return got == version, "the program is unpacked and its VERSION file is there" if got == version else f"expected {version!r} in {p}, found {got!r}"
    return Task(ask, {"zip": zpath, "dest": dest}, [], check, 1)


def _uninstall(c: Context) -> Task:
    ask = "uninstall the program installed in {dest} by removing its folder, and leave everything else alone"
    base = c.folder(c.word(6))
    dest = os.path.join(base, "program")
    keep = os.path.join(base, "documents")
    os.makedirs(dest, exist_ok=True)
    os.makedirs(keep, exist_ok=True)
    note = c.word(10)
    for name in ("app.exe", "VERSION"):
        with open(os.path.join(dest, name), "w", encoding="utf-8", newline="") as f:
            f.write(c.word(8))
    with open(os.path.join(keep, "mine.txt"), "w", encoding="utf-8", newline="") as f:
        f.write(note)

    def check():
        p = os.path.join(keep, "mine.txt")
        kept = os.path.isfile(p) and open(p, encoding="utf-8").read() == note
        if os.path.exists(dest):
            return False, "the program folder is still there"
        return kept, "the program is gone and the other files are untouched" if kept else "the person's own file was damaged"
    return Task(ask, {"dest": dest}, [], check, 1)


def _find_release(c: Context) -> Task:
    ask = "which release file does the project page at {url} offer for download"
    site = c.folder(c.word(6))
    good = c.word(7) + "-" + f"{c.rng.randint(1, 9)}.{c.rng.randint(0, 9)}.{c.rng.randint(0, 9)}-windows.zip"
    decoys = [c.word(7) + "-source.tar.gz", c.word(7) + "-linux.tar.gz"]
    with open(os.path.join(site, "index.html"), "w", encoding="utf-8", newline="") as f:
        f.write(f'<html><body><h1>{c.word(6)}</h1><a href="{good}">Windows release</a></body></html>')
    for i, d in enumerate(decoys):                        # files of the other kinds exist on the server but this page does not offer them
        with open(os.path.join(site, f"other{i}.html"), "w", encoding="utf-8", newline="") as f:
            f.write(f'<html><body><a href="{d}">other</a></body></html>')
    base = _serve(c, site)
    return Task(ask, {"url": base + "/index.html"}, [good, Absent(tuple(decoys))], None, 0)


def subjects() -> List[Subject]:
    hs = HIGH_SCHOOL
    return [
        Subject("web_local_heading", "read the heading of a web page", hs, _heading, ("browse a page", "fetch html"), needs=("file_read",)),
        Subject("web_local_json_value", "read a value from a web service", hs, _json_value, ("call an api", "fetch json"), needs=("web_local_heading",)),
        Subject("install_unzip_release", "install a program from a zip release", hs, _install_zip, ("unpack a release", "install from archive"), needs=("web_local_heading",)),
        Subject("uninstall_folder", "uninstall a program and leave the rest", hs, _uninstall, ("remove a program", "clean up an install"), needs=("install_unzip_release",)),
        Subject("web_find_release", "find the release file a project page offers", hs, _find_release, ("find the download", "release link"), needs=("web_local_heading",)),
    ]
