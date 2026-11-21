"""The study document store: parsing, chapter/section passages with pages, BM25 search, courses (tmp data only)."""
import io

import pytest

from core import storage, study_seed, study_store

NOTES = """# Chapter 1: Hardware

## 1.1 Memory
RAM is volatile working memory. It loses its contents when power is removed.

# Chapter 2: Operating Systems

The kernel schedules processes and manages memory.

# Chapter 3: Switching

## 3.1 Frames
A switch forwards frames using the MAC address table. It learns the source address of every frame it receives.

## 3.2 VLANs
A VLAN splits one switch into separate broadcast domains. Trunk ports carry several VLANs with 802.1Q tags.
"""


def _pdf(pages):
    """A small text PDF, one string per page, built with pypdf's own writer objects (no other library)."""
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = PdfWriter()
    for text in pages:
        page = writer.add_blank_page(width=612, height=792)
        font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"),
                                 NameObject("/BaseFont"): NameObject("/Helvetica")})
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
        lines = "".join(f"({line}) Tj 0 -16 Td " for line in text.split("\n"))
        stream = DecodedStreamObject()
        stream.set_data(f"BT /F1 12 Tf 72 720 Td {lines}ET".encode())
        page[NameObject("/Contents")] = writer._add_object(stream)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def test_markdown_is_split_by_chapter_and_section_and_never_across_them():
    chunks = study_store.chunk(study_store.pages_of(NOTES.encode(), "notes.md"))
    assert [c["chapter"] for c in chunks] == ["Chapter 1: Hardware", "Chapter 2: Operating Systems",
                                              "Chapter 3: Switching", "Chapter 3: Switching"]
    assert chunks[2]["section"] == "3.1 Frames" and "MAC address table" in chunks[2]["text"]
    assert "VLAN" not in chunks[2]["text"]
    assert chunks[1]["section"] == ""                       # a new chapter clears the old section


def test_a_long_paragraph_is_split_between_sentences():
    sentence = "The quick packet crosses the lazy router on its way home. "
    chunks = study_store.chunk([(7, sentence * 60)])
    assert len(chunks) > 2 and all(len(c["text"]) <= study_store.CHUNK_CHARS + len(sentence) for c in chunks)
    assert all(c["text"].endswith(".") and c["page"] == 7 for c in chunks)


def test_a_pdf_keeps_its_page_numbers_and_search_cites_the_page():
    pytest.importorskip("pypdf")
    filler = "Routers forward packets between networks using the destination address in each packet header."
    data = _pdf([f"Chapter 1 Routing\n{filler}\n{filler}", f"Chapter 2 Naming\n{filler}",
                 "Chapter 3 Switching\nA switch forwards frames using the MAC address table.\nIt floods unknown destinations."])
    info = study_store.add_material("Networking", "Net book", data, "net.pdf")
    assert info["kind"] == "pdf" and info["pages"] == 3 and info["chapters"][-1].startswith("Chapter 3")
    hit = study_store.search("mac address table", info["course"])[0]
    assert hit["page"] == 3 and "MAC address table" in hit["text"]
    assert study_store.cite(hit) == "Net book, Chapter 3 Switching, page 3"


def test_unreadable_formats_say_what_to_do():
    with pytest.raises(study_store.StudyError, match="PDF or text"):
        study_store.pages_of(b"PK\x03\x04", "slides.pptx")
    with pytest.raises(study_store.StudyError, match="no readable text"):
        study_store.add_material("Empty", "x", "   ")


def test_bm25_ranks_the_passage_about_the_question_first_and_stems_plurals():
    info = study_store.add_material("Net 101", "Class notes", NOTES, "notes.md")
    hits = study_store.search("what are vlans", info["course"])
    assert hits[0]["section"] == "3.2 VLANs" and hits[0]["score"] > 0
    assert study_store.search("zebra crossing", info["course"]) == []
    assert study_store.search("kernel", info["course"], chapter="Chapter 3: Switching") == []


def test_courses_list_materials_and_always_include_the_builtin_one():
    assert [c["id"] for c in study_store.courses()] == [study_seed.ID]
    first = study_store.add_material("Net 101", "Class notes", NOTES)
    again = study_store.add_material("Net 101", "Class notes", NOTES)
    assert again["id"] == first["id"] and again["duplicate"] and not first["duplicate"]
    mine, builtin = study_store.courses()
    assert mine["title"] == "Net 101" and len(mine["materials"]) == 1 and mine["chapters"] == 3
    assert builtin["builtin"] and builtin["chapters"] == len(study_seed.OUTLINE)
    assert study_store.find_course("net")["id"] == "net-101" and study_store.find_course("")["id"] == "net-101"
    assert study_store.find_course("astronomy") is None
    assert study_store.remove_material(first["id"]) and not study_store.remove_material("../../x")
    assert study_store.courses()[0]["materials"] == []


def test_chapters_are_found_by_number_word_or_name():
    cid = study_store.add_material("Net 101", "Class notes", NOTES)["course"]
    for wanted in ("3", "chapter three", "everything in Chapter 3", "switching"):
        assert study_store.find_chapter(cid, wanted) == "Chapter 3: Switching", wanted
    assert study_store.find_chapter(cid, "chapter 9") is None
    assert study_store.find_chapter(study_seed.ID, "networking") == "Networking"
    assert study_store.find_chapter(study_seed.ID, "chapter 2") == "Operating Systems"


def test_sync_conflict_copies_and_half_written_files_are_ignored():
    info = study_store.add_material("Net 101", "Class notes", NOTES)
    folder = storage.user_dir() / "study" / "materials"
    (folder / f"{info['id']}.sync-conflict-20261112-000000-ABCDEFG.json").write_text("{}", encoding="utf-8")
    (folder / "broken.json").write_text("{\"id\": ", encoding="utf-8")
    assert len(study_store.courses()[0]["materials"]) == 1
    assert (folder / "broken.json").exists()                # never renamed or "repaired" by a read
    courses = storage.user_dir() / "study" / "courses"
    (courses / "half.json").write_text("{\"id\": \"half\", ", encoding="utf-8")          # the other machine, mid-sync
    (courses / "old.corrupt.json").write_text("{\"id\": \"old\", \"title\": \"Old\"}", encoding="utf-8")
    assert [c["id"] for c in study_store.courses()] == ["net-101", "it-fundamentals"]
    assert (courses / "half.json").exists() and not list(courses.glob("half.corrupt*"))
    (courses / "half.json").write_text("{\"id\": \"half\", \"title\": \"Half\", \"created\": \"2026\"}", encoding="utf-8")
    assert "half" in [c["id"] for c in study_store.courses()]                              # whole now: picked up


def test_the_builtin_outline_is_searchable_and_every_concept_is_a_passage():
    passages = study_store.passages(study_seed.ID)
    assert len(passages) >= 50 and len({p["id"] for p in passages}) == len(passages)
    hit = study_store.search("which service gives a device its ip address automatically")[0]
    assert hit["concept"] == "DHCP" and study_store.cite(hit) == "Concept outline, Networking, DHCP"
