"""An ordinary close / minimize of an app the owner named the short way runs without "Should I...?" (core.authz.names_app).
Destructive and unnamed targets still ask. Pure policy tests: nothing here touches a window."""
import pytest

from core import authz, pcsemantic


@pytest.fixture(autouse=True)
def catalogue(monkeypatch):
    monkeypatch.setattr(pcsemantic, "_catalogue", lambda: [
        "microsoft edge", "calculator", "epic games launcher", "visual studio code", "google chrome", "paint", "notepad",
        "file explorer", "microsoft teams", "blender", "grand theft auto v", "onenote"])


def verdict(tool, app, said, **kw):
    kw.setdefault("owner", True)
    return authz.decide([{"tool": tool, "arguments": {"app": app}}], said, autonomous=kw.pop("autonomous", True), **kw)


@pytest.mark.parametrize("tool,app,said", [
    ("close_app", "microsoft edge", "close edge"),
    ("close_app", "calculator", "close the calc"),
    ("close_app", "epic games launcher", "close epic"),
    ("close_app", "visual studio code", "close vs code"),
    ("close_app", "calculator", "Close calculator."),
    ("minimize_app", "microsoft edge", "minimize edge"),
    ("minimize_app", "epic games launcher", "hide epic for now"),
    ("close_app", "google chrome", "can you shut chrome down please"),
])
def test_a_named_app_is_closed_or_minimized_without_asking(tool, app, said):
    v = verdict(tool, app, said)
    assert not v.ask, v.reasons


@pytest.mark.parametrize("tool,app,said", [
    ("close_app", "paint", "close chat tvt"),                    # a model's guess at a misheard name: still asks
    ("close_app", "file explorer", "close the file"),            # a general word is not the app
    ("close_app", "microsoft edge", "close the browser"),        # not named: asked (which browser?)
    ("close_app", "visual studio code", "close code"),
    ("close_app", "notepad", "close the note"),
    ("close_app", "microsoft teams", "close microsoft"),         # the vendor word alone names nothing
    ("minimize_app", "blender", "minimize that thing"),
    ("close_app", "grand theft auto v", "close the"),
])
def test_an_app_he_did_not_name_still_asks(tool, app, said):
    assert verdict(tool, app, said).ask


def test_a_close_still_needs_a_close_word_even_when_the_app_is_named():
    v = verdict("close_app", "microsoft edge", "edge is being slow today")
    assert v.ask and "did not ask me to close" in " ".join(v.reasons)


def test_ask_first_mode_and_other_people_still_get_the_question():
    assert verdict("close_app", "microsoft edge", "close edge", autonomous=False).ask


def test_the_short_name_rule_never_authorizes_anything_that_cannot_be_undone():
    """names_app only widens in_scope for close / minimize / focus. An uninstall aimed at the short name still asks."""
    v = authz.decide([{"tool": "uninstall_program", "arguments": {"name": "epic games launcher"}}], "close epic", autonomous=True, owner=True)
    assert v.ask
    assert not authz.in_scope({"tool": "type_text", "arguments": {"app": "microsoft edge", "text": "x"}}, "type x in the browser") \
        or "type_text" not in authz._SCOPED
    assert authz.named({"tool": "close_app", "arguments": {"app": "paint"}}, "close chat tvt") is False


@pytest.mark.parametrize("app,said,expected", [
    ("microsoft edge", "close edge", True), ("calculator", "the calc", True), ("epic games launcher", "epic", True),
    ("paint", "chat tvt", False), ("onenote", "close one", False), ("notepad", "", False), ("", "close edge", False),
])
def test_names_app(app, said, expected):
    assert authz.names_app(app, said) is expected
