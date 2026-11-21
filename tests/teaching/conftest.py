"""Small dependency set; CI cuts off parent fixtures with --confcutdir."""
import sys
from pathlib import Path
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[2]))


@pytest.fixture(autouse=True)
def private_data(tmp_path, monkeypatch):
    from core import storage, users
    monkeypatch.setattr(storage,'DATA',tmp_path/'data')
    users.set_current('owner')
