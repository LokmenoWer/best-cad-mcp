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

import math
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.cad_database import CADDatabase

from .common import current_scope, ensure_understanding_schema, get_db
from .image_trace import _latest_trace, _load_tile_index, _load_trace
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
        "source_width": 0,
        "source_height": 0,
        "source_image": {"width": 0, "height": 0},
        "observed_image": {"width": 0, "height": 0},
        "observed_to_source": [],
        "source_to_observed": [],
        "source_to_global": [],
        "observed_to_global": [],
        "global_coordinate_space": "",
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


def _scale_matrix(scale_x: float, scale_y: float) -> List[List[float]]:
    return [
        [float(scale_x), 0.0, 0.0],
        [0.0, float(scale_y), 0.0],
        [0.0, 0.0, 1.0],
    ]


def _identity_matrix() -> List[List[float]]:
    return _scale_matrix(1.0, 1.0)


def _matrix_multiply(left: List[List[float]],
                     right: List[List[float]]) -> List[List[float]]:
    return [
        [
            float(sum(left[row][index] * right[index][column] for index in range(3)))
            for column in range(3)
        ]
        for row in range(3)
    ]


def _finite_positive_dimension(value: Any) -> Optional[float]:
    try:
        numeric = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return numeric if math.isfinite(numeric) and numeric > 0.0 else None


def _canonical_affine_matrix(value: Any) -> Optional[List[List[float]]]:
    """Return a finite 3x3 affine matrix or ``None`` for unsafe metadata."""
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        return None
    matrix: List[List[float]] = []
    for row in value:
        if not isinstance(row, (list, tuple)) or len(row) != 3:
            return None
        try:
            numeric_row = [float(component) for component in row]
        except (TypeError, ValueError, OverflowError):
            return None
        if not all(math.isfinite(component) for component in numeric_row):
            return None
        matrix.append(numeric_row)
    if not all(math.isclose(matrix[2][index], expected, rel_tol=0.0, abs_tol=1e-12)
               for index, expected in enumerate((0.0, 0.0, 1.0))):
        return None
    return matrix


def _attach_coordinate_contract(prep: Dict[str, Any],
                                *,
                                source_coordinate_space: str,
                                global_coordinate_space: str,
                                source_to_global: Any,
                                artifact_role: str,
                                snapshot_id: str = "",
                                image_id: str = "",
                                tile_id: str = "") -> Optional[Dict[str, Any]]:
    """Attach an exact observed-image to global-pixel coordinate contract."""
    observed_width = _finite_positive_dimension(prep.get("width"))
    observed_height = _finite_positive_dimension(prep.get("height"))
    source_width = _finite_positive_dimension(prep.get("source_width"))
    source_height = _finite_positive_dimension(prep.get("source_height"))
    observed_to_source = _canonical_affine_matrix(prep.get("observed_to_source"))
    source_to_global_matrix = _canonical_affine_matrix(source_to_global)
    if not all((
        observed_width, observed_height, source_width, source_height,
        observed_to_source, source_to_global_matrix,
    )):
        prep.setdefault("warnings", []).append(
            "Image dimensions or coordinate transforms were unavailable; do not report pixel coordinates from this embedded image."
        )
        return None
    observed_to_global = _matrix_multiply(
        source_to_global_matrix, observed_to_source
    )
    contract = {
        "schema_version": "VisualSourceRef/v1",
        "artifact_role": str(artifact_role or "image"),
        "coordinate_space": "observed_image",
        "observed_image": {
            "width": int(observed_width),
            "height": int(observed_height),
        },
        "source_image": {
            "width": int(source_width),
            "height": int(source_height),
        },
        "source_coordinate_space": str(source_coordinate_space),
        "global_coordinate_space": str(global_coordinate_space),
        "observed_to_source": observed_to_source,
        "source_to_global": source_to_global_matrix,
        "observed_to_global": observed_to_global,
    }
    if snapshot_id:
        contract["snapshot_id"] = str(snapshot_id)
    if image_id:
        contract["image_id"] = str(image_id)
    if tile_id:
        contract["tile_id"] = str(tile_id)
    prep["coordinate_contract"] = contract
    prep["global_coordinate_space"] = str(global_coordinate_space)
    prep["source_to_global"] = source_to_global_matrix
    prep["observed_to_global"] = observed_to_global
    # The model should echo this exact object as finding.source_ref whenever
    # it reports pixels measured in the embedded (possibly downscaled) image.
    prep["source_ref_template"] = dict(contract)
    return contract


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
    source_width = source_height = 0
    downscaled = False
    if pil is not None:
        try:
            with pil.open(working) as img:
                width, height = img.size
                source_width, source_height = width, height
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
            "source_width": int(source_width),
            "source_height": int(source_height),
            "source_image": {
                "width": int(source_width),
                "height": int(source_height),
            },
            "observed_image": {"width": int(width), "height": int(height)},
            "observed_to_source": (
                _scale_matrix(
                    source_width / float(width),
                    source_height / float(height),
                )
                if source_width > 0 and source_height > 0 and width > 0 and height > 0
                else []
            ),
            "source_to_observed": (
                _scale_matrix(
                    width / float(source_width),
                    height / float(source_height),
                )
                if source_width > 0 and source_height > 0 and width > 0 and height > 0
                else []
            ),
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


def _tile_source_to_global(tile: Dict[str, Any]) -> Optional[List[List[float]]]:
    matrix = _canonical_affine_matrix(tile.get("local_to_global"))
    if matrix is not None:
        return matrix
    global_bbox = tile.get("global_pixel_bbox") or tile.get("pixel_bbox") or []
    if not isinstance(global_bbox, (list, tuple)) or len(global_bbox) < 4:
        return None
    try:
        offset_x, offset_y = float(global_bbox[0]), float(global_bbox[1])
    except (TypeError, ValueError, OverflowError):
        return None
    if not all(math.isfinite(value) for value in (offset_x, offset_y)):
        return None
    return [
        [1.0, 0.0, offset_x],
        [0.0, 1.0, offset_y],
        [0.0, 0.0, 1.0],
    ]


def _attach_resolved_coordinate_contract(prep: Dict[str, Any],
                                         *,
                                         artifact_role: str,
                                         global_coordinate_space: str,
                                         global_image: Dict[str, Any],
                                         tile: Optional[Dict[str, Any]] = None,
                                         snapshot_id: str = "",
                                         image_id: str = "") -> Optional[Dict[str, Any]]:
    source_image = (tile or {}).get("image") if tile else global_image
    source_image = source_image if isinstance(source_image, dict) else {}
    expected_width = _finite_positive_dimension(source_image.get("width"))
    expected_height = _finite_positive_dimension(source_image.get("height"))
    actual_width = _finite_positive_dimension(prep.get("source_width"))
    actual_height = _finite_positive_dimension(prep.get("source_height"))
    if not all((expected_width, expected_height, actual_width, actual_height)):
        prep.setdefault("warnings", []).append(
            "Could not verify source image dimensions; pixel-coordinate findings from this image must be rejected."
        )
        return None
    if (
        not math.isclose(actual_width, expected_width, rel_tol=0.0, abs_tol=1e-9)
        or not math.isclose(actual_height, expected_height, rel_tol=0.0, abs_tol=1e-9)
    ):
        prep.setdefault("warnings", []).append(
            "Embedded image dimensions do not match the mapped source artifact; pixel-coordinate findings from this image must be rejected."
        )
        return None
    source_to_global = _tile_source_to_global(tile) if tile else _identity_matrix()
    if source_to_global is None:
        prep.setdefault("warnings", []).append(
            "Tile-to-global transform is missing or invalid; pixel-coordinate findings from this tile must be rejected."
        )
        return None
    return _attach_coordinate_contract(
        prep,
        source_coordinate_space=(
            "tile_local" if tile else global_coordinate_space
        ),
        global_coordinate_space=global_coordinate_space,
        source_to_global=source_to_global,
        artifact_role=artifact_role,
        snapshot_id=snapshot_id,
        image_id=image_id,
        tile_id=str((tile or {}).get("tile_id") or ""),
    )


def resolve_snapshot_images(snapshot_id: Optional[str] = None,
                            which: str = "auto",
                            max_dim: int = DEFAULT_MAX_DIM,
                            database: Optional[CADDatabase] = None,
                            tile_id: Optional[str] = None) -> ToolResult:
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

    requested_tile = str(tile_id or "").strip().upper()
    tile: Optional[Dict[str, Any]] = None
    if requested_tile:
        tiles = [item for item in snapshot.get("tiles", []) if isinstance(item, dict)]
        tile = next((
            item for item in tiles
            if str(item.get("tile_id") or "").strip().upper() == requested_tile
        ), None)
        if tile is None:
            return error_result(
                f"Unknown snapshot tile: {requested_tile}",
                data={
                    "snapshot_id": snapshot_id,
                    "available_tile_ids": [item.get("tile_id") for item in tiles],
                },
                next_tools=["render_drawing_view", "export_view_image_with_mapping"],
            )
        requested_which = (which or "auto").strip().lower()
        clean_tile = str(tile.get("clean_tile_path") or "")
        overlay_tile = str(tile.get("overlay_tile_path") or "")
        if requested_which == "clean":
            wanted = [("tile_clean", clean_tile)]
        elif requested_which == "overlay":
            wanted = [("tile_overlay", overlay_tile)]
        elif requested_which == "both":
            wanted = [("tile_clean", clean_tile), ("tile_overlay", overlay_tile)]
        else:
            wanted = (
                [("tile_overlay", overlay_tile), ("tile_clean", clean_tile)]
                if overlay_tile else [("tile_clean", clean_tile)]
            )
        candidates = [{"role": role, "path": path} for role, path in wanted if path]
    else:
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
        if prep.get("embeddable"):
            _attach_resolved_coordinate_contract(
                prep,
                artifact_role=candidate["role"],
                global_coordinate_space="snapshot_global",
                global_image=snapshot.get("image") or {},
                tile=tile,
                snapshot_id=str(snapshot_id or ""),
            )
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

    source_ref_templates = [
        image.get("source_ref_template")
        for image in images
        if isinstance(image.get("source_ref_template"), dict)
    ]
    return ok_result(
        f"Snapshot {snapshot_id}: {len(embeddable)} image(s) ready for the model.",
        data={
            "snapshot_id": snapshot_id,
            "which": (which or "auto").strip().lower(),
            "vlm_ready": bool(snapshot.get("vlm_ready")),
            "images": images,
            "source_ref_templates": source_ref_templates,
            **({"source_ref_template": source_ref_templates[0]}
               if len(source_ref_templates) == 1 else {}),
            **({
                "tile_id": requested_tile,
                "tile": tile,
                "coordinate_space": "tile_local",
                "global_pixel_bbox": tile.get("global_pixel_bbox") or tile.get("pixel_bbox"),
                "local_to_global": tile.get("local_to_global"),
            } if tile else {}),
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
                        database: Optional[CADDatabase] = None,
                        tile_id: Optional[str] = None) -> ToolResult:
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
    tile: Optional[Dict[str, Any]] = None
    requested_tile = str(tile_id or "").strip().upper()
    if requested_tile:
        tiles = [
            item for item in _load_tile_index(trace).get("tiles", [])
            if isinstance(item, dict)
        ]
        tile = next(
            (item for item in tiles if str(item.get("tile_id") or "").strip().upper() == requested_tile),
            None,
        )
        if tile is None:
            return error_result(
                f"Unknown image trace tile: {requested_tile}",
                data={
                    "image_id": trace.get("image_id"),
                    "available_tile_ids": [item.get("tile_id") for item in tiles],
                },
                next_tools=["prepare_image_trace", "get_trace_source_image"],
            )
        artifact_path = str(tile.get("image_path") or "")
        if not artifact_path or not tile.get("crop_ready", bool(artifact_path)):
            return error_result(
                f"Trace tile {requested_tile} has no model-viewable crop. Re-run prepare_image_trace with Pillow installed.",
                data={"image_id": trace.get("image_id"), "tile": tile},
                next_tools=["check_runtime_environment", "prepare_image_trace"],
            )
        resolved_role = "tile"
    else:
        artifact_path = _trace_artifact_path(trace, role)
        resolved_role = (role or "normalized").strip().lower()
    prep = prepare_model_image(artifact_path, max_dim=max_dim)
    prep["role"] = resolved_role
    if not prep["embeddable"]:
        return error_result(
            prep.get("reason") or "Trace image could not be made model-viewable.",
            data={"image_id": trace.get("image_id"), "vision": prep},
            warnings=prep.get("warnings", []),
            next_tools=["check_runtime_environment"],
        )
    _attach_resolved_coordinate_contract(
        prep,
        artifact_role=resolved_role,
        global_coordinate_space="image_global",
        global_image={
            "width": trace.get("image_width"),
            "height": trace.get("image_height"),
        },
        tile=tile,
        image_id=str(trace.get("image_id") or ""),
    )
    source_ref_template = prep.get("source_ref_template")
    return ok_result(
        f"Image trace {trace.get('image_id')} ({prep['role']}) ready for the model.",
        data={
            "image_id": trace.get("image_id"),
            "domain": trace.get("domain", ""),
            "vision": prep,
            **({"source_ref_template": source_ref_template}
               if isinstance(source_ref_template, dict) else {}),
            **({
                "tile_id": requested_tile,
                "tile": tile,
                "coordinate_space": "tile_local",
                "global_pixel_bbox": tile.get("global_pixel_bbox") or tile.get("pixel_bbox"),
                "local_to_global": tile.get("local_to_global"),
            } if tile else {}),
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
            "see a prepared trace source. For dense trace drawings, inspect the global "
            "image first and then call get_trace_source_image(tile_id='T...') for real "
            "local crops whose coordinates can be rebased through local_to_global. "
            "Dense mapped DWG views use render_drawing_view(include_tiles=True) followed "
            "by get_snapshot_image(tile_id='T...') with the same coordinate contract. "
            "Perceive → act by handle → re-render → verify."
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
