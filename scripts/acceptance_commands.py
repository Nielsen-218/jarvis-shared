"""Acceptance for taught commands through the REAL voice pipeline: Kokoro says the sentence, Whisper hears it, the
wake word is stripped exactly as main.py does, Brain.ask() answers on the real local models.

    .venv\\Scripts\\python.exe scripts\\acceptance_commands.py            # both phases, two processes, one data folder
    .venv\\Scripts\\python.exe scripts\\acceptance_commands.py --seed 7
    .venv\\Scripts\\python.exe scripts\\acceptance_commands.py --phase similar --data <folder>   # similar-sounding triggers

Phase "teach" runs in one process (teach a generated trigger, use it, normal chat); phase "restart" runs in a SECOND
process on the same temporary data folder, so nothing survives but the files: that is the restart the owner saw fail.
Trigger phrases and responses are drawn at random (print the seed to repeat a run). Exit code 1 when a check failed.
Live memories, reminders and the vault are never touched (a temporary data folder, like scripts/convo_local.py)."""
import argparse
import json
import os
import random
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

WORDS_A = ["amber", "velvet", "copper", "silver", "golden", "purple", "quiet", "hollow", "paper", "crimson", "marble", "frozen"]
WORDS_B = ["lantern", "compass", "meadow", "anchor", "falcon", "ledger", "window", "garden", "pencil", "button", "candle", "mirror"]
REPLIES = ["noted", "copy that", "all systems go", "standing by", "the kettle is on", "acknowledged"]
_ERRORS = ("something went wrong", "try again in a moment", "local intelligence is unavailable", "can't reach my servers")


def _norm(text: str) -> str:
    import re
    return re.sub(r"[^a-z0-9 ]", "", text.lower().replace("'", "")).strip()


# -- one phase: a process with the whole voice stack ----------------------------------------------------------------
class Session:
    def __init__(self, data: Path, seed: int):
        os.environ.setdefault("JARVIS_INTELLIGENCE", "local-first")
        os.environ["JARVIS_LOCAL_ONLY"] = "1"
        os.environ["JARVIS_STT"] = "whisper"                       # the PC's path: Whisper on the GPU, no Parakeet
        for v in ("JARVIS_PC_URL", "JARVIS_SERVER_URL", "JARVIS_SERVER_TOKEN", "JARVIS_SERVER_SSH", "JARVIS_ROLE"):
            os.environ.pop(v, None)
        from core import storage
        storage.DATA = data
        os.environ["JARVIS_VAULT"] = str(data / "vault")
        os.environ["JARVIS_COMPANION_DATA_DIR"] = str(data / "companion")
        # PC plans awaiting review live outside storage.DATA: never the real ledger (a pending test plan was approved by
        # the next sentence, 2026-11-10)
        os.environ["JARVIS_PC_STATE_DIR"] = str(data / "pc-control")
        os.environ["JARVIS_PC_ROOT"] = str(data / "jarvis_pc")
        import webbrowser
        webbrowser.open = lambda url, *a, **k: True
        import main as voice_loop
        from audio import stt, tts
        from brain.llm import Brain
        from core.config import Config, load_personality
        from core import users
        from tools import load_all, memory, registry

        load_all()
        users.set_current("owner")
        self.cfg = Config()
        self.voice_loop, self.tts, self.memory = voice_loop, tts, memory
        self._Brain, self._personality = Brain, load_personality(self.cfg, "owner")
        self.voice = self.cfg.voice if self.cfg.voice.startswith("kokoro:") else "kokoro:bm_lewis"
        t = time.time()
        self.stt = stt.Transcriber()
        print(f"[stt] ready in {time.time() - t:.1f}s (gpu={self.stt.on_gpu})")
        self.calls: list[tuple[str, dict]] = []
        run = registry.run
        registry.run = lambda name, args: (self.calls.append((name, args)), run(name, args))[1]
        self.results: list[tuple[str, bool]] = []
        self.transcript: list[dict] = []
        self.rng = random.Random(seed)
        self.brain = self.new_brain()

    def new_brain(self):
        b = self._Brain(self.cfg.model, self.cfg.effort, self._personality, uid="owner")
        b.context = self.memory.context_block
        return b

    def phrase(self) -> str:
        return f"{self.rng.choice(WORDS_A)} {self.rng.choice(WORDS_B)}"

    def hear(self, sentence: str) -> str:
        """Speak it with Jarvis's own voice, transcribe it with the real STT, strip the wake word as the loop does."""
        import numpy as np
        from scipy.signal import resample_poly
        pcm = self.tts.kokoro_say(sentence, self.voice, self.cfg.voice_rate)    # int16 @ 24 kHz
        audio = resample_poly(pcm.astype(np.float32), 2, 3)                      # -> 16 kHz, what Whisper expects
        heard = self.stt.transcribe(np.clip(audio, -32768, 32767).astype(np.int16))
        return self.voice_loop.fix_misheard(self.voice_loop.WAKE_PREFIX.sub("", heard).strip()), heard

    def say(self, sentence: str, brain=None) -> str:
        brain = brain or self.brain
        self.calls.clear()
        t = time.time()
        command, heard = self.hear(sentence)
        stt_s = time.time() - t
        said: list[str] = []
        t = time.time()
        brain.ask(command, said.append)
        reply = " ".join(said)
        print(f"\nSAID : {sentence}\nHEARD: {heard}\n  tools: {[n for n, _ in self.calls]}  stt {stt_s:.1f}s  brain {time.time() - t:.1f}s"
              f"\n  JARVIS: {reply[:400]}")
        self.transcript.append({"said": sentence, "heard": heard, "command": command, "tools": [n for n, _ in self.calls],
                                "reply": reply, "stt_s": round(stt_s, 1), "brain_s": round(time.time() - t, 1)})
        return reply

    def check(self, name: str, ok: bool) -> None:
        self.results.append((name, ok))
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")

    def clean(self, reply: str) -> bool:
        low = reply.lower()
        return bool(reply.strip()) and not any(x in low for x in ("{", "tool_call", "<think>", "answer_directly", *_ERRORS))

    def taught(self, trigger: str) -> dict | None:
        from core import commands
        return commands.find(trigger)

    def finish(self, phase: str) -> int:
        from core import storage
        out = storage.DATA / f"acceptance-{phase}.json"
        out.write_text(json.dumps({"results": self.results, "transcript": self.transcript}, indent=1), encoding="utf-8")
        failed = [n for n, ok in self.results if not ok]
        print(f"\n{phase}: {len(self.results) - len(failed)}/{len(self.results)} checks passed -> {out}")
        return 1 if failed else 0


def phase_teach(s: Session, state: Path) -> int:
    t1, r1 = s.phrase(), s.rng.choice(REPLIES)
    print(f"\n===== teach (trigger '{t1}' -> '{r1}') =====")
    reply = s.say(f"Hey Jarvis, when I say {t1}, reply only with {r1}.")
    rule = s.taught(t1)
    s.check("1a. the teaching sentence stores a rule (heard through the real STT)", rule is not None and not s.calls)
    if rule is None:                                             # what was stored under a misheard trigger, if anything
        from core import commands
        print(f"  rules on file: {[(r['trigger'], r['response']) for r in commands.rules()]}")
        return s.finish("teach")
    s.check("1b. ...with the response the owner said", _norm(rule["response"]) == _norm(r1))
    s.check("1c. ...and confirms it in words", "learned" in reply.lower() and rule["trigger"].lower() in reply.lower())
    reply = s.say(f"Hey Jarvis, {t1}.")
    s.check("1d. the trigger gets exactly the taught response, no model, no tool", _norm(reply) == _norm(rule["response"]) and not s.calls)
    reply = s.say(f"Hey Jarvis, I saw a {t1} in a shop yesterday, funny name for a thing.")
    s.check("1e. a sentence that merely contains the trigger is not hijacked", _norm(reply) != _norm(rule["response"]) and s.clean(reply))
    reply = s.say("Hey Jarvis, how are you doing today?")
    s.check("1f. small talk still gets a spoken answer with no tools", s.clean(reply) and not s.calls)
    state.write_text(json.dumps({"t1": rule["trigger"], "r1": rule["response"]}), encoding="utf-8")
    return s.finish("teach")


def phase_restart(s: Session, state: Path) -> int:
    saved = json.loads(state.read_text(encoding="utf-8"))
    t1, r1 = saved["t1"], saved["r1"]
    print(f"\n===== restart (new process; '{t1}' -> '{r1}' must come back from the files) =====")
    reply = s.say(f"Hey Jarvis, {t1}.")
    s.check("2. after a restart the trigger still gets the taught response", _norm(reply) == _norm(r1) and not s.calls)
    fresh = s.new_brain()
    reply = s.say(f"Hey Jarvis, {t1}.", brain=fresh)
    s.check("3. in a fresh conversation too", _norm(reply) == _norm(r1) and not s.calls)
    t2 = s.phrase()
    while _norm(t2) == _norm(t1):
        t2 = s.phrase()
    r2 = s.rng.choice([r for r in REPLIES if _norm(r) != _norm(r1)])
    s.say(f"Hey Jarvis, if I say {t2}, say {r2}.", brain=fresh)
    rule2 = s.taught(t2)
    s.check("4a. a second, unrelated trigger is learned", rule2 is not None and _norm(rule2["response"]) == _norm(r2))
    if rule2 is None:
        return s.finish("restart")
    reply = s.say(f"Hey Jarvis, {t2}.", brain=fresh)
    s.check("4b. the second trigger works", _norm(reply) == _norm(rule2["response"]) and not s.calls)
    reply = s.say(f"Hey Jarvis, {t1}.", brain=fresh)
    s.check("4c. ...and the first still does", _norm(reply) == _norm(r1) and not s.calls)
    r1b = s.rng.choice([r for r in REPLIES if _norm(r) not in (_norm(r1), _norm(rule2["response"]))])
    reply = s.say(f"Hey Jarvis, when I say {t1}, reply with {r1b} instead.", brain=fresh)
    rule1 = s.taught(t1)
    s.check("5a. updating a trigger is confirmed as an update", "updated" in reply.lower() and rule1 is not None
            and _norm(rule1["response"]) == _norm(r1b))
    reply = s.say(f"Hey Jarvis, {t1}.", brain=fresh)
    s.check("5b. the new response replaces the old one", rule1 is not None and _norm(reply) == _norm(rule1["response"]) and _norm(reply) != _norm(r1))
    s.check("5c. the old response is kept as history, not as a live rule", rule1 is not None and [_norm(h["response"]) for h in rule1["history"]] == [_norm(r1)])
    reply = s.say(f"Hey Jarvis, forget the {t2} command.", brain=fresh)
    s.check("6a. deleting a trigger is confirmed", "forgotten" in reply.lower() and s.taught(t2) is None)
    reply = s.say(f"Hey Jarvis, {t2}.", brain=fresh)
    s.check("6b. the deleted trigger no longer runs (the brain answers it as ordinary speech)", _norm(reply) != _norm(rule2["response"]) and s.clean(reply))
    reply = s.say(f"Hey Jarvis, {t1}.", brain=fresh)
    s.check("6c. the other trigger is untouched", rule1 is not None and _norm(reply) == _norm(rule1["response"]))
    reply = s.say("Hey Jarvis, what did I teach you?", brain=fresh)
    s.check("6d. 'what did I teach you' lists the command, not a reminder", t1.lower() in reply.lower()
            and (rule1 or {}).get("response", "\0").lower() in reply.lower() and "reminder" not in reply.lower()
            and t2.lower() not in reply.lower())
    # 7. everything else still works: facts, reminders, instant answers, small talk
    s.say("Hey Jarvis, remember that my sister Marie lives in Miami.", brain=fresh)
    s.check("7a. a fact is still saved with the memory skill (not as a command)", any(n in ("remember", "remember_note") for n, _ in s.calls)
            and s.taught("my sister marie lives in miami") is None)
    reply = s.say("Hey Jarvis, where does my sister live?", brain=s.new_brain())
    s.check("7b. ...and recalled in a new conversation", "miami" in reply.lower() and s.clean(reply))
    s.say("Hey Jarvis, remind me to call the dentist tomorrow at nine in the morning.", brain=fresh)
    s.check("7c. reminders still go to the reminder skill", "set_reminder" in [n for n, _ in s.calls])
    reply = s.say("Hey Jarvis, what's 15 percent of 80?", brain=fresh)
    s.check("7d. instant answers still work", "12" in reply and not s.calls)
    reply = s.say("Hey Jarvis, thanks, that's all for now.", brain=fresh)
    s.check("7e. small talk still gets a spoken answer with no tools", s.clean(reply) and not s.calls)
    return s.finish("restart")


SIMILAR = [("copper candle", "copper handle"), ("red kite", "red light"), ("silver mirror", "silver meadow"),
           ("paper button", "paper mutton"), ("amber lantern", "amber lanterns")]


def phase_similar(s: Session, state: Path) -> int:
    """Similar-sounding triggers through the real STT: each must run its own response, never the neighbour's."""
    pairs = s.rng.sample(SIMILAR, 3)
    print(f"\n===== similar (pairs {pairs}) =====")
    for a, b in pairs:
        ra, rb = s.rng.sample(REPLIES, 2)
        s.say(f"Hey Jarvis, when I say {a}, reply with {ra}.")
        s.say(f"Hey Jarvis, when I say {b}, reply with {rb}.")
        rule_a, rule_b = s.taught(a), s.taught(b)
        s.check(f"both '{a}' and '{b}' are on file as separate rules", rule_a is not None and rule_b is not None and rule_a is not rule_b
                and _norm(rule_a["trigger"]) != _norm(rule_b["trigger"]))
        if rule_a is None or rule_b is None:
            continue
        for trigger, own, other in ((a, rule_a, rule_b), (b, rule_b, rule_a)):
            for _ in range(2):
                reply = s.say(f"Hey Jarvis, {trigger}.")
                heard = s.transcript[-1]["heard"]
                wrong = _norm(reply) == _norm(other["response"])
                s.check(f"'{trigger}' (heard '{heard}') never gets the other command's response", not wrong)
                s.check(f"'{trigger}' gets its own response or, if the STT made it ambiguous, the brain's ordinary answer",
                        _norm(reply) == _norm(own["response"]) or (s.taught(heard) is None and s.clean(reply)))
    return s.finish("similar")


def main(argv) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", choices=["teach", "restart", "similar"])
    ap.add_argument("--data")
    ap.add_argument("--seed", type=int)
    a = ap.parse_args(argv)
    seed = a.seed if a.seed is not None else random.randrange(1 << 30)
    if a.phase:
        data = Path(a.data)
        s = Session(data, seed if a.phase == "teach" else seed + 1)
        phase = {"teach": phase_teach, "restart": phase_restart, "similar": phase_similar}[a.phase]
        return phase(s, data / "acceptance-state.json")
    data = Path(a.data) if a.data else Path(tempfile.mkdtemp(prefix="jarvis-accept-"))
    print(f"seed {seed}; data folder {data}")
    worst = 0
    for phase in ("teach", "restart"):                           # two processes: the second one IS the restart
        code = subprocess.call([sys.executable, __file__, "--phase", phase, "--data", str(data), "--seed", str(seed)])
        worst = max(worst, code)
        if phase == "teach" and not (data / "acceptance-state.json").exists():
            print("teach phase stored no rule; the restart phase has nothing to verify")
            return 1
    print(f"\nseed {seed}; results in {data}/acceptance-teach.json and acceptance-restart.json")
    return worst


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
