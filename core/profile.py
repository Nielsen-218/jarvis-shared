"""A person's profile card: a square photo and a one-line tagline, kept in their own data folder
(data/users/<id>/profile/). The Command Center shows both; the owner sets them from the window, and
scripts/set_profile_photo.py does the same from a terminal."""
from __future__ import annotations

import base64
import io
import json

from core import storage

MAX_UPLOAD = 8 * 1024 * 1024          # bytes read from an upload
MAX_PIXELS = 40_000_000               # a decompression-bomb guard, well above any phone photo
SIDE = 320                            # the stored square, in pixels
FORMATS = {"JPEG", "PNG", "WEBP"}
DEFAULT_TAGLINE = "Your tagline here"
MAX_TAGLINE = 90


class ProfileError(ValueError):
    """What is wrong with the upload, in words that can be shown to the person."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _dir(uid: str):
    return storage.user_dir(uid) / "profile"


def photo_path(uid: str):
    return _dir(uid) / "photo.jpg"


def save_photo(uid: str, data: bytes) -> None:
    """Validate the picture, crop it to a centred square (a little above the middle, where faces are) and store it."""
    if not data:
        raise ProfileError("No picture arrived.")
    if len(data) > MAX_UPLOAD:
        raise ProfileError("That picture is too big (keep it under 8 MB).", 413)
    from PIL import Image, ImageOps, UnidentifiedImageError
    try:
        img = Image.open(io.BytesIO(data))
        fmt = img.format
        if fmt not in FORMATS:
            raise ProfileError("Use a JPEG, PNG or WebP picture.", 415)
        if img.width * img.height > MAX_PIXELS:
            raise ProfileError("That picture has too many pixels. Use one under 40 megapixels.", 413)
        img.load()
    except ProfileError:
        raise
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        raise ProfileError("That file isn't a usable picture.") from None
    try:
        img = ImageOps.exif_transpose(img).convert("RGB")
        img = ImageOps.fit(img, (SIDE, SIDE), Image.LANCZOS, centering=(0.5, 0.35))
        out = io.BytesIO()
        img.save(out, "JPEG", quality=88, optimize=True)
    except Exception:  # noqa: BLE001  (a malformed EXIF block or palette is a bad picture, not a server error)
        raise ProfileError("That file isn't a usable picture.") from None
    path = photo_path(uid)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(out.getvalue())
    tmp.replace(path)


def photo_data_url(uid: str) -> str:
    """The stored photo as a small data URL ("" when none is set), so the page needs no second authenticated request."""
    try:
        return "data:image/jpeg;base64," + base64.b64encode(photo_path(uid).read_bytes()).decode("ascii")
    except OSError:
        return ""


def _file(uid: str):
    return _dir(uid) / "profile.json"


def clean_tagline(text) -> str:
    text = " ".join(str(text or "").split())
    return "".join(c for c in text if c.isprintable())[:MAX_TAGLINE].strip()


def tagline(uid: str) -> str:
    try:
        saved = clean_tagline(json.loads(_file(uid).read_text(encoding="utf-8")).get("tagline"))
    except (OSError, ValueError, AttributeError):
        saved = ""
    return saved or DEFAULT_TAGLINE


def set_tagline(uid: str, text) -> str:
    """Store the tagline; an empty one goes back to the default."""
    clean = clean_tagline(text)
    storage._write(_file(uid), {"tagline": clean} if clean else {})
    return clean or DEFAULT_TAGLINE
