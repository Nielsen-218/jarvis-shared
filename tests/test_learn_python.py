"""The Python-debugging skill on the learning core: judged for real inside the Windows AppContainer sandbox."""
import os
import sys

import pytest

from core import learn, learn_pytasks as P
from core.dojo_store import Store
from core.learn_python import PythonAdapter

pytestmark = [pytest.mark.skipif(sys.platform != "win32", reason="the sandbox is a Windows AppContainer"), pytest.mark.serial]   # AppContainer profile setup isn't safe across parallel workers


@pytest.fixture(scope="module")
def lab(tmp_path_factory):
    d = tmp_path_factory.mktemp("lab")
    old = os.environ.get("JARVIS_LEARN_LAB")
    os.environ["JARVIS_LEARN_LAB"] = str(d / "lab")
    yield d
    if old is None:
        os.environ.pop("JARVIS_LEARN_LAB")
    else:
        os.environ["JARVIS_LEARN_LAB"] = old


@pytest.fixture(scope="module")
def adapter(lab):
    return PythonAdapter()


def _task(adapter, func_prefix="py:sum_to:const"):
    return next(t for t in adapter.tasks("train") if t["id"].startswith(func_prefix))


def test_real_sandbox_judges_the_bug_and_the_fix(adapter):
    t = _task(adapter)
    bad = P.judge(t, t["buggy"])
    good = P.judge(t, t["correct"])
    assert not bad["full"] and bad["fail_count"] > 0
    assert good["full"] and good["fail_count"] == 0 and good["total"] == len(t["inputs"])


def test_sandbox_contains_hostile_and_runaway_candidates(adapter, tmp_path):
    t = _task(adapter)
    marker = tmp_path / "escaped.txt"
    hostile = f"def sum_to(n):\n    open({str(marker)!r}, 'w').write('x')\n    return 0\n"
    P.judge(t, hostile)
    assert not marker.exists()                                           # the AppContainer denied the write
    spin = "def sum_to(n):\n    while True:\n        pass\n"
    v = P.judge(t, spin)
    assert not v["full"] and v["total"] == len(t["inputs"])              # step limit, not a hang


def test_splits_are_disjoint_and_use_different_functions(adapter):
    tr, ho = adapter.tasks("train"), adapter.tasks("holdout")
    assert len(tr) >= 40 and len(ho) >= 40
    assert {t["func"] for t in tr}.isdisjoint({t["func"] for t in ho})
    assert adapter.validate()


def test_every_task_has_a_visible_failure_and_a_hidden_judge(adapter):
    for t in adapter.tasks("train")[:6] + adapter.tasks("holdout")[:6]:
        assert t["visible_fail"] >= 1 and not P.judge(t, t["buggy"])["full"]


def test_search_repairs_a_bug_without_a_model_and_records_a_lesson(adapter, tmp_path):
    e = learn.Engine(adapter, store=Store("python", root=tmp_path / "s"), gaming=lambda: False)
    t = _task(adapter)
    row = e.run(t["id"], "search")
    assert row["outcome"]["full"] and row["action_kind"] == "search" and not row["assisted"]
    assert any(l["kind"] == "python_fix" for l in e.store.lessons())    # a verified fix became a lesson
    e.seal_holdout()
    ho = adapter.tasks("holdout")[0]
    e.run(ho["id"], "search", purpose="exam")
    assert not any(l.get("task") == ho["id"] for l in e.store.lessons())   # an exam never teaches


def test_noop_is_the_floor(adapter, tmp_path):
    e = learn.Engine(adapter, store=Store("python", root=tmp_path / "s"), gaming=lambda: False)
    row = e.run(_task(adapter)["id"], "noop")
    assert not row["outcome"]["full"] and row["outcome"]["fail_count"] > 0
