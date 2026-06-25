"""Direct model vision helpers.

The rest of the MCP returns *file paths* for every visual artifact. A
vision-capable model that drives this server (Claude, GPT-4o, Gemini, ...)
therefore never actually *sees* the drawing it is working on through the tool
result — it only receives a path and has to rely on a separate, agent-side VLM
call. That breaks the perceive→act→verify loop the model is built for.

This module turns any CAD raster artifact (an exported view, an overlay, a
prepared image-trace source, or an arbitrary local image) into a *model-viewable*
PNG/JPEG and reports enough metadata for ``server.py`` to attach it as an MCP
``ImageContent`` block. The model then sees the image directly in the tool
result.

Like the rest of ``cad_understanding`` this module has **no MCP dependency**:
``server.py`` owns the ``Image`` content wrapping. Functions here never raise for
expected failure modes (missing file, missing converter, missing Pillow); they
return a structured dict so the tool layer can always answer.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.cad_database import CADDatabase

from .common import current_scope, ensure_understanding_schema, get_db
from .image_trace import _latest_trace, _load_trace
from .result import ToolResult, error_result, ok_result
from .view_grounding import _load_snapshot, _try_convert_wmf_to_raster

# Long edge that keeps a CAD raster legible while staying within the input
# budget of current vision models (Claude tops out around 1568px on the long
# edge before downsampling server-side anyway).
DEFAULT_MAX_DIM = 1568
MIN_MAX_DIM = 64
MAX_MAX_DIM = 4096

# Hard ceiling on the raw bytes we will base64-inline into a single tool result.
# Aligns with typical vision-API per-image limits and bounds context cost when
# Pillow is unavailable (no downscaling) or a caller asks for a large max_dim.
MAX_EMBED_BYTES = 5_000_000

# Suffixes a model can ingest as-is via MCP ImageContent.
EMBEDDABLE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
# Raster suffixes we can transcode to PNG with Pillow.
TRANSCODE_SUFFIXES = {".bmp", ".tif", ".tiff"}


def _clamp_max_dim(max_dim: Any) -> int:
    try:
        value = int(max_dim)
    except (TypeError, ValueError):
        value = DEFAULT_MAX_DIM
    return max(MIN_MAX_DIM, min(value, MAX_MAX_DIM))


def _pillow():
    """Return the Pillow ``Image`` module or ``None`` when unavailable."""
    try:
        from PIL import Image as PILImage  # type: ignore

        return PILImage
    except Exception:  # pragma: no cover - environment without the visual extra
        return None


def _empty_prep(original: str) -> Dict[str, Any]:
    return {
        "ok": False,
        "embeddable": False,
        "image_path": "",
        "original_path": original,
        "mime_type": "",
        "source_format": Path(original).suffix.lower().lstrip(".") if original else "",
        "width": 0,
        "height": 0,
        "downscaled": False,
        "file_bytes": 0,
        "warnings": [],
        "reason": "",
    }


def _mime_for(suffix: str) -> str:
    return {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
    }.get(suffix.lower(), "image/png")


def prepare_model_image(path: str, max_dim: int = DEFAULT_MAX_DIM) -> Dict[str, Any]:
    """Resolve any local image into a model-viewable PNG/JPEG.

    Pipeline: resolve & exist-check → convert WMF→PNG (reusing the shared
    converter) → transcode BMP/TIFF→PNG with Pillow → downscale oversized
    rasters to ``max_dim`` on the long edge. The returned dict tells the tool
    layer whether ``image_path`` can be embedded as MCP image content.
    """
    prep = _empty_prep(str(path or ""))
    max_dim = _clamp_max_dim(max_dim)

    if not path or not str(path).strip():
        prep["reason"] = "No image path was provided."
        return prep

    try:
        source = Path(str(path)).expanduser()
        if not source.exists():
            prep["reason"] = f"Image file not found: {source}"
            return prep
        if not source.is_file():
            prep["reason"] = f"Path is not a file: {source}"
            return prep
    except (OSError, ValueError) as exc:
        # Illegal path (NUL byte, bad characters, too long, ...) must not raise.
        prep["reason"] = f"Invalid image path: {exc}"
        return prep

    prep["original_path"] = str(source)
    prep["ok"] = True
    suffix = source.suffix.lower()
    prep["source_format"] = suffix.lstrip(".")
    working = source

    # 1) WMF (AutoCAD's native COM export) — convert to PNG if a renderer exists.
    if suffix == ".wmf":
        converted = None
        try:
            converted = _try_convert_wmf_to_raster(source)
        except Exception as exc:  # pragma: no cover - converter edge cases
            prep["warnings"].append(f"WMF→PNG conversion error: {exc}")
        if not converted:
            prep["reason"] = (
                "AutoCAD exported WMF and no WMF→PNG converter is installed "
                "(ImageMagick, wand, Inkscape, or LibreOffice). Install one to "
                "let the model see this view, or export a PDF/PNG instead."
            )
            return prep
        working = Path(converted)
        suffix = working.suffix.lower()
        prep["warnings"].append("Converted WMF to PNG for model viewing.")

    pil = _pillow()

    # 2) Non-embeddable raster (BMP/TIFF) — transcode to PNG when Pillow exists.
    if suffix in TRANSCODE_SUFFIXES:
        if pil is None:
            prep["reason"] = (
                f"{suffix} images need Pillow to become model-viewable. "
                "Install the 'visual' extra (pip install -e \".[visual]\")."
            )
            return prep
        try:
            png_path = working.with_name(f"{working.stem}_view.png")
            with pil.open(working) as img:
                img.convert("RGB").save(png_path, format="PNG")
            working = png_path
            suffix = ".png"
            prep["warnings"].append(f"Transcoded {prep['source_format']} to PNG for model viewing.")
        except Exception as exc:
            prep["reason"] = f"Failed to transcode image to PNG: {exc}"
            return prep

    if suffix not in EMBEDDABLE_SUFFIXES:
        prep["reason"] = (
            f"Unsupported image type for model vision: {suffix or '(none)'}. "
            "Supported: png, jpg, jpeg, gif, webp (plus auto-converted wmf, bmp, tiff)."
        )
        return prep

    # 3) Downscale oversized rasters so the model sees the whole view within
    #    its input budget. Without Pillow we still embed the original.
    width = height = 0
    downscaled = False
    if pil is not None:
        try:
            with pil.open(working) as img:
                width, height = img.size
                long_edge = max(width, height)
                if long_edge > max_dim:
                    scale = max_dim / float(long_edge)
                    new_size = (max(1, int(width * scale)), max(1, int(height * scale)))
                    resized = img.convert("RGB") if img.mode not in {"RGB", "RGBA", "L"} else img
                    resized = resized.resize(new_size, pil.LANCZOS)
                    scaled_path = working.with_name(f"{working.stem}_view{max_dim}.png")
                    resized.save(scaled_path, format="PNG")
                    working = scaled_path
                    suffix = ".png"
                    width, height = new_size
                    downscaled = True
                    prep["warnings"].append(
                        f"Downscaled to {width}x{height} (long edge {max_dim}px) for the model."
                    )
        except Exception as exc:  # pragma: no cover - Pillow read edge cases
            prep["warnings"].append(f"Could not measure/resize image: {exc}")
    else:
        prep["warnings"].append(
            "Pillow not installed; embedding the image at full resolution. "
            "Install the 'visual' extra for automatic downscaling."
        )

    try:
        file_bytes = working.stat().st_size
    except OSError:
        file_bytes = 0

    if file_bytes > MAX_EMBED_BYTES:
        prep["file_bytes"] = int(file_bytes)
        prep["reason"] = (
            f"Image is {file_bytes // 1024} KB, over the {MAX_EMBED_BYTES // 1024} KB "
            "inline limit. Install the 'visual' extra (Pillow) for automatic "
            "downscaling, or pass a smaller max_dim."
        )
        return prep

    prep.update(
        {
            "embeddable": True,
            "image_path": str(working),
            "mime_type": _mime_for(suffix),
            "width": int(width),
            "height": int(height),
            "downscaled": downscaled,
            "file_bytes": int(file_bytes),
        }
    )
    return prep


def view_image(path: str, max_dim: int = DEFAULT_MAX_DIM,
               label: str = "") -> ToolResult:
    """ToolResult describing an arbitrary local image for direct model viewing."""
    prep = prepare_model_image(path, max_dim=max_dim)
    if not prep["ok"]:
        return error_result(
            prep.get("reason") or "Could not read the image.",
            data={"vision": prep},
            next_tools=["check_runtime_environment"],
        )
    message = "Image is ready for the model to view." if prep["embeddable"] else (
        prep.get("reason") or "Image could not be made model-viewable."
    )
    if label:
        message = f"{label}: {message}"
    return ok_result(
        message,
        data={"vision": prep, "label": label},
        warnings=prep.get("warnings", []),
    )


def _latest_snapshot_id(database: CADDatabase) -> Optional[str]:
    ensure_understanding_schema(database)
    scope = current_scope(database)
    with database._conn() as conn:
        row = conn.execute(
            """
            SELECT snapshot_id
            FROM cad_view_snapshots
            WHERE workspace_id = ? AND drawing_id = ?
              AND conversation_id = ? AND thread_id = ?
            ORDER BY created_at DESC, rowid DESC
            LIMIT 1
            """,
            (
                scope["workspace_id"],
                scope["drawing_id"],
                scope["conversation_id"],
                scope["thread_id"],
            ),
        ).fetchone()
    return row["snapshot_id"] if row else None


def _snapshot_image_candidates(snapshot: Dict[str, Any], which: str) -> List[Dict[str, str]]:
    which = (which or "auto").strip().lower()
    clean = snapshot.get("clean_image_path") or snapshot.get("image_path") or ""
    overlay = snapshot.get("overlay_image_path") or ""
    if which == "clean":
        wanted = [("clean", clean)]
    elif which == "overlay":
        wanted = [("overlay", overlay)]
    elif which == "both":
        wanted = [("clean", clean), ("overlay", overlay)]
    else:  # auto → prefer overlay (numbered IDs help grounding), fall back to clean
        wanted = [("overlay", overlay), ("clean", clean)] if overlay else [("clean", clean)]
    return [{"role": role, "path": path} for role, path in wanted if path]


def resolve_snapshot_images(snapshot_id: Optional[str] = None,
                            which: str = "auto",
                            max_dim: int = DEFAULT_MAX_DIM,
                            database: Optional[CADDatabase] = None) -> ToolResult:
    """Resolve a prior view snapshot's image(s) into model-viewable payloads."""
    db = get_db(database)
    try:
        if not snapshot_id:
            snapshot_id = _latest_snapshot_id(db)
            if not snapshot_id:
                return error_result(
                    "No view snapshot exists yet. Run export_view_image_with_mapping "
                    "or render_drawing_view first.",
                    next_tools=["render_drawing_view", "export_view_image_with_mapping"],
                )
        snapshot = _load_snapshot(db, snapshot_id)
    except Exception as exc:  # locked/unavailable DB must not raise out of a tool
        return error_result(
            f"Could not read view snapshots: {exc}",
            next_tools=["check_runtime_environment"],
        )
    if not snapshot:
        return error_result(
            f"Unknown view snapshot: {snapshot_id}",
            next_tools=["render_drawing_view", "export_view_image_with_mapping"],
        )

    candidates = _snapshot_image_candidates(snapshot, which)
    if not candidates:
        return error_result(
            f"Snapshot {snapshot_id} has no raster image for which='{which}'.",
            data={"snapshot_id": snapshot_id},
            next_tools=["render_drawing_view"],
        )

    images: List[Dict[str, Any]] = []
    warnings: List[str] = []
    for candidate in candidates:
        prep = prepare_model_image(candidate["path"], max_dim=max_dim)
        prep["role"] = candidate["role"]
        images.append(prep)
        warnings.extend(prep.get("warnings", []))

    embeddable = [img for img in images if img.get("embeddable")]
    if not embeddable:
        reason = next((img.get("reason") for img in images if img.get("reason")), "")
        return error_result(
            reason or "Snapshot images could not be made model-viewable.",
            data={"snapshot_id": snapshot_id, "images": images},
            warnings=sorted(set(warnings)),
            next_tools=["check_runtime_environment"],
        )

    return ok_result(
        f"Snapshot {snapshot_id}: {len(embeddable)} image(s) ready for the model.",
        data={
            "snapshot_id": snapshot_id,
            "which": (which or "auto").strip().lower(),
            "vlm_ready": bool(snapshot.get("vlm_ready")),
            "images": images,
        },
        handles=snapshot.get("visible_handles", []),
        warnings=sorted(set(warnings)),
        next_tools=["ground_vlm_region", "ground_vlm_overlay_id", "explain_entity"],
    )


_TRACE_ROLE_SUFFIX = {
    "normalized": "_normalized",
    "high_contrast": "_high_contrast",
    "edges": "_edges",
}


def _trace_artifact_path(trace: Dict[str, Any], role: str) -> str:
    role = (role or "normalized").strip().lower()
    normalized = trace.get("normalized_image_path") or ""
    if role in {"source", "original"}:
        return trace.get("image_path") or normalized
    if role == "normalized" or not normalized:
        return normalized or (trace.get("image_path") or "")
    norm_path = Path(normalized)
    # Artifacts are written next to the normalized image: <id>_normalized.png,
    # <id>_high_contrast.png, <id>_edges.png. Derive from the normalized stem.
    base_stem = norm_path.stem
    if base_stem.endswith("_normalized"):
        base_stem = base_stem[: -len("_normalized")]
    suffix = _TRACE_ROLE_SUFFIX.get(role)
    if not suffix:
        return normalized
    candidate = norm_path.with_name(f"{base_stem}{suffix}.png")
    return str(candidate) if candidate.exists() else normalized


def resolve_trace_image(image_id: Optional[str] = None,
                        role: str = "normalized",
                        max_dim: int = DEFAULT_MAX_DIM,
                        database: Optional[CADDatabase] = None) -> ToolResult:
    """Resolve a prepared image-trace artifact into a model-viewable payload."""
    db = get_db(database)
    try:
        trace = _load_trace(db, image_id) if image_id else _latest_trace(db)
    except Exception as exc:  # locked/unavailable DB must not raise out of a tool
        return error_result(
            f"Could not read image traces: {exc}",
            next_tools=["check_runtime_environment"],
        )
    if not trace:
        return error_result(
            "No image trace found. Run prepare_image_trace first."
            if not image_id else f"Unknown image trace: {image_id}",
            next_tools=["prepare_image_trace"],
        )
    artifact_path = _trace_artifact_path(trace, role)
    prep = prepare_model_image(artifact_path, max_dim=max_dim)
    prep["role"] = (role or "normalized").strip().lower()
    if not prep["embeddable"]:
        return error_result(
            prep.get("reason") or "Trace image could not be made model-viewable.",
            data={"image_id": trace.get("image_id"), "vision": prep},
            warnings=prep.get("warnings", []),
            next_tools=["check_runtime_environment"],
        )
    return ok_result(
        f"Image trace {trace.get('image_id')} ({prep['role']}) ready for the model.",
        data={
            "image_id": trace.get("image_id"),
            "domain": trace.get("domain", ""),
            "vision": prep,
        },
        warnings=prep.get("warnings", []),
        next_tools=["copy_drawing_from_image", "validate_image_drawing_spec"],
    )


def vision_capabilities() -> Dict[str, Any]:
    """Report whether the server can show images to the model, and how."""
    pil = _pillow()
    converters = {
        "imagemagick": _which_any(["magick", "convert"]),
        "inkscape": _which_any(["inkscape"]),
        "libreoffice": _which_any(["soffice", "libreoffice"]),
        "wand": _module_available("wand"),
        "pillow": pil is not None,
    }
    wmf_ready = any(
        converters[name] for name in ("imagemagick", "inkscape", "libreoffice", "wand")
    )
    return {
        "direct_vision": True,
        "embeddable_formats": sorted(EMBEDDABLE_SUFFIXES),
        "auto_converted_formats": sorted({".wmf"} | TRANSCODE_SUFFIXES),
        "default_max_dim": DEFAULT_MAX_DIM,
        "pillow_installed": pil is not None,
        "downscaling_available": pil is not None,
        "wmf_to_png_available": wmf_ready,
        "converters": converters,
        "vision_tools": [
            "view_image",
            "get_snapshot_image",
            "render_drawing_view",
            "get_trace_source_image",
        ],
        "workflow": (
            "Vision-capable models can SEE drawings directly: call render_drawing_view "
            "(export + see in one step) or get_snapshot_image to review the current "
            "drawing, view_image for any reference file, and get_trace_source_image to "
            "see a prepared trace source. Perceive → act by handle → re-render → verify."
        ),
    }


def _which_any(names: List[str]) -> bool:
    import shutil

    return any(shutil.which(name) for name in names)


def _module_available(name: str) -> bool:
    import importlib.util

    try:
        return importlib.util.find_spec(name) is not None
    except Exception:  # pragma: no cover
        return False
