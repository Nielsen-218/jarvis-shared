"""Split a restaurant bill plus tip between people to the cent, in exact integer cents (no floating point anywhere).

Rounding policy, stated once: the tip is applied to the pre-tip total; the total with tip is rounded HALF-UP to the nearest cent
(10.5 cents -> 11); the result is divided into whole cents; any leftover cents go one each to the first people, so shares differ by at most one cent."""
from decimal import Decimal, ROUND_HALF_UP


class SkillError(Exception):
    """A NAMED failure: `code` must be one of the manifest's declared errors."""

    def __init__(self, code, message=""):
        super().__init__(message or code)
        self.code = code


def run(total, tip_percent, people):
    bill_cents = int(Decimal(total) * 100)                     # `total` has at most 2 decimal places (the host enforces it), so this is exact
    if bill_cents <= 0:
        raise SkillError("zero_total", "the bill must be above zero")
    cents = int((Decimal(bill_cents) * (100 + Decimal(tip_percent)) / 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    if people > cents:
        raise SkillError("not_enough_money", "less than one cent each")
    base, extra = divmod(cents, people)
    shares = [base + 1] * extra + [base] * (people - extra)
    hi, lo = max(shares), min(shares)
    money = lambda c: "$%d.%02d" % (c // 100, c % 100)
    who = lambda n: "%d %s" % (n, "person pays" if n == 1 else "people pay")
    text = ("each person pays " + money(hi)) if hi == lo else ("%s %s and %s %s" % (who(extra), money(hi), who(people - extra), money(lo)))
    return {"total_cents": cents, "shares_cents": shares, "people": people, "people_text": "%d %s" % (people, "person" if people == 1 else "people"),
            "tip_percent": format(Decimal(tip_percent).normalize(), "f"), "total_with_tip": "%d.%02d" % (cents // 100, cents % 100), "share_text": text}
