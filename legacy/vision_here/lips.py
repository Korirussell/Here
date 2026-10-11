"""Face boxes and a lip-motion speaking score for tracked people (HERE_PLAN.md §9.1, mouth-movement scoring).

Runs MediaPipe's face landmarker on the head region of each person box and tracks the
`jawOpen` blendshape per track. Talking makes the jaw move, so the score is how much
jawOpen varied over the last LIP_WINDOW_S: a still mouth, open or closed, scores ~0.
No identity is computed (§21): landmarks are used for the mouth only.
"""

import urllib.request
from collections import deque
from pathlib import Path

import numpy as np

from . import config as C


def speaking_score(values):
    """0..1 from recent jawOpen values (needs a few samples)."""
    if len(values) < 3:
        return 0.0
    return float(min(1.0, np.std(values) / C.LIP_STD_FULL))


def ensure_model():
    path = Path(__file__).with_name(C.FACE_MODEL)
    if not path.exists():
        print(f"[vision_here] downloading {C.FACE_MODEL} ...")
        tmp = path.with_suffix(".part")
        urllib.request.urlretrieve(C.FACE_MODEL_URL, tmp)
        tmp.replace(path)
    return path


class LipReader:
    def __init__(self):
        import mediapipe as mp
        from mediapipe.tasks.python import vision
        from mediapipe.tasks.python.core.base_options import BaseOptions

        self.mp = mp
        options = vision.FaceLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=str(ensure_model())),
            running_mode=vision.RunningMode.IMAGE, num_faces=1, output_face_blendshapes=True)
        self.landmarker = vision.FaceLandmarker.create_from_options(options)
        self.history = {}        # track_id -> deque[(t, jawOpen)]

    def face(self, img, bbox):
        """(face_bbox [x, y, w, h] in frame pixels, jawOpen 0..1) for one person box, or None."""
        x, y, w, h = bbox
        H, W = img.shape[:2]
        x0, x1 = max(0, int(x - 0.1 * w)), min(W, int(x + 1.1 * w))
        y0, y1 = max(0, int(y - 0.05 * h)), min(H, int(y + C.HEAD_CROP * h))
        if x1 - x0 < C.MIN_HEAD_PX or y1 - y0 < C.MIN_HEAD_PX:
            return None
        crop = np.ascontiguousarray(img[y0:y1, x0:x1, ::-1])       # BGR -> RGB
        res = self.landmarker.detect(self.mp.Image(image_format=self.mp.ImageFormat.SRGB, data=crop))
        if not res.face_landmarks:
            return None
        pts = np.array([(p.x, p.y) for p in res.face_landmarks[0]])
        cw, ch = x1 - x0, y1 - y0
        fx0, fy0 = pts.min(axis=0) * (cw, ch)
        fx1, fy1 = pts.max(axis=0) * (cw, ch)
        jaw = next((b.score for b in res.face_blendshapes[0] if b.category_name == "jawOpen"), None)
        if jaw is None:
            return None
        box = [round(float(v), 1) for v in (x0 + fx0, y0 + fy0, fx1 - fx0, fy1 - fy0)]
        return box, float(jaw)

    def update(self, track_id, t, jaw):
        """Add a sample and return the track's speaking score."""
        h = self.history.setdefault(track_id, deque())
        h.append((t, jaw))
        while h and h[0][0] < t - C.LIP_WINDOW_S:
            h.popleft()
        return speaking_score([v for _, v in h])

    def prune(self, t):
        for tid in [k for k, h in self.history.items() if not h or h[-1][0] < t - 5.0]:
            del self.history[tid]
