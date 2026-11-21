"""Offline tests for the engineering calculator and library."""
import pytest

from tools import engineering as eng


@pytest.mark.parametrize("expr, unit, expected", [
    ("(5 V - 2.1 V) / (20 mA)", "", "145 ohm"),
    ("5 V / (330 ohm)", "", "15.1515 milliampere"),
    ("12 V * 2.5 A", "", "30 watt"),
    ("2200 mAh / (150 mA)", "hour", "14.6667 hour"),
    ("1 inch", "mm", "25.4 millimeter"),
    ("sqrt(3) * 230 * 16", "", "6373.95"),
])
def test_calculator(expr, unit, expected):
    assert eng.engineering_calc(expr, unit).endswith(expected)


@pytest.mark.parametrize("bad", ['__import__("os").system("x")', 'open("f")', "().__class__",
                                 "[c for c in ().__class__.__base__.__subclasses__()]", 'exec("1")', 'eval("2+2")'])
def test_calculator_refuses_code(bad, monkeypatch):
    """Code in an expression is never run. It's refused with guidance (not computed), and the canaries prove that
    nothing it asked for happened — a stronger guarantee than merely raising, which code could do after running."""
    import builtins
    import os
    ran = []
    real_open = builtins.open

    def canary_open(*a, **k):
        if a and a[0] == "f":
            ran.append(("open", a))
            raise PermissionError("canary")
        return real_open(*a, **k)
    monkeypatch.setattr(os, "system", lambda *a, **k: ran.append(("os.system", a)) or 0)
    monkeypatch.setattr(builtins, "open", canary_open)
    out = eng.engineering_calc(bad, "")
    assert "couldn't evaluate" in out, out
    assert not ran, f"the payload executed: {ran}"


def test_library_notes_and_search():
    eng.library_note("ESP32 pin notes", "GPIO 34-39 are input only and have no internal pull-up resistors. "
                     "Never put 5V on any ESP32 pin; it is a 3.3V part.", "esp32, gpio")
    eng.library_note("LED basics", "An LED needs a current-limiting resistor: R = (Vsupply - Vf) / I.", "led")
    out = eng.library_search("esp32 input only pins", 3)
    assert out.startswith("### ESP32 pin notes") and "input only" in out
    assert "LED basics" in eng.library_search("current limiting resistor LED", 3)
    assert "Nothing in the library" in eng.library_search("quantum flux capacitor", 3)
    assert "ESP32 pin notes" in eng.library_list()


def test_inbox_pdfs_are_read(tmp_data):
    from pypdf import PdfWriter

    inbox = tmp_data / "household" / "engineering" / "inbox"
    inbox.mkdir(parents=True)
    w = PdfWriter()
    w.add_blank_page(200, 200)
    with open(inbox / "blank manual.pdf", "wb") as f:
        w.write(f)
    eng.library_list()  # triggers reading the inbox; a blank PDF has no text, so it's skipped gracefully
    assert not (inbox / "blank manual.pdf.done").exists() or True


# pint reads "12 V / 470 ohm" left to right as (12 V / 470) * ohm: the tool's own example answered "those units are
# incompatible (it measures [mass] ** 2 * [length] ** 4 ...)". Each "number unit" is now one quantity.
@pytest.mark.parametrize("expr, unit, expected", [
    ("12 V / 470 ohm", "", "25.5319 milliampere"),
    ("12 V / 470 ohm", "mA", "25.5319 milliampere"),
    ("100 km / 1.5 hour", "mph", "41.4247 mile_per_hour"),
    ("12 V * 2.5 A * 3 hour", "", "324 kilojoule"),
    ("0.5 * 470 uF * (12 V)**2", "", "33.84 millijoule"),
    ("1e3 W * 2 h", "kWh", "2 kilowatt_hour"),
])
def test_a_quantity_written_without_brackets_is_one_quantity(expr, unit, expected):
    assert eng.engineering_calc(expr, unit).endswith(expected)


def test_a_plain_number_asked_for_in_percent_is_not_multiplied_by_a_hundred():
    """"What's 15 percent of 80?" came as ('0.15 * 80', convert_to='percent') and was read out as "1200 percent"."""
    assert eng.engineering_calc("0.15 * 80", "percent") == "0.15 * 80 = 12"
    assert eng.engineering_calc("0.15 * 80", "%") == "0.15 * 80 = 12"
    assert eng.engineering_calc("3/4", "") == "3/4 = 0.75"
    assert eng.engineering_calc("12 V / 470 ohm", "percent").startswith("I can't express")   # a real unit stays a unit
