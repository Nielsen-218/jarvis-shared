"""The owner records his Creole voice: the script, the recording page, its routes, the training dataset and the
tuned-voice preference. Nothing real runs: the audio is a synthetic tone in a WAV, the decoder for browser formats and
the voice model are stand-ins, the data folder is the test's own, and no microphone, speaker or network is touched."""
import io
import json
import re
import wave
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from audio import kreyol
from core import kreyol_record as record
from core import kreyol_script, users

APP = Path(__file__).resolve().parent.parent / "app"
RATE = record.RATE


def tone(seconds=1.6, level=0.4, silence=0.8, rate=RATE, clipped=False) -> np.ndarray:
    t = np.arange(int(seconds * rate)) / rate
    voice = np.sin(2 * np.pi * 180 * t) * level
    if clipped:
        voice = np.clip(voice * 20, -1, 1)
    quiet = np.zeros(int(silence * rate))
    return np.concatenate([quiet, voice, quiet]).astype(np.float32)


def wav_bytes(samples: np.ndarray, rate=RATE) -> bytes:
    box = io.BytesIO()
    with wave.open(box, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes((np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes())
    return box.getvalue()


# -- the script ----------------------------------------------------------------------------------------------------------
def test_the_script_is_about_130_short_sentences_in_creole_spelling():
    lines = [s["text"] for s in kreyol_script.SENTENCES]
    assert 125 <= len(lines) <= 140 and len(set(lines)) == len(lines)
    assert [s["id"] for s in kreyol_script.SENTENCES][:2] == ["s001", "s002"]
    for line in lines + kreyol_script.AB_SENTENCES:
        low = line.lower()
        assert 5 <= len(line.split()) <= 14, line
        assert not set(low) - set("abcdefghijklmnoprstuvwyzèòà' ,.?!-"), line           # no q, x, é, digits
        assert not re.search(r"c(?!h)", low), line                                        # c only in ch
        assert not re.search(r"(?<!o)u(?!i)", low), line                                  # u only in ou and ui
        assert line[0].isupper() and line[-1] in ".?!", line
        assert kreyol.prepare(line).replace(" ", "") == re.sub(r"[ ,.?!]", "", low), line   # the voice reads every letter
    assert not set(kreyol_script.AB_SENTENCES) & set(lines)                              # the A/B set is never trained on


def test_the_script_covers_the_sounds_of_creole():
    text = " ".join(s["text"].lower() for s in kreyol_script.SENTENCES)
    for sound in ("an", "en", "on", "oun", "ou", "ui", "ch", "j", "r", "tch", "dj", "è", "ò", "e ", "o ", "m ap", "l ap",
                  "n ap", "k ap", "pa t ", "ny", "w", "y", "z", "lendi", "samdi", "dimanch", "janvye", "desanm", "dis"):
        assert text.count(sound) >= (1 if len(sound) > 2 else 3), sound


# -- the page ------------------------------------------------------------------------------------------------------------
def test_the_page_uses_the_design_system_and_records_with_processing_off():
    page = (APP / "voice-record.html").read_text(encoding="utf-8")
    assert '<link rel="stylesheet" href="/design-system.css">' in page
    assert not re.search(r"#[0-9a-fA-F]{6}\b", re.sub(r'<meta name="theme-color"[^>]*>', "", page).split("<script>")[0])
    assert "getUserMedia(MIC)" in page and "MediaRecorder" in page
    mic = page[page.index("const MIC ="):].split("\n")[0]
    for off in ("echoCancellation: false", "noiseSuppression: false", "autoGainControl: false"):
        assert off in mic
    for wanted in ("ds-empty", "ds-skeleton", "ds-toast", "Keep", "Redo", "Skip", "Edit", "clips kept", "15 to 20 cm", "Quiet room".lower()):
        assert wanted in page or wanted in page.lower(), wanted
    assert not re.search(r"\b(alert|confirm|prompt)\(", page)
    assert "/api/voice-record/take" in page and "/api/voice-record/sentence" in page and "localStorage" in page
    assert "/voice-record.html" in (APP / "languages.html").read_text(encoding="utf-8")
    sw = (APP / "sw.js").read_text(encoding="utf-8")
    assert '"/voice-record.html"' in sw and "jarvis-v36-trainer-garmin" not in sw


# -- the routes ----------------------------------------------------------------------------------------------------------
@pytest.fixture
def client(monkeypatch):
    from core import appserver

    monkeypatch.setattr(appserver, "CACHE", appserver.storage.DATA / "cache")
    monkeypatch.setattr(record, "clock", lambda: 1_800_000_000)
    return TestClient(appserver.AppServer(lambda uid: None).app)


def pair(client, uid="owner"):
    from tools import voice

    voice.pair_codes["333333"] = uid
    r = client.post("/api/pair", json={"code": "333333", "device": "Phone"})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['token']}"}


def upload(client, auth, sid="s001", samples=None, name="take.wav", kind="audio/wav", data=None):
    body = data if data is not None else wav_bytes(tone() if samples is None else samples)
    return client.post("/api/voice-record/take", headers=auth, data={"sentence": sid, "seconds": "3.2"},
                       files={"audio": (name, body, kind)})


def test_the_page_is_served_and_the_api_is_for_the_paired_owner_only(client):
    assert client.get("/voice-record.html").status_code == 200
    assert client.get("/api/voice-record").status_code == 401
    assert client.post("/api/voice-record/sentence", json={"id": "s001", "text": "x"}).status_code == 401
    guest = pair(client, users.add("Guest", {}))
    assert client.get("/api/voice-record", headers=guest).status_code == 403
    assert upload(client, guest).status_code == 403
    assert client.post("/api/voice-record/sentence", headers=guest, json={"id": "s001", "text": "Bonjou"}).status_code == 403
    assert client.delete("/api/voice-record/take/s001-0123456789ab", headers=guest).status_code == 403
    assert not record.folder("owner").exists() and not record.folder(users.find("Guest")).exists()


def test_a_take_is_kept_as_trimmed_16k_mono_wav_with_its_metadata(client):
    auth = pair(client)
    start = client.get("/api/voice-record", headers=auth).json()
    assert start["kept"] == 0 and start["total"] == len(kreyol_script.SENTENCES) and start["target_seconds"] == 1200
    assert start["sentences"][0] == {"id": "s001", "text": kreyol_script.SENTENCES[0]["text"],
                                     "original": kreyol_script.SENTENCES[0]["text"], "edited": False, "take": None}
    out = upload(client, auth, data=wav_bytes(tone(rate=48000), rate=48000)).json()          # a 48 kHz upload
    take = out["take"]
    assert take["state"] == "kept" and take["sentence"] == "s001" and 1.6 <= take["seconds"] <= 1.95   # 3.2 s, silence cut
    assert out["data"]["kept"] == 1 and abs(out["data"]["seconds"] - take["seconds"]) < 0.06
    base = record.folder("owner")
    with wave.open(str(base / take["file"]), "rb") as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, 16000)
        assert abs(w.getnframes() / 16000 - take["seconds"]) < 0.02
    assert (base / take["raw"]).is_file()                                                 # the upload itself is kept too
    meta = json.loads((base / "takes" / f"{take['id']}.json").read_text(encoding="utf-8"))
    assert meta["sentence"] == "s001" and meta["created"] == 1_800_000_000
    assert record.kept("owner") == [{"id": take["id"], "sentence": "s001", "text": kreyol_script.SENTENCES[0]["text"],
                                     "file": base / take["file"], "seconds": take["seconds"]}]
    played = client.get(f"/api/voice-record/take/{take['id']}/audio", headers=auth)
    assert played.status_code == 200 and played.content[:4] == b"RIFF"


@pytest.mark.parametrize("samples, word", [
    (tone(level=0.01), "too quiet"), (tone(clipped=True), "too loud"), (tone(seconds=0.3), "too short"),
    (np.zeros(RATE, dtype=np.float32), "too quiet"), (tone(seconds=24), "too long")])
def test_a_bad_take_is_refused_with_a_friendly_reason_and_nothing_is_kept(client, samples, word):
    auth = pair(client)
    r = upload(client, auth, samples=samples)
    assert r.status_code == 400 and word in r.json()["detail"]
    assert client.get("/api/voice-record", headers=auth).json()["kept"] == 0
    assert not list(record.folder("owner").rglob("s001-*"))


def test_bad_uploads_are_a_400(client):
    auth = pair(client)
    assert upload(client, auth, sid="s999").status_code == 400
    assert upload(client, auth, data=b"").status_code == 400
    r = upload(client, auth, data=b"RIFFnot really a wav", name="take.wav")
    assert r.status_code == 400 and "record it again" in r.json()["detail"]
    assert client.delete("/api/voice-record/take/s001-..x", headers=auth).status_code == 404
    assert client.delete("/api/voice-record/take/s001-000000000000", headers=auth).status_code == 404


def test_redo_replaces_and_delete_removes(client):
    auth = pair(client)
    first = upload(client, auth).json()["take"]
    second = upload(client, auth, samples=tone(seconds=2.4)).json()
    assert second["data"]["kept"] == 1 and second["take"]["id"] != first["id"]
    assert not list(record.folder("owner").rglob(f"{first['id']}.*"))
    gone = client.delete(f"/api/voice-record/take/{second['take']['id']}", headers=auth).json()
    assert gone["data"]["kept"] == 0 and gone["data"]["sentences"][0]["take"] is None
    assert not list(record.folder("owner").rglob("s001-*"))


def test_his_correction_of_a_sentence_is_saved_and_is_the_transcript(client):
    auth = pair(client)
    upload(client, auth, sid="s002")
    out = client.post("/api/voice-record/sentence", headers=auth, json={"id": "s002", "text": "  Mwen kontan\nwè ou ankò. "}).json()
    assert out["sentence"] == {"id": "s002", "text": "Mwen kontan wè ou ankò.", "original": kreyol_script.SENTENCES[1]["text"], "edited": True}
    seen = client.get("/api/voice-record", headers=auth).json()
    again = seen["sentences"][1]
    assert again["text"] == "Mwen kontan wè ou ankò." and again["edited"]
    # the take was read from the OLD wording: it asks for a redo and is never trained on with the new text
    assert again["take"]["state"] == "rejected" and "changed after this take" in again["take"]["reason"]
    assert seen["kept"] == 0 and record.kept("owner") == []
    fresh = upload(client, auth, sid="s002").json()
    assert fresh["take"]["state"] == "kept" and fresh["data"]["kept"] == 1
    assert [t["text"] for t in record.kept("owner")] == ["Mwen kontan wè ou ankò."] and len(record.takes("owner")) == 1
    back = client.post("/api/voice-record/sentence", headers=auth, json={"id": "s002", "text": ""}).json()
    assert back["sentence"]["edited"] is False and not list((record.folder("owner") / "edits").glob("*.json"))
    assert back["data"]["kept"] == 0                                   # that take was read from his wording
    assert client.post("/api/voice-record/sentence", headers=auth, json={"id": "s002", "text": "?!"}).status_code == 400
    assert client.post("/api/voice-record/sentence", headers=auth, json={"id": "nope", "text": "Bonjou"}).status_code == 400


def test_each_person_has_their_own_folder():
    record.add_take("s001", wav_bytes(tone()), "take.wav", uid="owner")
    other = users.add("Second", {})
    record.save_sentence("s001", "Bonjou, sa k ap fèt?", uid=other)
    assert record.overview("owner")["kept"] == 1 and record.overview(other)["kept"] == 0
    assert record.sentences("owner")[0]["edited"] is False and record.sentences(other)[0]["edited"] is True
    assert record.folder("owner") != record.folder(other)


def test_a_machine_with_no_decoder_stores_the_upload_and_another_converts_it(client, monkeypatch):
    auth = pair(client)
    monkeypatch.setattr(record, "decoders", lambda: [])                       # the laptop: no PyAV, no ffmpeg
    out = upload(client, auth, sid="s003", data=b"\x1aE\xdf\xa3 webm bytes", name="take.webm", kind="audio/webm").json()
    assert out["take"]["state"] == "pending" and out["take"]["seconds"] == 3.2 and out["data"]["pending"] == 1
    raw = record.folder("owner") / out["take"]["raw"]
    assert raw.suffix == ".webm" and raw.read_bytes().endswith(b"webm bytes")
    assert record.convert_pending("owner") == {"converted": 0, "rejected": 0, "waiting": 1}
    assert record.kept("owner") == []                                         # nothing trains on an unchecked take

    monkeypatch.setattr(record, "decoders", lambda: ["av"])                   # the PC: a stand-in decoder
    monkeypatch.setattr(record, "_decode_av", lambda path: tone(seconds=2.0))
    data = client.get("/api/voice-record", headers=auth).json()               # opening the page there converts it
    assert data["pending"] == 0 and data["sentences"][2]["take"]["state"] == "kept"
    assert 2.0 <= data["sentences"][2]["take"]["seconds"] <= 2.3 and len(record.kept("owner")) == 1


def test_a_take_that_fails_the_later_check_asks_for_a_redo(monkeypatch):
    monkeypatch.setattr(record, "decoders", lambda: [])
    record.add_take("s004", b"opus", "take.ogg", uid="owner")
    monkeypatch.setattr(record, "decoders", lambda: ["av"])
    monkeypatch.setattr(record, "_decode_av", lambda path: tone(level=0.01))
    assert record.convert_pending("owner") == {"converted": 0, "rejected": 1, "waiting": 0}
    row = record.overview("owner")["sentences"][3]
    assert row["take"]["state"] == "rejected" and "too quiet" in row["take"]["reason"] and record.overview("owner")["kept"] == 0


def test_sync_conflict_copies_and_broken_files_are_ignored():
    take = record.add_take("s001", wav_bytes(tone()), "take.wav", uid="owner")
    base = record.folder("owner")
    (base / "takes" / f"{take['id']}.sync-conflict-20261112-101010-ABCDEFG.json").write_text('{"sentence": "s005"}', encoding="utf-8")
    (base / "takes" / "s006-aaaaaaaaaaaa.json").write_text("{half a file", encoding="utf-8")
    (base / "edits").mkdir()
    (base / "edits" / "s001.sync-conflict-20261112-101010-ABCDEFG.json").write_text('{"text": "Lòt bagay"}', encoding="utf-8")
    seen = record.overview("owner")
    assert seen["kept"] == 1 and [s["id"] for s in seen["sentences"] if s["take"]] == ["s001"]
    assert seen["sentences"][0]["edited"] is False
    assert record.delete_take(take["id"], "owner") and not list(base.rglob(f"{take['id']}*"))   # its conflict copy goes too


# -- review fixes --------------------------------------------------------------------------------------------------------
@pytest.fixture
def ticking(monkeypatch):
    ticks = iter(range(1_800_000_000, 1_800_001_000))
    monkeypatch.setattr(record, "clock", lambda: next(ticks))


def test_a_redo_waiting_for_its_check_never_costs_him_the_good_take(monkeypatch, ticking):
    good = record.add_take("s001", wav_bytes(tone()), "take.wav", uid="owner")
    monkeypatch.setattr(record, "decoders", lambda: [])                        # the redo comes in through the laptop
    redo = record.add_take("s001", b"opus", "take.ogg", uid="owner")
    assert redo["state"] == "pending" and [t["id"] for t in record.kept("owner")] == [good["id"]]
    monkeypatch.setattr(record, "decoders", lambda: ["av"])
    monkeypatch.setattr(record, "_decode_av", lambda path: tone(level=0.01))   # ...and the PC finds it too quiet
    assert record.convert_pending("owner") == {"converted": 0, "rejected": 1, "waiting": 0}
    assert [t["id"] for t in record.kept("owner")] == [good["id"]]             # the good take is still there
    seen = record.overview("owner")
    assert seen["kept"] == 1 and seen["sentences"][0]["take"]["id"] == good["id"]
    monkeypatch.setattr(record, "decoders", lambda: [])
    again = record.add_take("s001", b"opus two", "take.ogg", uid="owner")
    monkeypatch.setattr(record, "decoders", lambda: ["av"])
    monkeypatch.setattr(record, "_decode_av", lambda path: tone(seconds=2.0))
    assert record.convert_pending("owner") == {"converted": 1, "rejected": 0, "waiting": 0}
    assert [t["id"] for t in record.takes("owner")] == [again["id"]]           # only now are the older ones removed
    assert [t["id"] for t in record.kept("owner")] == [again["id"]]


def test_nothing_longer_than_half_a_minute_is_decoded(tmp_path, monkeypatch):
    import sys
    import types

    long = tmp_path / "long.wav"
    long.write_bytes(wav_bytes(tone(seconds=50, silence=0, rate=8000), rate=8000))
    samples = record.decode(long)
    assert samples.size <= 31 * RATE and "too long" in record.check(samples)[1]

    ran = []

    def run(cmd, capture_output, timeout):
        ran.append((cmd, timeout))
        return types.SimpleNamespace(returncode=0, stdout=b"\x00\x00" * 10, stderr=b"")
    monkeypatch.setattr(record.subprocess, "run", run)
    record._decode_ffmpeg(tmp_path / "take.webm", "ffmpeg")
    cmd, timeout = ran[0]
    before = cmd[:cmd.index("-i")]
    assert before[before.index("-protocol_whitelist") + 1] == "file,pipe" and before[before.index("-f") + 1] == "matroska"
    assert before[before.index("-t") + 1] == "30" and timeout <= 20

    class Frame:
        def to_ndarray(self):
            return np.zeros((1, RATE), dtype=np.int16)

    class Box:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def decode(self, audio=0):
            while True:                                                        # a file that never ends
                yield Frame()
    fake = types.SimpleNamespace(open=lambda path: Box(),
                                 AudioResampler=lambda **kw: types.SimpleNamespace(resample=lambda frame: [frame] if frame else []))
    monkeypatch.setitem(sys.modules, "av", fake)
    assert record._decode_av(tmp_path / "endless.webm").size <= 31 * RATE


def test_a_page_load_converts_one_take_and_never_fails_on_a_conversion_problem(client, monkeypatch):
    auth = pair(client)
    monkeypatch.setattr(record, "decoders", lambda: [])
    for sid in ("s001", "s002"):
        upload(client, auth, sid=sid, data=b"opus", name="take.ogg", kind="audio/ogg")
    monkeypatch.setattr(record, "decoders", lambda: ["av"])
    monkeypatch.setattr(record, "_decode_av", lambda path: tone(seconds=2.0))
    first = record.takes("owner")[0]["id"]
    with record._lock(first):                                                  # another load is converting it right now
        assert record.convert_pending("owner", limit=1) == {"converted": 1, "rejected": 0, "waiting": 1}
    assert client.get("/api/voice-record", headers=auth).json()["pending"] == 0     # one take a load
    assert not list(record.folder("owner").rglob("*.tmp"))

    monkeypatch.setattr(record, "decoders", lambda: [])
    upload(client, auth, sid="s003", data=b"opus", name="take.ogg", kind="audio/ogg")
    monkeypatch.setattr(record, "decoders", lambda: ["av"])

    def locked(base, take, raw):
        raise PermissionError("the file is in use")
    monkeypatch.setattr(record, "_convert", locked)
    assert record.convert_pending("owner") == {"converted": 0, "rejected": 0, "waiting": 1}
    monkeypatch.setattr(record, "convert_pending", lambda uid=None, limit=0: 1 / 0)
    r = client.get("/api/voice-record", headers=auth)
    assert r.status_code == 200 and r.json()["pending"] == 1


def test_an_oversized_upload_is_refused_by_its_declared_size(client, monkeypatch):
    auth = pair(client)
    monkeypatch.setattr(record, "MAX_BYTES", 2000)
    monkeypatch.setattr(record, "add_take", lambda *a, **k: pytest.fail("the body is never handed on"))
    r = upload(client, auth)
    assert r.status_code == 413 and "too large" in r.json()["detail"]
    assert not record.folder("owner").exists()


def test_the_page_lets_go_of_the_microphone_and_keeps_a_take_whose_upload_failed():
    page = (APP / "voice-record.html").read_text(encoding="utf-8")
    stop = page[page.index("recorder.onstop"):page.index("started = performance.now()")]
    assert "closeMic()" in stop                                               # the stream ends with the take
    assert 'addEventListener("visibilitychange"' in page and "document.hidden" in page
    keep = page[page.index("async function keep()"):page.index("async function act(")]
    assert "take = null" not in keep and "take.failed = true" in keep and "Try again" in page


def test_the_trainer_will_not_install_into_a_worktree_and_survives_a_missing_stock_voice(tmp_path, monkeypatch, capsys):
    from scripts import kreyol_finetune as ft

    record.add_take("s001", wav_bytes(tone()), "take.wav", uid="owner")
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / ".git").write_text("gitdir: elsewhere", encoding="utf-8")
    monkeypatch.setattr(ft, "ROOT", tree)
    monkeypatch.setattr(ft, "prerequisites", lambda recipe, checkpoint: [])
    monkeypatch.setattr(ft, "MIN_CLIPS", 1)
    work = tmp_path / "work"

    def train(recipe, config):
        (work / "trained").mkdir(parents=True)
        for name in kreyol.TUNED_FILES:
            (work / "trained" / name).write_bytes(b"{}")
        return 0
    monkeypatch.setattr(ft, "train", train)
    assert ft.main(["--work", str(work)]) == 2
    said = capsys.readouterr().out
    assert "worktree" in said and "--target" in said and not work.exists()

    def no_stock(out, tuned):
        raise OSError("facebook/mms-tts-hat is not in the local cache")
    monkeypatch.setattr(ft, "ab_listen", no_stock)
    target = tmp_path / "models" / "kreyol-tuned"
    assert ft.main(["--work", str(work), "--target", str(target)]) == 0        # installed; the listen is what is missing
    said = capsys.readouterr().out
    assert said.index("Installs to:") < said.index("Recordings:") and str(work) in said.split("Recordings:")[0]
    assert (target / "model.safetensors").is_file() and "A/B listen was not written" in said and "not in the local cache" in said
    assert ft.main(["--ab-only", "--work", str(work), "--target", str(target)]) == 1
    assert "A/B listen was not written" in capsys.readouterr().out


# -- the training script -------------------------------------------------------------------------------------------------
def test_the_dataset_is_built_from_the_kept_takes(tmp_path):
    from scripts import kreyol_finetune as ft

    record.save_sentence("s001", "Bonjou, mwen gen 3 mesaj!", uid="owner")       # corrected first, then read
    for n in range(1, 13):
        record.add_take(f"s{n:03d}", wav_bytes(tone(seconds=1.5 + n / 10)), "take.wav", uid="owner")
    takes = record.kept("owner")
    broken = tmp_path / "broken.wav"
    broken.write_bytes(b"nope")
    loud = tmp_path / "wrong-rate.wav"
    loud.write_bytes(wav_bytes(tone(), rate=44100))
    takes += [{"id": "s050-bad", "sentence": "s050", "text": "Sa pa bon.", "file": broken, "seconds": 2},
              {"id": "s051-rate", "sentence": "s051", "text": "Sa pa bon.", "file": loud, "seconds": 2},
              {"id": "s052-text", "sentence": "s052", "text": "?!", "file": takes[0]["file"], "seconds": 2}]
    out = tmp_path / "dataset"
    plan = ft.build_dataset(takes, out, write=False)
    assert not out.exists() and len(plan["train"]) == 11 and len(plan["test"]) == 1          # a dry run writes nothing
    data = ft.build_dataset(takes, out)
    assert [r["id"] for r in data["test"]] == [takes[9]["id"]]
    assert {why.split(" ")[0] for _, why in data["skipped"]} == {"unreadable", "not", "no"} and len(data["skipped"]) == 3
    rows = (out / "train" / "metadata.csv").read_text(encoding="utf-8").splitlines()
    assert rows[0] == "file_name,text" and len(rows) == 12
    assert rows[1] == f"{takes[0]['id']}.wav,bonjou mwen gen twa mesaj"                      # what the voice is given
    for row in rows[1:]:
        with wave.open(str(out / "train" / row.split(",")[0]), "rb") as w:
            assert w.getframerate() == 16000 and w.getnchannels() == 1
    assert len(list((out / "test").glob("*.wav"))) == 1
    assert abs(data["seconds"] - sum(t["seconds"] for t in takes[:12])) < 0.1
    (out / "train" / "stale.wav").write_bytes(b"x")
    ft.build_dataset(takes[:2], out)
    assert not (out / "train" / "stale.wav").exists() and len(list((out / "train").glob("*.wav"))) == 2
    report = "\n".join(ft.report(data, 2))
    assert "12 good clips" in report and "2 takes still waiting" in report and "NOT ENOUGH" in report


def test_prerequisites_say_exactly_what_to_fetch(tmp_path):
    from scripts import kreyol_finetune as ft

    recipe, checkpoint = tmp_path / "finetune-hf-vits", tmp_path / "ckpt"
    missing = ft.prerequisites(recipe, checkpoint, python="py", have=lambda name: name != "datasets", cuda=False)
    text = "\n".join(missing)
    assert len(missing) == 4 and "pip install datasets" in text and "No CUDA GPU" in text
    assert f"git clone {ft.RECIPE_URL}" in text and "convert_original_discriminator_checkpoint.py --language_code hat" in text
    recipe.mkdir()
    (recipe / "run_vits_finetuning.py").write_text("", encoding="utf-8")
    text = "\n".join(ft.prerequisites(recipe, checkpoint, python="py", have=lambda name: True, cuda=True))
    assert "build_ext --inplace" in text and "Cython" in text and "git clone" not in text
    (recipe / "monotonic_align" / "monotonic_align").mkdir(parents=True)
    (recipe / "monotonic_align" / "monotonic_align" / "core.cp313-win_amd64.pyd").write_bytes(b"")
    checkpoint.mkdir()
    (checkpoint / "config.json").write_text("{}", encoding="utf-8")
    (checkpoint / "model.safetensors").write_bytes(b"")
    assert ft.prerequisites(recipe, checkpoint, have=lambda name: True, cuda=True) == []
    config = ft.training_config(tmp_path / "d", checkpoint, tmp_path / "o", epochs=3)
    assert config["model_name_or_path"] == str(checkpoint) and config["push_to_hub"] is False and config["num_train_epochs"] == 3
    calls = []
    assert ft.train(recipe, tmp_path / "c.json", python="py", run=lambda cmd, cwd: calls.append((cmd, cwd)) or type("R", (), {"returncode": 0})) == 0
    assert calls == [(["py", "-m", "accelerate.commands.launch", "run_vits_finetuning.py", str(tmp_path / "c.json")], str(recipe))]


def test_a_dry_run_reports_and_writes_nothing(tmp_path, monkeypatch, capsys):
    from scripts import kreyol_finetune as ft

    record.add_take("s001", wav_bytes(tone()), "take.wav", uid="owner")
    monkeypatch.setattr(ft, "prerequisites", lambda recipe, checkpoint: ["The training recipe is not at X. Fetch it"])
    monkeypatch.setattr(ft, "train", lambda *a, **k: pytest.fail("a dry run never trains"))
    monkeypatch.setattr(ft, "ab_listen", lambda *a, **k: pytest.fail("a dry run never loads a voice"))
    assert ft.main(["--dry-run", "--work", str(tmp_path / "work"), "--out", str(tmp_path / "tuned")]) == 1
    said = capsys.readouterr().out
    assert "1 good clip," in said and "NOT ENOUGH" in said and "1 thing missing" in said and "Fetch it" in said
    assert "nothing was written" in said and not (tmp_path / "work").exists() and not (tmp_path / "tuned").exists()
    assert ft.main(["--work", str(tmp_path / "work"), "--out", str(tmp_path / "tuned")]) == 1     # not ready: never starts
    assert not (tmp_path / "work").exists()


def test_the_trained_voice_is_installed_and_the_previous_one_kept(tmp_path):
    from scripts import kreyol_finetune as ft

    trained, stock, target = tmp_path / "trained", tmp_path / "stock", tmp_path / "models" / "kreyol-tuned"
    trained.mkdir()
    stock.mkdir()
    assert "did not finish" in ft.install_model(trained, target)[0] and not target.exists()
    (trained / "config.json").write_text("{}", encoding="utf-8")
    (trained / "model.safetensors").write_bytes(b"one")
    assert "vocab.json" in ft.install_model(trained, target)[0] and not target.exists()
    (stock / "vocab.json").write_text("{}", encoding="utf-8")
    assert ft.install_model(trained, target, stock) == [] and (target / "vocab.json").is_file()
    (trained / "model.safetensors").write_bytes(b"two")
    assert ft.install_model(trained, target, stock) == []
    assert (target / "model.safetensors").read_bytes() == b"two"
    assert (target.with_name("kreyol-tuned.previous") / "model.safetensors").read_bytes() == b"one"


def test_the_ab_listen_says_the_same_six_sentences_with_both_voices(tmp_path):
    from scripts import kreyol_finetune as ft

    asked = []

    def speaker(source):
        def say(text):
            asked.append((source, text))
            return (np.ones(800) * 1000).astype("<i2"), 16000
        return say
    files = ft.ab_listen(tmp_path / "ab", tmp_path / "tuned", speaker)
    assert [p.name for p in files] == [f"{n}-{v}.wav" for v in ("stock", "tuned") for n in range(1, 7)]
    assert [t for s, t in asked if s == ft.STOCK] == [t for s, t in asked if s != ft.STOCK] == kreyol_script.AB_SENTENCES
    with wave.open(str(files[0]), "rb") as w:
        assert w.getframerate() == 16000 and w.getnframes() == 800
    assert len(ft.ab_listen(tmp_path / "ab2", None, speaker)) == 6                         # no tuned voice yet: stock only


# -- using the tuned voice -----------------------------------------------------------------------------------------------
class FakeModel:
    def to(self, device):
        return self


@pytest.fixture
def loads(monkeypatch):
    from audio import tts

    seen = []

    def load(source, local_only=True):
        seen.append(source)
        if "broken" in source:
            raise OSError("half a file")
        return FakeModel(), object()
    monkeypatch.setattr(tts, "_creole_load", load)
    monkeypatch.setattr(tts, "_creole_device", lambda: "cpu")
    return seen


def tuned_folder(path: Path) -> Path:
    path.mkdir(parents=True)
    for name in kreyol.TUNED_FILES:
        (path / name).write_bytes(b"{}")
    return path


def test_the_tuned_voice_is_preferred_when_its_folder_is_complete(tmp_path, monkeypatch, loads):
    from audio import tts

    monkeypatch.setattr(kreyol, "TUNED_DIR", tmp_path / "none")
    assert kreyol.tuned_model() is None and tts._CreoleVoice().source == tts.CREOLE_MODEL and loads == [tts.CREOLE_MODEL]
    folder = tuned_folder(tmp_path / "kreyol-tuned")
    monkeypatch.setattr(kreyol, "TUNED_DIR", folder)
    del loads[:]
    assert kreyol.tuned_model() == folder and tts._CreoleVoice().source == str(folder) and loads == [str(folder)]
    (folder / "model.safetensors").unlink()                                   # a folder still being copied is not used
    assert kreyol.tuned_model() is None


def test_a_setting_switches_back_to_the_stock_voice(tmp_path, monkeypatch, loads):
    from audio import tts

    folder = tuned_folder(tmp_path / "elsewhere")
    monkeypatch.setenv("JARVIS_KREYOL_TUNED", str(folder))                    # a path names another folder
    assert kreyol.tuned_model() == folder
    monkeypatch.setattr(kreyol, "TUNED_DIR", folder)
    monkeypatch.setenv("JARVIS_KREYOL_TUNED", "off")
    assert kreyol.tuned_model() is None and tts._CreoleVoice().source == tts.CREOLE_MODEL and loads == [tts.CREOLE_MODEL]


def test_a_broken_tuned_voice_falls_back_to_the_stock_one(tmp_path, monkeypatch, loads):
    from audio import tts

    monkeypatch.setattr(kreyol, "TUNED_DIR", tuned_folder(tmp_path / "broken-tuned"))
    voice = tts._CreoleVoice()
    assert voice.source == tts.CREOLE_MODEL and loads == [str(tmp_path / "broken-tuned"), tts.CREOLE_MODEL]


def test_a_machine_with_only_the_tuned_voice_has_a_creole_voice(tmp_path, monkeypatch):
    from audio import tts

    monkeypatch.setattr(kreyol, "TUNED_DIR", tuned_folder(tmp_path / "kreyol-tuned"))
    monkeypatch.setenv("JARVIS_KREYOL_MMS", "")
    monkeypatch.setattr(tts, "_creole_ready", None)
    assert tts.creole_ready() is True
    monkeypatch.setenv("JARVIS_KREYOL_MMS", "off")                           # the old switch still turns Creole MMS off
    monkeypatch.setattr(tts, "_creole_ready", None)
    assert tts.creole_ready() is False
