"""Making room for FLUX: ordinary desktop apps holding some VRAM must not block a render."""
import pytest

from tools import studio


@pytest.fixture(autouse=True)
def quiet(monkeypatch):
    monkeypatch.setattr(studio, "_ollama", lambda *a, **k: None)
    monkeypatch.setattr(studio, "_loaded_ollama_models", lambda: [])
    monkeypatch.delenv("JARVIS_FLUX_LOWVRAM", raising=False)


def test_enough_room_uses_the_normal_fast_mode(monkeypatch):
    monkeypatch.setattr(studio, "_wait_vram_free", lambda *a, **k: True)
    studio._free_gpu_for_flux()
    import os
    assert os.getenv("JARVIS_FLUX_LOWVRAM") is None


def test_apps_holding_a_few_gb_switch_to_low_vram_instead_of_failing(monkeypatch):
    monkeypatch.setattr(studio, "_wait_vram_free", lambda *a, **k: False)
    monkeypatch.setattr(studio, "_vram_free_mib", lambda: 6200)   # browser + Electron apps + overlay
    studio._free_gpu_for_flux()
    import os
    assert os.environ["JARVIS_FLUX_LOWVRAM"] == "1"
    monkeypatch.delenv("JARVIS_FLUX_LOWVRAM")


def test_a_gpu_genuinely_taken_by_a_game_is_refused_clearly(monkeypatch):
    monkeypatch.setattr(studio, "_wait_vram_free", lambda *a, **k: False)
    monkeypatch.setattr(studio, "_vram_free_mib", lambda: 1200)
    with pytest.raises(RuntimeError, match="GPU_BUSY"):
        studio._free_gpu_for_flux()


def test_unmeasurable_vram_still_tries(monkeypatch):
    monkeypatch.setattr(studio, "_vram_free_mib", lambda: None)
    assert studio._wait_vram_free(timeout=1) is True
