"""Settings loaded from .env (see .env.example)."""
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")


@dataclass(frozen=True)
class Config:
    model: str = os.getenv("JARVIS_MODEL", "claude-sonnet-5-5")
    effort: str = os.getenv("JARVIS_EFFORT", "low")
    user_title: str = os.getenv("JARVIS_USER_TITLE", "sir")

    voice: str = os.getenv("JARVIS_VOICE", "en-GB-RyanNeural")
    voice_rate: str = os.getenv("JARVIS_VOICE_RATE", "+8%")
    # Voice for Haitian Creole replies: an edge-tts voice, or "mms" (Meta's Kreyol model)
    kreyol_voice: str = os.getenv("JARVIS_KREYOL_VOICE", "fr-FR-RemyMultilingualNeural")
    french_voice: str = os.getenv("JARVIS_FRENCH_VOICE", "fr-FR-RemyMultilingualNeural")
    # Any other language (e.g. Spanish during a lesson): a multilingual voice that adapts
    other_voice: str = os.getenv("JARVIS_OTHER_VOICE", "en-US-AndrewMultilingualNeural")

    whisper_model: str = os.getenv("WHISPER_MODEL", "large-v3-turbo")
    whisper_device: str = os.getenv("WHISPER_DEVICE", "cuda")

    wake_threshold: float = float(os.getenv("WAKE_THRESHOLD", "0.5"))

    # After a reply, how long Jarvis keeps listening for a follow-up before sleeping
    followup_seconds: float = float(os.getenv("JARVIS_FOLLOWUP_SECONDS", "30"))

    # How much louder than Jarvis's own voice (through the speakers) you must be
    # to interrupt him. Lower = easier to interrupt; raise it if he cuts himself off.
    barge_in_ratio: float = float(os.getenv("JARVIS_BARGE_IN_RATIO", "2.0"))

    # The transparent desktop companion (the 3D robot). OFF by default since 2026-11-19: the live 3D scene in a
    # see-through window cost ~8% GPU all the time and lagged the PC. JARVIS_COMPANION=1 (or --companion) brings it back.
    companion: bool = os.getenv("JARVIS_COMPANION", "0").strip().lower() not in ("0", "false", "no", "off")

    # Forget the conversation after this many idle seconds
    conversation_timeout: int = 600


def titles(uid: str = "owner", cfg: Config | None = None) -> dict:
    """How Jarvis addresses this person in English, Kreyòl and French ("sir", "mesye", "monsieur" unless they chose)."""
    from core import users

    given = users.get(uid).get("titles") or {}
    return {"en": given.get("en") or (cfg or Config()).user_title, "ht": given.get("ht") or "mesye",
            "fr": given.get("fr") or "monsieur"}


def load_personality(cfg: Config, uid: str = "owner") -> str:
    """Jarvis's instructions, addressed to one household member."""
    from core import users

    person = users.get(uid)
    names = titles(uid, cfg)
    text = (ROOT / "core" / "personality.md").read_text(encoding="utf-8")
    # The same character sheet as the chat persona (wave 3b, 2026-11-21): command turns no longer speak in a second, stiffer voice
    text = text.replace("{character}", load_character())
    return (text.replace("{title}", names["en"])
                .replace("{title_ht}", names["ht"])
                .replace("{title_fr}", names["fr"])
                .replace("{name}", person.get("name") or "the owner of this PC"))


def load_persona_core(cfg: Config | None = None, uid: str = "owner") -> str:
    """The short chat persona (core/persona-core.md with core/character.md in it, about 1,900 tokens): who Jarvis is and how he talks, for a turn that
    is conversation. The operations manual (load_personality) goes only to turns that may use a skill. '' when the file
    is missing: every turn then takes the manual, as before."""
    from core import users

    try:
        text = (ROOT / "core" / "persona-core.md").read_text(encoding="utf-8")
    except OSError:
        return ""
    person = users.get(uid)
    names = titles(uid, cfg)
    text = text.replace("{character}", load_character())      # who he is and how he talks: one sheet (core/character.md)
    return (text.replace("{title}", names["en"])
                .replace("{title_ht}", names["ht"])
                .replace("{title_fr}", names["fr"])
                .replace("{name}", person.get("name") or "the person who owns this PC").strip())


def load_character() -> str:
    """The character sheet (core/character.md): who Jarvis is, how he addresses them, his humor and what he never says.
    One file, so the voice is defined once (2026-11-14: the chat persona, the manual, the canned lines and the
    acknowledgements each described a different one). '' when the file is missing: the prompt simply has no such part.
    Its {name} and {title} are filled by the caller, with the rest of the prompt."""
    try:
        return (ROOT / "core" / "character.md").read_text(encoding="utf-8").strip()
    except OSError:
        return ""
