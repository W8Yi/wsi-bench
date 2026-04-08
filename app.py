#!/usr/bin/env python3
import json
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
from multiprocessing import Process, Queue
from pathlib import Path
from typing import Dict, List
from urllib.parse import parse_qs, unquote

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

try:
    import openslide  # type: ignore
except Exception:
    openslide = None  # type: ignore

try:
    from PIL import Image, ImageDraw
except Exception:
    Image = None  # type: ignore
    ImageDraw = None  # type: ignore

try:
    import tifffile  # type: ignore
except Exception:
    tifffile = None  # type: ignore

APP_HOST = os.environ.get("WSI_VIEWER_HOST", "127.0.0.1")
APP_PORT = int(os.environ.get("WSI_VIEWER_PORT", "8080"))
THUMB_TIMEOUT_SEC = float(os.environ.get("WSI_THUMB_TIMEOUT_SEC", "6"))

SLIDE_EXTS = {
    ".svs", ".tif", ".tiff", ".ndpi", ".mrxs", ".scn", ".vms", ".vmu", ".bif", ".jpg", ".jpeg", ".png"
}
FEATURE_EXTS = {
    ".h5", ".pt", ".pth", ".npy", ".npz", ".pkl", ".parquet", ".csv", ".tsv"
}

CASE_RE = re.compile(r"(TCGA-[A-Z0-9]{2}-[A-Z0-9]{4})", re.IGNORECASE)
SLIDE_ID_RE = re.compile(r"(TCGA-[A-Z0-9]{2}-[A-Z0-9]{4}-\d{2}[A-Z]-\d{2}-DX\d+)", re.IGNORECASE)


def _existing_dirs(candidates: List[str]) -> List[Path]:
    out: List[Path] = []
    for c in candidates:
        if not c:
            continue
        p = Path(c).expanduser()
        if p.exists() and p.is_dir():
            out.append(p)
    return out


def resolve_slide_roots() -> List[Path]:
    env = os.environ.get("WSI_SLIDES_DIR", "").strip()
    if env:
        return _existing_dirs([s.strip() for s in env.split(",")])
    return _existing_dirs([
        "/mnt/data/WSI_slides",
        "/mnt/data/TCGA_slides",
    ])


def resolve_feature_roots() -> List[Path]:
    env = os.environ.get("WSI_FEATURES_DIRS", "").strip()
    if env:
        return _existing_dirs([s.strip() for s in env.split(",")])
    return _existing_dirs([
        "/mnt/data/WSI_features",
        "/mnt/data/TCGA_features",
        "/mnt/data/features-sea",
    ])


def format_ts(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def to_slide_id(name: str) -> str:
    m = SLIDE_ID_RE.search(name)
    if m:
        return m.group(1).upper()
    stem = Path(name).stem
    return stem.split(".")[0].upper()


def to_case_id(name: str) -> str:
    m = CASE_RE.search(name)
    if m:
        return m.group(1).upper()
    return "UNKNOWN"


def infer_encoder(path: Path) -> str:
    parts = [p.lower() for p in path.parts]
    for i, part in enumerate(parts):
        if "feature" in part:
            original = path.parts[i]
            if "_" in original:
                return original.split("_", 1)[1]
            if "-" in original:
                return original.split("-", 1)[1]
            return original
    if len(path.parts) >= 2:
        return path.parts[-2]
    return "unknown"


@dataclass
class FeatureFile:
    path: str
    root: str
    encoder: str
    filename: str
    size_bytes: int
    modified_at: str


@dataclass
class SlideFile:
    path: str
    root: str
    filename: str
    case_id: str
    slide_id: str
    size_bytes: int
    modified_at: str


class IndexCache:
    def __init__(self) -> None:
        self.generated_at = 0.0
        self.data: Dict = {}

    def build(self) -> Dict:
        slide_roots = resolve_slide_roots()
        feature_roots = resolve_feature_roots()

        slides: List[SlideFile] = []
        for root in slide_roots:
            for p in root.rglob("*"):
                if not p.is_file() or p.suffix.lower() not in SLIDE_EXTS:
                    continue
                st = p.stat()
                slides.append(SlideFile(
                    path=str(p),
                    root=str(root),
                    filename=p.name,
                    case_id=to_case_id(p.name),
                    slide_id=to_slide_id(p.name),
                    size_bytes=st.st_size,
                    modified_at=format_ts(st.st_mtime),
                ))

        features_by_key: Dict[str, List[FeatureFile]] = {}
        for root in feature_roots:
            for p in root.rglob("*"):
                if not p.is_file() or p.suffix.lower() not in FEATURE_EXTS:
                    continue
                st = p.stat()
                f = FeatureFile(
                    path=str(p),
                    root=str(root),
                    encoder=infer_encoder(p),
                    filename=p.name,
                    size_bytes=st.st_size,
                    modified_at=format_ts(st.st_mtime),
                )
                features_by_key.setdefault(to_slide_id(p.name), []).append(f)

        records = []
        for s in sorted(slides, key=lambda x: x.filename):
            matched = features_by_key.get(s.slide_id, [])
            records.append({
                "slide": s.__dict__,
                "feature_count": len(matched),
                "encoders": sorted({m.encoder for m in matched}),
                "features": [m.__dict__ for m in sorted(matched, key=lambda x: (x.encoder, x.filename))],
            })

        slide_keys = {s.slide_id for s in slides}
        unknown_feature_count = sum(len(items) for k, items in features_by_key.items() if k not in slide_keys)

        self.generated_at = time.time()
        self.data = {
            "generated_at": format_ts(self.generated_at),
            "slides_root": [str(p) for p in slide_roots],
            "features_root": [str(p) for p in feature_roots],
            "slide_count": len(slides),
            "matched_feature_count": sum(r["feature_count"] for r in records),
            "unmatched_feature_count": unknown_feature_count,
            "records": records,
        }
        return self.data


CACHE = IndexCache()
THUMB_CACHE: Dict[str, bytes] = {}
THUMB_CACHE_ORDER: List[str] = []
THUMB_CACHE_MAX = 128


def json_bytes(payload: Dict) -> bytes:
    return json.dumps(payload, ensure_ascii=True).encode("utf-8")


def is_within_roots(candidate: Path, roots: List[Path]) -> bool:
    for root in roots:
        try:
            candidate.relative_to(root)
            return True
        except ValueError:
            continue
    return False


def parse_size(val: str) -> int:
    try:
        size = int(val)
    except ValueError:
        return 256
    return max(96, min(size, 1024))


def cache_put(key: str, blob: bytes) -> None:
    if key in THUMB_CACHE:
        THUMB_CACHE[key] = blob
        return
    THUMB_CACHE[key] = blob
    THUMB_CACHE_ORDER.append(key)
    if len(THUMB_CACHE_ORDER) > THUMB_CACHE_MAX:
        oldest = THUMB_CACHE_ORDER.pop(0)
        THUMB_CACHE.pop(oldest, None)


def image_to_jpeg_bytes(img, quality: int = 85) -> bytes:
    if Image is None:
        raise RuntimeError("Pillow is required to encode thumbnails.")
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    elif img.mode == "L":
        img = img.convert("RGB")
    out = BytesIO()
    img.save(out, format="JPEG", quality=quality, optimize=True)
    return out.getvalue()


def placeholder_jpeg(size: int, title: str, subtitle: str) -> bytes:
    if Image is None or ImageDraw is None:
        return b""
    w = max(256, size)
    h = max(160, int(size * 0.7))
    img = Image.new("RGB", (w, h), color=(244, 247, 252))
    draw = ImageDraw.Draw(img)
    draw.rectangle((0, 0, w - 1, h - 1), outline=(201, 210, 225), width=2)
    draw.text((14, 12), title, fill=(40, 55, 85))
    draw.text((14, 36), subtitle, fill=(85, 100, 125))
    return image_to_jpeg_bytes(img)


def render_thumbnail(slide_path: Path, size: int) -> bytes:
    ext = slide_path.suffix.lower()

    if ext in {".jpg", ".jpeg", ".png"}:
        if Image is None:
            raise RuntimeError("Pillow is not available for image thumbnails.")
        with Image.open(slide_path) as img:
            img.thumbnail((size, size))
            return image_to_jpeg_bytes(img)

    if ext in {".svs", ".tif", ".tiff"} and tifffile is not None and Image is not None:
        try:
            with tifffile.TiffFile(str(slide_path)) as tf:  # type: ignore[attr-defined]
                if len(tf.pages) > 0:
                    idx = min(
                        range(len(tf.pages)),
                        key=lambda i: int(tf.pages[i].imagelength) * int(tf.pages[i].imagewidth),
                    )
                    arr = tf.pages[idx].asarray()
                    thumb = Image.fromarray(arr)
                    thumb.thumbnail((size, size))
                    return image_to_jpeg_bytes(thumb)
        except Exception:
            pass

    if openslide is None:
        raise RuntimeError("No backend available for this slide type.")

    with openslide.OpenSlide(str(slide_path)) as slide:  # type: ignore[attr-defined]
        assoc = getattr(slide, "associated_images", {})
        if assoc:
            for k in assoc.keys():
                if k.lower() == "thumbnail":
                    thumb = assoc[k]
                    if hasattr(thumb, "copy"):
                        thumb = thumb.copy()
                    if hasattr(thumb, "thumbnail"):
                        thumb.thumbnail((size, size))
                    return image_to_jpeg_bytes(thumb)
        return image_to_jpeg_bytes(slide.get_thumbnail((size, size)))


def _thumb_worker(slide_path_str: str, size: int, queue: Queue) -> None:
    try:
        queue.put(("ok", render_thumbnail(Path(slide_path_str), size)))
    except Exception as e:
        queue.put(("err", str(e)))


class Handler(BaseHTTPRequestHandler):
    def _send(self, status: int, body: bytes, content_type: str = "text/plain; charset=utf-8") -> None:
        try:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            # Client disconnected before response finished (common for canceled thumbnail requests).
            return

    def do_GET(self) -> None:
        raw_path, _, raw_query = self.path.partition("?")
        path = unquote(raw_path)
        qs = parse_qs(raw_query)

        if path == "/" or path == "/index.html":
            self._send(200, (Path(__file__).parent / "templates" / "index.html").read_bytes(), "text/html; charset=utf-8")
            return
        if path == "/static/style.css":
            self._send(200, (Path(__file__).parent / "static" / "style.css").read_bytes(), "text/css; charset=utf-8")
            return
        if path == "/static/app.js":
            self._send(200, (Path(__file__).parent / "static" / "app.js").read_bytes(), "application/javascript; charset=utf-8")
            return

        if path == "/api/index":
            payload = CACHE.build()
            payload["thumbnail_enabled"] = bool(Image is not None and (openslide is not None or tifffile is not None))
            self._send(200, json_bytes(payload), "application/json; charset=utf-8")
            return

        if path == "/api/health":
            self._send(200, json_bytes({"ok": True}), "application/json; charset=utf-8")
            return

        if path == "/api/thumbnail":
            req_path = (qs.get("path") or [""])[0]
            if not req_path:
                self._send(400, b"Missing query param: path")
                return
            candidate = Path(req_path).expanduser()
            if not candidate.is_absolute():
                self._send(400, b"Path must be absolute.")
                return
            if not candidate.exists() or not candidate.is_file():
                self._send(404, b"Slide path not found.")
                return
            if candidate.suffix.lower() not in SLIDE_EXTS:
                self._send(400, b"Unsupported slide extension.")
                return
            if not is_within_roots(candidate, resolve_slide_roots()):
                self._send(403, b"Path is outside configured slide roots.")
                return

            size = parse_size((qs.get("size") or ["256"])[0])
            allow_placeholder = (qs.get("fallback") or ["1"])[0] != "0"
            st = candidate.stat()
            cache_key = f"{candidate}:{st.st_mtime_ns}:{size}"
            blob = THUMB_CACHE.get(cache_key)
            if blob is None:
                q: Queue = Queue(maxsize=1)
                proc = Process(target=_thumb_worker, args=(str(candidate), size, q), daemon=True)
                proc.start()
                proc.join(THUMB_TIMEOUT_SEC)
                if proc.is_alive():
                    proc.terminate()
                    proc.join(timeout=1)
                    if allow_placeholder:
                        blob = placeholder_jpeg(size, "Preview unavailable", f"Timeout: {candidate.name}")
                        if blob:
                            cache_put(cache_key, blob)
                            self._send(200, blob, "image/jpeg")
                            return
                    self._send(504, f"Thumbnail timeout after {THUMB_TIMEOUT_SEC:.0f}s for: {candidate.name}".encode("utf-8"))
                    return
                if q.empty():
                    if allow_placeholder:
                        blob = placeholder_jpeg(size, "Preview unavailable", f"No worker output: {candidate.name}")
                        if blob:
                            cache_put(cache_key, blob)
                            self._send(200, blob, "image/jpeg")
                            return
                    self._send(500, b"Thumbnail error: worker returned no data.")
                    return
                status, payload = q.get()
                if status != "ok":
                    if allow_placeholder:
                        blob = placeholder_jpeg(size, "Preview unavailable", f"Render error: {candidate.name}")
                        if blob:
                            cache_put(cache_key, blob)
                            self._send(200, blob, "image/jpeg")
                            return
                    self._send(500, f"Thumbnail error: {payload}".encode("utf-8"))
                    return
                blob = payload
                cache_put(cache_key, blob)

            self._send(200, blob, "image/jpeg")
            return

        self._send(404, b"Not Found")

    def log_message(self, format: str, *args) -> None:
        return


def main() -> None:
    print(f"Starting WSI viewer at http://{APP_HOST}:{APP_PORT}")
    print("Set WSI_SLIDES_DIR and WSI_FEATURES_DIRS to customize data roots.")
    print("Thumbnail support uses tifffile+pillow (fast path) and/or openslide.")
    server = ThreadingHTTPServer((APP_HOST, APP_PORT), Handler)
    server.serve_forever()


if __name__ == "__main__":
    main()
