"""Worked examples whose numbers are computed here, never by the model.

The local model is unreliable at arithmetic (subnet sizes, binary, how many comparisons a search needs, RAID capacity,
chmod digits), and a wrong number on a whiteboard teaches the wrong thing. For the topics below the tutor asks THIS
module for the facts, tells the model to use exactly those numbers, and draws the board from them in code
(core/study_board.worked).

    subnet / split      CIDR -> mask, network, broadcast, usable hosts; borrowing bits -> the equal subnets
    binary              decimal <-> binary with the place values
    bigo                comparisons for n items in each growth class
    raid                usable capacity and failures survived
    perms               chmod digits <-> rwx for the three classes
    factorial           the call chain of a recursive factorial and the values coming back

example(topic, source) picks the builder from the topic's words and the numbers from the student's own passage when it has
them (a "/26", "4 disks of 2 TB", "754", "one million", "factorial(4)"), else a fixed sensible default.
Pure: no model, no storage."""
import ipaddress
import math
import re

from core.words import plural

WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}


# ---- subnets -------------------------------------------------------------------------------------------------------------
def subnet(cidr: str) -> dict:
    """'192.168.1.0/24' -> the facts of that network. Raises ValueError on anything that is not IPv4 CIDR."""
    net = ipaddress.ip_network(cidr.strip(), strict=False)
    if net.version != 4:
        raise ValueError("IPv4 only")
    host_bits = 32 - net.prefixlen
    total = 2 ** host_bits
    return {"cidr": str(net), "prefix": net.prefixlen, "mask": str(net.netmask), "network": str(net.network_address),
            "broadcast": str(net.broadcast_address), "host_bits": host_bits, "total": total,
            "usable": max(total - 2, 0) if net.prefixlen < 31 else total,
            "first": str(net.network_address + 1) if net.prefixlen < 31 else str(net.network_address),
            "last": str(net.broadcast_address - 1) if net.prefixlen < 31 else str(net.broadcast_address)}


def split(cidr: str, borrow: int) -> list[dict]:
    """The 2**borrow equal subnets made by borrowing `borrow` host bits (at most 4, so at most 16 subnets)."""
    net = ipaddress.ip_network(cidr.strip(), strict=False)
    if not 1 <= borrow <= 4 or net.prefixlen + borrow > 30:
        raise ValueError("cannot borrow that many bits")
    return [subnet(str(s)) for s in net.subnets(prefixlen_diff=borrow)]


# ---- binary --------------------------------------------------------------------------------------------------------------
def binary(n: int) -> dict:
    """Decimal n (0..65535) -> its bits, the place values that are on, and the sum that proves it."""
    if not 0 <= n <= 65535:
        raise ValueError("out of range")
    bits = format(n, "b")
    places = [2 ** (len(bits) - 1 - i) for i, b in enumerate(bits) if b == "1"]
    return {"n": n, "bits": bits, "places": places, "sum": " + ".join(str(p) for p in places) or "0"}


def from_binary(bits: str) -> int:
    if not re.fullmatch(r"[01]{1,16}", bits or ""):
        raise ValueError("not a binary number")
    return int(bits, 2)


# ---- big-O ---------------------------------------------------------------------------------------------------------------
def bigo(n: int) -> dict:
    """How many steps each growth class takes on n items (binary search: the worst number of comparisons)."""
    if not 2 <= n <= 10 ** 9:
        raise ValueError("out of range")
    return {"n": n, "O(1)": 1, "O(log n)": n.bit_length(), "O(n)": n, "O(n log n)": n * math.ceil(math.log2(n)), "O(n^2)": n * n}


# ---- RAID ----------------------------------------------------------------------------------------------------------------
def raid(level: int, disks: int, size_tb: int) -> dict:
    """Usable capacity (TB) and how many failed disks the array survives, for the classic levels."""
    minimum = {0: 2, 1: 2, 5: 3, 6: 4, 10: 4}
    if level not in minimum or disks < minimum[level] or size_tb < 1 or (level == 10 and disks % 2):
        raise ValueError("not a valid array")
    usable = {0: disks, 1: 1, 5: disks - 1, 6: disks - 2, 10: disks // 2}[level] * size_tb
    survives = {0: 0, 1: disks - 1, 5: 1, 6: 2, 10: 1}[level]
    return {"level": level, "disks": disks, "size": size_tb, "raw": disks * size_tb, "usable": usable, "survives": survives}


# ---- file permissions ----------------------------------------------------------------------------------------------------
def perms(value: str) -> dict:
    """'754' or 'rwxr-xr--' -> both forms and the digit of each class with its parts (r=4, w=2, x=1)."""
    value = (value or "").strip()
    if re.fullmatch(r"[0-7]{3}", value):
        digits = [int(c) for c in value]
    elif re.fullmatch(r"[r-][w-][x-][r-][w-][x-][r-][w-][x-]", value):
        digits = [sum(v for ch, v in zip(value[i:i + 3], (4, 2, 1)) if ch != "-") for i in (0, 3, 6)]
    else:
        raise ValueError("not a permission")
    sym = "".join(("r" if d & 4 else "-") + ("w" if d & 2 else "-") + ("x" if d & 1 else "-") for d in digits)
    parts = ["+".join(str(v) for v in (4, 2, 1) if d & v) or "0" for d in digits]
    return {"octal": "".join(map(str, digits)), "symbolic": sym, "digits": digits, "parts": parts}


# ---- recursion -----------------------------------------------------------------------------------------------------------
def factorial(n: int) -> dict:
    """The calls factorial(n) makes down to the base case, and the answers as they come back up."""
    if not 0 <= n <= 8:
        raise ValueError("out of range")
    down = [f"factorial({k}) = {k} x factorial({k - 1})" for k in range(n, 0, -1)] + ["factorial(0) = 1"]
    up, value = [], 1
    for k in range(1, n + 1):
        value *= k
        up.append(f"factorial({k}) = {value}")
    return {"n": n, "down": down, "up": up, "result": value}


# ---- choosing a builder for a topic ----------------------------------------------------------------------------------------
def _num(text: str):
    text = (text or "").strip().lower().replace(",", "")
    return int(text) if text.isdigit() else WORDS.get(text)


_PERMS_OTHER = re.compile(r" (?:ntfs|windows|android|ios|iphone|acls?|iam|azure|aws|gcp|sharepoint|active directory|apps?|share|shares|browser|camera|microphone|location) ")
_PERMS_UNIX = re.compile(r" (?:chmod|rwx|unix|linux|octal|posix) |file permissions? ")
_RECURSION_OTHER = re.compile(r" (?:dns|resolvers?|quer(?:y|ies)|zones?|name ?servers?|lookups?) ")


def kind(topic: str, source: str = "") -> str:
    """Which worked example a topic wants: subnet, binary, bigo, raid, perms, recursion, or ''. The topic has to be the concept
    itself: 'recursive DNS', 'NTFS permissions' and 'cyclomatic complexity' share a word with one and are about another thing.
    A permissions topic that does not say 'unix' or 'file' is taken only when the passage talks chmod or rwx."""
    t = " " + re.sub(r"[^a-z0-9]+", " ", (topic or "").lower()) + " "
    for name, pattern in (("subnet", r"subnet|cidr|netmask|net mask"), ("binary", r" binary (?!search|tree|heap)| bits and bytes | base 2 "),
                          ("bigo", r" big o | big oh | time complexity | algorithm complexity | algorithmic complexity "),
                          ("raid", r" raid ?(?:0|1|5|6|10)? ")):
        if re.search(pattern, t):
            return name
    if "permission" in t or " chmod " in t or " rwx " in t:
        if _PERMS_OTHER.search(t):
            return ""
        if _PERMS_UNIX.search(t) or re.search(r"\b(?:chmod|rwx)\b|\b[r-][w-][x-][r-][w-][x-][r-][w-][x-]\b", source or "", re.I):
            return "perms"
        return ""
    if re.search(r" recursion | recursive (?:functions?|algorithms?|calls?|methods?|cases?|solutions?|definitions?|programs?|procedures?) | factorial ", t):
        return "" if _RECURSION_OTHER.search(t) else "recursion"
    return ""


def _find(pattern, *texts, flags=0):
    """The first match of the pattern in the first text that has one: the topic is asked before the passage."""
    for text in texts:
        m = re.search(pattern, text or "", flags)
        if m:
            return m
    return None


def _raid_level(topic: str, src: str) -> int:
    m = _find(r"\bRAID\s*-?\s*(10|0|1|5|6)\b", topic, flags=re.I)
    if m:
        return int(m.group(1))
    found = [int(x) for x in re.findall(r"\bRAID\s*-?\s*(10|0|1|5|6)\b", src, re.I)]
    return max(found, key=lambda lv: (found.count(lv), -found.index(lv))) if found else 5          # the level the passage is about, else the classic


def _binary_number(topic: str, src: str):
    m = re.search(r"\b(\d{1,5})\b", topic or "")
    if m:
        return int(m.group(1))
    for pattern in (r"\b(\d{1,5})\s+(?:in|to|as)\s+binary", r"\b(?:number|convert|converting|decimal|write|represent|value)\s+(\d{1,5})\b",
                    r"\b(\d{1,5})\s*(?:=|is|equals)\s*[01]{4,}\b"):
        m = re.search(pattern, src or "", re.I)
        if m:
            return int(m.group(1))
    return None


def example(topic: str, source: str = "") -> dict | None:
    """The worked example for a topic: {"kind", "facts": [lines for the prompt], ...the numbers}, or None. The numbers come
    from the student's passage where it gives them, else defaults; every figure is computed here."""
    k, src = kind(topic, source), source or ""
    topic = topic or ""
    try:
        if k == "subnet":
            m = _find(r"\b(\d{1,3}(?:\.\d{1,3}){3})/(\d{1,2})\b", topic, src)
            base = f"{m.group(1)}/{m.group(2)}" if m else "192.168.1.0/24"
            parent = subnet(base)
            borrow = 2
            b = _find(r"borrow\w*\s+(\w+)\s+bits?", topic, src, flags=re.I)
            if b and _num(b.group(1)) and 1 <= _num(b.group(1)) <= 4:
                borrow = _num(b.group(1))
            if parent["prefix"] + borrow > 30:
                parent, borrow = subnet("192.168.1.0/24"), 2
            kids = split(parent["cidr"], borrow)
            facts = [f"{parent['cidr']}: mask {parent['mask']}, {parent['host_bits']} host bits, {parent['total']} addresses, "
                     f"{parent['usable']} usable ({parent['network']} is the network address, {parent['broadcast']} the broadcast).",
                     f"Borrowing {borrow} bit{'s' if borrow > 1 else ''} makes {len(kids)} subnets of /{kids[0]['prefix']}, "
                     f"{kids[0]['total']} addresses ({kids[0]['usable']} usable) each: "
                     + ", ".join(f"{s['network']}-{s['broadcast'].split('.')[-1]}" for s in kids) + "."]
            return {"kind": k, "parent": parent, "borrow": borrow, "subnets": kids, "facts": facts,
                    "anchors": [str(parent["total"]), str(parent["usable"]), f"/{kids[0]['prefix']}"]}
        if k == "binary":
            n = _binary_number(topic, src)
            n = n if n and 0 < n <= 65535 else 13
            b = binary(n)
            return {"kind": k, "binary": b, "facts": [f"{n} in binary is {b['bits']} (place values {b['sum']} = {n})."], "anchors": [b["bits"]]}
        if k == "bigo":
            n = 1_000_000                      # the classic size: a million items
            c = bigo(n)
            return {"kind": k, "counts": c, "anchors": ["1,000,000|1000000|million", str(c["O(log n)"])], "facts": [f"For n = {n:,} items: O(1) = {plural(1, 'step')}, O(log n) = {plural(c['O(log n)'], 'step')} (binary search worst case), "
                                                       f"O(n) = {plural(c['O(n)'], 'step')}, O(n log n) = {plural(c['O(n log n)'], 'step')}, "
                                                       f"O(n^2) = {plural(c['O(n^2)'], 'step')}."]}
        if k == "raid":
            level = _raid_level(topic, src)
            d = re.search(r"\b(\d+|two|three|four|five|six)\s+disks?\s+of\s+(\d+)\s*TB", src, re.I)
            disks, size = (_num(d.group(1)), int(d.group(2))) if d else (4, 2)
            if level != 5 and not d:
                disks = 2 if level in (0, 1) else 4
            r = raid(level, disks, size)
            return {"kind": k, "raid": r, "anchors": [f"{r['usable']} TB|{r['usable']}TB|{r['usable']} terabytes"], "facts": [f"RAID {level} with {disks} disks of {size} TB: raw {r['raw']} TB, usable {r['usable']} TB, "
                                                     f"survives {r['survives']} failed disk{'s' if r['survives'] != 1 else ''}."]}
        if k == "perms":
            m = _find(r"\b([0-7]{3})\b|\b([r-][w-][x-][r-][w-][x-][r-][w-][x-])\b", topic, src)
            p = perms(m.group(1) or m.group(2)) if m else perms("754")
            return {"kind": k, "perms": p, "anchors": [p["octal"]], "facts": [f"{p['symbolic']} = {p['octal']}: owner {p['digits'][0]} ({p['parts'][0]}), "
                                                     f"group {p['digits'][1]} ({p['parts'][1]}), others {p['digits'][2]} ({p['parts'][2]})."]}
        if k == "recursion":
            m = _find(r"factorial\((\d)\)", topic, src, flags=re.I)
            f = factorial(int(m.group(1)) if m and 1 <= int(m.group(1)) <= 8 else 4)
            return {"kind": k, "fact": f, "anchors": [str(f["result"])], "facts": [f"factorial({f['n']}) = {f['result']}. Calls: " + "; ".join(f["down"]) +
                                                    ". Coming back: " + "; ".join(f["up"]) + "."]}
    except ValueError:
        return None
    return None
