"""Missing optional configuration is visible, actionable and does not use network."""
import pytest
from tools import homelab


@pytest.mark.parametrize("name,args", [
    ("jellyfin_overview", ()), ("jellyfin_search", ("movie",)), ("jellyfin_rescan", ()),
])
@pytest.mark.parametrize("setting", [None, "", "   "])
def test_missing_configuration_reports_setup_without_request(monkeypatch, name, args, setting):
    if setting is None:
        monkeypatch.delenv("JELLYFIN_API_KEY", raising=False)
    else:
        monkeypatch.setenv("JELLYFIN_API_KEY", setting)
    def forbidden(*a, **k):
        raise AssertionError("An unconfigured service must not be contacted")
    monkeypatch.setattr(homelab, "_jf", forbidden)
    result = getattr(homelab, name)(*args)
    assert "isn't configured" in result and "Dashboard > API Keys" in result


def test_configured_unexpected_errors_are_not_hidden(monkeypatch):
    monkeypatch.setenv("JELLYFIN_API_KEY", "offline-test-placeholder")
    def broken(*a, **k):
        raise RuntimeError("unexpected schema bug")
    monkeypatch.setattr(homelab, "_jf", broken)
    with pytest.raises(RuntimeError, match="unexpected schema bug"):
        homelab.jellyfin_overview()
