"""Opt-in live gamecoach lifecycle inside the existing Jarvis Windows process.

No second Jarvis instance, cloud API, game input, injection, or persistent worker.
GameWatcher calls stop when the game exits; owner can also stop by voice.
"""
from __future__ import annotations
import sys
from pathlib import Path
from threading import Event,Lock,Thread
from typing import Callable

ROOT=Path(__file__).resolve().parents[1]
GAMES={"apex":"Apex Legends","cod":"Call of Duty"}
_calibrations=ROOT/"data"/"gaming"

class CoachService:
    def __init__(self,root:Path|None=None,platform:str|None=None,runner:Callable|None=None):
        self.root=root if root is not None else _calibrations
        self.platform=platform if platform is not None else sys.platform
        self.runner=runner
        self._lock=Lock()
        self._thread:Thread|None=None
        self._stop:Event|None=None
        self._game:str|None=None
        self.last_error:str|None=None
        self.last_report:dict|None=None

    def file(self,game:str)->Path:
        if game not in GAMES:raise ValueError("Choose apex or cod")
        return self.root/f"coach-{game}.json"

    def profile_file(self,game:str,kind:str)->Path:
        if game not in GAMES or kind not in {"low_health","low_shield","low_ammo"}:
            raise ValueError("Invalid gaming HUD profile")
        return self.root/f"coach-{game}-{kind}.json"

    def profile_files(self,game:str)->list[Path]:
        profiles=[self.profile_file(game,k) for k in ("low_health","low_shield","low_ammo")
                  if self.profile_file(game,k).is_file()]
        legacy=self.file(game)
        if legacy.is_file():
            import json
            try:
                old_kind=json.loads(legacy.read_text(encoding="utf-8")).get("kind")
            except (ValueError,OSError,AttributeError):
                old_kind=None
            if ((old_kind in {"low_health","low_shield","low_ammo"} and not any(
                old_kind in p.stem for p in profiles)) or (old_kind is None and not profiles)):
                # Old installations and injected test runners still see legacy path.
                profiles.append(legacy)
        return profiles

    def badge_files(self,game:str)->list[Path]:
        if game not in GAMES:return []
        return sorted(self.root.glob(f"badge-{game}-*.json"))[:5]

    def vision_file(self,game:str)->Path:
        if game not in GAMES:raise ValueError("Choose Apex or Call of Duty")
        return self.root/f"vision-{game}.json"

    def start(self,game:str,voice:bool=True,tactical_vision:bool=False)->str:
        if game not in GAMES:return "Choose Apex or Call of Duty."
        if self.platform!="win32":return "Live screen coaching needs the Windows gaming PC."
        paths=self.profile_files(game)
        if tactical_vision:
            manifest=self.vision_file(game)
            try:
                from core.gamecoach_enemy_detector import load_config
                load_config(manifest,game)
                import onnxruntime  # noqa: F401 - CPU backend availability check
            except (OSError,ValueError,ImportError,KeyError,TypeError) as exc:
                return ("Tactical visual coaching needs a game-trained, locally validated "
                        f"ONNX model for {GAMES[game]}. Not enabled: {type(exc).__name__}. "
                        "The ordinary HUD coach remains available.")
        else:
            manifest=None
        if not paths:
            return f"No {GAMES[game]} HUD profile yet. Say 'calibrate my {GAMES[game]} gaming coach' first."
        with self._lock:
            if self._thread and self._thread.is_alive():
                return f"Coach is already running for {GAMES[self._game]}."
            self._stop=Event()
            self._game=game
            self.last_error=None
            self.last_report=None
            event=self._stop
            path=paths[0]
            others=paths[1:]
            badge_paths=self.badge_files(game)
            # Load profile in the owner-command thread, not after user context is
            # lost on the game-worker thread (users.current() is thread-local).
            weights={}
            if self.runner is None:
                from core.gamecoach_training import current_profile
                from core.gamecoach_feedback import current_preferences,combine_training_weights
                try:
                    trained=current_profile(game)["weights"]
                    preferences,_=current_preferences(game)
                    weights=combine_training_weights(trained,preferences)
                except (OSError,ValueError,KeyError,TypeError):
                    weights={}
            def work():
                try:
                    if self.runner is None:
                        from scripts.gamecoach_live import live
                        result=live(path,seconds=0,quiet=False,voice=voice,stop_event=event,
                                    extra_paths=others,game=game,badge_paths=badge_paths,
                                    training_weights=weights,vision_manifest=manifest)
                    else:
                        result=self.runner(path,event,voice)
                    self.last_report=result
                except Exception as e:
                    self.last_error=f"{type(e).__name__}: {e}"
            self._thread=Thread(target=work,daemon=True,name=f"jarvis-gamecoach-{game}")
            self._thread.start()
        scope=("validated on-screen enemy and HUD cues" if tactical_vision
               else "confirmed HUD warnings")
        return (f"{GAMES[game]} local coach starting with {scope}. "
                "Say 'stop gaming coach' to end it.")

    def stop(self)->str:
        with self._lock:
            if not self._thread or not self._thread.is_alive():
                return "Live gaming coach is not running."
            self._stop.set()
            thread=self._thread
        thread.join(timeout=2.0)
        return ("Live gaming coach stopped." if not thread.is_alive()
                else "Coach stop requested; completing its current operation.")

    def status(self)->str:
        with self._lock:
            active=bool(self._thread and self._thread.is_alive())
            game=self._game
        if active:return f"Coaching {GAMES.get(game,game)} locally."
        if self.last_error:return f"Gaming coach could not continue: {self.last_error}"
        if self.last_report:
            return "Last coaching session ended. Capture stats are available in the local report."
        return "Gaming coach is off."

    def calibrate(self,game:str,kind:str="low_health")->str:
        if game not in GAMES:return "Choose Apex or Call of Duty."
        if self.platform!="win32":return "Calibration must run on the Windows gaming PC."
        if kind not in {"low_health","low_shield","low_ammo"}:
            return "Select health, shield, or ammo."
        from scripts.gamecoach_live import calibrate
        try:
            calibrate(self.profile_file(game,kind),kind)
            return f"{GAMES[game]} {kind.replace('_',' ')} HUD calibration saved locally."
        except Exception as e:
            return f"Calibration unsuccessful: {type(e).__name__}: {e}"

service=CoachService()
