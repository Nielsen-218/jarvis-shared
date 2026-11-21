"""'Make the PC go to sleep' puts the PC to sleep; it is not his goodbye to Jarvis (holdout review 2026-11-21)."""
import pytest

import main


@pytest.mark.parametrize("norm", ["make the pc go to sleep", "let the computer go to sleep", "make my laptop go to sleep now",
                                  "have the monitors go to sleep"])
def test_the_machine_going_to_sleep_is_no_dismissal(norm):
    assert not main._dismissal(norm)


@pytest.mark.parametrize("norm", ["go to sleep", "you can go to sleep now", "thank you jarvis you can go to sleep for now",
                                  "ok jarvis go back to sleep"])
def test_his_goodbye_still_dismisses(norm):
    assert main._dismissal(norm)


def test_make_the_pc_go_to_sleep_is_a_power_command():
    from core import pcpower
    assert pcpower.parse("make the pc go to sleep") == ("sleep", False)
    assert pcpower.parse("go to sleep") is None
