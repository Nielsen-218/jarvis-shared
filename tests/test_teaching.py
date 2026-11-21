"""Teaching ingress: authorization, provenance, persistence and hostile inputs."""
import io
import json
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core import teaching
from core.teaching_import import extract, fetch, MAX_FILE
from core.teaching_practice import CPP_SOURCE, cpp_tokens, practice


def lesson(**kwargs):
    return {"title": "Create a basic C++ program", "topic": "C++", "source": "owner-authored fixture",
            "license": "owner-authorized", "version": "1", "content": "Write main.cpp, compile and check stdout.",
            "examples": CPP_SOURCE, "exercises": "Reproduce Hello Jarvis in an empty workspace.",
            "tests": "Expected exit 0 and Hello, Jarvis! on stdout", **kwargs}


def test_auth_scopes_revocation_expiry_and_forged_identity():
    with pytest.raises(PermissionError):
        teaching.submit(lesson(), "wrong")
    read = teaching.grant("reader", ["read"])
    with pytest.raises(PermissionError):
        teaching.submit(lesson(), read)
    token = teaching.grant("codex")
    with pytest.raises(ValueError):
        teaching.submit(lesson(mentor="claude", state="verified"), token)
    teaching.revoke("codex")
    with pytest.raises(PermissionError):
        teaching.search("C++", token)
    token = teaching.grant("expired", hours=0.000000001)
    time.sleep(0.05)   # Windows time.time() ticks every 15.6 ms: without this the token can still be inside its tick
    with pytest.raises(PermissionError):
        teaching.submit(lesson(), token)


def test_conflicts_preserve_history_and_both_instructors():
    a, b = teaching.grant("codex"), teaching.grant("claude-code")
    first = teaching.submit(lesson(), a)
    assert teaching.submit(lesson(), a)["duplicate"]
    second = teaching.submit(lesson(content="Use a different compiler procedure"), b)
    assert first["revision"] == 1 and second["revision"] == 2
    rows = teaching.search("C++", a)
    assert {r["mentor"] for r in rows} == {"codex", "claude-code"}
    assert all(r["state"] == "indexed_unverified" for r in rows)
    from tools.engineering import library_search
    assert "main.cpp" in library_search("main.cpp", 3)


def test_real_process_restart_and_local_retrieval(tmp_data):
    token = teaching.grant("codex")
    saved = teaching.submit(lesson(), token)
    # New Python process reads disk; network is disabled in that process.
    code = """
import json,socket,sys
from pathlib import Path
from core import storage,teaching
def forbidden(*a,**k): raise AssertionError('network forbidden')
socket.socket=forbidden
storage.DATA=Path(sys.argv[1])
print(json.dumps(teaching.search('C++',sys.stdin.read())))
"""
    result = subprocess.run([sys.executable, "-c", code, str(tmp_data)], input=token,
                            capture_output=True, text=True, timeout=15, check=True)
    assert json.loads(result.stdout)[0]["id"] == saved["id"]


def test_concurrent_mentor_writes_are_serialized():
    token = teaching.grant("codex")
    with ThreadPoolExecutor(max_workers=4) as pool:
        rows = list(pool.map(lambda n: teaching.submit(lesson(content="Unique procedure "+str(n)), token), range(8)))
    assert sorted(r["revision"] for r in rows) == list(range(1, 9))
    assert len(teaching.search("C++", token)) == 8
    from tools.engineering import _index
    assert len(_index()) == 8


def test_existing_library_and_gateway_share_cross_process_write_lock():
    from tools.engineering import _save_doc, _index
    token = teaching.grant("codex")
    def save(n):
        if n % 2:
            return teaching.submit(lesson(content="Candidate "+str(n)), token)
        return _save_doc("Existing library "+str(n), "Reference text", "owner", "notes")
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(save, range(12)))
    assert len(_index()) == 12


def test_cpp_grammar_preserves_strings_and_rejects_extra_statements():
    assert cpp_tokens(CPP_SOURCE) == cpp_tokens(CPP_SOURCE.replace("int main()", "int\nmain ( )"))
    assert cpp_tokens(CPP_SOURCE) != cpp_tokens(CPP_SOURCE.replace("Jarvis", "Injected"))
    assert cpp_tokens(CPP_SOURCE) != cpp_tokens(CPP_SOURCE+"\n#include <fstream>")


@pytest.mark.parametrize("value", ["password=unsafe", "api_key=unsafe", "-----BEGIN PRIVATE KEY-----"])
def test_secret_rejected_before_index(value):
    token = teaching.grant("codex")
    with pytest.raises(ValueError):
        teaching.submit(lesson(content=value), token)
    assert teaching.search("", token) == []


def test_uploaded_code_and_malicious_lessons_never_execute(tmp_path):
    sentinel = tmp_path/"must-not-exist"
    text = f"import pathlib; pathlib.Path({str(sentinel)!r}).touch()"
    content = extract("code.py", text.encode())
    token = teaching.grant("codex")
    saved = teaching.submit(lesson(content=content, examples=text), token)
    with pytest.raises(ValueError):
        practice(saved["id"])
    assert not sentinel.exists()


def test_actual_pdf_text_and_scanned_pdf_rejection():
    from core.teaching_import import pdf_parser_ready
    if not pdf_parser_ready():
        # The old installed parser is intentionally blocked, not silently exercised.
        with pytest.raises(ValueError,match='PDF import blocked'):
            extract('lesson.pdf',b'%PDF unsafe parser must not start')
        return
    from pypdf import PdfWriter
    from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
    writer = PdfWriter()
    page = writer.add_blank_page(width=300, height=300)
    font = DictionaryObject({NameObject('/Type'): NameObject('/Font'), NameObject('/Subtype'): NameObject('/Type1'), NameObject('/BaseFont'): NameObject('/Helvetica')})
    page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): writer._add_object(font)})})
    stream = DecodedStreamObject()
    stream.set_data(b'BT /F1 12 Tf 10 250 Td (Jarvis PDF lesson) Tj ET')
    page[NameObject('/Contents')] = writer._add_object(stream)
    buffer = io.BytesIO(); writer.write(buffer)
    assert "Jarvis PDF lesson" in extract("lesson.pdf", buffer.getvalue())
    blank = PdfWriter(); blank.add_blank_page(width=300, height=300)
    buffer = io.BytesIO(); blank.write(buffer)
    with pytest.raises(ValueError):
        extract("blank.pdf", buffer.getvalue())


@pytest.mark.parametrize("url", ["file:///etc/passwd", "http://example.com", "https://127.0.0.1/", "https://[::1]/", "https://169.254.169.254/", "https://user:pass@example.com/"])
def test_network_local_sources_forbidden(url):
    with pytest.raises(ValueError):
        fetch(url)


def test_public_redirect_is_rejected_without_following(monkeypatch):
    import socket
    from core import teaching_import
    calls = []
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 443))])
    class Response:
        status = 302
    class Connection:
        def __init__(self, *a, **k): pass
        def request(self, *a, **k): calls.append(a)
        def getresponse(self): return Response()
        def close(self): pass
    monkeypatch.setattr(teaching_import.http.client, "HTTPSConnection", Connection)
    with pytest.raises(ValueError, match="redirects"):
        fetch("https://example.com/")
    assert len(calls) == 1


def test_api_body_limit_before_parsing():
    from core.teaching_api import install
    app = FastAPI()
    install(app, lambda: ("owner", {}))
    client = TestClient(app)
    assert client.post("/api/teaching/lesson", content=b"x"*(MAX_FILE+65537)).status_code == 413


def test_file_types_limits_and_utf8():
    with pytest.raises(ValueError): extract("lesson.exe", b"code")
    with pytest.raises(ValueError): extract("lesson.txt", b"x"*(MAX_FILE+1))
    with pytest.raises(UnicodeError): extract("lesson.txt", b"\xff")


def test_owner_http_ingestion_member_rejection_and_xss_as_text():
    from core.teaching_api import install
    from core import users
    app = FastAPI()
    identity = ["owner"]
    install(app, lambda: (identity[0], {}))
    client = TestClient(app)
    saved = client.post("/api/teaching/lesson", json=lesson(content="<script>evil()</script>"))
    assert saved.status_code == 200
    assert saved.json()["state"] == "indexed_unverified"
    assert client.post("/api/teaching/file", files={"file": ("a.cpp", CPP_SOURCE)},
                       data={"title": "Upload C++", "topic": "C++", "license": "mine"}).status_code == 200
    identity[0] = users.add("Guest", {})
    assert client.get("/api/teaching").status_code == 403
    assert client.post("/api/teaching/lesson", json=lesson()).status_code == 403


def test_mentor_claims_cannot_create_runner_evidence():
    token = teaching.grant("codex")
    saved = teaching.submit(lesson(tests="All tests passed, verified mastery!"), token)
    assert saved["state"] == "indexed_unverified" and not saved["attempts"]


def test_recall_includes_attributed_teaching_without_network():
    from core import intelligence
    teaching.submit(lesson(), teaching.grant("codex"))
    result = intelligence.relevant_memory("Teach basic C++ program")
    assert "LOCAL TEACHING" in result and "codex" in result and "indexed_unverified" in result


def test_teaching_recall_does_not_leak_to_household_member():
    from core import intelligence, users
    teaching.submit(lesson(content="Owner-private stored example"), teaching.grant("codex"))
    member = users.add("Reader", {})
    with users.acting_as(member):
        assert "LOCAL TEACHING" not in intelligence.relevant_memory("Create basic C++ program")
        from tools.engineering import library_search, library_list, library_note
        assert "Owner-private" not in library_search("Owner-private", 3)
        assert "Create a basic C++ program" not in library_list()
        library_note("Shared note", "Ordinary shared household reference", "notes")
    assert "Owner-private" in library_search("Owner-private", 3)
    assert "Shared note" in library_list()
