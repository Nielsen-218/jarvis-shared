"""The tutor's whiteboard: an explanation drawn step by step.

A BOARD is a small JSON scene the Study page animates. It is data, never markup: the page builds SVG nodes from it
and sets every piece of text with textContent.

    {"v": 1, "w": 1000, "h": 560, "steps": [{"say": "the sentence shown and spoken", "ops": [op, ...]}, ...]}

The fixed vocabulary of drawing operations (anything else is dropped):
    {"op": "box",    "id", "x", "y", "w", "h", "text"}          x, y are the CENTRE of the shape
    {"op": "circle", "id", "x", "y", "r", "text"}
    {"op": "icon",   "id", "icon", "x", "y", "size", "text"}    icon: one of ICONS
    {"op": "label",  "id", "x", "y", "text"}
    {"op": "line",   "id", "x1", "y1", "x2", "y2"}
    {"op": "arrow",  "id", "from", "to", "text"}                between two shapes already on the board
    {"op": "move",   "path": [shape ids], "text"}               a token travels shape to shape (a packet)
    {"op": "highlight", "target"}   {"op": "bounce", "target"}   {"op": "erase", "target"}   {"op": "erase", "all": true}

Where a board comes from, best first:
  * hand-made scenes for the built-in networking concepts (HAND), with fewer and bigger shapes on the simpler rungs;
  * the local model's scene, written as JSON next to its explanation: parsed by the caller, then validate() keeps only
    what is in the vocabulary, clamps every number, drops operations whose targets do not exist, strips markup from
    every text, and lays the shapes out in code (the model is never trusted with coordinates that overlap);
  * fallback(): a generic but correct hub-and-spoke layout built in code from the explanation's own key terms.
Pure: no model, no storage."""
import math
import re

from core.study_ladder import RUNGS, _SPLIT

W, H = 1000, 560
ICONS = ("computer", "phone", "router", "server", "cloud", "lock", "key", "database", "person")
MAX_STEPS, MAX_OPS, MAX_LABELS, MAX_LINES = 8, 10, 4, 6
CAP = {"college": 12, "high": 10, "middle": 8, "elementary": 6, "prek": 4, "one": 2}     # shapes on one board
SCALE = {"college": 1.0, "high": 1.0, "middle": 1.15, "elementary": 1.3, "prek": 1.7, "one": 2.3}
_SOLID = ("box", "circle", "icon")
_BAD = re.compile(r"<\s*/?\s*(?:script|style|iframe|svg|img|object|embed|link|meta)\b|javascript:|data:text/html|\bon\w+\s*=", re.I)


def sentences(text: str) -> list[str]:
    return [s.strip() for s in _SPLIT.split(" ".join(str(text or "").split())) if s.strip()]


# ---- reading a scene somebody else wrote ----------------------------------------------------------------------------
def _label(value, limit=40) -> str:
    """Text for the board: a short plain string. Markup is removed; a label that carries script is dropped whole."""
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return ""
    text = str(value)
    if _BAD.search(text):
        return ""
    text = re.sub(r"<[^>]{0,200}>", " ", text)
    text = re.sub(r"[<>\x00-\x1f\x7f  ]", " ", text)
    return " ".join(text.split())[:limit].strip()


def _id(value) -> str:
    return re.sub(r"[^a-z0-9_-]", "", str(value if isinstance(value, (str, int)) and not isinstance(value, bool) else "").lower())[:24]


def _num(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(value)


def _clamp(value, low, high, default):
    value = _num(value)
    return default if value is None else round(max(low, min(high, value)), 1)


def validate(scene, rung: str = "college", layout: str = "auto"):
    """A scene from anywhere -> a scene the page can draw, or None when nothing usable is left.
    layout: "auto" places the shapes in code unless every one has coordinates and none overlap; "keep" only clamps
    (hand-made scenes); "force" always places them in code."""
    rung = rung if rung in RUNGS else "college"
    steps = scene.get("steps") if isinstance(scene, dict) else scene
    if not isinstance(steps, list):
        return None
    shapes, live, arrows, out = {}, set(), {}, []
    labels = lines = 0
    for step in steps[:MAX_STEPS]:
        if not isinstance(step, dict):
            continue
        ops = []
        for op in (step.get("ops") if isinstance(step.get("ops"), list) else [])[:MAX_OPS * 3]:
            if not isinstance(op, dict) or len(ops) >= MAX_OPS:
                continue
            kind = str(op.get("op") or "").lower()
            text = _label(op.get("text") if op.get("text") is not None else op.get("label"))
            if kind in ("box", "circle", "icon", "label"):
                sid = _id(op.get("id")) or f"s{len(shapes) + 1}"
                if sid in shapes or sid in arrows:
                    continue
                if kind == "icon" and op.get("icon") not in ICONS:
                    if not text:
                        continue
                    kind = "box"                                  # an icon this board does not have: its name in a box
                if kind == "label":
                    if not text or labels >= MAX_LABELS:
                        continue
                    labels += 1
                elif sum(s["op"] in _SOLID for s in shapes.values()) >= CAP[rung]:
                    continue
                clean = {"op": kind, "id": sid, "text": text, "x": _num(op.get("x")), "y": _num(op.get("y"))}
                if kind == "icon":
                    clean.update(icon=op["icon"], size=_num(op.get("size")))
                elif kind == "box":
                    clean.update(w=_num(op.get("w")), h=_num(op.get("h")))
                elif kind == "circle":
                    clean.update(r=_num(op.get("r")))
                shapes[sid] = clean
                live.add(sid)
                ops.append(clean)
            elif kind == "line":
                pts = [_num(op.get(k)) for k in ("x1", "y1", "x2", "y2")]
                sid = _id(op.get("id")) or f"l{lines + 1}"
                if None in pts or lines >= MAX_LINES or sid in shapes or sid in arrows:
                    continue
                lines += 1
                arrows[sid] = None                                # a line can be highlighted or erased, never an arrow's end
                ops.append({"op": "line", "id": sid, "x1": _clamp(pts[0], 0, W, 0), "y1": _clamp(pts[1], 0, H, 0),
                            "x2": _clamp(pts[2], 0, W, 0), "y2": _clamp(pts[3], 0, H, 0)})
            elif kind == "arrow":
                a, b = _id(op.get("from")), _id(op.get("to"))
                sid = _id(op.get("id")) or f"{a}-{b}"[:24]
                if a == b or a not in live or b not in live or sid in shapes or sid in arrows or (a, b) in arrows.values():
                    continue
                arrows[sid] = (a, b)
                ops.append({"op": "arrow", "id": sid, "from": a, "to": b, "text": text[:28]})
            elif kind == "move":
                path = op.get("path")
                if not isinstance(path, list):
                    along = arrows.get(_id(op.get("along")))
                    path = list(along) if along else [op.get("from"), op.get("to")]
                stops = []
                for p in path[:6]:
                    p = _id(p)
                    if p in live and (not stops or stops[-1] != p):
                        stops.append(p)
                if len(stops) >= 2:
                    ops.append({"op": "move", "path": stops, "text": text[:16]})
            elif kind in ("highlight", "bounce", "erase"):
                if kind == "erase" and op.get("all") is True:
                    live.clear()
                    arrows = {k: None for k in arrows}
                    ops.append({"op": "erase", "all": True})
                    continue
                target = _id(op.get("target") if op.get("target") is not None else op.get("id"))
                if target in live or (target in arrows and kind != "bounce"):
                    if kind == "erase":
                        live.discard(target)
                        arrows = {k: (None if v and target in v or k == target else v) for k, v in arrows.items()}
                    ops.append({"op": kind, "target": target})
        say = _label(step.get("say"), 400)
        if ops or say:
            out.append({"say": say, "ops": ops})
    solid = [s for s in shapes.values() if s["op"] in _SOLID]
    if not out or not solid:
        return None
    _place(solid, [s for s in shapes.values() if s["op"] == "label"], rung, layout)
    return {"v": 1, "w": W, "h": H, "rung": rung, "steps": out}


def _extent(s) -> tuple[float, float, float]:
    """How far a shape reaches from its centre: sideways, up, and down (an icon's caption hangs below it)."""
    if s["op"] == "icon":
        font = max(18.0, min(36.0, s["size"] * 0.17)) * 1.5          # the page writes up to this much larger on a narrow board (a phone)
        rows = 2 if len(s["text"]) * font * 0.56 > max(s["size"] * 2, 260) else 1
        return s["size"] / 2, s["size"] / 2, s["size"] / 2 + (font * (0.75 + 1.2 * rows) if s["text"] else 0)
    if s["op"] == "circle":
        return s["r"], s["r"], s["r"]
    return s["w"] / 2, s["h"] / 2, s["h"] / 2


def _place(solid, labels, rung, layout):
    scale = SCALE[rung]
    complete = all(s["x"] is not None and s["y"] is not None for s in solid)
    for s in solid:                                                       # sizes first: clamped, with a default
        if s["op"] == "icon":
            s["size"] = _clamp(s["size"], 50, 340, round(120 * scale))
        elif s["op"] == "circle":
            s["r"] = _clamp(s["r"], 24, 220, round(62 * scale))
        else:
            s["w"], s["h"] = _clamp(s["w"], 70, 640, round(230 * scale)), _clamp(s["h"], 44, 320, round(84 * scale))
    auto = layout == "force" or not complete
    if not auto and layout == "auto":
        for s in solid:
            s["x"], s["y"] = _clamp(s["x"], 0, W, W / 2), _clamp(s["y"], 0, H, H / 2)
        for n, a in enumerate(solid):
            for b in solid[n + 1:]:
                (aw, ah, _), (bw, bh, _) = _extent(a), _extent(b)
                if abs(a["x"] - b["x"]) < (aw + bw) * 0.9 and abs(a["y"] - b["y"]) < (ah + bh) * 0.9:
                    auto = True
    if auto:
        n = len(solid)
        rows = 1 if n <= 4 else 2 if n <= 8 else 3
        cols = math.ceil(n / rows)
        top, bottom = (70 if labels else 30), H - 40
        cw, ch = W / cols, (bottom - top) / rows
        for i, s in enumerate(solid):
            r, c = divmod(i, cols)
            inrow = min(cols, n - r * cols)
            s["x"] = round(W * (c + 0.5) / inrow, 1)
            zig = (ch * 0.16 if rows == 1 and inrow >= 3 else 0) * (1 if c % 2 else -1)   # so a long arrow clears the middle shape
            s["y"] = round(top + ch * (r + 0.5) + zig, 1)
            if s["op"] == "icon":
                s["size"] = round(min(s["size"], cw * 0.5, ch * 0.52), 1)
            elif s["op"] == "circle":
                s["r"] = round(min(s["r"], cw * 0.3, ch * 0.3), 1)
            else:
                s["w"], s["h"] = round(min(s["w"], cw * 0.74), 1), round(min(s["h"], ch * 0.4), 1)
    for s in solid:                                                       # and every shape stays on the board
        hw, up, down = _extent(s)
        s["x"] = _clamp(s["x"], min(hw + 8, W / 2), max(W - hw - 8, W / 2), W / 2)
        s["y"] = _clamp(s["y"], min(up + 8, H / 2), max(H - down - 8, up + 8), H / 2)
    spare = [26, H - 18, 58, H - 50]
    for s in labels:
        if s["x"] is None or s["y"] is None:
            s["x"], s["y"] = W / 2, spare.pop(0) if spare else H / 2
        s["x"], s["y"] = _clamp(s["x"], 20, W - 20, W / 2), _clamp(s["y"], 18, H - 12, 26)


def fit(beats: list[list[dict]], lines: list[str]) -> list[dict]:
    """Drawing beats and the explanation's sentences -> steps, in order, so that every step says something and draws
    something: the longer list is grouped evenly onto the shorter one."""
    beats = [b for b in beats if b]
    lines = [s for s in lines if s] or [""]
    n = max(1, min(len(beats), len(lines), MAX_STEPS))

    def groups(items):
        return [items[len(items) * i // n: len(items) * (i + 1) // n] for i in range(n)]
    return [{"say": " ".join(say), "ops": [op for beat in ops for op in beat]} for say, ops in zip(groups(lines), groups(beats))]


# ---- the fallback: a correct, generic board from the explanation's own words ----------------------------------------
_STOP = set("the a an and or but of to in on for with from by at as is are was were be been it its this that these those you your "
            "they them their he she his her we our not no yes so if then than when where which who what why how all any each every "
            "one two three can will just only also into out over more most very same other there here have has had does did do "
            "because while about after before between through during again once here's imagine think thinks every worked example "
            "new term keep called means like".split())


def key_terms(text: str, topic: str = "", count: int = 4) -> list[str]:
    """The words of an explanation worth a box of their own: acronyms and names first, then its longest plain words,
    in the order they appear. Never the topic itself."""
    skip = set(re.findall(r"[a-z0-9]+", topic.lower()))
    found, seen = [], set()

    def add(word, rank, pos):
        key = word.lower().rstrip("s")
        if key and key not in seen and word.lower() not in skip and key not in skip and word.lower() not in _STOP:
            seen.add(key)
            found.append((rank, pos, word))

    plain = _label(text, 4000)
    for m in re.finditer(r"\b[A-Z][A-Z0-9]{1,7}(?:-[A-Z]{2,5})?\b", plain):
        add(m.group(0), 0, m.start())
    for m in re.finditer(r"(?<=[a-z,;:] )[A-Z][a-z]{2,14}\b", plain):
        add(m.group(0), 1, m.start())
    for m in re.finditer(r"\b[a-z]{6,14}\b", plain):
        add(m.group(0), 2, m.start())
    best = sorted(found)[:count]
    return [w for _, _, w in sorted(best, key=lambda t: t[1])]


def fallback(topic: str, text: str, rung: str = "college") -> dict:
    """A board that is always right: the idea in the middle, its key terms around it one at a time, each sentence of
    the explanation as a caption. The one-year-old rung is one big circle that bounces."""
    rung = rung if rung in RUNGS else "college"
    name = _label(topic, 40) or "This idea"
    terms = key_terms(text, topic, {"college": 5, "high": 4, "middle": 3, "elementary": 2, "prek": 1, "one": 0}[rung])
    k = SCALE[rung]
    if not terms:
        beats = [[{"op": "circle", "id": "main", "x": W / 2, "y": H / 2, "r": min(200, 95 * k), "text": name}], [{"op": "bounce", "target": "main"}]]
    else:
        centre = (W / 2, H / 2) if len(terms) > 1 else (300, H / 2)
        beats = [[{"op": "box", "id": "main", "x": centre[0], "y": centre[1], "w": min(360, 250 * k), "h": min(170, 96 * k), "text": name}]]
        for i, term in enumerate(terms):
            angle = math.pi + 2 * math.pi * i / len(terms) if len(terms) > 1 else 0
            if len(terms) == 2:
                angle = math.pi * i
            x = centre[0] + (370 if len(terms) > 1 else 420) * math.cos(angle)
            y = centre[1] + 195 * math.sin(angle)
            beats.append([{"op": "box", "id": f"t{i + 1}", "x": x, "y": y, "w": min(300, 200 * k), "h": min(130, 76 * k), "text": term},
                          {"op": "arrow", "from": "main", "to": f"t{i + 1}"}, {"op": "highlight", "target": f"t{i + 1}"}])
    scene = validate({"steps": fit(beats, sentences(text) or [name])}, rung, layout="keep")
    return dict(scene, source="fallback")


# ---- hand-made scenes for the built-in networking chapter -----------------------------------------------------------
# Each concept: beats in the order its explanations tell it (the thing, the mechanism, the result). A text given as
# (full, simple) has a technical wording for the upper rungs and a friendly one for middle and elementary school.
# d=True marks detail that only the college and high-school boards draw. "tiny": the one or two big shapes of the
# pre-K and one-year-old boards.
def _I(sid, icon, x, y, text="", size=120, d=False):
    return {"op": "icon", "id": sid, "icon": icon, "x": x, "y": y, "size": size, "text": text, "d": d}


def _B(sid, x, y, w, h, text, d=False):
    return {"op": "box", "id": sid, "x": x, "y": y, "w": w, "h": h, "text": text, "d": d}


def _L(sid, x, y, text, d=False):
    return {"op": "label", "id": sid, "x": x, "y": y, "text": text, "d": d}


def _A(a, b, text="", d=False):
    return {"op": "arrow", "from": a, "to": b, "text": text, "d": d}


def _M(path, text="", d=False):
    return {"op": "move", "path": path, "text": text, "d": d}


def _H(target, d=False):
    return {"op": "highlight", "target": target, "d": d}


HAND = {
    "IP address": {"beats": [
        [_I("pc", "computer", 150, 320, ("192.168.1.20", "Mia, house 20"))],
        [_I("srv", "server", 850, 320, ("93.184.216.34", "Leo, house 34"))],
        [_B("pkt", 500, 90, 420, 84, ("to 93.184.216.34, from 192.168.1.20", "To: 34   From: 20")), _H("pkt")],
        [_I("r", "router", 500, 340, ("Router", "Mail carrier")), _A("pc", "r"), _A("r", "srv", ("reads destination", "reads the number")),
         _M(["pc", "r", "srv"], ("packet", "letter"))],
        [_M(["srv", "r", "pc"], "reply", d=True), _H("pc", d=True)],
    ], "tiny": [("computer", "number 20"), ("computer", "number 34")]},
    "Subnet mask": {"beats": [
        [_I("me", "computer", 130, 330, ("192.168.1.20", "You: red, seat 20")),
         _B("mask", 520, 70, 520, 80, ("mask 255.255.255.0 = /24", "Rule: the colour is the room"))],
        [_H("mask"), _L("net", 190, 160, ("network 192.168.1 | host 20", "red room | seat 20"))],
        [_I("a", "computer", 470, 215, ("192.168.1.77", "Red, seat 77")), _A("me", "a", ("same network: direct", "same room")), _M(["me", "a"], ("frame", "note"))],
        [_I("r", "router", 470, 455, ("Router", "Hall monitor")), _I("b", "computer", 850, 455, ("192.168.2.5", "Blue, seat 5")),
         _A("me", "r", ("other network", "other room")), _A("r", "b"), _M(["me", "r", "b"], ("packet", "note"))],
    ], "tiny": [("person", "same room"), ("person", "other room")]},
    "DNS": {"beats": [
        [_I("pc", "computer", 140, 330, ("Your laptop", "Sam")), _L("q", 150, 490, ("example.com = ?", "Where is Zoe?"))],
        [_I("res", "server", 500, 150, ("DNS resolver", "Librarian")), _A("pc", "res", ("what is the address?", "asks")), _M(["pc", "res"], ("query", "?"))],
        [_I("auth", "database", 860, 150, "Root, TLD, authoritative", d=True), _A("res", "auth", "walks the hierarchy", d=True),
         _M(["res", "auth", "res"], "lookup", d=True)],
        [_A("res", "pc", ("93.184.216.34", "number 34")), _M(["res", "pc"], ("answer", "34")), _H("pc")],
        [_I("web", "cloud", 850, 440, ("example.com", "Zoe's house, 34")), _A("pc", "web", ("connects to the number", "walks there")),
         _M(["pc", "web"], ("request", "Sam"))],
    ], "tiny": [("person", "you"), ("person", "helper")]},
    "DHCP": {"beats": [
        [_I("ph", "phone", 170, 290, ("New device, no address", "New child"))],
        [_I("srv", "router", 830, 290, ("DHCP server", "Teacher")), _A("ph", "srv", ("1 Discover", "any seat?")), _M(["ph", "srv"], ("Discover", "?"))],
        [_A("srv", "ph", ("2 Offer 192.168.1.50", "desk 5")), _M(["srv", "ph"], ("Offer", "5"))],
        [_L("dora", 500, 60, "Discover, Offer, Request, Acknowledge", d=True), _M(["ph", "srv"], "Request", d=True), _M(["srv", "ph"], "Ack", d=True)],
        [_B("lease", 500, 480, 560, 84, ("lease: address, mask, gateway, DNS", "Your desk: number 5")), _H("lease")],
    ], "tiny": [("person", "teacher"), ("person", "you")]},
    "Switch": {"beats": [
        [_B("sw", 500, 290, 230, 100, ("Switch", "Teacher")), _I("a", "computer", 150, 150, ("PC, port 1", "Ana")),
         _I("b", "computer", 150, 420, ("Laptop, port 2", "Cam")), _I("c", "computer", 850, 290, ("Printer, port 3", "Ben"))],
        [_A("a", "sw", ("frame for the printer", "note for Ben")), _M(["a", "sw"], ("frame", "note"))],
        [_B("tbl", 560, 80, 500, 76, "MAC table: PC = port 1, printer = port 3", d=True), _H("tbl", d=True)],
        [_A("sw", "c", ("port 3 only", "only to Ben")), _M(["sw", "c"], ("frame", "note")), _H("c")],
        [_L("no", 580, 455, ("nothing sent to port 2", "not for Cam"))],
    ], "tiny": [("person", "teacher"), ("person", "Ben")]},
    "Router": {"beats": [
        [_I("pc", "computer", 110, 310, ("192.168.1.20", "Tom"), 110), _L("home", 150, 180, ("home network", "this village"))],
        [_I("r", "router", 380, 310, ("Home router", "Bridge keeper"), 110), _A("pc", "r", ("not local", "for far away")), _M(["pc", "r"], ("packet", "letter"))],
        [_B("tbl", 560, 80, 470, 76, "routing table: default route to the ISP", d=True), _H("tbl", d=True)],
        [_I("net", "cloud", 650, 310, ("Internet", "The bridge"), 110), _I("srv", "server", 900, 310, ("8.8.8.8", "Far village"), 110),
         _A("r", "net"), _A("net", "srv"), _M(["r", "net", "srv"], ("packet", "letter"))],
        [_M(["srv", "net", "r", "pc"], "reply", d=True), _H("pc", d=True)],
    ], "tiny": [("person", "this town"), ("person", "that town")]},
    "TCP and UDP": {"beats": [
        [_I("c", "computer", 170, 250, ("Client", "Friend 1")), _I("s", "server", 830, 250, ("Server", "Friend 2")),
         _L("t", 500, 90, ("TCP: connect first, confirm everything", "The careful way"))],
        [_A("c", "s", ("SYN", "Ready?")), _M(["c", "s"], ("SYN", "?"))],
        [_A("s", "c", ("SYN-ACK", "Ready!")), _M(["s", "c"], ("SYN-ACK", "!"))],
        [_M(["c", "s"], ("ACK", "go")), _M(["c", "s"], ("data", "balloon")), _M(["s", "c"], ("ACK", "got it"))],
        [_L("u", 500, 440, ("UDP: no handshake, nothing resent", "The fast way: just throw")), _M(["c", "s"], ("datagram", "balloon")),
         _M(["c", "s"], ("datagram", "balloon"))],
    ], "tiny": [("person", "Ready?"), ("person", "Ready!")]},
    "Port number": {"beats": [
        [_I("pc", "computer", 120, 300, ("Client", "Visitor")), _I("srv", "server", 520, 300, ("93.184.216.34", "Big building"), 140)],
        [_B("p443", 860, 130, 240, 80, ("443: web server", "Door 80: library")), _B("p22", 860, 300, 240, 80, ("22: remote shell", "Door 22: workshop")),
         _B("p53", 860, 470, 240, 80, "53: DNS", d=True), _A("srv", "p443"), _A("srv", "p22"), _A("srv", "p53", d=True)],
        [_A("pc", "srv", ("to port 443", "which door?")), _M(["pc", "srv", "p443"], ("segment", "visitor")), _H("p443")],
        [_M(["pc", "srv", "p22"], ("port 22", "visitor")), _H("p22")],
    ], "tiny": [("person", "knock knock"), ("key", "the right door")]},
    "Default gateway": {"beats": [
        [_I("pc", "computer", 130, 300, ("192.168.1.20", "Tom")), _I("pr", "computer", 470, 140, ("192.168.1.30", "Neighbour"))],
        [_A("pc", "pr", ("same network: direct", "walks over")), _M(["pc", "pr"], ("frame", "apple"))],
        [_I("gw", "router", 520, 420, ("Gateway 192.168.1.1", "The gate")), _A("pc", "gw", ("everything else", "far away")), _M(["pc", "gw"], ("packet", "apple")), _H("gw")],
        [_I("net", "cloud", 870, 420, ("Other networks", "Faraway town")), _A("gw", "net"), _M(["gw", "net"], ("packet", "apple"))],
    ], "tiny": [("person", "you"), ("cloud", "far away")]},
    "NAT": {"beats": [
        [_I("pc", "computer", 130, 320, ("192.168.1.20 private", "Lily")), _I("r", "router", 500, 320, ("NAT router 203.0.113.5", "Dad at the mailbox")),
         _I("web", "server", 870, 320, ("Website", "Grandma"))],
        [_A("pc", "r", ("from 192.168.1.20", "Lily's letter")), _M(["pc", "r"], ("packet", "letter"))],
        [_B("tbl", 500, 90, 560, 80, ("table: port 40001 = 192.168.1.20", "Dad's note: Lily is waiting")), _H("tbl")],
        [_A("r", "web", ("from 203.0.113.5", "family address")), _M(["r", "web"], ("packet", "letter"))],
        [_M(["web", "r", "pc"], ("reply", "answer")), _H("pc")],
    ], "tiny": [("person", "Dad"), ("person", "you")]},
}


def _tier(rung):
    return "full" if rung in ("college", "high") else "simple" if rung in ("middle", "elementary") else "tiny"


def _spread(beats, k):
    """Fewer shapes, so they move apart to fill the board and grow by k."""
    solid = [op for beat in beats for op in beat if op["op"] in ("box", "icon", "label")]
    xs, ys = [s["x"] for s in solid], [s["y"] for s in solid]
    cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    fx = min(1.25, 700 / (max(xs) - min(xs))) if max(xs) > min(xs) else 1
    fy = min(1.25, 330 / (max(ys) - min(ys))) if max(ys) > min(ys) else 1
    for s in solid:
        s["x"], s["y"] = W / 2 + (s["x"] - cx) * max(fx, 1), H / 2 + (s["y"] - cy) * max(fy, 1)
        if s["op"] == "icon":
            s["size"] = s["size"] * k
        elif s["op"] == "box":
            s["w"], s["h"] = min(s["w"] * 1.05, 600), s["h"] * k


def builtin(concept: str, rung: str, text: str):
    """The hand-made board for a built-in networking concept at a rung, captioned with that rung's explanation.
    -> scene, or None when the concept has no hand-made scene."""
    spec = HAND.get(concept)
    rung = rung if rung in RUNGS else "college"
    if not spec:
        return None
    tier = _tier(rung)
    if tier == "tiny":
        (icon_a, text_a), (icon_b, text_b) = spec["tiny"]
        if rung == "one":
            beats = [[_I("a", icon_a, 300, 270, text_a, 250), {"op": "bounce", "target": "a"}],
                     [_I("b", icon_b, 710, 270, text_b, 250), {"op": "bounce", "target": "b"}]]
        else:
            beats = [[_I("a", icon_a, 220, 270, text_a, 200)],
                     [_I("b", icon_b, 780, 270, text_b, 200), _A("a", "b"), _M(["a", "b"])],
                     [{"op": "bounce", "target": "b"}]]
    else:
        beats = []
        for beat in spec["beats"]:
            ops = []
            for op in beat:
                if op.get("d") and tier != "full":
                    continue
                op = {k: v for k, v in op.items() if k != "d"}
                if isinstance(op.get("text"), tuple):
                    op["text"] = op["text"][0 if tier == "full" else 1]
                if isinstance(op.get("path"), list):
                    op["path"] = list(op["path"])
                ops.append(op)
            beats.append(ops)
        if tier == "simple":
            _spread(beats, 1.3 if rung == "elementary" else 1.18)
    scene = validate({"steps": fit(beats, sentences(text))}, rung, layout="keep")
    return dict(scene, source="hand") if scene else None


# ---- the model's scene ----------------------------------------------------------------------------------------------
SHAPE = ('{"steps": [{"say": "A student wants a book.", "ops": [{"op": "icon", "id": "stu", "icon": "person", "text": "Student"}]}, '
         '{"say": "She asks the catalogue where it is.", "ops": [{"op": "icon", "id": "cat", "icon": "database", "text": "Catalogue"}, '
         '{"op": "arrow", "from": "stu", "to": "cat", "text": "1 where is it?"}, {"op": "move", "path": ["stu", "cat"], "text": "question"}]}, '
         '{"say": "It answers: shelf 12.", "ops": [{"op": "arrow", "from": "cat", "to": "stu", "text": "2 shelf 12"}, '
         '{"op": "highlight", "target": "stu"}]}]}')
STEPS = {"college": "4 to 6 steps", "high": "4 to 6 steps", "middle": "3 to 5 steps", "elementary": "3 or 4 steps",
         "prek": "2 or 3 steps", "one": "1 or 2 steps"}


def prompt(rung: str, topic: str = "") -> str:
    """What the model is told about the board it must draw next to its explanation."""
    rung = rung if rung in RUNGS else "college"
    size = {"college": "up to 7 shapes", "high": "up to 6 shapes", "middle": "at most 4 shapes", "elementary": "at most 4 shapes, with friendly story names",
            "prek": "2 big shapes only", "one": "1 or 2 big shapes only, and a bounce"}[rung]
    return ("Also draw it on a whiteboard, built up step by step while you explain. Rules: (1) " + STEPS[rung] + ": one step per sentence of the "
            "explanation, in the same order, \"say\" being that sentence. (2) Every step ADDS something: a new labelled shape, and an arrow or "
            "move that connects it to what is already drawn. (3) Draw the parts the explanation names (who or what takes part, the layers, "
            "the stages) as labelled shapes, and what passes between them as arrows with a short label; number the arrows of a process 1, 2, 3. "
            "(4) Where the explanation works an example, write its real numbers in the labels, the same numbers as the text. "
            "Allowed ops ONLY: box, circle, icon (icons: " + ", ".join(ICONS) + "), label, "
            "arrow (from/to are ids of shapes already drawn), move (a token travelling along a path of shape ids, such as a packet), "
            f"highlight, bounce, erase. No coordinates: shapes are placed for you in the order you draw them. For this level use {size}. "
            "Texts on the board are 1 to 4 words (a number or an address counts as one word).")


def only_prompt(rung: str, text: str, topic: str = "") -> str:
    """The second chance: the explanation is written and kept; only its board is asked for again."""
    return (f"Here is an explanation of \"{topic or 'a topic'}\":\n\"\"\"\n{text}\n\"\"\"\n\n{prompt(rung, topic)}\n\n"
            f"Reply with JSON only, exactly this shape (an example about something else):\n{SHAPE}")


def from_model(draft, rung: str, text: str):
    """The model's scene made safe, captioned with the explanation where it left a caption out. -> scene or None."""
    scene = validate(draft, rung, layout="force")
    if not scene:
        return None
    lines = sentences(text)
    for i, step in enumerate(scene["steps"]):
        if not step["say"] and i < len(lines):
            step["say"] = lines[i]
    scene["steps"] = [s for s in scene["steps"] if s["say"] or s["ops"]]
    return dict(scene, source="model")


def healthy(scene, rung: str = "college") -> bool:
    """Is a model scene worth drawing? Several steps that each add something, parts that are labelled, and for a
    mechanism the arrows between them. One step holding everything is a picture, not a build-up."""
    if not scene:
        return False
    steps = scene["steps"]
    ops = [op for s in steps for op in s["ops"]]
    solid = [o for o in ops if o["op"] in _SOLID]
    low = rung in ("prek", "one")
    if len(steps) < (1 if low else 2) or any(not s["ops"] for s in steps) or not solid:
        return False
    if any(not o.get("text") for o in solid):
        return False
    return low or len(solid) < 2 or any(o["op"] in ("arrow", "move") for o in ops)


def spread(beats: list[list[dict]], lines: list[str]) -> list[dict]:
    """Drawing beats and the explanation's sentences -> steps, in order, so the board builds up shape by shape while the
    sentences are said once each. More beats than sentences: a sentence is said at the first beat of its group and the
    others draw in silence (a one-sentence story still builds up). More sentences than beats: sentences are grouped."""
    beats = [b for b in beats if b][:MAX_STEPS]
    lines = [s for s in lines if s]
    if not beats:
        return []
    if not lines:
        return [{"say": "", "ops": ops} for ops in beats]
    n, m = len(beats), len(lines)
    if n >= m:
        first = {i * n // m: lines[i] for i in range(m)}
        return [{"say": first.get(i, ""), "ops": ops} for i, ops in enumerate(beats)]
    return [{"say": " ".join(lines[len(lines) * i // n: len(lines) * (i + 1) // n]), "ops": ops} for i, ops in enumerate(beats)]


def align(scene, text: str):
    """The steps keep their drawings but speak the explanation's own sentences, in order: the board is never ahead of
    or behind what is said."""
    lines = sentences(text)
    if not lines or not scene or not scene["steps"]:
        return scene
    return dict(scene, steps=spread([s["ops"] for s in scene["steps"]], lines))


def repair(draft, text: str, rung: str = "college", topic: str = ""):
    """A scene the model wrote badly (everything in one step, shapes with no label, no arrows) -> a scene that builds up
    shape by shape, one beat per shape, spoken as the explanation. None when there is nothing to rebuild from."""
    steps = draft.get("steps") if isinstance(draft, dict) else draft
    if not isinstance(steps, list):
        return None
    flat = [dict(op) for st in steps if isinstance(st, dict) and isinstance(st.get("ops"), list) for op in st["ops"] if isinstance(op, dict)]
    terms = key_terms(text, topic, 8)
    beats, chain, n = [], [], 0
    for op in flat:
        kind = str(op.get("op") or "").lower()
        if kind in ("box", "circle", "icon", "label"):
            if not _label(op.get("text") if op.get("text") is not None else op.get("label")):
                if kind == "label" or not terms:
                    continue
                op["text"] = terms.pop(0)
            n += 1
            op["id"] = _id(op.get("id")) or f"r{n}"
            if kind != "label":
                chain.append(op["id"])
            beats.append([op])
        elif beats:
            if kind in ("arrow", "move") and any(str(o.get("op") or "").lower() in ("arrow", "move") for o in beats[-1]):
                beats.append([op])                               # a second message is its own beat: SYN, then SYN-ACK, then ACK
            else:
                beats[-1].append(op)
    if not chain:
        return None
    if len(chain) >= 2 and rung not in ("prek", "one") and not any(
            str(o.get("op") or "").lower() in ("arrow", "move") for b in beats for o in b):
        where = {o["id"]: i for i, b in enumerate(beats) for o in b if "id" in o}
        for a, b in zip(chain, chain[1:]):
            beats[where[b]].append({"op": "arrow", "from": a, "to": b})
    scene = validate({"steps": spread(beats, sentences(text))}, rung, layout="force")
    return dict(scene, source="repaired") if scene else None


# ---- worked examples: the numbers come from core/study_calc, the drawing from here ---------------------------------------
def _box(sid, text):
    return {"op": "box", "id": sid, "text": text}


def _arrow(a, b, text=""):
    return {"op": "arrow", "from": a, "to": b, "text": text}


def _hi(target):
    return {"op": "highlight", "target": target}


def _worked_steps(ex: dict, rung: str):
    from core.words import plural
    k = ex["kind"]
    cap = 6 if rung == "college" else 4
    if k == "subnet":
        p, kids = ex["parent"], ex["subnets"]
        shown = kids[:cap]
        return [
            (f"The network {p['cidr']} has the mask {p['mask']}: {p['prefix']} network bits and {p['host_bits']} host bits.", [_box("net", p["cidr"])]),
            (f"{p['host_bits']} host bits give 2^{p['host_bits']} = {p['total']} addresses, and {p['usable']} are usable: the first is the "
             f"network address and the last is the broadcast address.", [_box("hosts", f"{p['host_bits']} host bits = {p['total']}"), _arrow("net", "hosts"), _hi("hosts")]),
            (f"Borrow {plural(ex['borrow'], 'bit')} from the host part: the prefix grows to /{kids[0]['prefix']}, which makes {len(kids)} subnets.",
             [_box("borrow", f"borrow {ex['borrow']}: /{kids[0]['prefix']}"), _arrow("hosts", "borrow")]),
            (f"Each subnet has {kids[0]['total']} addresses and {kids[0]['usable']} usable hosts. They start at "
             + ", ".join(s["network"] for s in shown) + (" and so on." if len(kids) > len(shown) else "."),
             [x for i, s in enumerate(shown) for x in (_box(f"s{i + 1}", f"{s['network']}/{s['prefix']}"), _arrow("borrow", f"s{i + 1}"))] + [_hi("s1")]),
        ]
    if k == "binary":
        b = ex["binary"]
        if len(b["bits"]) > 8:
            return None
        places = [2 ** i for i in range(len(b["bits"]) - 1, -1, -1)]
        return [
            ("Binary uses only 0 and 1, and each place is worth double the one on its right: " + ", ".join(map(str, places)) + ".",
             [_box(f"p{v}", str(v)) for v in places]),
            (f"To write {b['n']}, take every place that fits: {b['sum']} = {b['n']}.",
             [_box("n", f"{b['n']} = {b['sum']}")] + [_hi(f"p{v}") for v in b["places"]]),
            (f"Put a 1 under each place used and a 0 under the others: {b['n']} is {b['bits']}.", [_box("bits", b["bits"]), _arrow("n", "bits")]),
        ]
    if k == "bigo":
        c = ex["counts"]
        order = [("O(1)", "o1", "constant: the same one step however large the input"), ("O(log n)", "olog", "binary search halves the list each time"),
                 ("O(n)", "on", "linear: look at every item once"), ("O(n log n)", "onlogn", "a good sort"), ("O(n^2)", "on2", "nested loops over the input")]
        steps, prev = [(f"Take a list of n = {c['n']:,}.", [_box("n", f"n = {c['n']:,}")])], "n"
        for label, sid, why in order:
            steps.append((f"{label} is {why}: {plural(c[label], 'step')}.", [_box(sid, f"{label}: {c[label]:,}"), _arrow(prev, sid)]
                          + ([_hi(sid)] if sid in ("olog", "on2") else [])))
            prev = sid
        return steps
    if k == "raid":
        r = ex["raid"]
        how = {0: "Data is striped across the disks: fast, but nothing is copied.",
               1: "Every disk holds a mirror copy of the same data, so only one disk's space is usable.",
               5: "One disk's worth of space holds parity, which can rebuild a lost disk.",
               6: "Two disks' worth of space hold parity, which can rebuild two lost disks.",
               10: "Disks are mirrored in pairs and the pairs are striped."}[r["level"]]
        name = {0: "striping", 1: "mirroring", 5: "striping + 1 parity", 6: "striping + 2 parity", 10: "mirror + stripe"}[r["level"]]
        return [(f"RAID {r['level']} combines {r['disks']} disks of {r['size']} TB: {r['raw']} TB of raw space.",
                 [_box(f"d{i + 1}", f"Disk {i + 1}: {r['size']} TB") for i in range(min(r["disks"], 6))]),
                (how, [_box("how", name)]),
                (f"The usable capacity is {r['usable']} TB, and the array survives {plural(r['survives'], 'failed disk')}.",
                 [_box("usable", f"usable: {r['usable']} TB"), _arrow("how", "usable"), _box("safe", f"survives {r['survives']} failed"), _hi("usable")])]
    if k == "perms":
        p = ex["perms"]
        steps = [(f"A file's permissions are three groups of r, w and x, worth 4, 2 and 1: {p['symbolic']}.", [_box("mode", p["symbolic"])])]
        for i, name in enumerate(("owner", "group", "others")):
            sym = p["symbolic"][i * 3:i * 3 + 3]
            steps.append((f"The {name} gets {sym}, which is {p['parts'][i]} = {p['digits'][i]}.", [_box(name, f"{name} {sym} = {p['digits'][i]}"), _arrow("mode", name)]))
        steps.append((f"Put the three digits together and the mode is {p['octal']}: chmod {p['octal']} file.", [_box("chmod", f"chmod {p['octal']}"), _hi("chmod")]))
        return steps
    if k == "recursion":
        f = ex["fact"]
        n = f["n"]
        if n < 1 or n > (5 if rung == "college" else 4):
            return None
        calls = [_box(f"c{i}", f"factorial({i})") for i in range(n, -1, -1)]
        links = [_arrow(f"c{i}", f"c{i - 1}") for i in range(n, 0, -1)]
        back = [_box(f"r{i}", up) for i, up in enumerate(f["up"], 1)]
        return [
            (f"factorial({n}) = {n} x factorial({n - 1}): the function calls itself with a smaller number.", calls[:2] + links[:1]),
            ("Each call waits for the next one, down to the base case, factorial(0) = 1, which stops the chain.", calls[2:] + links[1:] + [_hi("c0")]),
            ("Now the answers come back up: " + ", ".join(f["up"]) + ".", [{"op": "erase", "all": True}] + back + [_arrow(f"r{i}", f"r{i + 1}") for i in range(1, n)]),
            (f"So factorial({n}) = {f['result']}.", [_hi(f"r{n}")]),
        ]
    return None


def worked(ex, rung: str = "college"):
    """The board of a worked example whose figures were computed by core/study_calc: step by step, every caption a
    sentence with the real numbers. Only the upper rungs draw it (the lower ones tell a story). -> scene or None."""
    if not ex or rung not in ("college", "high"):
        return None
    try:
        steps = _worked_steps(ex, rung)
    except (KeyError, IndexError, ValueError):
        return None
    if not steps:
        return None
    scene = validate({"steps": [{"say": say, "ops": ops} for say, ops in steps]}, rung, layout="force")
    return dict(scene, source="worked") if scene else None


def for_explanation(topic: str, text: str, rung: str, concept: str = "", draft=None, example=None) -> dict:
    """The board for one explanation: the worked example (numbers computed in code) for a topic that has one, else the
    model's scene when it is usable (rebuilt by repair() when it is badly shaped), else the hand-made one for a built-in
    networking concept, else the fallback. Always returns a board."""
    scene = worked(example, rung)
    if scene:
        return scene
    if draft is not None:
        scene = from_model(draft, rung, text)
        if not healthy(scene, rung):
            scene = repair(draft, text, rung, topic) or scene
        if scene:
            return dict(align(scene, text), source=scene["source"])
    return builtin(concept, rung, text) or fallback(topic, text, rung)
