"""vision_here tunables."""

MODEL = "yolo11m.pt"          # downloaded on first run; sized for the RTX 5000 with FP16. yolo11s.pt if FPS drops
TRACKER = "bytetrack.yaml"
CONFIDENCE = 0.35
IMGSZ = 640
DEVICE = "cuda:0"             # falls back to "cpu" if CUDA isn't usable
HALF = True                   # FP16 inference on the GPU (~2x faster, same accuracy); ignored on CPU
HEADING_BUFFER_S = 3.0

# Face boxes + lip-motion speaking score per person (lips.py). Off: no face_bbox / speaking fields.
LIPS = True
FACE_MODEL = "face_landmarker.task"   # downloaded next to lips.py on first run (~4 MB)
FACE_MODEL_URL = ("https://storage.googleapis.com/mediapipe-models/face_landmarker/"
                  "face_landmarker/float16/1/face_landmarker.task")
HEAD_CROP = 0.45              # top fraction of a person box searched for the face
MIN_HEAD_PX = 48              # smaller head regions are skipped (landmarks get unreliable)
MAX_FACES = 4                 # lip scoring for at most this many people per frame (largest boxes first)
LIP_WINDOW_S = 0.8            # speaking = jawOpen variation over this window
LIP_STD_FULL = 0.08           # jawOpen std that maps to speaking = 1 (tune on fixture 1)

# COCO class name -> contract EntityKind (HERE_PLAN.md §8.3). Other classes are ignored.
CLASS_MAP = {
    "person": "person",
    "dog": "dog",
    "cat": "cat",
    "tv": "tv",
    "laptop": "laptop",
    "cell phone": "phone",
    "car": "vehicle",
    "bus": "vehicle",
    "truck": "vehicle",
    "motorcycle": "vehicle",
}
