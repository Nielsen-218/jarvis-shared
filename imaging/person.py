"""Helpers that keep try-ons looking like *you*:

- crop_to_person: cut the avatar photo down to the person (a webcam shot is wide
  and the person is small in it), so the edit focuses on them.
- restore_face: blend the real face from the avatar photo back onto the result.
"""
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps

# YuNet face detector from OpenCV's model zoo (models/ in the project folder).
_YUNET = Path(__file__).resolve().parent.parent / "models" / "face_detection_yunet_2023mar.onnx"
_HOG = cv2.HOGDescriptor()
_HOG.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())


def _cv(img: Image.Image) -> np.ndarray:
    return cv2.cvtColor(np.asarray(img.convert("RGB")), cv2.COLOR_RGB2BGR)


def find_face(img: Image.Image, within: tuple[int, int, int, int] | None = None
              ) -> tuple[int, int, int, int] | None:
    """Most confident face (x, y, w, h), optionally only inside the `within` box.
    Searched at 2x so small faces in wide webcam shots are found."""
    bgr = cv2.resize(_cv(img), None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    det = cv2.FaceDetectorYN.create(str(_YUNET), "", (bgr.shape[1], bgr.shape[0]), 0.6)
    _, faces = det.detect(bgr)
    if faces is None:
        return None
    best = None
    for f in faces:
        x, y, w, h = (f[:4] / 2).astype(int)
        if within is not None:
            bx, by, bw, bh = within
            if not (bx <= x + w / 2 <= bx + bw and by <= y + h / 2 <= by + bh):
                continue
        # confidence x face size: a tiny high-contrast decoy (a patterned phone case, a poster) must not beat the
        # real, larger face that scored a little lower (measured 2026-11-12: 19x25 px at 0.75 beat a 289x291 face at 0.63)
        score = float(f[-1]) * float(max(w * h, 1)) ** 0.5
        if best is None or score > best[1]:
            best = ((int(x), int(y), int(w), int(h)), score)
    return best[0] if best else None


def _find_body(img: Image.Image) -> tuple[int, int, int, int] | None:
    bgr = _cv(img)
    scale = 640 / max(bgr.shape[:2])
    small = cv2.resize(bgr, None, fx=scale, fy=scale) if scale < 1 else bgr
    scale = min(scale, 1.0)
    rects, weights = _HOG.detectMultiScale(small, winStride=(8, 8), padding=(16, 16), scale=1.05)
    if len(rects) == 0:
        return None
    x, y, w, h = rects[int(np.argmax(weights))]
    return int(x / scale), int(y / scale), int(w / scale), int(h / scale)


def crop_to_person(img: Image.Image) -> Image.Image:
    """Portrait (2:3) crop around the person, with some margin. Returns the
    original if no one is found."""
    # Phone cameras store the frame sideways plus an EXIF orientation flag; honour
    # it so detection and the edit run on an upright image, not the raw landscape
    # pixels (which crop around whatever decoy lands where the head should be).
    img = ImageOps.exif_transpose(img)
    W, H = img.size
    # HOG can rank furniture as a person in wide webcam photos. Never let such a
    # box suppress a confidently detected real face elsewhere in the frame.
    face = find_face(img)
    body = _find_body(img)
    if body is not None and face is not None:
        bx, by, bw, bh = body
        fx, fy, fw, fh = face
        if not (bx <= fx + fw / 2 <= bx + bw and by <= fy + fh / 2 <= by + bh):
            body = None
    if body is None and face is None:
        return img
    if body is None and H / W >= 2.2 and face[1] < H * 0.25:
        # Already a tall, head-to-toe photo (a phone full-body shot is about 3:8): a 2:3 portrait crop would cut
        # the person off at the knees and the try-on would never show the legs. Keep the whole frame.
        return img
    if body is not None:
        x, y, w, h = body
        cx, top, bottom = x + w / 2, y, y + h
        if face is not None:  # HOG boxes are loose; the face pins the centre and top
            cx, top = face[0] + face[2] / 2, min(top, face[1] - face[3])
    else:  # face only: a standing adult is roughly 7.5 head-heights tall
        fx, fy, fw, fh = face
        cx, top, bottom = fx + fw / 2, fy - fh * 0.6, fy + fh * 7.5
    height = min((bottom - top) * 1.12, H)
    width = min(height * 2 / 3, W)
    height = min(height, width * 3 / 2)
    left = int(np.clip(cx - width / 2, 0, W - width))
    upper = int(np.clip((top + bottom) / 2 - height / 2, 0, H - height))
    return img.crop((left, upper, left + int(width), upper + int(height)))


def output_size(img: Image.Image, megapixels: float = 1.0) -> tuple[int, int]:
    """Generation size with the same aspect ratio as img (multiples of 16)."""
    aspect = img.width / img.height
    h = (megapixels * 1e6 / aspect) ** 0.5
    w = h * aspect
    return int(round(w / 16) * 16), int(round(h / 16) * 16)


_REMBG = None


def _grabcut_alpha(img: Image.Image) -> np.ndarray:
    """Fallback background matte via GrabCut (seeded with a centred person box)."""
    rgb = np.asarray(img.convert("RGB"))
    h, w = rgb.shape[:2]
    bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    mask = np.zeros((h, w), np.uint8)
    rect = (int(w * 0.10), int(h * 0.02), int(w * 0.80), int(h * 0.97))
    cv2.grabCut(bgr, mask, rect, np.zeros((1, 65), np.float64), np.zeros((1, 65), np.float64),
                6, cv2.GC_INIT_WITH_RECT)
    m = np.where((mask == cv2.GC_FGD) | (mask == cv2.GC_PR_FGD), 255, 0).astype(np.uint8)
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8))
    n, lbl, stats, _ = cv2.connectedComponentsWithStats(m)
    if n > 1:
        big = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
        m = np.where(lbl == big, 255, 0).astype(np.uint8)
    return cv2.GaussianBlur(m, (0, 0), 1.6)


def cutout(img: Image.Image) -> Image.Image:
    """Remove the background so the person floats (for the holo avatar chamber). Uses rembg's
    human-segmentation model for clean hair edges; falls back to GrabCut if rembg is unavailable.
    Trims to the person so they fill the frame. Returns RGBA; never raises (returns the original
    opaque on total failure so a render is never lost)."""
    try:
        img = img.convert("RGB")
        rgb = np.asarray(img)
        alpha = None
        try:  # rembg: a proper human matte (keeps wispy hair, drops the studio grey cleanly)
            from rembg import new_session, remove
            global _REMBG
            if _REMBG is None:
                _REMBG = new_session("u2net_human_seg")
            cut = remove(img, session=_REMBG, post_process_mask=True)
            a = np.asarray(cut.convert("RGBA"))[:, :, 3]
            if a.mean() > 3:                       # sane amount of foreground
                alpha = cv2.GaussianBlur(a, (0, 0), 1.0)
        except Exception:
            alpha = None
        if alpha is None:
            alpha = _grabcut_alpha(img)
        # Keep only the largest connected blob — drops a detached remnant (e.g. a flag behind the
        # head that the matte caught) while leaving the person whole.
        binm = (alpha > 40).astype(np.uint8)
        n, lbl, stats, _ = cv2.connectedComponentsWithStats(binm)
        if n > 2:
            big = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
            alpha = np.where(lbl == big, alpha, 0).astype(np.uint8)
        rgba = Image.fromarray(np.dstack([rgb, alpha]).astype(np.uint8), "RGBA")
        box = Image.fromarray(alpha).getbbox()     # trim transparent margin so the person fills the frame
        if box:
            pad = round(0.03 * max(rgba.size))
            rgba = rgba.crop((max(0, box[0] - pad), max(0, box[1] - pad),
                              min(rgba.width, box[2] + pad), min(rgba.height, box[3] + pad)))
        return rgba
    except Exception:
        return img.convert("RGBA")


def head_matte(person: np.ndarray, face: tuple[int, int, int, int]) -> np.ndarray:
    """Float 0..1 mask of just the head (face, hair, ears) cut from a person matte of the ORIGINAL photo: the matte
    limited to a window around the face that stops at the chin, so none of the wall, board or furniture behind the
    head is carried along, and the hair keeps its real outline. Feathered at the edges."""
    H, W = person.shape
    x, y, w, h = face
    window = np.zeros(person.shape, np.uint8)
    x0, x1 = max(0, int(x - w * 0.55)), min(W, int(x + w * 1.55))
    y0, y1 = max(0, int(y - h * 0.9)), min(H, int(y + h * 1.06))   # above the hair, down to the chin
    window[y0:y1, x0:x1] = 1
    m = (person > 0.5).astype(np.uint8) * window
    n, lbl, stats, _ = cv2.connectedComponentsWithStats(m)
    if n > 1:                                   # the blob holding the face (a neighbour's shoulder is dropped)
        cy, cx = min(H - 1, y + h // 2), min(W - 1, x + w // 2)
        keep = int(lbl[cy, cx]) or 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
        m = (lbl == keep).astype(np.uint8)
    k = max(3, int(w * 0.02)) | 1
    m = cv2.erode(m, np.ones((k, k), np.uint8)).astype(np.float32)   # drop the thin halo of wall at the hair edge
    # fade out below the chin so the neck blends into the generated body
    ramp = np.clip((y + h * 1.06 - np.arange(H, dtype=np.float32)) / (h * 0.30), 0.0, 1.0)[:, None]
    return cv2.GaussianBlur(m * ramp, (0, 0), sigmaX=max(1.5, w * 0.025))


def restore_face(original: Image.Image, result: Image.Image) -> Image.Image:
    """Put the real head from `original` (the real photo) onto `result` (same framing).

    Only the upper part of a full-body frame is searched for the face: a standing
    person's head sits in the top third, so this ignores high-contrast decoys lower
    down (a patterned phone case in a hand, a bag, a belt buckle) that the detector
    could otherwise rank above a small, backlit real face. The head is cut out of the
    original with a human matte (head_matte), never as a box or ellipse, so the wall
    behind the head in the photo is not pasted onto the new backdrop."""
    orig = original.convert("RGB").resize(result.size, Image.LANCZOS)
    head = (0, 0, result.size[0], int(result.size[1] * 0.6))  # top 60%: the head zone
    f_orig = find_face(orig, within=head)
    if f_orig is None:
        return result
    f_new = find_face(result, within=head)
    ox, oy, ow, oh = f_orig
    if f_new is not None:
        nx, ny, nw, nh = f_new
        # Scale the real face to the size of the head the model actually generated, so it never
        # pastes an oversized head (bobblehead) or a tiny one onto a differently-proportioned body.
        scale = max(0.4, min(2.5, nw / ow)) if ow else 1.0
    else:
        nx, ny, nw, nh = f_orig           # no face found in the result: trust the shared framing
        scale = 1.0
    matte = head_matte(person_mask(orig), f_orig)
    size = (max(1, round(orig.width * scale)), max(1, round(orig.height * scale)))
    scaled = orig.resize(size, Image.LANCZOS)
    scaled_matte = Image.fromarray((matte * 255).astype(np.uint8)).resize(size, Image.LANCZOS)
    # Align the real face centre onto the generated head centre.
    fcx, fcy = (ox + ow / 2) * scale, (oy + oh / 2) * scale
    tcx, tcy = nx + nw / 2, ny + nh / 2
    at = (round(tcx - fcx), round(tcy - fcy))
    canvas = Image.new("RGB", result.size)
    canvas.paste(scaled, at)
    mask_img = Image.new("L", result.size, 0)
    mask_img.paste(scaled_matte, at)
    mask = (np.asarray(mask_img).astype(np.float32) / 255.0)[..., None]
    src = np.asarray(canvas).astype(np.float32)
    out = np.asarray(result.convert("RGB")).astype(np.float32) * (1 - mask) + src * mask
    return Image.fromarray(out.clip(0, 255).astype(np.uint8))


# -- keeping the photo's own clothes out of the render -------------------------------------------------------
_SEG = None


def person_mask(img: Image.Image) -> np.ndarray:
    """Soft person matte (float 0..1, same size as img) from rembg's human-segmentation model, GrabCut if rembg
    is missing. Hair is included; the background is not."""
    img = img.convert("RGB")
    try:
        from rembg import new_session, remove
        global _SEG
        if _SEG is None:
            _SEG = new_session("u2net_human_seg")
        a = np.asarray(remove(img, session=_SEG).convert("RGBA"))[:, :, 3].astype(np.float32) / 255.0
        if a.mean() > 0.01:
            return a
    except Exception:  # noqa: BLE001
        pass
    return _grabcut_alpha(img).astype(np.float32) / 255.0


def skin_mask(img: Image.Image, face: tuple[int, int, int, int]) -> np.ndarray:
    """Pixels whose colour matches this person's own face skin (float 0/1): the neck, hands and forearms, found
    by sampling the face instead of assuming a skin tone. Dark clothes are told apart by chroma and brightness."""
    lab = cv2.cvtColor(np.asarray(img.convert("RGB")), cv2.COLOR_RGB2LAB).astype(np.float32)
    x, y, w, h = face
    patch = lab[y + int(h * 0.35): y + int(h * 0.75), x + int(w * 0.25): x + int(w * 0.75)].reshape(-1, 3)
    if len(patch) < 20:
        return np.zeros(lab.shape[:2], np.float32)
    med = np.median(patch, axis=0)
    spread = np.maximum(patch.std(axis=0), np.array([14.0, 4.0, 4.0]))
    ab = np.sqrt(((lab[..., 1:] - med[1:]) ** 2).sum(-1))
    ok = (ab < max(10.0, float(spread[1:].max()) * 2.2)) & (lab[..., 0] > med[0] - 70) & (lab[..., 0] < med[0] + 60)
    m = ok.astype(np.uint8)
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    return m.astype(np.float32)


def clothing_region(person: np.ndarray, skin: np.ndarray, face: tuple[int, int, int, int],
                    part: str = "both") -> np.ndarray:
    """Float 0..1 mask of the body area that clothes cover: the person minus the head, the skin and the shoes.
    part: "top" (neck to hip line), "bottom" (hip line to the ankles) or "both". The hip line is read from the
    head size (about 2.75 detected-face-heights below the chin, the shoes in the last sixth)."""
    H, W = person.shape
    fx, fy, fw, fh = face
    chin = fy + fh
    neck = chin + int(fh * 0.12)
    hip = int(chin + fh * 2.75)
    rows = np.where((person > 0.5).any(axis=1))[0]
    ankle = int(rows[0] + 0.83 * (rows[-1] - rows[0])) if len(rows) else H   # leave the shoes as they are
    top, bottom = {"top": (neck, hip), "bottom": (hip - int(fh * 0.25), ankle)}.get(part, (neck, ankle))
    region = np.zeros_like(person)
    region[max(0, top):min(H, bottom), :] = 1.0
    body = cv2.erode((person > 0.5).astype(np.uint8), np.ones((3, 3), np.uint8)).astype(np.float32)
    keep_skin = cv2.dilate(cv2.morphologyEx(skin, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8)),
                           np.ones((5, 5), np.uint8))
    out = region * body * (1.0 - keep_skin)
    # grow slightly so a print on the very edge of the clothes does not survive, then smooth the border
    out = cv2.dilate(out, np.ones((7, 7), np.uint8))
    out = out * (person > 0.3) * (1.0 - keep_skin)      # never paint onto the wall behind, nor over a hand
    return cv2.GaussianBlur(out, (0, 0), 2.0)


def neutralise_clothes(img: Image.Image, part: str = "both", grey: int = 150) -> Image.Image:
    """The person photo with the clothes' area flattened to a plain grey, so the render has nothing to copy a print
    or logo from but still sees the pose, the body shape, the hands and the face."""
    img = img.convert("RGB")
    face = find_face(img, within=(0, 0, img.width, int(img.height * 0.6)))
    if face is None:
        return img
    person = person_mask(img)
    region = clothing_region(person, skin_mask(img, face), face, part)[..., None]
    arr = np.asarray(img).astype(np.float32)
    out = arr * (1 - region) + float(grey) * region
    return Image.fromarray(out.clip(0, 255).astype(np.uint8))
