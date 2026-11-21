"""The tutor's quality gates, with a fake model: the rung referee (core/study_check), the regenerate-once-with-the-reason loop
in study_engine.explain, the board repair and its second chance, and the worked-example boards whose numbers come from code."""
import json

import pytest

from core import study_board as board
from core import study_calc as calc
from core import study_check as check
from core import study_engine as engine
from core import study_ladder as ladder

SOURCE = ("DNS is the Domain Name System that translates names such as example.com into IP addresses. A client asks a recursive resolver, "
          "which queries a root server, then a top-level domain server, then the authoritative server, and returns the answer.")
PASSAGE = {"id": "m:0", "chapter": "Chapter 2", "section": "", "page": 3, "material": "Notes", "text": SOURCE}
COLLEGE = ("DNS works because a recursive resolver walks the hierarchy on behalf of the client. It asks a root server, which refers it to a top-level domain "
           "server, which in turn refers it to the authoritative server that holds the record. The answer is cached for its TTL.")
HIGH = ("DNS turns a name into an address. Example: your laptop asks the resolver for example.com. The resolver asks the servers one "
        "after another, and the answer 93.184.216.34 comes back.")
MIDDLE = "DNS is like a phone book: you look up a name and it gives you the number. The number is called an address."


class Model:
    def __init__(self, *replies):
        self.replies, self.prompts = list(replies), []

    def __call__(self, prompt):
        self.prompts.append(prompt)
        reply = self.replies.pop(0) if self.replies else "nothing"
        return reply if isinstance(reply, str) else json.dumps(reply)


def draft(text, analogy="", board_=None):
    out = {"explanation": text, "analogy": analogy, "limit": "", "check": ""}
    if board_ is not None:
        out["board"] = board_
    return out


GOOD_BOARD = {"steps": [
    {"say": "A laptop asks.", "ops": [{"op": "icon", "id": "pc", "icon": "computer", "text": "Laptop"}]},
    {"say": "The resolver looks it up.", "ops": [{"op": "icon", "id": "res", "icon": "server", "text": "Resolver"}, {"op": "arrow", "from": "pc", "to": "res", "text": "1 name?"}]},
    {"say": "The answer returns.", "ops": [{"op": "arrow", "from": "res", "to": "pc", "text": "2 93.184.216.34"}]}]}


# ---- the referee ----------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("rung, text, analogy, previous, code", [
    ("college", COLLEGE, "", None, None),
    ("college", "", "", None, "empty"),
    ("college", " ".join(f"The resolver asks server number {i} for the DNS record." for i in range(9)), "", None, "long"),
    ("college", "DNS is the Domain Name System that translates names such as example.com into IP addresses.", "", None, "copy"),
    ("high", HIGH, "", {"level": "college", "text": COLLEGE}, None),
    ("high", COLLEGE, "", {"level": "college", "text": COLLEGE}, "repeat"),
    ("middle", MIDDLE, "a phone book", {"level": "high", "text": HIGH}, None),
    ("middle", MIDDLE, "", {"level": "high", "text": HIGH}, "no_analogy"),
    ("middle", "Bananas are yellow and grow on trees in warm places.", "a fruit stand", None, "off_topic"),
    ("middle", "DNS is like a phone book for the Domain Name System resolver authoritative recursive hierarchy servers and records, "
               "explaining translation of names into addresses through delegation of zones.", "a phone book",
     {"level": "high", "text": "DNS turns a name into an address."}, "harder"),
    ("elementary", "Sam wants to find Zoe's house. The librarian knows every name in town. DNS works like that librarian.", "a librarian",
     {"level": "middle", "text": MIDDLE}, None),
    ("prek", "I ask the helper. The helper knows. I go there!", "a helper", {"level": "elementary", "text": "Sam wants to find Zoe's house. The librarian knows every name in town. He asks her and she tells him where Zoe lives, so he can go there. DNS works like that."}, None),
    ("prek", "I ask the helper. The helper knows where the house is. I go there! Yay! We play all day! It is so fun! Yay!", "a helper", None, "long"),
    ("one", "Where is name? There! (point)", "", {"level": "prek", "text": "I ask the helper. The helper knows. I go there!"}, None),
    ("one", "Name! Where? (point) There! Name! Where? There! Name! Where? There! Yes yes yes!", "", None, "long"),
    ("one", "Bananas! Yum! (rub tummy)", "", None, "off_topic"),
])
def test_the_referee(rung, text, analogy, previous, code):
    verdict = check.judge(rung, text, "DNS", SOURCE, analogy, previous)
    assert (verdict[0] if verdict else None) == code
    if verdict:
        assert verdict[1].endswith(".")                      # a sentence the model can act on


def test_jargon_is_refused_at_the_bottom_of_the_ladder():
    verdict = check.judge("one", "Resolver! Authoritative hierarchy!", "DNS", SOURCE, "a helper")
    assert verdict and verdict[0] == "jargon"


def test_simpler_is_measured_on_reading_level_and_length():
    assert check.grade("The cat sat on the mat.") < check.grade(COLLEGE)
    assert check.echo(COLLEGE, COLLEGE) == 1.0 and check.echo(COLLEGE, MIDDLE) < 0.3
    assert len(check.sentences("One. Two! Three?")) == 3 and check.words("Yay! (clap) Go") == ["Yay", "Go"]


# ---- regenerate once, with the reason ----------------------------------------------------------------------------------
def test_a_rung_that_repeats_the_one_above_is_asked_again_with_the_reason():
    previous = {"level": "college", "text": COLLEGE}
    model = Model(draft(COLLEGE, "x", GOOD_BOARD), draft(HIGH, "", GOOD_BOARD))
    out = engine.explain("DNS", [PASSAGE], "high", model, previous=previous)
    assert out["source"] == "model" and out["text"] == HIGH and len(model.prompts) == 2 and out["refused"] == ["repeat"]
    assert "Your last try was refused: It repeats the previous explanation." in model.prompts[1] and "refused" not in model.prompts[0]
    assert "exactly ONE step simpler" in model.prompts[0] and ladder.EXAMPLE_TOPIC in model.prompts[0]    # the few-shot ladder is in


def test_a_second_attempt_that_is_still_off_the_idea_falls_back_to_the_material():
    junk = draft("Bananas are yellow and grow on trees in warm places.", "fruit")
    model = Model(junk, junk)
    out = engine.explain("DNS", [PASSAGE], "middle", model)
    assert out["source"] == "passage" and len(model.prompts) == 2 and "DNS is the Domain Name System" in out["text"]


def test_a_draft_that_is_only_too_long_is_still_kept_when_the_retry_fails():
    long = draft("DNS is the Domain Name System and it answers names with addresses. " + "A resolver asks servers one after another. " * 7, "")
    model = Model(long, "this is not json")
    out = engine.explain("DNS", [PASSAGE], "college", model)
    assert out["source"] == "model" and out["text"].startswith("DNS is the Domain Name System") and len(model.prompts) == 3    # + the board-only call


def test_the_bottom_rungs_without_a_model_are_not_the_sources_own_sentence():
    for rung in ("prek", "one"):
        out = engine.explain("DNS", [PASSAGE], rung, None)
        assert out["source"] == "passage" and out["templated"] and "Domain Name System" not in out["text"] and "DNS" in out["text"]
    assert engine.explain("DNS", [PASSAGE], "college", None)["text"].startswith("DNS is the Domain Name System")


# ---- the board: repair and a second chance ---------------------------------------------------------------------------------
ONE_STEP = {"steps": [{"say": "", "ops": [
    {"op": "icon", "id": "pc", "icon": "computer", "text": "Laptop"}, {"op": "icon", "id": "res", "icon": "server", "text": "Resolver"},
    {"op": "box", "id": "root", "text": ""}, {"op": "arrow", "from": "pc", "to": "res"}, {"op": "arrow", "from": "res", "to": "root"}]}]}


def test_everything_in_one_step_is_rebuilt_to_build_up_one_shape_at_a_time():
    text = "The laptop asks the resolver. The resolver asks the root server. The root points onward."
    scene = board.for_explanation("DNS", text, "college", draft=ONE_STEP)
    assert scene["source"] == "repaired" and len(scene["steps"]) == 4          # three shapes, and the second arrow is a beat of its own
    assert [s["say"] for s in scene["steps"]] == ["The laptop asks the resolver.", "The resolver asks the root server.", "The root points onward.", ""]
    shapes = [[o["id"] for o in s["ops"] if o["op"] in ("icon", "box")] for s in scene["steps"]]
    assert shapes == [["pc"], ["res"], ["root"], []] and all(o.get("text") for s in scene["steps"] for o in s["ops"] if o["op"] in ("icon", "box"))
    assert scene["steps"][2]["ops"][0]["text"]                       # the unlabelled box got one of the explanation's own terms


def test_shapes_with_no_arrows_are_chained_in_the_order_they_appear():
    bare = {"steps": [{"ops": [{"op": "box", "id": "a", "text": "Client"}, {"op": "box", "id": "b", "text": "Server"}, {"op": "box", "id": "c", "text": "Disk"}]}]}
    scene = board.for_explanation("Request", "The client asks the server. The server reads the disk. The answer returns.", "high", draft=bare)
    arrows = [(o["from"], o["to"]) for s in scene["steps"] for o in s["ops"] if o["op"] == "arrow"]
    assert scene["source"] == "repaired" and arrows == [("a", "b"), ("b", "c")]


def test_a_healthy_model_board_stays_the_models_and_speaks_the_explanation_in_order():
    text = "A laptop wants a page. The resolver looks it up. The answer comes back."
    scene = board.for_explanation("DNS", text, "high", draft=GOOD_BOARD)
    assert scene["source"] == "model" and [s["say"] for s in scene["steps"]] == ["A laptop wants a page.", "The resolver looks it up.", "The answer comes back."]
    assert board.healthy(scene, "high") and not board.healthy(None) and not board.healthy({"steps": [{"say": "x", "ops": []}]})


def test_a_board_that_cannot_be_rebuilt_is_the_fallback():
    for junk in (None, "text", 5, {"steps": "no"}, {"steps": [{"ops": [{"op": "arrow", "from": "a", "to": "b"}]}]}, {"steps": [{"ops": [{"op": "bounce", "target": "x"}]}]}):
        scene = board.for_explanation("DNS", COLLEGE, "college", draft=junk)
        assert scene["source"] == "fallback" and scene["steps"], junk
    assert board.repair({"steps": [{"ops": [{"op": "label", "text": ""}]}]}, COLLEGE) is None


def test_a_board_written_as_a_string_is_read_and_a_missing_board_is_asked_for_once_more():
    model = Model(draft(HIGH, "", json.dumps(GOOD_BOARD)))
    out = engine.explain("DNS", [PASSAGE], "high", model)
    assert out["board"]["source"] == "model" and len(model.prompts) == 1
    model = Model(draft(HIGH, ""), GOOD_BOARD)                         # no board in the answer: the second call returns only the board
    out = engine.explain("DNS", [PASSAGE], "high", model)
    assert len(model.prompts) == 2 and model.prompts[1].startswith('Here is an explanation of "DNS"') and HIGH in model.prompts[1]
    assert out["board"]["source"] == "model" and out["text"] == HIGH
    model = Model(draft(HIGH, ""), "garbage")                           # and when that fails too: the board built in code
    assert engine.explain("DNS", [PASSAGE], "high", model)["board"]["source"] == "fallback"


# ---- worked examples: numbers computed by code ---------------------------------------------------------------------------------
def texts(scene):
    return [o.get("text", "") for s in scene["steps"] for o in s["ops"] if o["op"] in ("box", "icon", "circle", "label")]


@pytest.mark.parametrize("topic, source, rung, expected", [
    ("Subnetting", "", "college", ["192.168.1.0/24", "8 host bits = 256", "borrow 2: /26", "192.168.1.0/26", "192.168.1.64/26", "192.168.1.128/26", "192.168.1.192/26"]),
    ("Subnetting", "Take 10.0.0.0/16 and borrow three bits.", "high", ["10.0.0.0/16", "16 host bits = 65536", "borrow 3: /19", "10.0.0.0/19", "10.0.32.0/19", "10.0.64.0/19", "10.0.96.0/19"]),
    ("Big-O notation", "", "high", ["n = 1,000,000", "O(1): 1", "O(log n): 20", "O(n): 1,000,000", "O(n log n): 20,000,000", "O(n^2): 1,000,000,000,000"]),
    ("RAID", "", "college", ["Disk 1: 2 TB", "Disk 4: 2 TB", "striping + 1 parity", "usable: 6 TB", "survives 1 failed"]),
    ("RAID", "RAID 1 on 2 disks of 4 TB.", "high", ["Disk 2: 4 TB", "mirroring", "usable: 4 TB"]),
    ("File permissions", "", "college", ["rwxr-xr--", "owner rwx = 7", "group r-x = 5", "others r-- = 4", "chmod 754"]),
    ("File permissions", "mode 640", "high", ["rw-r-----", "owner rw- = 6", "group r-- = 4", "others --- = 0", "chmod 640"]),
    ("Recursion", "", "college", ["factorial(4)", "factorial(0)", "factorial(1) = 1", "factorial(3) = 6", "factorial(4) = 24"]),
    ("Binary numbers", "", "college", ["8", "13 = 8 + 4 + 1", "1101"]),
])
def test_worked_boards_carry_exactly_the_computed_numbers(topic, source, rung, expected):
    scene = board.worked(calc.example(topic, source), rung)
    assert scene and scene["source"] == "worked" and scene["rung"] == rung
    for want in expected:
        assert want in texts(scene), (want, texts(scene))
    assert all(s["say"] for s in scene["steps"]) and len(scene["steps"]) <= board.MAX_STEPS


def test_the_worked_board_is_for_the_upper_rungs_only_and_wins_over_a_wrong_model_board():
    ex = calc.example("Subnetting", "")
    assert board.worked(ex, "middle") is None and board.worked(None, "college") is None
    wrong = {"steps": [{"say": "x", "ops": [{"op": "box", "id": "a", "text": "/24 = 128 hosts"}, {"op": "box", "id": "b", "text": "/26 = 30 hosts"},
                                              {"op": "arrow", "from": "a", "to": "b"}]}]}
    scene = board.for_explanation("Subnetting", "Anything.", "high", draft=wrong, example=ex)
    assert scene["source"] == "worked" and "/24 = 128 hosts" not in texts(scene) and "192.168.1.64/26" in texts(scene)
    low = board.for_explanation("Subnetting", "Rooms in a house.", "middle", draft=wrong, example=ex)
    assert low["source"] != "worked"


def test_the_prompt_hands_the_model_the_computed_figures_and_the_board_is_drawn_from_them():
    source = {"id": "m:1", "chapter": "Net", "section": "", "page": 1, "material": "Notes", "text": "Subnetting divides a network into smaller networks."}
    model = Model(draft("Subnetting splits one network into smaller ones by borrowing host bits for the network part. A /24 has 8 host bits, so it holds 256 addresses. Borrowing 2 of those bits gives four /26 subnets of 64 addresses each, which a router can then keep apart.", "", {"steps": []}))
    out = engine.explain("Subnetting", [source], "college", model)
    assert "Use EXACTLY these figures" in model.prompts[0] and "254 usable" in model.prompts[0] and "192.168.1.64-127" in model.prompts[0]
    assert out["board"]["source"] == "worked" and "192.168.1.192/26" in texts(out["board"]) and len(model.prompts) == 1
    model = Model(draft("Rooms in a house.", "a house"))
    engine.explain("Subnetting", [source], "middle", model)
    assert "Use EXACTLY" not in model.prompts[0]                         # figures only where there is a worked example to draw


def test_the_fallback_never_reads_a_passage_of_another_chapter():
    other = dict(PASSAGE, id="m:1", chapter="Chapter 9", text="Encryption is a two-way operation that turns data into ciphertext with a key.")
    for turn in range(4):
        out = engine.explain("DNS", [PASSAGE, other], "college", None, turn=turn)
        assert "Domain Name System" in out["text"] and "Encryption" not in out["text"]


@pytest.mark.parametrize("rung, text, code", [
    ("college", "DNS translates names into addresses through a recursive resolver.", "thin"),
    ("high", "DNS turns names into numbers that machines use to find each other on a network. A resolver does the asking on behalf of the client, one server after another. The answer is cached for a while so the next request is fast.", "no_example"),
    ("high", "DNS is like a phone book for the internet, where every name has one number next to it. Imagine looking up a friend in it. The number comes back to your laptop as 93.184.216.34 and the page can load.", "comparison"),
    ("college", "DNS works like a phone book. The resolver queries the root server, which refers it onward to the top-level domain servers. Then the TLD server answers with the address of the authoritative server, and that one gives the record.", "comparison"),
    ("high", HIGH, None),
])
def test_the_upper_rungs_are_not_thin_have_an_example_and_use_no_comparison(rung, text, code):
    verdict = check.judge(rung, text, "DNS", SOURCE, "")
    assert (verdict[0] if verdict else None) == code


def test_a_thin_college_draft_is_asked_again_with_the_reason():
    model = Model(draft("DNS translates names.", ""), draft(COLLEGE, "", GOOD_BOARD))
    out = engine.explain("DNS", [PASSAGE], "college", model)
    assert out["text"] == COLLEGE and "too thin" in model.prompts[1]


@pytest.mark.parametrize("beats, lines, says", [
    (3, ["One.", "Two.", "Three."], ["One.", "Two.", "Three."]),
    (4, ["One."], ["One.", "", "", ""]),                                    # a one-sentence story still builds up, in silence after the sentence
    (4, ["One.", "Two."], ["One.", "", "Two.", ""]),
    (2, ["One.", "Two.", "Three.", "Four."], ["One. Two.", "Three. Four."]),
    (3, ["One.", "Two.", "Three.", "Four."], ["One.", "Two.", "Three. Four."]),
    (2, [], ["", ""]),
])
def test_spread_says_every_sentence_once_in_order(beats, lines, says):
    steps = board.spread([[{"op": "box", "id": f"b{i}", "text": "x"}] for i in range(beats)], lines)
    assert [s["say"] for s in steps] == says and all(s["ops"] for s in steps)
    assert board.spread([], lines) == [] and len(board.spread([[{"op": "box", "id": "a"}]] * 12, ["x"])) == board.MAX_STEPS


@pytest.mark.parametrize("rung, text, analogy, code", [
    ("middle", "First the client says hello. Then the server answers. Then the client confirms.", "a phone call", "no_analogy"),
    ("middle", "DNS is like a phone book, where a name gives you a number. The number is called an address.", "a phone book", None),
    ("elementary", "Sam asks the librarian where Zoe lives. The librarian looks in the big book and answers, and that is DNS.", "a librarian", None),
    ("one", "Banana! Yay!", "", "off_topic"),
    ("one", "DNS finds the number.", "", None),
    ("one", "Find DNS! Yay! (point)", "", None),
    ("prek", "I ask. She tells. I go. We play. It is fun. Yes yes yes.", "a helper", "long"),
])
def test_the_lower_rungs_must_use_their_comparison_and_the_baby_rung_its_picture_book_shape(rung, text, analogy, code):
    verdict = check.judge(rung, text, "DNS", SOURCE, analogy)
    assert (verdict[0] if verdict else None) == code


@pytest.mark.parametrize("rung, text, want", [
    ("one", "Encryption is a box with a key to open it later. Hashing is a blender that makes smoothie; you cannot get the fruit back.",
     "Encryption is a box with a key to open it later!"),
    ("one", "You say a name, it finds the number.", "You say a name, it finds the number!"),
    ("one", "Where? (look) There! Ball!", "Where? There!"),
    ("one", "One two three four five six seven eight nine ten eleven twelve thirteen fourteen.", "One two three four five six seven eight nine ten eleven twelve!"),
    ("prek", "One. Two. Three. Four.", "One. Two. Three."),
    ("college", "  Keep   all   of it. And more.  ", "Keep all of it. And more."),
])
def test_the_bottom_rungs_are_trimmed_to_what_they_are(rung, text, want):
    assert check.finalize(rung, text) == want
    assert check.finalize(rung, check.finalize(rung, text)) == want                   # idempotent


def test_an_unreadable_reply_is_asked_again_once_and_the_upper_rungs_carry_no_comparison_to_ban():
    model = Model("sorry, here you go: not json", draft(COLLEGE, "", GOOD_BOARD))
    out = engine.explain("DNS", [PASSAGE], "college", model)
    assert out["text"] == COLLEGE and out["refused"] == ["unreadable"] and "not the JSON object asked for" in model.prompts[1]
    model = Model("nope", "still nope")
    assert engine.explain("DNS", [PASSAGE], "college", model)["source"] == "passage" and len(model.prompts) == 2
    # a comparison named on the college rung is dropped, so an earlier rung's comparison cannot get a good explanation refused
    model = Model(draft(COLLEGE, "a phone book", GOOD_BOARD))
    out = engine.explain("DNS", [PASSAGE], "college", model, avoid=["a phone book"])
    assert out["source"] == "model" and out["analogy"] == "" and len(model.prompts) == 1


def test_finalize_cuts_a_long_baby_sentence_at_a_clause_not_mid_way():
    cut = check.finalize("one", "The computer puts your favorite toys inside a big box, but when the box is full it hides some toys in a far closet.")
    assert cut == "The computer puts your favorite toys inside a big box!"
    assert check.finalize("one", "Oneword") == "Oneword!"


@pytest.mark.parametrize("text, code", [
    ("RAID 5 puts parity on every disk, so it survives one failed disk. For example, 4 disks of 2 TB give 8 TB raw and 6 TB usable. "
     "That is why parity arrays cost one disk of space but keep the data safe.", None),
    ("RAID 0 stripes the data. For example, four disks of 2 TB give 8 TB and survive zero failed disks. Mirroring copies the data instead, "
     "which is why RAID 1 survives one failure but halves the capacity of two disks.", "numbers"),
])
def test_the_text_must_use_the_computed_figures_when_it_was_given_them(text, code):
    anchors = calc.example("RAID", "")["anchors"]
    verdict = check.judge("high", text, "RAID", "RAID disks parity stripes mirrors", "", None, anchors)
    assert (verdict[0] if verdict else None) == code
    assert check.judge("high", text, "RAID", "RAID disks parity stripes mirrors", "", None) is None or code is None      # without figures: no such rule


def test_the_anchors_are_the_figures_of_each_worked_example():
    assert calc.example("Subnetting", "")["anchors"] == ["256", "254", "/26"]
    assert calc.example("Big-O notation", "")["anchors"] == ["1,000,000|1000000|million", "20"]
    assert calc.example("File permissions", "")["anchors"] == ["754"] and calc.example("Recursion", "")["anchors"] == ["24"]
    assert calc.example("Binary numbers", "")["anchors"] == ["1101"]
    model = Model(draft("Subnetting splits one network into smaller ones by borrowing host bits for the network part. A network has some host bits and "
                        "many addresses. Borrowing a few of those bits gives several smaller subnets that a router keeps apart from each other.", "", GOOD_BOARD),
                  draft("Subnetting splits one network into smaller ones by borrowing host bits. A /24 has 256 addresses and 254 usable hosts. "
                        "Borrowing 2 bits gives four /26 subnets of 64 addresses each, which a router keeps apart from each other.", "", GOOD_BOARD))
    src = {"id": "m:1", "chapter": "Net", "section": "", "page": 1, "material": "N", "text": "Subnetting divides a network into smaller networks."}
    out = engine.explain("Subnetting", [src], "college", model)
    assert out["refused"] == ["numbers"] and "exactly these numbers: 256, 254, /26" in model.prompts[1] and "254 usable hosts" in out["text"]


def test_a_close_cousin_of_the_rung_above_is_kept_on_the_second_try_but_a_duplicate_is_not():
    previous = {"level": "college", "text": COLLEGE}
    cousin = ("DNS works because a recursive resolver walks the hierarchy for the client. It asks a root server, which refers it onward to a top-level domain "
              "server, and that server refers it to the authoritative server with the record. The answer is cached.")
    assert 0.7 <= check.echo(cousin, COLLEGE) < 0.9
    model = Model(draft(COLLEGE, "", GOOD_BOARD), draft(cousin, "", GOOD_BOARD))
    out = engine.explain("DNS", [PASSAGE], "high", model, previous=previous)
    assert out["source"] == "model" and out["text"] == cousin and out["refused"] == ["repeat", "repeat"]
    model = Model(draft(COLLEGE, "", GOOD_BOARD), draft(COLLEGE, "", GOOD_BOARD))
    assert engine.explain("DNS", [PASSAGE], "high", model, previous=previous)["source"] == "passage"


def test_an_elementary_story_that_names_its_comparison_is_on_the_topic_without_the_term():
    story = "Sam asks the librarian where Zoe lives. The librarian checks the big book and tells him the house number."
    assert check.judge("elementary", story, "DNS", SOURCE, "a librarian") is None
    assert check.judge("elementary", "Bananas grow in warm places and taste sweet.", "DNS", SOURCE, "a fruit stand")[0] == "off_topic"


def test_a_text_that_reads_the_source_back_is_a_copy_even_when_long_and_a_duplicate_is_caught_before_length():
    src = ("A virtual LAN splits one switch into several separate broadcast domains. Each VLAN is identified by a number and a port joins one VLAN. "
           "A trunk port carries several VLANs between switches and tags each frame. Traffic between VLANs must pass through a router.")
    verdict = check.judge("college", src, "VLAN", src)
    assert verdict and verdict[0] == "copy"
    long_dup = " ".join(["The resolver asks one server after another until the answer comes back to the laptop."] * 5)
    again = check.judge("prek", long_dup, "DNS", SOURCE, "a helper", {"level": "elementary", "text": long_dup})
    assert again and again[0] == "repeat"                                            # not "long": a duplicate is the bigger fault


def test_when_the_model_will_not_use_the_computed_figures_the_codes_own_worked_sentence_is_added():
    src = {"id": "m:1", "chapter": "Disks", "section": "", "page": 1, "material": "N", "text": "RAID combines several disks into one volume for speed or safety."}
    vague = ("RAID combines several physical disks into one logical volume. RAID 0 stripes data across the disks for speed but has no copy, "
             "so one failed disk loses everything. Mirroring in RAID 1 keeps the same data on two disks and survives one failure, at half the space.")
    model = Model(draft(vague, "", GOOD_BOARD), draft(vague, "", GOOD_BOARD))
    out = engine.explain("RAID", [src], "college", model)
    assert out["refused"] == ["numbers", "numbers"] and out["text"].endswith("Worked example: RAID 5 with 4 disks of 2 TB: raw 8 TB, usable 6 TB, survives 1 failed disk.")
    assert out["board"]["source"] == "worked" and "usable: 6 TB" in texts(out["board"])


def test_when_the_model_gives_nothing_the_bottom_rungs_say_the_rung_above_in_fewer_words():
    elementary = "DNS is seven boxes in a line. A letter starts at the top box and goes down to the bottom one. At every box it gets a sticker."
    previous = {"level": "elementary", "text": elementary}
    out = engine.explain("DNS", [PASSAGE], "prek", None, previous=previous)
    assert out["text"] == "DNS is seven boxes in a line. A letter starts at the top box and goes down to the bottom one." and out["source"] == "model"
    one = engine.explain("DNS", [PASSAGE], "one", Model(draft(elementary, "x"), draft(elementary, "x")), previous=previous)
    assert one["text"] == "DNS is seven boxes in a line!"
    assert engine.explain("DNS", [PASSAGE], "prek", None)["templated"]                  # with no rung above to shorten: the frame


def test_a_college_rung_that_is_the_sources_own_text_is_the_source_not_a_failure():
    src = {"id": "m:2", "chapter": "VLAN", "section": "", "page": 1, "material": "N", "text": (
        "A virtual LAN splits one switch into several separate broadcast domains for devices. Each VLAN is identified by a number and a port joins one VLAN. "
        "A trunk port carries several VLANs between switches and tags each frame. Traffic between VLANs must pass through a router or a layer 3 switch.")}
    model = Model(draft(src["text"], "", GOOD_BOARD), draft(src["text"], "", GOOD_BOARD))
    out = engine.explain("VLAN", [src], "college", model)
    assert out["source"] == "model" and out["text"] == src["text"] and out["refused"] == ["copy", "copy"] and len(model.prompts) == 2


# ---- review fixes: a wrong-topic worked example, a dead model retried as if it were unreadable, a budget for the whole turn ----
@pytest.mark.parametrize("topic", ["Recursive DNS resolution", "NTFS permissions", "Cyclomatic complexity"])
def test_a_topic_that_only_shares_a_word_is_explained_without_a_wrong_worked_example(topic):
    named = f"{topic} works like this. {COLLEGE}"
    model = Model(draft(named, "", GOOD_BOARD))
    out = engine.explain(topic, [PASSAGE], "college", model)
    assert out["source"] == "model" and out["text"] == named and out["refused"] == []
    assert "Worked example" not in out["text"] and "factorial" not in out["text"] and "chmod" not in out["text"]
    assert "EXACTLY these figures" not in model.prompts[0] and len(model.prompts) == 1
    assert out["board"]["source"] != "worked"


class DeadModel:
    def __init__(self, error):
        self.error, self.calls = error, 0

    def __call__(self, prompt):
        self.calls += 1
        raise self.error


@pytest.mark.parametrize("error", [TimeoutError("slow"), RuntimeError("server error"), OSError("gone")])
def test_a_model_that_fails_is_not_asked_again_but_an_unreadable_reply_is(error):
    dead = DeadModel(error)
    out = engine.explain("router", [PASSAGE], "college", dead)
    assert dead.calls == 1 and out["source"] == "passage" and out["refused"] == []          # one wait, then the material's own words
    unreadable = Model("not json", "still not json")
    assert engine.explain("router", [PASSAGE], "college", unreadable)["source"] == "passage" and len(unreadable.prompts) == 2


class SlowModel(Model):
    """A fake model that spends fake seconds: the engine's clock is the same fake."""

    def __init__(self, now, step, *replies):
        super().__init__(*replies)
        self.now, self.step = now, step

    def __call__(self, prompt):
        self.now["t"] += self.step
        return super().__call__(prompt)


def test_the_whole_turn_has_a_time_budget_across_the_retry_and_the_board_call(monkeypatch):
    now = {"t": 0.0}
    monkeypatch.setattr(engine, "mono", lambda: now["t"])
    monkeypatch.setattr(engine, "BUDGET_S", 60)
    # each call takes 40 s: the unreadable first reply is retried (40 s used), the good draft has no board, and the board-only call is skipped (80 s)
    slow = SlowModel(now, 40, "not json", draft(COLLEGE))
    out = engine.explain("DNS", [PASSAGE], "college", slow)
    assert out["source"] == "model" and out["text"] == COLLEGE and len(slow.prompts) == 2 and out["board"]
    # each call takes 70 s: the first reply used the whole budget, so there is no retry
    now["t"] = 0.0
    slow = SlowModel(now, 70, "not json", draft(COLLEGE, "", GOOD_BOARD))
    out = engine.explain("DNS", [PASSAGE], "college", slow)
    assert len(slow.prompts) == 1 and out["source"] == "passage"
    # a refused first draft is kept as the fallback when the budget is spent
    now["t"] = 0.0
    slow = SlowModel(now, 70, draft("DNS is the Domain Name System that translates names such as example.com into IP addresses.", "", GOOD_BOARD))
    out = engine.explain("DNS", [PASSAGE], "college", slow)
    assert len(slow.prompts) == 1 and out["refused"] == ["copy"]
