"""Companion swaps preserve the old appearance and restore complete profiles."""
from pathlib import Path
from core import companion_profile as cp
from hud.app import Api

class Window:
    def __init__(self): self.calls = []
    def evaluate_js(self, script): self.calls.append(script)

def test_builtin_appearances_are_available():
    profiles = {p['id']: p for p in cp.list_profiles()}
    assert profiles['original-robot']['rig_url'] == cp.DEFAULT_RIG_URL
    assert profiles['neon-sentinel']['rig_url'] is None
    assert cp.load_profile('neon-sentinel')['model_id'] == 'neon-sentinel'
    for pid in ('original-robot', 'neon-sentinel'):
        assert (Path(__file__).resolve().parents[1] / 'app' / profiles[pid]['model_url'].lstrip('/')).exists()

def test_apply_switches_model_and_preserves_previous(tmp_path, monkeypatch):
    monkeypatch.setattr(cp, 'PROFILES_DIR', tmp_path)
    original = cp.default_profile()
    cp.save_profile(original)
    api = object.__new__(Api)
    api._companion = Window()
    new = cp.load_profile('neon-sentinel')
    result = api.apply_companion_profile(new)
    assert result['model_id'] == 'neon-sentinel'
    assert cp.load_profile()['rig_url'] is None
    assert cp.load_profile('previous-companion')['model_url'] == original['model_url']
    api.apply_companion_profile(cp.load_profile('original-robot'))
    assert cp.load_profile()['model_url'] == original['model_url']
    assert cp.load_profile('previous-companion')['model_url'] == original['model_url']
    assert api._companion.calls == ['location.reload()', 'location.reload()']
    before = cp.load_profile()
    assert 'error' in api.apply_companion_profile({**new, 'model_url':'https://invalid/x.glb'})
    assert cp.load_profile() == before
