"""Child-process PDF extractor. No OCR, network, embedded files or scripts."""
import json
import sys
import socket


def main():
    from core.pdf_policy import parser_ready
    if not parser_ready():raise ValueError('PDF parser dependency is outside the reviewed range')
    def deny_network(*a, **k):
        raise PermissionError("PDF extraction has no network capability")
    socket.socket.connect = deny_network
    socket.create_connection = deny_network
    from pypdf import PdfReader
    reader = PdfReader(sys.argv[1])
    if reader.is_encrypted or len(reader.pages) > 100:
        raise ValueError("Encrypted PDF or too many pages")
    parts = []
    size = 0
    readable = False
    for n, page in enumerate(reader.pages, 1):
        extracted = page.extract_text() or ""
        readable |= bool(extracted.strip())
        text = f"[Page {n}]\n" + extracted
        size += len(text)
        if size > 190000:
            raise ValueError("Too much extracted text")
        parts.append(text)
    if not readable:
        raise ValueError("Scanned PDF needs OCR")
    print(json.dumps("\n\n".join(parts)))


if __name__ == "__main__":
    main()
