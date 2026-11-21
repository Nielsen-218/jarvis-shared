"""Round 8 (2026-11-15): "from $X to $Y" is the OLD-value pattern unless the sentence carries an explicit range cue; plus the temperature caveat's
words, two-digit rounded figures, more family words, and "$5 B" / "$1.2m"."""
from datetime import date

import pytest

from core import intelligence, webanswer, webintent, websearch

TODAY = date(2026, 11, 15)
TODAY_ISO = TODAY.isoformat()


@pytest.fixture(autouse=True)
def _pinned_day(monkeypatch):
    monkeypatch.setattr(webanswer, "today", lambda: TODAY)


def pack(question, *texts):
    rows = [{"url": f"https://s{i}.com/x", "title": "t", "snippet": text, "date": TODAY_ISO, "site": f"s{i}.com"} for i, text in enumerate(texts)]
    return {"question": question, "query": question, "provider": "exa", "results": rows, "failures": [], "searched_at": "2026-11-15T12:00:00+00:00"}


def status(question, a, b):
    return webanswer.assess(pack(question, a, b), question, TODAY)


Q = "how much is the iphone 17"


# --- 1. from-to is the old price, unless the sentence says RANGE ---------------------------------------------------------------------------------------
@pytest.mark.parametrize("a, old, new", [
    ("Apple reduced the price from $999 to $799.", "$999", "$799"),
    ("Apple cut the price from $999 to $799.", "$999", "$799"),
    ("The iPhone 17 was slashed from $999 to $799.", "$999", "$799"),
    ("The price drops from $999 to $799.", "$999", "$799"),
    ("The price was lowered from $999 to $799.", "$999", "$799"),
    ("Price was discounted from $999 to $799.", "$999", "$799"),
    ("Price has been trimmed from $999 to $799.", "$999", "$799"),
    ("Price sank from $999 to $799.", "$999", "$799"),
    ("The price went from $999 to $799.", "$999", "$799"),
    ("Apple is changing the price from $799 to $899.", "$799", "$899"),
    ("Apple raised the price from $799 to $899.", "$799", "$899"),
    ("Apple hiked the price from $799 to $899.", "$799", "$899"),
    ("Apple bumped the price from $799 to $899.", "$799", "$899"),
    ("Apple upped the price from $799 to $899.", "$799", "$899"),
    ("The iPhone 17 price goes from $799 to $899.", "$799", "$899"),
    ("The iPhone 17 price is going from $799 to $899.", "$799", "$899"),
    ("The iPhone 17 price has risen sharply from $799 to $899.", "$799", "$899"),
    ("Down from $999 to $799, the iPhone 17 is cheaper.", "$999", "$799"),
])
def test_from_x_to_y_is_the_old_price_by_default(a, old, new):
    figs = [shown for _, shown in webanswer.figures("money", a)]
    assert figs[0] == new and old not in figs
    # ...so a source that still says the OLD price is a conflict, never an agreement on it
    r = status(Q, a, f"The iPhone 17 costs {old}.")
    assert r["status"] != "agree", r
    # ...and one that says the new price agrees
    assert status(Q, a, f"The iPhone 17 costs {new}.")["status"] == "agree"


@pytest.mark.parametrize("a, b", [
    ("The iPhone 17 costs from $799 to $1,199.", "The iPhone 17 starts at $799."),
    ("Plans start at $10 to $15 per month.", "Plans start at $10 per month."),
    ("Tickets from $30 to $40.", "Tickets are $30."),
    ("It costs $5 to $10 to ship.", "It costs $5 to ship."),
    ("Shipping is free for orders $50 to $100.", "Shipping costs $50."),
    ("Expect to pay $799 up to $1,199.", "The iPhone costs $799."),
    ("Tickets range from $30 to $40.", "Tickets are $40."),
    ("Tickets go for between $30 and $40.", "Tickets are $30."),
    ("The iPhone 17 is from $799 to $1,199 depending on storage.", "The iPhone 17 is $1,199 for the top model."),
    ("Prices run from $799 up to $1,199.", "The iPhone 17 starts at $799."),
    ("Prices from $799 to $1,199 for the iPhone 17.", "The iPhone 17 starts at $799."),
    ("Plans are anywhere from $10 to $15.", "Plans start at $10."),
    ("It is priced from $799 to $1,199.", "The iPhone 17 costs $799."),
    ("Pricing from $799 to $1,199 for the iPhone 17.", "The iPhone 17 costs $1,199."),
    ("The iPhone 17 varies from $799 to $1,199.", "The iPhone 17 costs $799."),
    ("The plan is from $10 to $15 a month.", "The plan costs $10."),
    ("Tickets cost from $30 to $40 each.", "Tickets are $40."),
    ("The iPhone 17 starting from $799 to $1,199.", "The iPhone 17 costs $799."),
])
def test_an_explicit_range_cue_keeps_both_ends(a, b):
    assert status(Q, a, b)["status"] == "agree"


@pytest.mark.parametrize("a", ["Price: $50->$60.", "Bitcoin: $70,000 → $65,000"])
def test_an_arrow_is_always_old_to_new(a):
    assert len(webanswer.figures("money", a)) == 1 and webanswer.figures("money", a)[0][1] in ("$60", "$65,000")


# --- (a) the honest temperature caveat ---------------------------------------------------------------------------------------------------------------------
PREFIXES = ["", "Sorry sir, ", "Honestly, ", "Sorry, sir. ", "I'm afraid ", "Unfortunately, ", "To be honest, sir, "]


def test_the_honest_temperature_caveat_passes_under_every_prefix():
    q = "what's the weather in Springfield November 15, 2026"
    p = pack(q, "Springfield, IL weather today: high of 62°F with clouds. Low of 45°F.", "Springfield weather: 45°F now, clearing up.")
    verdict = webanswer.verdict(p, q, TODAY)
    assert "can't compare them like for like" in verdict
    evidence = websearch.evidence_text(p)
    sentences = [verdict] + [s for s in verdict.split(". ") if s]
    for prefix in PREFIXES:
        for sentence in sentences:
            reply = prefix + (sentence[0].lower() + sentence[1:] if prefix and not prefix.endswith(". ") else sentence)
            assert intelligence._web_content_grounded(reply, evidence), reply
    own = "I can't compare them like for like (they report different things, such as a reading now and the day's high), so treat s0.com's 62°F as unconfirmed."
    assert intelligence._web_content_grounded(own, evidence)


# --- (b) two-digit rounded figures -------------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("a, b", [("1.5 million", "1,547,382"), ("1.2 million", "1,249,000"), ("330 million", "331,449,281"), ("8,300,000", "8,336,817")])
def test_a_two_digit_rounded_figure_agrees_with_the_number_it_rounds(a, b):
    assert status("how many people attended", f"About {a} people attended.", f"{b} people attended.")["status"] == "agree"


@pytest.mark.parametrize("a, b", [("1,000", "1,499"), ("1.5 million", "1,574,999"), ("1.2 million", "1,251,000"), ("100 million", "140,000,000"),
                                  ("1,000,000", "1,049,999"), ("20,000", "24,000")])
def test_it_still_conflicts_when_it_does_not_round_to_it_or_has_one_digit(a, b):
    assert status("how many people attended", f"About {a} people attended.", f"{b} people attended.")["status"] == "conflict"


# --- (c) more family words ------------------------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("line", ["search for the grandparents' house", "search for a gift for the aunt", "search for the uncle's address", "search for the cousin's wedding",
                                  "search for the niece and nephew school", "search for the in-laws visit", "search for the grandma recipes", "search for the grandpa's doctor"])
def test_more_family_words_are_private(line):
    assert webintent.calls_for("search", line) == []


@pytest.mark.parametrize("line", ["search for the family dollar store hours", "search for the parents guide for Frozen", "search for the family guy cast",
                                  "search for the family feud questions", "search for the family tree template", "search for family friendly restaurants"])
def test_public_phrases_with_a_family_word_are_searched(line):
    assert len(webintent.calls_for("search", line)) == 1


# --- (d) "$5 B", "$1.2m" -------------------------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("text, value", [("It raised $5 B.", 5e9), ("It raised $1.2m.", 1.2e6), ("It raised $5B.", 5e9), ("It raised $2.5M.", 2.5e6), ("It raised $300k.", 3e5),
                                         ("It raised $4 billion.", 4e9)])
def test_a_letter_after_a_dollar_figure_is_millions_or_billions(text, value):
    fig = webanswer.figures("money", text)[0]
    assert fig[0][0] == pytest.approx(value)
