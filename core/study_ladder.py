"""The explanation ladder: six rungs, from college down to a one-year-old, each a genuinely different explanation of
the same idea (not the same text with shorter words).

  college     precise and technical, the proper terms
  high        the mechanism in plain sentences, with one worked example
  middle      one everyday comparison, one new term at a time
  elementary  a story with concrete objects
  prek        two or three tiny sentences with things a small child knows
  one         a handful of words, sounds and a gesture

"Simpler" moves DOWN one rung from where the last explanation of that concept was, "more detail" moves UP one; a rung
or age he names ("like I'm five" = pre-K) is taken as named. Nothing ever jumps to the bottom on its own.

The built-in course's networking chapter has a hand-written explanation at every rung (HAND). Every other built-in
concept gets its rungs from templates around the outline's own summary (templated()), and is marked as templated so
the page and the voice can say so. Pure: no model, no storage."""
import re

RUNGS = ("college", "high", "middle", "elementary", "prek", "one")
START = "college"                    # he is a college student: the first explanation is the real one, then he steps down
LABELS = {"college": "College", "high": "High school", "middle": "Middle school", "elementary": "Elementary",
          "prek": "Pre-K", "one": "One-year-old"}
SPOKEN = {"college": "college level", "high": "high-school level", "middle": "middle-school level",
          "elementary": "elementary-school level", "prek": "pre-K level", "one": "one-year-old level"}
# What makes each rung different: this sentence goes into the model prompt as it is.
RULES = {
    "college": "COLLEGE level: precise and technical, as in a textbook for a student of the subject. Use the proper terms, name the "
               "protocols, fields, numbers and standards that matter, and state the mechanism step by step, exactly. Never an everyday "
               "comparison. 4 to 6 sentences. Do not copy the source's sentences: restate them and add how it works.",
    "high": "HIGH-SCHOOL level: the same mechanism in plain sentences for a teenager. Keep the real terms but explain each one in passing, "
            "and walk through exactly ONE worked example with concrete values (real numbers, names or addresses). No everyday comparison. "
            "4 to 6 sentences, shorter sentences than the college level.",
    "middle": "MIDDLE-SCHOOL level: explain with exactly ONE everyday comparison from a young person's daily life (a school, a kitchen, "
              "the post office, a game), and keep it true: say which part of the comparison is which part of the idea. Introduce only ONE "
              "technical term, defined when it appears. 3 to 5 short sentences.",
    "elementary": "ELEMENTARY-SCHOOL level: tell it as a short story with concrete objects and named children (letters, houses, toys, a "
                  "classroom) in which the objects do what the parts of the idea do. No technical word except the name of the idea itself. "
                  "3 to 5 short sentences.",
    "prek": "PRE-K level: two or three tiny sentences, using only things a small child knows (toys, a house, a ball, a door, mom, a "
            "teacher). No technical words at all, but the SAME idea: the little picture must work the way the real thing works.",
    "one": "ONE-YEAR-OLD level: a handful of single words and sounds plus one gesture in parentheses, like a picture book for a baby. "
           "Playful, and still about the idea. At most 12 words.",
}
# A whole ladder for a topic that is not in the course, shown to the model so it sees what each step down changes: more
# technical -> a worked example -> one true everyday comparison -> a story -> a few tiny sentences -> a few words.
EXAMPLE_TOPIC = "cache memory"
EXAMPLE = {
    "college": "A cache is a small, fast memory placed between a processor and a larger, slower store, and it keeps copies of recently "
               "used data. On each read the hardware checks the cache first: a hit returns the data within a few nanoseconds, while a "
               "miss fetches it from main memory, about a hundred times slower, and stores a copy, evicting an older entry by a "
               "replacement policy such as least recently used. Programs reuse recent and nearby addresses (locality), so hit rates "
               "above 90 percent are common and the average access time stays close to the cache's own speed.",
    "high": "A cache is a small, very fast memory that keeps copies of the data you used recently, so the computer does not have to fetch "
            "it again from the slow main memory. Example: a program reads the same number from RAM 100 times. The first read is slow "
            "and copies the number into the cache. The other 99 reads find it there and are about 100 times faster. When the cache "
            "is full, it throws out the entry that was used longest ago.",
    "middle": "A cache is like keeping your favourite books on your desk instead of walking to the library every time. The desk is "
              "small, but anything on it is instant to reach, while the library has everything but takes a trip. A computer keeps the "
              "data it used lately in a small, fast memory called a cache. When the desk is full, the book you have not touched for "
              "the longest time goes back.",
    "elementary": "Sam does his homework at a table far from the bookshelf. Every time he needs the dictionary, he has to walk across "
                  "the room. So he puts the dictionary right next to him on his desk. Now he finds words fast, and he walks to the shelf "
                  "only for a book he has not used before.",
    "prek": "My toy box is far away. My best toys are right here by me. I play with them fast!",
    "one": "Toys! Close! (hold a toy near) Yay!",
}


def example_block() -> str:
    """The prompt's few-shot: the whole example ladder, one line per rung."""
    lines = "\n".join(f"- {LABELS[r]}: {EXAMPLE[r]}" for r in RUNGS)
    return (f"Here is how a whole ladder looks for a DIFFERENT topic ({EXAMPLE_TOPIC}), so you see what each step down changes. "
            f"Never copy its words or its comparisons:\n{lines}\n")


# Older names for a level (the three-level explain this replaced, and loose words a model may pass).
ALIASES = {"exam": "college", "university": "college", "beginner": "middle", "five": "simpler", "simple": "simpler",
           "easier": "simpler", "down": "simpler", "up": "deeper", "detail": "deeper", "high school": "high",
           "middle school": "middle", "elementary school": "elementary", "pre-k": "prek", "prekindergarten": "prek",
           "preschool": "prek", "baby": "one", "one-year-old": "one", "1": "one"}
MOVES = ("simpler", "deeper")


def norm(level) -> str:
    """A rung name, "simpler", "deeper", or "" for anything else."""
    level = " ".join(str(level or "").lower().split())
    level = ALIASES.get(level, level)
    return level if level in RUNGS or level in MOVES else ""


def move(current: str, level: str = "") -> str:
    """The rung to explain at: the named rung, or one step down/up from `current` (never past either end)."""
    current = current if current in RUNGS else START
    level = norm(level)
    if level in RUNGS:
        return level
    i = RUNGS.index(current)
    if level == "simpler":
        return RUNGS[min(i + 1, len(RUNGS) - 1)]
    if level == "deeper":
        return RUNGS[max(i - 1, 0)]
    return current


# ---- what he says ---------------------------------------------------------------------------------------------------
_AS = r"(?:like|as if|as though)\s+i(?:'?m| am| was| were)\s+"
_AGE = r"(?:[- ]years?(?:[- ]old)?)?"
_NAMED = [
    ("one", re.compile(_AS + r"(?:(?:a\s+)?(?:one|1)(?:" + _AGE + r")(?![- ]?\w)|a (?:baby|newborn|toddler))|"
                       r"\b(?:to|for) a (?:one[- ]year[- ]old|baby|toddler)\b|\b(?:one[- ]year[- ]old|baby) (?:level|version)\b", re.I)),
    ("prek", re.compile(_AS + r"(?:in |at )?(?:pre[- ]?k\b|pre[- ]?school\b|preschool\b|kindergarten\b|a (?:pre[- ]?schooler|kindergartner|kindergartener)\b|"
                        r"(?:a\s+)?(?:two|three|four|2|3|4)" + _AGE + r"(?![- ]?\w))|"
                        r"\b(?:pre[- ]?k|pre[- ]?school|kindergarten) (?:level|version)\b|\b(?:to|for) a (?:pre[- ]?schooler|kindergartner)\b", re.I)),
    ("elementary", re.compile(_AS + r"(?:in |at )?(?:(?:elementary|primary|grade)(?: school)?\b|an? (?:elementary|primary|grade)[- ]schooler\b|"
                              r"(?:in )?(?:first|second|third|fourth|fifth) grade\b|a (?:first|second|third|fourth|fifth)[- ]grader\b)|"
                              r"\b(?:elementary|primary|grade)[- ]school (?:level|version)\b|\bat (?:an? )?elementary(?:[- ]school)? level\b", re.I)),
    ("middle", re.compile(_AS + r"(?:in |at )?(?:middle school\b|junior high\b|a middle[- ]schooler\b|(?:in )?(?:sixth|seventh|eighth) grade\b|"
                          r"a (?:sixth|seventh|eighth)[- ]grader\b)|\bmiddle[- ]school (?:level|version)\b|\bat (?:a )?middle[- ]school level\b", re.I)),
    ("high", re.compile(_AS + r"(?:in |at )?(?:high school\b|a high[- ]schooler\b|a (?:freshman|sophomore) in high school\b)|"
                        r"\bhigh[- ]school (?:level|version)\b|\bat (?:a )?high[- ]school level\b", re.I)),
    ("college", re.compile(_AS + r"(?:in |at )?(?:college\b|university\b|a (?:college|university) student\b|a grad(?:uate)? student\b)|"
                           r"\b(?:college|university)[- ]level\b|\bat (?:a )?(?:college|university) level\b|\b(?:college|university) version\b|"
                           r"\b(?:exam[- ]ready|for (?:the|my) (?:exam|test)|at exam level)\b", re.I)),
]
# "like I'm a kid" / "for dummies" name no age or rung: they ask for simpler, one step from where he is.
_SIMPLER = re.compile(_AS + r"(?:a\s+)?(?:five|six|seven|5|6|7|a (?:kid|child|little kid)|kid|child|stupid|dumb|an idiot|new(?: to (?:this|it))?)" + _AGE + r"\b|"
                      r"\beli5\b|\bto a (?:five[- ]year[- ]old|child|kid)\b|\bin (?:simple|plain|simpler|plainer) (?:terms|words|english)\b|"
                      r"\bfor (?:a )?(?:beginners?|dummies)\b|\b(?:more )?simply\b|\bsimpler\b|\bdumb it down\b|\bdumbed down\b|\beasier\b", re.I)
_DEEPER = re.compile(r"\bin (?:more|greater) (?:detail|depth)\b|\bharder\b|\bmore (?:difficult|challenging)\b|\bmore (?:detail|depth|technical)\b|\bgo(?:ing)? deeper\b|\bproperly\b|"
                     r"\bthe (?:proper|real|technical|full|grown[- ]up) (?:explanation|version)\b|\bmore advanced\b", re.I)


# A NAMED age goes straight to its rung: 1-2 one-year-old, 3-5 pre-K, 6-10 elementary, 11-13 middle, 14-18 high school.
_NUMBERS = {w: i for i, w in enumerate("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen "
                                       "fifteen sixteen seventeen eighteen".split())}
_N = r"(1[0-8]|[1-9]|" + "|".join(sorted((w for w in _NUMBERS if w != "zero"), key=len, reverse=True)) + r")"
_AGES = [
    re.compile(_AS + r"(?:an?\s+)?" + _N + _AGE + r"(?![- ]?\w)", re.I),
    re.compile(r"\b(?:like|to|for)\s+(?:an?\s+)?" + _N + r"[- ]years?[- ]old\b", re.I),
    re.compile(r"\beli\s?" + _N + r"\b", re.I),
]
_PEOPLE = [
    ("one", re.compile(r"\blike\s+an?\s+(?:baby|newborn|toddler)\b", re.I)),
    ("prek", re.compile(r"\blike\s+an?\s+(?:pre[- ]?schooler|kindergart(?:e)?ner)\b", re.I)),
    ("elementary", re.compile(r"\b(?:like|to|for)\s+an?\s+(?:elementary|primary|grade)[- ]school(?:er| student| kid)?\b|"
                              r"\b(?:like|to|for)\s+an?\s+(?:first|second|third|fourth|fifth)[- ]grader\b", re.I)),
    ("middle", re.compile(r"\b(?:like|to|for)\s+an?\s+(?:middle[- ]school(?:er| student| kid)?|junior high(?: student| kid)?|"
                          r"(?:sixth|seventh|eighth)[- ]grader)\b", re.I)),
    ("high", re.compile(r"\b(?:like|to|for)\s+an?\s+high[- ]school(?:er| student| kid)?\b", re.I)),
    ("college", re.compile(_AS + r"(?:an?\s+)?(?:adult|grown[- ]?up|expert|professional)\b|"
                           r"\b(?:like|to|for)\s+an?\s+(?:adult|grown[- ]?up|expert|professional)\b", re.I)),
]


def _age_rung(age: str) -> str:
    n = int(age) if age.isdigit() else _NUMBERS[age.lower()]
    return "one" if n <= 2 else "prek" if n <= 5 else "elementary" if n <= 10 else "middle" if n <= 13 else "high"


def named(text: str) -> str:
    """The rung he named in this sentence ("like I'm in middle school", "like I'm five", "for the exam"), or ""."""
    for pattern in _AGES:
        m = pattern.search(text or "")
        if m:
            return _age_rung(m.group(1))
    for rung, pattern in _PEOPLE:
        if pattern.search(text or ""):
            return rung
    for rung, pattern in _NAMED:
        if pattern.search(text or ""):
            return rung
    return ""


def level_in(text: str) -> str:
    """What his sentence asks of the ladder: a named rung, "simpler", "deeper", or "" (stay where he is)."""
    return named(text) or ("simpler" if _SIMPLER.search(text or "") else "deeper" if _DEEPER.search(text or "") else "")


def strip(text: str) -> str:
    """His sentence without the words that only name a level (what is left is the topic)."""
    for pattern in [*_AGES, *(p for _, p in _PEOPLE), *(p for _, p in _NAMED)]:
        text = pattern.sub(" ", text)
    return _DEEPER.sub(" ", _SIMPLER.sub(" ", text))


# ---- the built-in course: hand-written rungs for the networking chapter ----------------------------------------------
def _rungs(*texts):
    assert len(texts) == len(RUNGS)
    return dict(zip(RUNGS, texts))


HAND = {
    "IP address": _rungs(
        "An IP address is the logical, layer-3 identifier of a network interface; routers forward each packet by matching its destination address against their routing tables. IPv4 addresses are 32 bits, written as four decimal octets such as 192.168.1.20, and IPv6 addresses are 128 bits written in hexadecimal. Every address has a network prefix and a host part, and the prefix length, for example /24, says where one ends and the other begins. Addresses are configured statically or leased from DHCP, and private ranges such as 10.0.0.0/8 are not routed on the public internet.",
        "An IP address is the number that tells the network where a device is, so data can be delivered to it. An IPv4 address is 32 bits, shown as four numbers from 0 to 255. Worked example: your laptop is 192.168.1.20 and wants a page from 93.184.216.34. Each packet it sends carries both numbers, the routers on the way read the destination and pass it along, and the reply comes back to 192.168.1.20 because that was written as the source.",
        "An IP address works like the street address on a house. When you send a parcel you write where it goes and where it came from, and the delivery service uses that to find the right door. Every device on a network has such an address. The one new term to keep is packet: one small parcel of data with both addresses written on it.",
        "Imagine a town where every house has a number on its door. Mia wants to send a drawing to Leo, so she writes Leo's house number on the envelope and her own on the back. The mail carrier reads the number and walks straight to Leo's door. Computers do the same thing: each one has its own number, and that number is its IP address.",
        "Every house has a number. The computer has a number too. That is how the message finds it.",
        "House number! (point to the door)"),
    "Subnet mask": _rungs(
        "A subnet mask is a 32-bit value whose leading one-bits mark the network portion of an IPv4 address and whose trailing zero-bits mark the host portion. A host ANDs its own address and the destination address with the mask: if the two results match, the destination is on-link and is reached directly using ARP; if not, the packet goes to the default gateway. 255.255.255.0 is the same as the prefix /24 and leaves 8 host bits, which gives 254 usable addresses once the network and broadcast addresses are removed.",
        "A subnet mask tells a device which part of an IP address names the network and which part names the device itself. Worked example: address 192.168.1.20 with mask 255.255.255.0. The 255s cover the first three numbers, so the network is 192.168.1 and the host is 20. 192.168.1.77 has the same network part, so the two talk directly. 192.168.2.5 does not, so traffic to it is handed to the router.",
        "Think of a phone number with an area code. The subnet mask is the rule that says how many digits are the area code and how many are the personal number. Numbers with the same area code are local, and anything else has to go through the exchange, which on a network is the router. The one new term: network part, the area-code section of an IP address.",
        "A school has many classrooms. Each child wears a badge that shows a room colour and a seat number. If two badges have the same colour, the children are in the same room and can just pass a note. If the colours are different, the note has to go through the hallway monitor. The subnet mask is the rule that says which part of the badge is the room colour.",
        "Same room, pass the note. Other room, ask the teacher.",
        "Red block, red block: same! (hold two together) Blue block: other!"),
    "DNS": _rungs(
        "DNS is the hierarchical, distributed naming system that resolves domain names to resource records such as A and AAAA addresses. A stub resolver sends the query to a recursive resolver, which, if the answer is not cached, walks the hierarchy: a root server refers it to the TLD servers, those refer it to the zone's authoritative server, and that returns the record. Answers are cached for the record's TTL, and queries normally use UDP port 53, with TCP for large responses. When resolution fails, hosts are still reachable by IP address, which is the classic way to tell a DNS fault from a connectivity fault.",
        "DNS turns a name such as example.com into the IP address computers actually connect to. Worked example: you type example.com. Your laptop asks its DNS resolver for the address of example.com. The resolver looks it up and answers 93.184.216.34, and only then does your browser connect to that number. If DNS is down, the name fails but typing the number still works.",
        "DNS is like the contacts list in a phone. You tap a name, and the phone looks up the number, because the phone network only understands numbers. Computers are the same: they connect to numbers, and DNS looks up the number for the name you typed. The one new term: resolve, which means looking up the number that belongs to a name.",
        "Sam wants to visit his friend Zoe, but he only knows her name, not where she lives. So he asks the librarian, who has a big book of every name in town. The librarian finds Zoe and says she lives at number 34. Now Sam can walk there. DNS is the librarian of the internet: you give it a name, and it gives back the number.",
        "You know the name. The helper knows where it lives. Ask the helper, then go there.",
        "Where's doggy? There! (point)"),
    "DHCP": _rungs(
        "DHCP assigns IP configuration to hosts automatically through a four-message exchange known as DORA: the client broadcasts a Discover, servers reply with an Offer, the client broadcasts a Request for one offer, and the server confirms with an Acknowledge. The lease carries the address, subnet mask, default gateway and DNS servers, and it is valid for a limited lease time that the client renews at half its length. The exchange uses UDP ports 67 and 68. A client that gets no answer falls back to a 169.254.0.0/16 link-local address, which is the usual sign that DHCP has failed.",
        "DHCP gives a device its network settings automatically when it joins. Worked example: your phone joins the Wi-Fi with no address at all. It calls out to find a DHCP server, and the router offers 192.168.1.50. The phone asks to take that address, and the router confirms. The phone now has an address, the mask, the gateway and a DNS server, borrowed for a set time called the lease.",
        "DHCP is like the front desk of a hotel. You arrive without a room, the desk gives you the key to a free room for your stay, and when you leave the room goes back for the next guest. A device joining a network gets its address the same way. The one new term: lease, the time you may keep the address before asking again.",
        "On the first day of school the children come in with no seat. The teacher stands at the door with a chart and gives each child a desk: you sit at desk 5, you sit at desk 6. Nobody has to choose, and no two children get the same desk. When a child moves away, the desk is free for someone new. DHCP is that teacher for computers, handing out the numbers.",
        "You come in. The teacher gives you a seat. Now you have a place!",
        "Here you go! Yours! (hand over a block)"),
    "Switch": _rungs(
        "A switch is a layer-2 device that forwards Ethernet frames within one broadcast domain. It learns by reading the source MAC address of every frame and recording it against the ingress port in its MAC address table, and it forwards by looking up the destination MAC and sending the frame out of that one port. Frames for an unknown destination, and broadcasts, are flooded to every port except the one they arrived on. Each port is its own collision domain, and VLANs split one switch into several separate broadcast domains.",
        "A switch connects the devices inside one local network and sends each frame only to the device it is for. Worked example: a PC on port 1 sends to a printer on port 3. The switch notes that the PC's MAC address is on port 1, looks up the printer's MAC, and finds port 3. It sends the frame out of port 3 only. If it does not yet know where the printer is, it sends the frame to all ports once and learns from the reply.",
        "A switch is like the person who hands out mail in an office. They know which desk each person sits at, so each letter goes to exactly one desk instead of being read out to the whole room. The one new term: MAC address, the fixed name tag built into every network card, which the switch uses to know who sits where.",
        "In a classroom, the teacher collects notes and passes them on. The teacher remembers that Ana sits by the window and Ben sits by the door. A note for Ben goes only to Ben, so nobody else is bothered. A switch does that for the computers plugged into it: it remembers who is on which plug and passes each message to the right one.",
        "A note for Ben. The teacher gives it to Ben. Only Ben!",
        "For you, not you! (point)"),
    "Router": _rungs(
        "A router is a layer-3 device that forwards packets between different IP networks. For each packet it looks up the destination address in its routing table, picks the longest matching prefix, decrements the TTL, and sends the packet out of the chosen interface in a new layer-2 frame addressed to the next hop. Routes are connected, static, or learned from routing protocols such as OSPF and BGP. Routers do not forward broadcasts, so each interface is the edge of a broadcast domain.",
        "A router joins different networks and passes packets from one to the next. Worked example: your laptop, 192.168.1.20, sends a packet to 8.8.8.8. That is not in the home network, so the laptop hands it to the home router. The router checks its table, sees that everything unknown goes to the internet provider, and forwards it. Routers further on repeat the same step until it arrives.",
        "A router is like a road junction with signs. Each car has a destination, the sign at the junction says which road leads there, and the car follows it to the next junction. A switch delivers inside one building, and a router connects the buildings. The one new term: routing table, the router's list of signs saying which way each network lies.",
        "Two villages are joined by one bridge, and a bridge keeper stands on it. When a letter is for someone in the same village, it never reaches the bridge. When it is for the other village, the keeper takes it across and hands it on. A router is that bridge keeper: it carries messages from one network to another.",
        "This town. That town. The bridge goes between.",
        "Vroom! Over the bridge! (push a toy car)"),
    "TCP and UDP": _rungs(
        "TCP and UDP are the two main transport-layer protocols. TCP is connection-oriented: a three-way handshake of SYN, SYN-ACK and ACK synchronises sequence numbers, every segment is acknowledged, lost segments are retransmitted, and flow and congestion control adapt the sending rate, so the application receives an ordered, complete byte stream. UDP is connectionless: an 8-byte header, no handshake, no acknowledgements and no ordering, which gives low overhead and low delay. HTTP, SSH and mail use TCP, while DNS queries, voice, video and games use UDP.",
        "TCP and UDP are two ways to send data: TCP first makes a connection and checks that everything arrived, and UDP just sends. Worked example with TCP: your browser sends SYN to ask for a connection. The server answers SYN-ACK. The browser sends ACK, and only then does the page data flow, with every piece confirmed and sent again if it was lost. A video call uses UDP instead, because a late piece of sound is useless anyway.",
        "TCP is like a phone call: you say hello, wait for a hello back, and if you miss something you ask the other person to repeat it. UDP is like shouting an announcement across a playground: fast, but nobody checks who heard it. The one new term: handshake, the short hello, hello, okay exchange TCP does before sending anything.",
        "Two friends are passing water balloons over a fence. The careful way: one asks if the other is ready, and waits for the answer. Then every balloon is counted, and a dropped balloon is thrown again. That is TCP. The fast way is to throw them all over and hope: some get lost, but it is quick, and that is UDP.",
        "Careful way: ask, then catch, then say got it. Fast way: just throw!",
        "Ready? Catch! (roll the ball)"),
    "Port number": _rungs(
        "A port number is a 16-bit transport-layer identifier, 0 to 65535, that selects the process a segment is delivered to on a host. A connection is identified by the combination of source address, source port, destination address, destination port and protocol. Servers listen on well-known ports below 1024, such as 22 for SSH, 53 for DNS, 80 for HTTP and 443 for HTTPS, while a client picks a temporary ephemeral port for its own side. Firewalls filter largely on these numbers.",
        "An IP address gets data to the right machine, and the port number gets it to the right program on that machine. Worked example: one server runs a website and a remote shell. Your browser connects to 93.184.216.34 on port 443 and reaches the web server program. An admin connects to the same address on port 22 and reaches the shell. It is the same machine with two different doors.",
        "If the IP address is the street address of an apartment building, the port number is the apartment number. The mail gets to the building with the address, and to the right family with the apartment number. The one new term: port, a numbered door on a computer that one program listens behind.",
        "A big building has one front address but many doors inside. Door 80 is the library and door 22 is the workshop. When a visitor arrives, the guard asks which door, and sends them to the right room. A port number is that door number, so a message reaches the right program.",
        "One big house. Many doors. Knock on the right door!",
        "Knock knock! Door! (knock)"),
    "Default gateway": _rungs(
        "The default gateway is the next-hop router a host uses for any destination that is not on its own subnet and has no more specific route; in the routing table it is the 0.0.0.0/0 entry. The host decides by applying its subnet mask to the destination: off-link traffic is framed to the gateway's MAC address, found by ARP, while the IP header still carries the final destination. A wrong or missing gateway produces the typical symptom of working local connectivity with no access to other networks.",
        "The default gateway is the router address a device uses for everything outside its own network. Worked example: your PC is 192.168.1.20 with gateway 192.168.1.1. A packet for the printer at 192.168.1.30 is local and goes direct. A packet for 8.8.8.8 is not local, so the PC hands it to 192.168.1.1. The router works out the rest of the way.",
        "The default gateway is like the front door of your house. To reach another room you just walk there, but to reach anywhere else in the world you first go through the front door. The one new term: gateway, the router that is your network's door to every other network.",
        "A village has one gate in its wall. If Tom wants to give an apple to his neighbour, he just walks over. If he wants to send an apple to a faraway town, he takes it to the gate, and the gatekeeper sends it on. The default gateway is that gate: the way out for every message that is not for a neighbour.",
        "Friend next door, walk over. Far away, go out the gate.",
        "Out the door! Bye-bye! (wave)"),
    "NAT": _rungs(
        "NAT rewrites IP addresses, and with port address translation also port numbers, as packets cross a router. On the way out the router replaces the private source address and port with its own public address and a free port, and records the mapping in a translation table. On the way back it matches the reply against the table and restores the original private address and port. This lets many hosts in private ranges such as 192.168.0.0/16 share one public IPv4 address, and it means unsolicited inbound connections have no table entry and are dropped unless a port forward exists.",
        "NAT lets a whole home network share one public IP address. Worked example: your laptop, 192.168.1.20, asks a website for a page. The router swaps the laptop's private address for its own public one, 203.0.113.5. It writes down that this conversation belongs to the laptop. When the reply arrives at 203.0.113.5, the router checks its note and passes the reply to the laptop.",
        "NAT is like the receptionist of an office with one phone number. Everyone inside calls out through the same number, and when someone calls back the receptionist remembers who made the call and puts it through to the right desk. The one new term: private address, an address that only works inside your own network.",
        "A family of five shares one mailbox with one address. When Lily sends a letter, Dad writes the family address on it. He makes a note that Lily is waiting for an answer from Grandma. When Grandma's answer comes to the mailbox, Dad reads his note and gives the letter to Lily. NAT is Dad at the mailbox for a home network.",
        "One mailbox for the whole family. Dad knows which letter is yours.",
        "Mail for you! (give it)"),
}

# ---- every other built-in concept: templates around the outline's own summary ----------------------------------------
_SOUNDS = {"Hardware": "Beep boop!", "Operating Systems": "Click click!", "Security": "Lock! Click!", "Cloud": "Up, up! Cloud!",
           "Databases": "Box! In the box!", "Troubleshooting": "Uh-oh! Fix, fix!", "Networking": "Hello? Hello!"}
_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])")


def templated(term: str, summary: str, chapter: str = "") -> dict:
    """Six rungs made from the outline's summary by fixed templates: correct, but not hand-written. The lower rungs
    are a frame around the summary rather than a new story, which is why they are marked as templated."""
    parts = _SPLIT.split(summary.strip())
    first, rest = parts[0], " ".join(parts[1:])
    return _rungs(
        f"{summary} The exact term to use is {term}.",
        f"{first} Here is what that means in practice. {rest}" if rest
        else f"{first} To make it concrete, find one real example of {term} on your own PC and say what it does there.",
        f"Every part of a computer system has one job, like each worker in a kitchen. This one is called {term}. Its job: {first}",
        f"Here is a story about {term}. In a busy workshop everybody has one job, and nobody can do the others' jobs. {first} "
        f"When {term} does its job well, the whole workshop runs smoothly.",
        f"This is {term}. It has one job. It helps the computer.",
        f"{term}! {_SOUNDS.get(chapter, 'Beep!')} (clap twice)")


def builtin(term: str, summary: str = "", chapter: str = ""):
    """The built-in course's rungs for a concept -> ({rung: text}, hand_written: bool)."""
    if term in HAND:
        return HAND[term], True
    return templated(term, summary, chapter), False
