"""Recursive self-improvement for the learning core: JARVIS proposes a NEW strategy for himself, tests it against the immutable judge, and
keeps it only as a proposal for the owner.

One cycle (skill-independent; the adapter supplies `rsi_brief`, `guard_strategy`, `run_generator` and the extra action):
  1 diagnose  the TRAINING tasks that no existing strategy has ever solved (never a sealed task)
  2 propose   the local model writes code for a new strategy. The prompt carries observations only; `unseeded_check` refuses to run a prompt that
              contains any line of any task's correct solution, so a seeded answer cannot masquerade as an invention
  3 test      the code runs in isolation (the same sandbox as everything else); every attempt, good or bad, is logged with prompt/code hashes
  4 verify    it must newly solve at least one unsolved training task and must not crash; then it is evaluated on the sealed held-out tasks
  5 remember  kept as a variant with status pending_review (owner approval promotes it; nothing is ever promoted automatically)"""
import hashlib
import time

from core import learn, learn_llm


def _norm(line):
    return " ".join(line.split())


def unseeded_check(prompt, engine, shown=()):
    """-> lines that would give the answer away: lines of a task's CORRECT solution that its buggy version lacks (the repair itself),
    found in the prompt outside the buggy listings the brief is allowed to show. Must be empty before any proposal is requested."""
    shown_lines = {_norm(l) for src in shown for l in src.splitlines()}
    prompt_lines = {_norm(l) for l in prompt.splitlines()}
    leaked = set()
    for split in ("train", "holdout"):
        for t in engine.adapter.tasks(split):
            fix = {_norm(l) for l in t.get("correct", "").splitlines()} - {_norm(l) for l in t.get("buggy", "").splitlines()}
            leaked |= {l for l in fix if len(l) >= 6 and l in prompt_lines and l not in shown_lines}
    return sorted(leaked)


class Cycle:
    MODELS = ("qwen3.5:9b",)  # the one brain since 2026-11-09 (the old study models were deleted)

    def __init__(self, engine, ask=None):
        self.engine, self.store, self.adapter = engine, engine.store, engine.adapter
        self.ask = ask or (lambda prompt, n: learn_llm.code_block(learn_llm.ask_text(prompt, store=self.store, model_name=self.MODELS[min(n, len(self.MODELS) - 1)], num_predict=2200)))
        self.adapter.attach_store(self.store)

    def hard_cases(self, k=6):
        """Training tasks that NO stored strategy (other than noop) ever fully solved, under the current judge."""
        rows = self.engine.training_episodes()
        solved = {e["task"] for e in rows if e["outcome"]["full"]}
        seen = {e["task"] for e in rows}
        hard = [t for t in self.adapter.tasks("train") if t["id"] in seen and t["id"] not in solved]
        for t in hard:
            self.engine.assert_trainable(t["id"])
        return hard[:k]

    def run(self, attempts=5):
        hard = self.hard_cases()
        if not hard:
            self.store.event("rsi_skipped", why="every training task is already solved by an existing strategy")
            return {"state": "nothing_to_improve"}
        prompt = self.adapter.rsi_brief(hard)
        leaked = unseeded_check(prompt, self.engine, shown=[t["buggy"] for t in hard])
        if leaked:
            raise learn.SealedError(f"the proposal prompt contains solution lines: {leaked[:2]}")
        psha = hashlib.sha256(prompt.encode()).hexdigest()[:12]
        log = []
        for n in range(attempts):
            try:
                code = self.ask(prompt if n == 0 else prompt + "\nYour previous attempt: " + log[-1]["why"] + "\nWrite a different, working propose(source).", n)
            except RuntimeError as e:
                log.append({"n": n, "ok": False, "why": f"model unavailable: {e}"})
                continue
            csha = hashlib.sha256(code.encode()).hexdigest()[:12]
            why = self.adapter.guard_strategy(code)
            if why:
                log.append({"n": n, "ok": False, "why": "guard: " + why, "code_sha": csha})
                continue
            wins, tested = [], []
            for t in hard:
                got = self.adapter.run_generator(code, t)
                tested.append({"task": t["id"], "candidates": len(got)})
                src = self.adapter._pick(t, got, None)
                v = self.adapter.judge_source(t, src)
                if v["full"]:
                    wins.append(t["id"])
            entry = {"n": n, "ok": bool(wins), "why": f"solved {len(wins)}/{len(hard)} previously unsolved training tasks", "code_sha": csha, "tested": tested, "wins": wins}
            log.append(entry)
            self.store.event("rsi_attempt", prompt_sha=psha, model=self.MODELS[min(n, len(self.MODELS) - 1)], **entry)
            if wins:
                row = self.store.add_variant({"skill": self.adapter.skill, "kind": "strategy_code"}, overlay_source=code,
                                             evidence={"hard_cases": [t["id"] for t in hard], "newly_solved": wins, "prompt_sha": psha, "unseeded": True,
                                                       "attempts": len(log), "judge": self.engine.judge_hash()}, name="proposed repair strategy")
                self.adapter.attach_store(self.store)
                return {"state": "proposed", "variant": row["id"], "status": row["status"], "newly_solved": wins, "attempts": log, "prompt_sha": psha}
        self.store.event("rsi_failed", prompt_sha=psha, attempts=log)
        return {"state": "no_improvement", "attempts": log, "prompt_sha": psha}

    def evaluate_on_holdout(self, variant_id):
        """Exam episodes of the proposed strategy on the sealed tasks (reported next to the existing strategies; never used to select it)."""
        action = f"proposed:{variant_id}"
        out = []
        for t in self.adapter.tasks("holdout"):
            out.append(self.engine.run(t["id"], action, purpose="exam")["outcome"]["full"])
        return {"solved": sum(out), "n": len(out)}
