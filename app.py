#!/usr/bin/env python3
import csv
import hashlib
import json
import os
import re
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
from multiprocessing import Process, Queue
from pathlib import Path
from typing import Any, Dict, List, Optional
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
SAE_MANIFEST_PATH = Path(os.environ.get("WSI_SAE_MANIFEST", Path(__file__).parent / "config" / "sae_models.json"))
SAE_TILE_CACHE_ROOT = Path(os.environ.get("WSI_SAE_TILE_CACHE_ROOT", "/mnt/data/WSI_thumbs/sae_tiles"))

SLIDE_EXTS = {
    ".svs", ".tif", ".tiff", ".ndpi", ".mrxs", ".scn", ".vms", ".vmu", ".bif", ".jpg", ".jpeg", ".png"
}
FEATURE_EXTS = {
    ".h5", ".pt", ".pth", ".npy", ".npz", ".pkl", ".parquet"
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


def _to_float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None or v == "":
            return default
        return float(v)
    except Exception:
        return default


def _to_int(v: Any, default: int = 0) -> int:
    try:
        if v is None or v == "":
            return default
        return int(float(v))
    except Exception:
        return default


def _percentile(values: List[float], pct: float) -> float:
    if not values:
        return 0.0
    if pct <= 0:
        return float(min(values))
    if pct >= 100:
        return float(max(values))
    vals = sorted(values)
    rank = (len(vals) - 1) * (pct / 100.0)
    lo = int(rank)
    hi = min(lo + 1, len(vals) - 1)
    w = rank - lo
    return float(vals[lo] * (1.0 - w) + vals[hi] * w)


def _hhi(counts: List[int]) -> float:
    total = float(sum(counts))
    if total <= 0:
        return 0.0
    return float(sum((c / total) ** 2 for c in counts if c > 0))


def resolve_slide_roots() -> List[Path]:
    env = os.environ.get("WSI_SLIDES_DIR", "").strip()
    if env:
        return _existing_dirs([s.strip() for s in env.split(",")])
    return _existing_dirs([
        "/mnt/data/wsi_slides",
        "/mnt/data/WSI_slides",
        "/mnt/data/TCGA_slides",
    ])


def resolve_feature_roots() -> List[Path]:
    env = os.environ.get("WSI_FEATURES_DIRS", "").strip()
    if env:
        return _existing_dirs([s.strip() for s in env.split(",")])
    return _existing_dirs([
        "/mnt/data/wsi_features",
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


def infer_cohort(path: Path) -> str:
    parts = list(path.parts)
    parts_l = [p.lower() for p in parts]

    # Current layout: /.../TCGA/<COHORT>/slides/<file>
    for i, p in enumerate(parts_l):
        if p == "tcga" and i + 1 < len(parts):
            return parts[i + 1].upper()

    # Legacy layout: .../TCGA-ACC/<file>
    for part in parts:
        up = part.upper()
        if up.startswith("TCGA-") and len(up) > 5:
            return up[5:]

    return "UNKNOWN"


def infer_encoder(path: Path) -> str:
    parts = list(path.parts)
    parts_l = [p.lower() for p in parts]

    # Prefer explicit known encoder folder names anywhere in the path.
    known = {"seal", "uni2", "virchow2", "gigapath"}
    for p in parts_l:
        if p in known:
            return p

    # Handle layout: /.../<encoder>/TCGA/<COHORT>/<ext>/<file>
    for i, p in enumerate(parts_l):
        if p == "tcga" and i > 0:
            enc = parts_l[i - 1]
            if enc not in {"features", "wsi_features", "tcga_features", "wsi"}:
                return enc

    # Legacy layouts with "features_*" or "features-*"
    for i, part in enumerate(parts_l):
        if "feature" in part:
            original = parts[i]
            if "_" in original:
                return original.split("_", 1)[1].lower()
            if "-" in original:
                return original.split("-", 1)[1].lower()

    if len(parts_l) >= 2:
        parent = parts_l[-2]
        if parent not in {"h5", "pt", "npy", "npz"}:
            return parent
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
    cohort: str
    slide_id: str
    size_bytes: int
    modified_at: str


class IndexCache:
    def __init__(self) -> None:
        self.generated_at = 0.0
        self.data: Dict[str, Any] = {}

    def build(self) -> Dict[str, Any]:
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
                    cohort=infer_cohort(p),
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

        records: List[Dict[str, Any]] = []
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


class SaeCache:
    def __init__(self, manifest_path: Path) -> None:
        self.manifest_path = manifest_path
        self.data: Dict[str, Dict[str, Any]] = {}
        self.models: List[Dict[str, Any]] = []
        self.errors: List[str] = []
        self.loaded = False

    def _resolve_path(self, p: str) -> Path:
        cand = Path(p).expanduser()
        if cand.is_absolute():
            return cand
        return (self.manifest_path.parent / cand).resolve()

    def _build_slide_lookup(self, slide_root: Path) -> Dict[str, str]:
        lookup: Dict[str, str] = {}
        if not slide_root.exists():
            return lookup
        for p in slide_root.rglob("*"):
            if not p.is_file() or p.suffix.lower() not in SLIDE_EXTS:
                continue
            sid = to_slide_id(p.name)
            lookup.setdefault(sid, str(p))
        return lookup

    def _load_prototype_rows(self, csv_path: Path) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        if not csv_path.exists():
            return rows
        with csv_path.open(newline="") as f:
            reader = csv.DictReader(f)
            for r in reader:
                slide_key = (r.get("slide_key") or "").strip().upper()
                if not slide_key:
                    continue
                rows.append({
                    "latent_idx": _to_int(r.get("latent_idx"), -1),
                    "latent_group": (r.get("latent_group") or "unknown").strip(),
                    "prototype_rank": _to_int(r.get("prototype_rank"), 0),
                    "activation": _to_float(r.get("activation"), 0.0),
                    "attention": _to_float(r.get("attention"), 0.0),
                    "label": r.get("label"),
                    "pred": r.get("pred"),
                    "prob_pos": _to_float(r.get("prob_pos"), 0.0),
                    "case_id": (r.get("case_id") or to_case_id(slide_key)).upper(),
                    "slide_key": slide_key,
                    "tile_index": _to_int(r.get("tile_index"), -1),
                    "coord_x": _to_int(r.get("coord_x"), 0),
                    "coord_y": _to_int(r.get("coord_y"), 0),
                    "h5_path": r.get("h5_path") or "",
                })
        return rows

    def _load_attention_rows(self, csv_path: Path) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        if not csv_path.exists():
            return rows
        with csv_path.open(newline="") as f:
            reader = csv.DictReader(f)
            for r in reader:
                slide_key = (r.get("slide_key") or "").strip().upper()
                if not slide_key:
                    continue
                rows.append({
                    "split_name": r.get("split_name") or "",
                    "data_split": r.get("data_split") or "",
                    "label": r.get("label"),
                    "pred": r.get("pred"),
                    "prob_pos": _to_float(r.get("prob_pos"), 0.0),
                    "case_id": (r.get("case_id") or to_case_id(slide_key)).upper(),
                    "slide_key": slide_key,
                    "tile_rank": _to_int(r.get("tile_rank"), 0),
                    "tile_index": _to_int(r.get("tile_index"), -1),
                    "attention": _to_float(r.get("attention"), 0.0),
                    "coord_x": _to_int(r.get("coord_x"), 0),
                    "coord_y": _to_int(r.get("coord_y"), 0),
                    "h5_path": r.get("h5_path") or "",
                })
        return rows

    def _load_json_file(self, path: Optional[Path]) -> Dict[str, Any]:
        if path is None or not path.exists():
            return {}
        try:
            raw = json.loads(path.read_text())
            return raw if isinstance(raw, dict) else {}
        except Exception:
            return {}

    def _infer_analytics_dir(self, entry: Dict[str, Any], rep_csv: Path) -> Optional[Path]:
        raw = str(entry.get("analytics_dir", "")).strip()
        if raw:
            path = self._resolve_path(raw)
            return path if path.exists() else None
        bundle_dir = rep_csv.parent
        bundle_name = bundle_dir.name
        if bundle_name.startswith("representatives_"):
            candidate = bundle_dir.parent / bundle_name.replace("representatives_", "analytics_", 1)
            if candidate.exists():
                return candidate
        return None

    def _resolve_analytics_artifact(
        self,
        *,
        entry: Dict[str, Any],
        analytics_dir: Optional[Path],
        plot_manifest: Dict[str, Any],
        entry_key: str,
        artifact_key: str,
        default_name: str,
    ) -> Optional[Path]:
        raw = str(entry.get(entry_key, "")).strip()
        if raw:
            return self._resolve_path(raw)
        if analytics_dir is None:
            return None
        artifacts = plot_manifest.get("artifacts", {}) if isinstance(plot_manifest, dict) else {}
        rel = str(artifacts.get(artifact_key, "")).strip()
        if rel:
            return analytics_dir / rel
        default_path = analytics_dir / default_name
        return default_path if default_path.exists() else None

    def _load_representative_rows(self, csv_path: Path) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        if not csv_path.exists():
            return rows
        with csv_path.open(newline="") as f:
            reader = csv.DictReader(f)
            for r in reader:
                slide_key = (r.get("slide_key") or "").strip().upper()
                if not slide_key:
                    continue
                rows.append({
                    "run_name": r.get("run_name") or "",
                    "stage": r.get("stage") or "",
                    "dataset": r.get("dataset") or "",
                    "encoder": r.get("encoder") or "",
                    "data_split": r.get("data_split") or "",
                    "latent_strategy": r.get("latent_strategy") or "",
                    "latent_idx": _to_int(r.get("latent_idx"), -1),
                    "latent_group": (r.get("latent_group") or "unknown").strip(),
                    "representative_method": r.get("representative_method") or "",
                    "row_kind": r.get("row_kind") or "",
                    "method_rank": _to_int(r.get("method_rank"), 0),
                    "source_rank": _to_int(r.get("source_rank"), 0),
                    "case_id": (r.get("case_id") or to_case_id(slide_key)).upper(),
                    "slide_key": slide_key,
                    "cohort": r.get("cohort") or "",
                    "tile_index": _to_int(r.get("tile_index"), -1),
                    "coord_x": _to_int(r.get("coord_x"), 0),
                    "coord_y": _to_int(r.get("coord_y"), 0),
                    "feature_relpath": r.get("feature_relpath") or "",
                    "feature_h5_name": r.get("feature_h5_name") or "",
                    "legacy_h5_path": r.get("legacy_h5_path") or "",
                    "activation": _to_float(r.get("activation"), 0.0),
                    "attention": 0.0,
                    "method_score": _to_float(r.get("method_score"), 0.0),
                    "slide_support_count": _to_int(r.get("slide_support_count"), 0),
                    "slide_max_activation": _to_float(r.get("slide_max_activation"), 0.0),
                    "slide_mean_activation": _to_float(r.get("slide_mean_activation"), 0.0),
                    "max_activation_global": _to_float(r.get("max_activation_global"), 0.0),
                    "variance_global": _to_float(r.get("variance_global"), 0.0),
                    "sparsity_score_global": _to_float(r.get("sparsity_score_global"), 0.0),
                })
        return rows

    def _load_latent_summary_rows(self, csv_path: Optional[Path]) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        if csv_path is None or not csv_path.exists():
            return rows
        with csv_path.open(newline="") as f:
            reader = csv.DictReader(f)
            for r in reader:
                rows.append({
                    "run_name": r.get("run_name") or "",
                    "stage": r.get("stage") or "",
                    "dataset": r.get("dataset") or "",
                    "encoder": r.get("encoder") or "",
                    "data_split": r.get("data_split") or "",
                    "latent_strategy": r.get("latent_strategy") or "",
                    "latent_idx": _to_int(r.get("latent_idx"), -1),
                    "latent_group": (r.get("latent_group") or "unknown").strip(),
                    "count": _to_int(r.get("support_tile_count"), 0),
                    "max_activation": _to_float(r.get("activation_max"), 0.0),
                    "mean_activation": _to_float(r.get("activation_mean"), 0.0),
                    "unique_slides": _to_int(r.get("unique_slide_count"), 0),
                    "unique_cases": _to_int(r.get("unique_case_count"), 0),
                    "activation_p50": _to_float(r.get("activation_p50"), 0.0),
                    "activation_p90": _to_float(r.get("activation_p90"), 0.0),
                    "max_activation_global": _to_float(r.get("max_activation_global"), 0.0),
                    "variance_global": _to_float(r.get("variance_global"), 0.0),
                    "sparsity_score_global": _to_float(r.get("sparsity_score_global"), 0.0),
                })
        rows.sort(key=lambda x: (x["latent_strategy"], x["max_activation"], x["count"]), reverse=True)
        return rows

    def _load_all_latent_metrics_rows(self, csv_path: Optional[Path]) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        if csv_path is None or not csv_path.exists():
            return rows
        with csv_path.open(newline="") as f:
            reader = csv.DictReader(f)
            for r in reader:
                rows.append({
                    "latent_idx": _to_int(r.get("latent_idx"), -1),
                    "is_alive": _to_int(r.get("is_alive"), 0),
                    "selected_strategies": [s for s in str(r.get("selected_strategies", "")).split(",") if s],
                    "max_activation_global": _to_float(r.get("max_activation_global"), 0.0),
                    "variance_global": _to_float(r.get("variance_global"), 0.0),
                    "sparsity_score_global": _to_float(r.get("sparsity_score_global"), 0.0),
                    "slide_prevalence": _to_float(r.get("slide_prevalence"), 0.0),
                    "case_prevalence": _to_float(r.get("case_prevalence"), 0.0),
                    "num_tiles_positive": _to_int(r.get("num_tiles_positive"), 0),
                    "mean_positive_activation": _to_float(r.get("mean_positive_activation"), 0.0),
                    "max_activation_seen": _to_float(r.get("max_activation_seen"), 0.0),
                    "cohort_entropy": _to_float(r.get("cohort_entropy"), 0.0),
                    "top_cohort": r.get("top_cohort") or "",
                    "top_cohort_share": _to_float(r.get("top_cohort_share"), 0.0),
                })
        rows.sort(key=lambda x: (x["selected_strategies"] != [], x["max_activation_seen"], x["slide_prevalence"]), reverse=True)
        return rows

    def _load_selected_latent_slide_stats_rows(self, csv_path: Optional[Path]) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        if csv_path is None or not csv_path.exists():
            return rows
        with csv_path.open(newline="") as f:
            reader = csv.DictReader(f)
            for r in reader:
                slide_key = (r.get("slide_key") or "").strip().upper()
                if not slide_key:
                    continue
                rows.append({
                    "latent_strategy": r.get("latent_strategy") or "",
                    "latent_idx": _to_int(r.get("latent_idx"), -1),
                    "latent_group": (r.get("latent_group") or "unknown").strip(),
                    "case_id": (r.get("case_id") or to_case_id(slide_key)).upper(),
                    "slide_key": slide_key,
                    "cohort": r.get("cohort") or "",
                    "slide_max_activation": _to_float(r.get("slide_max_activation"), 0.0),
                    "slide_mean_positive_activation": _to_float(r.get("slide_mean_positive_activation"), 0.0),
                    "positive_tile_count": _to_int(r.get("positive_tile_count"), 0),
                    "total_tiles_seen": _to_int(r.get("total_tiles_seen"), 0),
                    "fires": _to_int(r.get("fires"), 0),
                })
        rows.sort(key=lambda x: (x["latent_strategy"], x["latent_idx"], x["slide_max_activation"], x["positive_tile_count"]), reverse=True)
        return rows

    def _load_cohort_enrichment_rows(self, csv_path: Optional[Path]) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        if csv_path is None or not csv_path.exists():
            return rows
        with csv_path.open(newline="") as f:
            reader = csv.DictReader(f)
            for r in reader:
                rows.append({
                    "latent_strategy": r.get("latent_strategy") or "",
                    "latent_idx": _to_int(r.get("latent_idx"), -1),
                    "latent_group": (r.get("latent_group") or "unknown").strip(),
                    "cohort": r.get("cohort") or "",
                    "slides_in_cohort": _to_int(r.get("slides_in_cohort"), 0),
                    "slides_with_activation": _to_int(r.get("slides_with_activation"), 0),
                    "prevalence_in_cohort": _to_float(r.get("prevalence_in_cohort"), 0.0),
                    "prevalence_global": _to_float(r.get("prevalence_global"), 0.0),
                    "enrichment_ratio": _to_float(r.get("enrichment_ratio"), 0.0),
                })
        rows.sort(key=lambda x: (x["latent_strategy"], x["latent_idx"], x["enrichment_ratio"]), reverse=True)
        return rows

    def _load_latent_umap_rows(self, csv_path: Optional[Path]) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        if csv_path is None or not csv_path.exists():
            return rows
        with csv_path.open(newline="") as f:
            reader = csv.DictReader(f)
            for r in reader:
                rows.append({
                    "latent_idx": _to_int(r.get("latent_idx"), -1),
                    "umap_x": _to_float(r.get("umap_x"), 0.0),
                    "umap_y": _to_float(r.get("umap_y"), 0.0),
                    "is_alive": _to_int(r.get("is_alive"), 0),
                    "selected_strategies": [s for s in str(r.get("selected_strategies", "")).split(",") if s],
                    "max_activation_global": _to_float(r.get("max_activation_global"), 0.0),
                    "variance_global": _to_float(r.get("variance_global"), 0.0),
                    "sparsity_score_global": _to_float(r.get("sparsity_score_global"), 0.0),
                })
        return rows

    def _load_histograms(self, json_path: Optional[Path]) -> Dict[tuple[str, int], Dict[str, Any]]:
        out: Dict[tuple[str, int], Dict[str, Any]] = {}
        payload = self._load_json_file(json_path)
        rows = payload.get("rows", []) if isinstance(payload, dict) else []
        if not isinstance(rows, list):
            return out
        for row in rows:
            if not isinstance(row, dict):
                continue
            key = (str(row.get("latent_strategy", "")), _to_int(row.get("latent_idx"), -1))
            out[key] = {
                "latent_strategy": key[0],
                "latent_idx": key[1],
                "latent_group": (row.get("latent_group") or "unknown").strip(),
                "bin_edges": row.get("bin_edges") if isinstance(row.get("bin_edges"), list) else [],
                "counts": row.get("counts") if isinstance(row.get("counts"), list) else [],
                "n_slides": _to_int(row.get("n_slides"), 0),
                "n_firing_slides": _to_int(row.get("n_firing_slides"), 0),
                "max_activation": _to_float(row.get("max_activation"), 0.0),
                "histogram_unit": payload.get("histogram_unit", "slide_max_activation") if isinstance(payload, dict) else "slide_max_activation",
            }
        return out

    def _build_analytics_data(self, entry: Dict[str, Any], rep_csv: Path) -> Dict[str, Any]:
        analytics_dir = self._infer_analytics_dir(entry, rep_csv)
        plot_manifest_path = None
        raw_plot_manifest = str(entry.get("plot_manifest_json", "")).strip()
        if raw_plot_manifest:
            plot_manifest_path = self._resolve_path(raw_plot_manifest)
        elif analytics_dir is not None and (analytics_dir / "plot_manifest.json").exists():
            plot_manifest_path = analytics_dir / "plot_manifest.json"
        plot_manifest = self._load_json_file(plot_manifest_path)

        analytics_summary_path = self._resolve_analytics_artifact(
            entry=entry,
            analytics_dir=analytics_dir,
            plot_manifest=plot_manifest,
            entry_key="analytics_summary_json",
            artifact_key="analytics_summary_json",
            default_name="analytics_summary.json",
        )
        analytics_summary = self._load_json_file(analytics_summary_path)
        all_latent_metrics_csv = self._resolve_analytics_artifact(
            entry=entry,
            analytics_dir=analytics_dir,
            plot_manifest=plot_manifest,
            entry_key="all_latent_metrics_csv",
            artifact_key="all_latent_metrics_csv",
            default_name="all_latent_metrics.csv",
        )
        selected_latent_slide_stats_csv = self._resolve_analytics_artifact(
            entry=entry,
            analytics_dir=analytics_dir,
            plot_manifest=plot_manifest,
            entry_key="selected_latent_slide_stats_csv",
            artifact_key="selected_latent_slide_stats_csv",
            default_name="selected_latent_slide_stats.csv",
        )
        cohort_enrichment_csv = self._resolve_analytics_artifact(
            entry=entry,
            analytics_dir=analytics_dir,
            plot_manifest=plot_manifest,
            entry_key="cohort_enrichment_csv",
            artifact_key="cohort_enrichment_csv",
            default_name="cohort_enrichment.csv",
        )
        latent_umap_csv = self._resolve_analytics_artifact(
            entry=entry,
            analytics_dir=analytics_dir,
            plot_manifest=plot_manifest,
            entry_key="latent_umap_csv",
            artifact_key="latent_umap_csv",
            default_name="latent_umap.csv",
        )
        histograms_json = self._resolve_analytics_artifact(
            entry=entry,
            analytics_dir=analytics_dir,
            plot_manifest=plot_manifest,
            entry_key="selected_latent_histograms_json",
            artifact_key="selected_latent_histograms_json",
            default_name="selected_latent_histograms.json",
        )
        case_label_enrichment_csv = self._resolve_analytics_artifact(
            entry=entry,
            analytics_dir=analytics_dir,
            plot_manifest=plot_manifest,
            entry_key="case_label_enrichment_csv",
            artifact_key="case_label_enrichment_csv",
            default_name="case_label_enrichment.csv",
        )

        all_latent_metrics = self._load_all_latent_metrics_rows(all_latent_metrics_csv)
        selected_slide_stats = self._load_selected_latent_slide_stats_rows(selected_latent_slide_stats_csv)
        cohort_enrichment = self._load_cohort_enrichment_rows(cohort_enrichment_csv)
        latent_umap = self._load_latent_umap_rows(latent_umap_csv)
        histograms_by_key = self._load_histograms(histograms_json)

        slide_stats_by_key: Dict[tuple[str, int], List[Dict[str, Any]]] = defaultdict(list)
        for row in selected_slide_stats:
            slide_stats_by_key[(str(row["latent_strategy"]), int(row["latent_idx"]))].append(row)
        for key in slide_stats_by_key:
            slide_stats_by_key[key].sort(key=lambda x: (x["slide_max_activation"], x["positive_tile_count"]), reverse=True)

        cohort_by_key: Dict[tuple[str, int], List[Dict[str, Any]]] = defaultdict(list)
        for row in cohort_enrichment:
            cohort_by_key[(str(row["latent_strategy"]), int(row["latent_idx"]))].append(row)
        for key in cohort_by_key:
            cohort_by_key[key].sort(key=lambda x: (x["enrichment_ratio"], x["prevalence_in_cohort"]), reverse=True)

        metrics_by_idx = {int(row["latent_idx"]): row for row in all_latent_metrics}
        available = bool(all_latent_metrics or latent_umap or histograms_by_key or analytics_summary)
        return {
            "available": available,
            "analytics_dir": str(analytics_dir) if analytics_dir is not None else "",
            "plot_manifest": plot_manifest,
            "summary": analytics_summary,
            "all_latent_metrics": all_latent_metrics,
            "metrics_by_idx": metrics_by_idx,
            "selected_latent_slide_stats": selected_slide_stats,
            "slide_stats_by_key": slide_stats_by_key,
            "cohort_enrichment": cohort_enrichment,
            "cohort_by_key": cohort_by_key,
            "latent_umap": latent_umap,
            "histograms_by_key": histograms_by_key,
            "paths": {
                "plot_manifest_json": str(plot_manifest_path) if plot_manifest_path is not None else "",
                "analytics_summary_json": str(analytics_summary_path) if analytics_summary_path is not None else "",
                "all_latent_metrics_csv": str(all_latent_metrics_csv) if all_latent_metrics_csv is not None else "",
                "selected_latent_slide_stats_csv": str(selected_latent_slide_stats_csv) if selected_latent_slide_stats_csv is not None else "",
                "cohort_enrichment_csv": str(cohort_enrichment_csv) if cohort_enrichment_csv is not None else "",
                "latent_umap_csv": str(latent_umap_csv) if latent_umap_csv is not None else "",
                "selected_latent_histograms_json": str(histograms_json) if histograms_json is not None else "",
                "case_label_enrichment_csv": str(case_label_enrichment_csv) if case_label_enrichment_csv is not None and case_label_enrichment_csv.exists() else "",
            },
        }

    def _dedupe_support_rows(self, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        seen: set[tuple[Any, ...]] = set()
        out: List[Dict[str, Any]] = []
        for row in rows:
            key = (
                row.get("latent_strategy", ""),
                row.get("latent_idx", -1),
                row.get("slide_key", ""),
                row.get("tile_index", -1),
                row.get("coord_x", 0),
                row.get("coord_y", 0),
            )
            if key in seen:
                continue
            seen.add(key)
            out.append(row)
        return out

    def _build_representative_model_data(self, entry: Dict[str, Any]) -> Dict[str, Any]:
        model_id = entry["model_id"]
        model_name = entry.get("model_name", model_id)
        encoder = entry.get("encoder", "unknown")
        dataset = entry.get("dataset", "")
        tile_size = _to_int(entry.get("tile_size", 256), 256)
        slides_root = self._resolve_path(entry["slides_root"])
        rep_csv = self._resolve_path(entry["representative_latents_csv"])
        support_csv = self._resolve_path(entry["representative_support_tiles_csv"])
        latent_summary_csv_raw = str(entry.get("latent_summary_csv", "")).strip()
        summary_json_raw = str(entry.get("bundle_summary_json", "")).strip()
        latent_summary_csv = self._resolve_path(latent_summary_csv_raw) if latent_summary_csv_raw else None
        summary_json = self._resolve_path(summary_json_raw) if summary_json_raw else None

        representative_rows = self._load_representative_rows(rep_csv)
        support_rows = self._load_representative_rows(support_csv)
        dedup_support_rows = self._dedupe_support_rows(support_rows)
        latent_rows = self._load_latent_summary_rows(latent_summary_csv)
        summary = self._load_json_file(summary_json)
        analytics = self._build_analytics_data(entry, rep_csv)
        slide_lookup = self._build_slide_lookup(slides_root)

        representative_methods: Dict[str, List[Dict[str, Any]]] = {}
        available_methods = sorted({str(r.get("representative_method", "")) for r in representative_rows if str(r.get("representative_method", ""))})
        available_strategies = sorted({str(r.get("latent_strategy", "")) for r in representative_rows if str(r.get("latent_strategy", ""))})

        for method in available_methods:
            rows = [r for r in representative_rows if r.get("representative_method") == method]
            rows.sort(key=lambda x: (x.get("method_score", 0.0), x.get("activation", 0.0), x.get("latent_idx", -1)), reverse=True)
            representative_methods[method] = rows

        support_by_slide: Dict[str, List[Dict[str, Any]]] = {}
        for row in support_rows:
            support_by_slide.setdefault(str(row["slide_key"]), []).append(row)

        slide_summaries: List[Dict[str, Any]] = []
        slide_rows_map: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for row in dedup_support_rows:
            slide_rows_map[str(row["slide_key"])].append(row)
        for slide_key, slide_rows in slide_rows_map.items():
            any_row = slide_rows[0]
            slide_summaries.append({
                "slide_key": slide_key,
                "case_id": any_row.get("case_id", to_case_id(slide_key)),
                "prototype_tiles": len(slide_rows),
                "attention_tiles": 0,
                "top_activation": max((r["activation"] for r in slide_rows), default=0.0),
                "top_attention": 0.0,
                "unique_latents": len({(r["latent_strategy"], r["latent_idx"]) for r in slide_rows}),
                "slide_path": slide_lookup.get(slide_key, ""),
            })
        slide_summaries.sort(key=lambda x: (x["top_activation"], x["prototype_tiles"]), reverse=True)

        if not latent_rows:
            latent_map: Dict[tuple[str, int], Dict[str, Any]] = {}
            for row in dedup_support_rows:
                key = (str(row.get("latent_strategy", "")), int(row.get("latent_idx", -1)))
                if key not in latent_map:
                    latent_map[key] = {
                        "latent_strategy": key[0],
                        "latent_idx": key[1],
                        "latent_group": row.get("latent_group", "unknown"),
                        "count": 0,
                        "max_activation": 0.0,
                        "mean_activation": 0.0,
                        "unique_slides": set(),
                    }
                ag = latent_map[key]
                ag["count"] += 1
                ag["max_activation"] = max(ag["max_activation"], row["activation"])
                ag["mean_activation"] += row["activation"]
                ag["unique_slides"].add(row["slide_key"])
            latent_rows = []
            for ag in latent_map.values():
                count = int(ag["count"])
                latent_rows.append({
                    "latent_strategy": ag["latent_strategy"],
                    "latent_idx": ag["latent_idx"],
                    "latent_group": ag["latent_group"],
                    "count": count,
                    "max_activation": ag["max_activation"],
                    "mean_activation": (ag["mean_activation"] / count) if count > 0 else 0.0,
                    "unique_slides": len(ag["unique_slides"]),
                })
            latent_rows.sort(key=lambda x: (x["latent_strategy"], x["max_activation"], x["count"]), reverse=True)

        activations = [r["activation"] for r in dedup_support_rows]
        activation_p50 = _percentile(activations, 50.0)
        activation_p95 = _percentile(activations, 95.0)
        rep_slide_keys = {str(r.get("slide_key", "")) for r in representative_rows if str(r.get("slide_key", ""))}
        latent_counts = [int(r.get("count", 0)) for r in latent_rows]
        rep_mean_unique_slides = (
            sum(_to_int(r.get("unique_slides"), 0) for r in latent_rows) / len(latent_rows)
        ) if latent_rows else 0.0
        if not summary:
            summary = {
                "model_id": model_id,
                "model_name": model_name,
                "encoder": encoder,
                "dataset": dataset,
                "total_slides": len(slide_summaries),
                "total_latents": len(latent_rows),
                "total_representative_rows": len(representative_rows),
                "total_support_rows": len(support_rows),
                "max_activation": max(activations) if activations else 0.0,
                "mean_activation": (sum(activations) / len(activations)) if activations else 0.0,
            }
        summary["model_id"] = model_id
        summary["model_name"] = model_name
        summary["encoder"] = encoder
        summary["dataset"] = dataset
        summary["total_slides"] = summary.get("total_slides", len(slide_summaries))
        summary["total_latents"] = summary.get("total_latents", len(latent_rows))
        summary["total_representative_rows"] = summary.get("total_representative_rows", len(representative_rows))
        summary["total_support_rows"] = summary.get("total_support_rows", len(support_rows))
        summary["total_prototype_rows"] = summary.get("total_prototype_rows", len(support_rows))
        summary["total_attention_rows"] = summary.get("total_attention_rows", 0)
        summary["activation_p50"] = summary.get("activation_p50", activation_p50)
        summary["activation_p95"] = summary.get("activation_p95", activation_p95)
        summary["activation_tail_ratio"] = summary.get("activation_tail_ratio", (activation_p95 / activation_p50) if activation_p50 > 0 else 0.0)
        summary["rep_method"] = summary.get("rep_method", "precomputed")
        summary["rep_latents"] = summary.get("rep_latents", len(representative_rows))
        summary["rep_slide_coverage"] = summary.get("rep_slide_coverage", (100.0 * len(rep_slide_keys) / len(slide_summaries)) if slide_summaries else 0.0)
        summary["rep_mean_unique_slides_per_latent"] = summary.get("rep_mean_unique_slides_per_latent", rep_mean_unique_slides)
        summary["latent_concentration_hhi"] = summary.get("latent_concentration_hhi", _hhi(latent_counts))
        summary["available_representative_methods"] = available_methods
        summary["available_latent_strategies"] = available_strategies
        summary["tile_size"] = tile_size
        summary["analytics_available"] = bool(analytics.get("available"))
        analytics_summary = analytics.get("summary", {})
        if isinstance(analytics_summary, dict):
            for field in [
                "total_cases",
                "total_tiles_seen",
                "alive_latents",
                "selected_latent_union",
                "selected_strategies",
                "histogram_unit",
                "hist_bins",
                "umap_source",
                "umap_backend",
            ]:
                if field in analytics_summary:
                    summary[field] = analytics_summary[field]

        return {
            "config": {
                "model_id": model_id,
                "model_name": model_name,
                "encoder": encoder,
                "dataset": dataset,
                "slides_root": str(slides_root),
                "representative_latents_csv": str(rep_csv),
                "representative_support_tiles_csv": str(support_csv),
                "latent_summary_csv": str(latent_summary_csv) if latent_summary_csv is not None else "",
                "bundle_summary_json": str(summary_json) if summary_json is not None else "",
                "plot_manifest_json": analytics["paths"].get("plot_manifest_json", ""),
                "analytics_summary_json": analytics["paths"].get("analytics_summary_json", ""),
                "all_latent_metrics_csv": analytics["paths"].get("all_latent_metrics_csv", ""),
                "selected_latent_slide_stats_csv": analytics["paths"].get("selected_latent_slide_stats_csv", ""),
                "cohort_enrichment_csv": analytics["paths"].get("cohort_enrichment_csv", ""),
                "latent_umap_csv": analytics["paths"].get("latent_umap_csv", ""),
                "selected_latent_histograms_json": analytics["paths"].get("selected_latent_histograms_json", ""),
                "case_label_enrichment_csv": analytics["paths"].get("case_label_enrichment_csv", ""),
                "tile_size": tile_size,
            },
            "summary": summary,
            "slide_lookup": slide_lookup,
            "representative_rows": representative_rows,
            "support_rows": support_rows,
            "support_by_slide": support_by_slide,
            "slide_summaries": slide_summaries,
            "latent_rows": latent_rows,
            "representative_methods": representative_methods,
            "analytics": analytics,
        }

    def _build_model_data(self, entry: Dict[str, Any]) -> Dict[str, Any]:
        if str(entry.get("representative_latents_csv", "")).strip():
            return self._build_representative_model_data(entry)

        model_id = entry["model_id"]
        model_name = entry.get("model_name", model_id)
        encoder = entry.get("encoder", "unknown")
        dataset = entry.get("dataset", "")
        tile_size = _to_int(entry.get("tile_size", 256), 256)
        slides_root = self._resolve_path(entry["slides_root"])
        proto_csv = self._resolve_path(entry["prototype_tiles_csv"])
        attn_csv_raw = str(entry.get("top_attention_tiles_csv", "")).strip()
        attn_csv = self._resolve_path(attn_csv_raw) if attn_csv_raw else None

        proto_rows = self._load_prototype_rows(proto_csv)
        attn_rows = self._load_attention_rows(attn_csv) if attn_csv is not None else []
        slide_lookup = self._build_slide_lookup(slides_root)

        proto_by_slide: Dict[str, List[Dict[str, Any]]] = {}
        attn_by_slide: Dict[str, List[Dict[str, Any]]] = {}
        latent_aggr: Dict[int, Dict[str, Any]] = {}
        latent_group_seen: Dict[str, set] = {}

        for row in proto_rows:
            proto_by_slide.setdefault(row["slide_key"], []).append(row)
            lidx = row["latent_idx"]
            if lidx not in latent_aggr:
                latent_aggr[lidx] = {
                    "latent_idx": lidx,
                    "latent_group": row.get("latent_group", "unknown"),
                    "count": 0,
                    "max_activation": 0.0,
                    "sum_activation": 0.0,
                    "slides": set(),
                }
            ag = latent_aggr[lidx]
            ag["count"] += 1
            ag["sum_activation"] += row["activation"]
            ag["max_activation"] = max(ag["max_activation"], row["activation"])
            ag["slides"].add(row["slide_key"])
            latent_group_seen.setdefault(row.get("latent_group", "unknown"), set()).add(lidx)

        for row in attn_rows:
            attn_by_slide.setdefault(row["slide_key"], []).append(row)

        slide_keys = set(proto_by_slide.keys()) | set(attn_by_slide.keys())
        slide_summaries: List[Dict[str, Any]] = []
        for slide_key in slide_keys:
            prs = proto_by_slide.get(slide_key, [])
            ars = attn_by_slide.get(slide_key, [])
            any_row = prs[0] if prs else (ars[0] if ars else {})
            slide_summaries.append({
                "slide_key": slide_key,
                "case_id": any_row.get("case_id", to_case_id(slide_key)),
                "prototype_tiles": len(prs),
                "attention_tiles": len(ars),
                "top_activation": max((r["activation"] for r in prs), default=0.0),
                "top_attention": max(([r["attention"] for r in prs] + [r["attention"] for r in ars]), default=0.0),
                "unique_latents": len({r["latent_idx"] for r in prs}),
                "slide_path": slide_lookup.get(slide_key, ""),
            })

        slide_summaries.sort(key=lambda x: (x["top_activation"], x["top_attention"], x["prototype_tiles"]), reverse=True)
        slide_summary_by_key = {s["slide_key"]: s for s in slide_summaries}

        latent_rows: List[Dict[str, Any]] = []
        for lidx, ag in latent_aggr.items():
            cnt = ag["count"]
            latent_rows.append({
                "latent_idx": lidx,
                "latent_group": ag["latent_group"],
                "count": cnt,
                "max_activation": ag["max_activation"],
                "mean_activation": (ag["sum_activation"] / cnt) if cnt > 0 else 0.0,
                "unique_slides": len(ag["slides"]),
            })
        latent_rows.sort(key=lambda x: (x["max_activation"], x["count"]), reverse=True)
        latent_stats_by_idx = {r["latent_idx"]: r for r in latent_rows}

        # Representative latent rows by the top-activation prototype tile.
        # Additional methods can be added later while keeping the same response shape.
        best_proto_by_latent: Dict[int, Dict[str, Any]] = {}
        for row in proto_rows:
            lidx = row["latent_idx"]
            prev = best_proto_by_latent.get(lidx)
            if prev is None or row["activation"] > prev["activation"]:
                best_proto_by_latent[lidx] = row

        representative_max_activation: List[Dict[str, Any]] = []
        for lidx, row in best_proto_by_latent.items():
            stats = latent_stats_by_idx.get(lidx, {})
            slide_key = row["slide_key"]
            slide_summary = slide_summary_by_key.get(slide_key, {})
            representative_max_activation.append({
                "latent_idx": lidx,
                "latent_group": row.get("latent_group", "unknown"),
                "method": "max_activation",
                "score": row["activation"],
                "max_activation": row["activation"],
                "mean_activation": _to_float(stats.get("mean_activation"), 0.0),
                "total_tiles": _to_int(stats.get("count"), 0),
                "unique_slides": _to_int(stats.get("unique_slides"), 0),
                "slide_key": slide_key,
                "case_id": row.get("case_id") or to_case_id(slide_key),
                "slide_path": slide_lookup.get(slide_key, ""),
                "slide_top_activation": _to_float(slide_summary.get("top_activation"), 0.0),
                "tile_index": row["tile_index"],
                "coord_x": row["coord_x"],
                "coord_y": row["coord_y"],
            })
        representative_max_activation.sort(
            key=lambda x: (x["score"], x["unique_slides"], x["total_tiles"]),
            reverse=True,
        )

        activation_values = [r["activation"] for r in proto_rows]
        act_p50 = _percentile(activation_values, 50.0)
        act_p95 = _percentile(activation_values, 95.0)
        rep_slide_keys = {r["slide_key"] for r in representative_max_activation}
        rep_slide_coverage = (100.0 * len(rep_slide_keys) / len(slide_keys)) if slide_keys else 0.0
        rep_mean_unique_slides = (
            sum(r["unique_slides"] for r in representative_max_activation) / len(representative_max_activation)
        ) if representative_max_activation else 0.0
        latent_counts = [r["count"] for r in latent_rows]

        summary = {
            "model_id": model_id,
            "model_name": model_name,
            "encoder": encoder,
            "dataset": dataset,
            "total_slides": len(slide_keys),
            "total_latents": len(latent_rows),
            "total_prototype_rows": len(proto_rows),
            "total_attention_rows": len(attn_rows),
            "max_activation": max((r["activation"] for r in proto_rows), default=0.0),
            "mean_activation": (sum(r["activation"] for r in proto_rows) / len(proto_rows)) if proto_rows else 0.0,
            "activation_p50": act_p50,
            "activation_p95": act_p95,
            "activation_tail_ratio": (act_p95 / act_p50) if act_p50 > 0 else 0.0,
            "rep_method": "max_activation",
            "rep_latents": len(representative_max_activation),
            "rep_slide_coverage": rep_slide_coverage,
            "rep_mean_unique_slides_per_latent": rep_mean_unique_slides,
            "latent_concentration_hhi": _hhi(latent_counts),
            "latent_group_counts": {k: len(v) for k, v in sorted(latent_group_seen.items(), key=lambda x: x[0])},
            "tile_size": tile_size,
        }

        return {
            "config": {
                "model_id": model_id,
                "model_name": model_name,
                "encoder": encoder,
                "dataset": dataset,
                "slides_root": str(slides_root),
                "prototype_tiles_csv": str(proto_csv),
                "top_attention_tiles_csv": str(attn_csv) if attn_csv is not None else "",
                "tile_size": tile_size,
            },
            "summary": summary,
            "slide_lookup": slide_lookup,
            "proto_rows": proto_rows,
            "attn_rows": attn_rows,
            "proto_by_slide": proto_by_slide,
            "attn_by_slide": attn_by_slide,
            "slide_summaries": slide_summaries,
            "latent_rows": latent_rows,
            "representative_methods": {
                "max_activation": representative_max_activation,
            },
        }

    def load(self, force: bool = False) -> None:
        if self.loaded and not force:
            return

        self.data = {}
        self.models = []
        self.errors = []

        if not self.manifest_path.exists():
            self.errors.append(f"Manifest not found: {self.manifest_path}")
            self.loaded = True
            return

        try:
            raw = json.loads(self.manifest_path.read_text())
            entries = raw.get("models", []) if isinstance(raw, dict) else raw
            if not isinstance(entries, list):
                raise ValueError("Manifest must contain a list of models.")
        except Exception as e:
            self.errors.append(f"Manifest parse error: {e}")
            self.loaded = True
            return

        for entry in entries:
            if not isinstance(entry, dict):
                self.errors.append("Skipped non-object model entry in manifest.")
                continue
            required = ["model_id", "slides_root"]
            for req in required:
                if req not in entry:
                    self.errors.append(f"Skipped model missing '{req}': {entry}")
                    entry = None
                    break
            if entry is None:
                continue
            has_representative_bundle = bool(str(entry.get("representative_latents_csv", "")).strip())
            if has_representative_bundle:
                for req in ["representative_latents_csv", "representative_support_tiles_csv"]:
                    if not str(entry.get(req, "")).strip():
                        self.errors.append(f"Skipped representative model missing '{req}': {entry}")
                        entry = None
                        break
            elif not str(entry.get("prototype_tiles_csv", "")).strip():
                self.errors.append(f"Skipped model missing 'prototype_tiles_csv': {entry}")
                entry = None
            if entry is None:
                continue

            model_id = str(entry["model_id"])
            try:
                model_data = self._build_model_data(entry)
                self.data[model_id] = model_data
                self.models.append({
                    "model_id": model_id,
                    "model_name": model_data["config"]["model_name"],
                    "encoder": model_data["config"]["encoder"],
                    "dataset": model_data["config"].get("dataset", ""),
                })
            except Exception as e:
                self.errors.append(f"Model '{model_id}' load failed: {e}")

        self.models.sort(key=lambda x: (x["encoder"], x["model_name"]))
        self.loaded = True

    def get_model(self, model_id: str) -> Optional[Dict[str, Any]]:
        self.load()
        return self.data.get(model_id)


CACHE = IndexCache()
SAE_CACHE = SaeCache(SAE_MANIFEST_PATH)
THUMB_CACHE: Dict[str, bytes] = {}
THUMB_CACHE_ORDER: List[str] = []
THUMB_CACHE_MAX = 128


def json_bytes(payload: Dict[str, Any]) -> bytes:
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
    h = w
    img = Image.new("RGB", (w, h), color=(244, 247, 252))
    draw = ImageDraw.Draw(img)
    draw.rectangle((0, 0, w - 1, h - 1), outline=(201, 210, 225), width=2)
    draw.text((14, 14), title, fill=(40, 55, 85))
    draw.text((14, 40), subtitle, fill=(85, 100, 125))
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


def render_sae_tile(slide_path: Path, x: int, y: int, size: int) -> bytes:
    ext = slide_path.suffix.lower()

    if ext in {".jpg", ".jpeg", ".png"}:
        if Image is None:
            raise RuntimeError("Pillow is not available for image tiles.")
        with Image.open(slide_path) as img:
            box = (x, y, x + size, y + size)
            tile = img.crop(box)
            return image_to_jpeg_bytes(tile)

    if openslide is None:
        raise RuntimeError("OpenSlide required for WSI tile crops.")

    with openslide.OpenSlide(str(slide_path)) as slide:  # type: ignore[attr-defined]
        region = slide.read_region((x, y), 0, (size, size)).convert("RGB")
        return image_to_jpeg_bytes(region)


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
            return

    def _send_json(self, status: int, payload: Dict[str, Any]) -> None:
        self._send(status, json_bytes(payload), "application/json; charset=utf-8")

    def _parse_int(self, qs: Dict[str, List[str]], key: str, default: int) -> int:
        return _to_int((qs.get(key) or [str(default)])[0], default)

    def _serve_qc_thumbnail(self, qs: Dict[str, List[str]]) -> None:
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

    def _sae_models(self) -> None:
        SAE_CACHE.load()
        encoders = sorted({m["encoder"] for m in SAE_CACHE.models})
        self._send_json(200, {
            "models": SAE_CACHE.models,
            "encoders": encoders,
            "errors": SAE_CACHE.errors,
        })

    def _sae_summary(self, qs: Dict[str, List[str]]) -> None:
        model_id = (qs.get("model_id") or [""])[0]
        model = SAE_CACHE.get_model(model_id)
        if not model:
            self._send_json(404, {"error": f"Unknown model_id: {model_id}"})
            return
        self._send_json(200, {
            "summary": model["summary"],
            "config": model["config"],
        })

    def _sae_analytics(self, qs: Dict[str, List[str]]) -> None:
        model_id = (qs.get("model_id") or [""])[0]
        model = SAE_CACHE.get_model(model_id)
        if not model:
            self._send_json(404, {"error": f"Unknown model_id: {model_id}"})
            return
        analytics = model.get("analytics", {})
        self._send_json(200, {
            "available": bool(analytics.get("available")),
            "summary": analytics.get("summary", {}),
            "plot_manifest": analytics.get("plot_manifest", {}),
            "all_latent_metrics": analytics.get("all_latent_metrics", []),
            "latent_umap": analytics.get("latent_umap", []),
        })

    def _sae_latents(self, qs: Dict[str, List[str]]) -> None:
        model_id = (qs.get("model_id") or [""])[0]
        model = SAE_CACHE.get_model(model_id)
        if not model:
            self._send_json(404, {"error": f"Unknown model_id: {model_id}"})
            return

        group = (qs.get("group") or [""])[0]
        limit = max(1, min(self._parse_int(qs, "limit", 100), 2000))

        rows = model["latent_rows"]
        if group:
            rows = [r for r in rows if str(r.get("latent_group", "")).lower() == group.lower()]

        self._send_json(200, {
            "rows": rows[:limit],
            "total": len(rows),
        })

    def _sae_slides(self, qs: Dict[str, List[str]]) -> None:
        model_id = (qs.get("model_id") or [""])[0]
        model = SAE_CACHE.get_model(model_id)
        if not model:
            self._send_json(404, {"error": f"Unknown model_id: {model_id}"})
            return

        q = (qs.get("q") or [""])[0].strip().lower()
        limit = max(1, min(self._parse_int(qs, "limit", 400), 5000))

        rows = model["slide_summaries"]
        if q:
            rows = [r for r in rows if q in r["slide_key"].lower() or q in r["case_id"].lower()]

        self._send_json(200, {
            "rows": rows[:limit],
            "total": len(rows),
        })

    def _sae_representatives(self, qs: Dict[str, List[str]]) -> None:
        model_id = (qs.get("model_id") or [""])[0]
        model = SAE_CACHE.get_model(model_id)
        if not model:
            self._send_json(404, {"error": f"Unknown model_id: {model_id}"})
            return

        method = (qs.get("method") or ["max_activation"])[0].strip().lower() or "max_activation"
        strategy = (qs.get("strategy") or [""])[0].strip().lower()
        group = (qs.get("group") or [""])[0].strip().lower()
        limit = max(1, min(self._parse_int(qs, "limit", 24), 256))

        methods = model.get("representative_methods", {})
        if method not in methods:
            self._send_json(400, {
                "error": f"Unsupported representative method: {method}",
                "available_methods": sorted(methods.keys()),
            })
            return

        rows = methods.get(method, [])
        if strategy:
            rows = [r for r in rows if str(r.get("latent_strategy", "")).strip().lower() == strategy]
        if group:
            rows = [r for r in rows if str(r.get("latent_group", "")).strip().lower() == group]

        self._send_json(200, {
            "method": method,
            "strategy": strategy,
            "available_methods": sorted(methods.keys()),
            "available_strategies": list(model.get("summary", {}).get("available_latent_strategies", [])),
            "rows": rows[:limit],
            "total": len(rows),
        })

    def _sae_slide_detail(self, qs: Dict[str, List[str]]) -> None:
        model_id = (qs.get("model_id") or [""])[0]
        slide_key = (qs.get("slide_key") or [""])[0].strip().upper()
        method = (qs.get("method") or [""])[0].strip().lower()
        strategy = (qs.get("strategy") or [""])[0].strip().lower()

        model = SAE_CACHE.get_model(model_id)
        if not model:
            self._send_json(404, {"error": f"Unknown model_id: {model_id}"})
            return
        if not slide_key:
            self._send_json(400, {"error": "Missing slide_key"})
            return

        summary = next((s for s in model["slide_summaries"] if s["slide_key"] == slide_key), None)
        if not summary:
            self._send_json(404, {"error": f"Slide not found in model: {slide_key}"})
            return

        if "support_by_slide" in model:
            prs = model["support_by_slide"].get(slide_key, [])
            if method:
                prs = [r for r in prs if str(r.get("representative_method", "")).strip().lower() == method]
            if strategy:
                prs = [r for r in prs if str(r.get("latent_strategy", "")).strip().lower() == strategy]
            ars = []
        else:
            prs = model["proto_by_slide"].get(slide_key, [])
            ars = model["attn_by_slide"].get(slide_key, [])

        latent_map: Dict[int, Dict[str, Any]] = {}
        for r in prs:
            key = (str(r.get("latent_strategy", "")), int(r["latent_idx"]))
            if key not in latent_map:
                latent_map[key] = {
                    "latent_idx": int(r["latent_idx"]),
                    "latent_strategy": r.get("latent_strategy", ""),
                    "latent_group": r.get("latent_group", "unknown"),
                    "count": 0,
                    "max_activation": 0.0,
                    "max_attention": 0.0,
                }
            ag = latent_map[key]
            ag["count"] += 1
            ag["max_activation"] = max(ag["max_activation"], r["activation"])
            ag["max_attention"] = max(ag["max_attention"], r["attention"])

        top_latents = sorted(latent_map.values(), key=lambda x: (x["max_activation"], x["count"]), reverse=True)[:40]

        top_tiles: List[Dict[str, Any]] = []
        if "support_by_slide" in model:
            ordered_prs = sorted(prs, key=lambda x: (x.get("method_rank", 0), -x.get("activation", 0.0)))
        else:
            ordered_prs = sorted(prs, key=lambda x: x["activation"], reverse=True)
        for r in ordered_prs[:120]:
            top_tiles.append({
                "source": "support" if "support_by_slide" in model else "prototype",
                "latent_idx": r["latent_idx"],
                "latent_strategy": r.get("latent_strategy", ""),
                "representative_method": r.get("representative_method", ""),
                "activation": r["activation"],
                "attention": r["attention"],
                "tile_index": r["tile_index"],
                "coord_x": r["coord_x"],
                "coord_y": r["coord_y"],
            })
        if not top_tiles:
            for r in sorted(ars, key=lambda x: x["attention"], reverse=True)[:120]:
                top_tiles.append({
                    "source": "attention",
                    "latent_idx": None,
                    "activation": 0.0,
                    "attention": r["attention"],
                    "tile_index": r["tile_index"],
                    "coord_x": r["coord_x"],
                    "coord_y": r["coord_y"],
                })

        self._send_json(200, {
            "slide": summary,
            "top_latents": top_latents,
            "tiles": top_tiles,
            "tile_size": model["summary"].get("tile_size", 256),
        })

    def _sae_latent_detail(self, qs: Dict[str, List[str]]) -> None:
        model_id = (qs.get("model_id") or [""])[0]
        latent_idx = self._parse_int(qs, "latent_idx", -1)
        strategy = (qs.get("strategy") or [""])[0].strip().lower()
        method = (qs.get("method") or ["max_activation"])[0].strip().lower() or "max_activation"

        model = SAE_CACHE.get_model(model_id)
        if not model:
            self._send_json(404, {"error": f"Unknown model_id: {model_id}"})
            return
        if latent_idx < 0:
            self._send_json(400, {"error": "Missing or invalid latent_idx"})
            return

        rep_rows = [r for r in model.get("representative_rows", []) if int(r.get("latent_idx", -1)) == latent_idx]
        if strategy:
            rep_rows = [r for r in rep_rows if str(r.get("latent_strategy", "")).strip().lower() == strategy]
        if not strategy and rep_rows:
            strategy = str(rep_rows[0].get("latent_strategy", "")).strip().lower()

        support_rows = [r for r in model.get("support_rows", []) if int(r.get("latent_idx", -1)) == latent_idx]
        if strategy:
            support_rows = [r for r in support_rows if str(r.get("latent_strategy", "")).strip().lower() == strategy]
        support_preview = support_rows
        if method:
            method_preview = [r for r in support_rows if str(r.get("representative_method", "")).strip().lower() == method]
            if method_preview:
                support_preview = method_preview
        support_preview = sorted(
            support_preview,
            key=lambda x: (x.get("method_rank", 0), -x.get("activation", 0.0), x.get("slide_key", "")),
        )[:24]

        representatives = sorted(
            rep_rows,
            key=lambda x: (str(x.get("representative_method", "")), -x.get("method_score", 0.0)),
        )
        summary_row = next(
            (
                r for r in model.get("latent_rows", [])
                if int(r.get("latent_idx", -1)) == latent_idx
                and (not strategy or str(r.get("latent_strategy", "")).strip().lower() == strategy)
            ),
            {},
        )

        analytics = model.get("analytics", {})
        metric_row = analytics.get("metrics_by_idx", {}).get(latent_idx, {})
        hist_row = analytics.get("histograms_by_key", {}).get((strategy, latent_idx), {})
        cohort_rows = analytics.get("cohort_by_key", {}).get((strategy, latent_idx), [])
        slide_stats = analytics.get("slide_stats_by_key", {}).get((strategy, latent_idx), [])

        default_slide_key = ""
        if representatives:
            matching_method = next((r for r in representatives if str(r.get("representative_method", "")).strip().lower() == method), None)
            default_slide_key = str((matching_method or representatives[0]).get("slide_key", ""))
        elif support_preview:
            default_slide_key = str(support_preview[0].get("slide_key", ""))

        self._send_json(200, {
            "latent_idx": latent_idx,
            "strategy": strategy,
            "method": method,
            "summary_row": summary_row,
            "metric_row": metric_row,
            "representatives": representatives,
            "support_preview": support_preview,
            "histogram": hist_row,
            "cohort_rows": cohort_rows[:24],
            "slide_stats": slide_stats[:120],
            "default_slide_key": default_slide_key,
            "available_methods": sorted({str(r.get("representative_method", "")) for r in rep_rows if str(r.get("representative_method", ""))}),
            "available_strategies": sorted({str(r.get("latent_strategy", "")) for r in model.get("representative_rows", []) if int(r.get("latent_idx", -1)) == latent_idx and str(r.get("latent_strategy", ""))}),
        })

    def _sae_tile(self, qs: Dict[str, List[str]]) -> None:
        model_id = (qs.get("model_id") or [""])[0]
        slide_key = (qs.get("slide_key") or [""])[0].strip().upper()
        x = self._parse_int(qs, "x", 0)
        y = self._parse_int(qs, "y", 0)
        size = parse_size((qs.get("size") or ["256"])[0])
        tile_index = self._parse_int(qs, "tile_index", -1)

        model = SAE_CACHE.get_model(model_id)
        if not model:
            self._send_json(404, {"error": f"Unknown model_id: {model_id}"})
            return
        if not slide_key:
            self._send_json(400, {"error": "Missing slide_key"})
            return

        slide_path = model["slide_lookup"].get(slide_key)
        if not slide_path:
            blob = placeholder_jpeg(size, "Tile unavailable", f"No slide path: {slide_key}")
            self._send(200, blob if blob else b"", "image/jpeg")
            return

        src = Path(slide_path)
        if not src.exists():
            blob = placeholder_jpeg(size, "Tile unavailable", f"Slide not found: {slide_key}")
            self._send(200, blob if blob else b"", "image/jpeg")
            return

        key = f"{slide_path}|{slide_key}|{x}|{y}|{size}|{tile_index}"
        digest = hashlib.sha1(key.encode("utf-8")).hexdigest()
        cache_dir = SAE_TILE_CACHE_ROOT / model_id / slide_key
        cache_file = cache_dir / f"{digest}.jpg"

        try:
            if cache_file.exists():
                self._send(200, cache_file.read_bytes(), "image/jpeg")
                return

            blob = render_sae_tile(src, x, y, size)
            cache_dir.mkdir(parents=True, exist_ok=True)
            cache_file.write_bytes(blob)
            self._send(200, blob, "image/jpeg")
            return
        except Exception as e:
            blob = placeholder_jpeg(size, "Tile unavailable", f"{type(e).__name__}: {slide_key}")
            if blob:
                self._send(200, blob, "image/jpeg")
                return
            self._send_json(500, {"error": f"Tile render failed: {e}"})

    def do_GET(self) -> None:
        raw_path, _, raw_query = self.path.partition("?")
        path = unquote(raw_path)
        qs = parse_qs(raw_query)

        if path in {"/", "/index.html"}:
            self._send(200, (Path(__file__).parent / "templates" / "index.html").read_bytes(), "text/html; charset=utf-8")
            return
        if path in {"/sae", "/sae.html"}:
            self._send(200, (Path(__file__).parent / "templates" / "sae.html").read_bytes(), "text/html; charset=utf-8")
            return

        if path == "/static/style.css":
            self._send(200, (Path(__file__).parent / "static" / "style.css").read_bytes(), "text/css; charset=utf-8")
            return
        if path == "/static/app.js":
            self._send(200, (Path(__file__).parent / "static" / "app.js").read_bytes(), "application/javascript; charset=utf-8")
            return
        if path == "/static/sae.css":
            self._send(200, (Path(__file__).parent / "static" / "sae.css").read_bytes(), "text/css; charset=utf-8")
            return
        if path == "/static/sae.js":
            self._send(200, (Path(__file__).parent / "static" / "sae.js").read_bytes(), "application/javascript; charset=utf-8")
            return

        if path == "/api/index":
            payload = CACHE.build()
            payload["thumbnail_enabled"] = bool(Image is not None and (openslide is not None or tifffile is not None))
            self._send_json(200, payload)
            return

        if path == "/api/health":
            self._send_json(200, {"ok": True})
            return

        if path == "/api/thumbnail":
            self._serve_qc_thumbnail(qs)
            return

        if path == "/api/sae/models":
            self._sae_models()
            return
        if path == "/api/sae/summary":
            self._sae_summary(qs)
            return
        if path == "/api/sae/analytics":
            self._sae_analytics(qs)
            return
        if path == "/api/sae/latents":
            self._sae_latents(qs)
            return
        if path == "/api/sae/slides":
            self._sae_slides(qs)
            return
        if path == "/api/sae/representatives":
            self._sae_representatives(qs)
            return
        if path == "/api/sae/latent":
            self._sae_latent_detail(qs)
            return
        if path == "/api/sae/slide":
            self._sae_slide_detail(qs)
            return
        if path == "/api/sae/tile":
            self._sae_tile(qs)
            return

        self._send(404, b"Not Found")

    def log_message(self, format: str, *args) -> None:
        return


def main() -> None:
    print(f"Starting WSI viewer at http://{APP_HOST}:{APP_PORT}")
    print("Set WSI_SLIDES_DIR and WSI_FEATURES_DIRS to customize data roots.")
    print("SAE route: /sae, manifest:", SAE_MANIFEST_PATH)
    print("Thumbnail support uses tifffile+pillow (fast path) and/or openslide.")
    server = ThreadingHTTPServer((APP_HOST, APP_PORT), Handler)
    server.serve_forever()


if __name__ == "__main__":
    main()
