"""Bounded text/code/PDF extraction and HTTPS fetching; never execute sources."""
import http.client
import ipaddress
import json
import os
import socket
import ssl
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from core.teaching import MAX_TEXT

MAX_FILE = 5 * 1024 * 1024
TEXT_EXT = {".txt", ".md", ".py", ".cpp", ".cc", ".c", ".h", ".hpp", ".cs",
            ".js", ".ts", ".java", ".rs", ".go", ".lua", ".json"}


def pdf_parser_ready():
    """Fail closed on reviewed vulnerable releases; never install on app startup."""
    from core.pdf_policy import parser_ready
    return parser_ready()


def fetch(url, user_agent="Jarvis-Teaching/1"):
    p = urlsplit(url)
    if (p.scheme != "https" or not p.hostname or p.username or p.password
            or p.port not in (None, 443) or p.fragment):
        raise ValueError("Use a public HTTPS URL on port 443 without credentials or fragment")
    addresses = socket.getaddrinfo(p.hostname, 443, type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
        raise ValueError("Private and special network destinations are forbidden")
    # Pin the validated address; preserve hostname verification and SNI. Disable
    # redirects entirely, including public-to-private redirects and DNS rebinding.
    conn = http.client.HTTPSConnection(p.hostname, timeout=10, context=ssl.create_default_context())
    addr = addresses[0][4][0]
    conn._create_connection = lambda address, timeout, source_address=None: socket.create_connection((addr, 443), timeout)
    try:
        conn.request("GET", (p.path or "/") + ("?"+p.query if p.query else ""),
                     headers={"User-Agent": user_agent, "Accept-Encoding": "identity"})
        response = conn.getresponse()
        if response.status != 200:
            raise ValueError("Source must respond directly with HTTP 200; redirects are not followed")
        data = response.read(MAX_FILE+1)
        if len(data) > MAX_FILE:
            raise ValueError("Source exceeds 5 MiB")
        return data, response.getheader("Content-Type", "").split(";")[0]
    finally:
        conn.close()


def extract(filename, data, content_type=""):
    if not data or len(data) > MAX_FILE:
        raise ValueError("Source must contain 1 byte to 5 MiB")
    ext = Path(filename).suffix.casefold()
    if ext == ".pdf" or data.startswith(b"%PDF") or content_type == "application/pdf":
        if not pdf_parser_ready():
            raise ValueError('PDF import blocked: reviewed pypdf >=6.21.4,<7 is required; owner-approved dependency update needed')
        # Parser has no access to app objects and is killed after 15 seconds.
        # Process isolation is not an OS security sandbox; OCR is not supported.
        with tempfile.TemporaryDirectory(prefix="jarvis-pdf-") as folder:
            source = Path(folder)/"source.pdf"
            source.write_bytes(data)
            run = subprocess.run([sys.executable, "-m", "scripts.teaching_pdf", str(source)],
                                 capture_output=True, timeout=15,
                                 env={k:v for k,v in os.environ.items() if k.upper() in
                                      {"SYSTEMROOT","WINDIR","PATH","TEMP","TMP"}},
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if run.returncode:
                raise ValueError("PDF extraction failed; encrypted/scanned/oversize PDFs need review")
            text = json.loads(run.stdout)
    elif content_type == "text/html":
        from tools.engineering import _html_text
        text = _html_text(data.decode("utf-8", errors="strict"))
    elif ext in TEXT_EXT or content_type.startswith("text/"):
        text = data.decode("utf-8-sig", errors="strict")
    else:
        raise ValueError("Supported sources: PDF, UTF-8 text, Markdown and code")
    if not text.strip() or len(text) > MAX_TEXT-5000 or "\x00" in text:
        raise ValueError("No readable text or extracted text exceeds the lesson limit")
    return text
