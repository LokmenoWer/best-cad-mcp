"""Image-to-CAD trace preparation, spec validation, and CADPlan compilation."""

from __future__ import annotations

import json
import math
import shutil
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from src.cad_database import CADDatabase

from .common import (
    current_scope,
    decode_json,
    ensure_understanding_schema,
    get_db,
    json_text,
    now_iso,
    stable_id,
)
from .result import ToolResult, error_result, ok_result
from . import plan as plan_module

SUPPORTED_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"}
SUPPORTED_KINDS = {
    "line",
    "circle",
    "arc",
    "ellipse",
    "polyline",
    "rectangle",
    "chamfered_rectangle",
    "filleted_rectangle",
    "hole",
    "slot",
    "centerline",
    "dimension",
    "text",
    "leader",
    "hatch",
    "table",
    "pattern",
}
FEATURE_KINDS = {
    "chamfered_rectangle",
    "filleted_rectangle",
    "hole",
    "slot",
    "pattern",
    "hatch",
}
GEOMETRY_KINDS = {
    "line",
    "circle",
    "arc",
    "ellipse",
    "polyline",
    "rectangle",
    "chamfered_rectangle",
    "filleted_rectangle",
    "hole",
    "slot",
    "centerline",
    "hatch",
    "pattern",
}
ANNOTATION_KINDS = {"dimension", "text", "leader"}
TABLE_KINDS = {"table"}
DEFAULT_TARGET_WIDTH = 1000.0
DEFAULT_LAYERS = {
    "object": "M-OBJECT",
    "hole": "M-HOLE",
    "centerline": "M-CENTER",
    "dimension": "M-DIM",
    "text": "M-TEXT",
    "hatch": "M-HATCH",
    "table": "M-TABLE",
    "reference": "REF-IMAGE",
}
BBox = List[float]


def _safe_step_id(value: Any, fallback: str) -> str:
    raw = str(value or fallback)
    safe = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in raw.lower()).strip("_")
    return safe or fallback


def _image_size(path: Path) -> Tuple[int, int]:
    try:
        from PIL import Image

        with Image.open(path) as image:
            return int(image.width), int(image.height)
    except Exception:
        if path.suffix.lower() == ".bmp":
            with path.open("rb") as fh:
                header = fh.read(26)
            if header[:2] == b"BM":
                width = int.from_bytes(header[18:22], "little", signed=True)
                height = abs(int.from_bytes(header[22:26], "little", signed=True))
                return width, height
        raise ValueError(
            "Image size could not be read. Install Pillow or provide a supported BMP with a valid header."
        )


def _copy_or_normalize_image(source: Path, target: Path, max_dimension: int = 1800) -> Tuple[str, List[str]]:
    warnings: List[str] = []
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        from PIL import Image, ImageOps

        with Image.open(source) as image:
            image = ImageOps.exif_transpose(image)
            image.thumbnail((max_dimension, max_dimension))
            image.convert("RGB").save(target)
        return str(target), warnings
    except Exception as exc:
        warnings.append(f"Pillow normalization unavailable; copied source image instead: {exc}")
        copied = target.with_suffix(source.suffix.lower())
        shutil.copyfile(source, copied)
        return str(copied), warnings


def _build_tiles(normalized_path: Path,
                 image_width: int,
                 image_height: int,
                 tile_size: int = 768,
                 tile_overlap: float = 0.2) -> Dict[str, Any]:
    if image_width <= 0 or image_height <= 0:
        return {"tile_index_path": "", "tiles": [], "warnings": ["Image dimensions are unavailable."]}
    step = max(1, int(tile_size * (1.0 - max(0.0, min(tile_overlap, 0.8)))))
    tiles = []
    tile_id = 1
    for y in range(0, image_height, step):
        y2 = min(image_height, y + tile_size)
        if y2 - y < max(64, tile_size // 4) and y > 0:
            continue
        for x in range(0, image_width, step):
            x2 = min(image_width, x + tile_size)
            if x2 - x < max(64, tile_size // 4) and x > 0:
                continue
            tiles.append({
                "tile_id": f"T{tile_id:03d}",
                "pixel_bbox": [float(x), float(y), float(x2), float(y2)],
                "image_path": str(normalized_path),
            })
            tile_id += 1
    index_path = normalized_path.with_name(f"{normalized_path.stem}_tiles.json")
    payload = {
        "source_image_path": str(normalized_path),
        "image": {"width": image_width, "height": image_height},
        "tile_size": tile_size,
        "tile_overlap": tile_overlap,
        "tiles": tiles,
    }
    index_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"tile_index_path": str(index_path), "tiles": tiles, "warnings": []}


def _normalize_bbox(value: Any) -> Optional[BBox]:
    if not isinstance(value, (list, tuple)) or len(value) < 4:
        return None
    try:
        x1, y1, x2, y2 = [float(v) for v in value[:4]]
    except Exception:
        return None
    if x2 < x1:
        x1, x2 = x2, x1
    if y2 < y1:
        y1, y2 = y2, y1
    if x2 - x1 <= 0.0 or y2 - y1 <= 0.0:
        return None
    return [x1, y1, x2, y2]


def _bbox_in_image(bbox: BBox, width: int, height: int) -> bool:
    x1, y1, x2, y2 = bbox
    return x2 >= 0.0 and y2 >= 0.0 and x1 <= float(width) and y1 <= float(height)


def _point(value: Any) -> Optional[List[float]]:
    if not isinstance(value, (list, tuple)) or len(value) < 2:
        return None
    try:
        return [float(value[0]), float(value[1])]
    except Exception:
        return None


def _points(value: Any) -> List[List[float]]:
    if not isinstance(value, (list, tuple)):
        return []
    if value and all(isinstance(v, (int, float)) for v in value):
        return [p for p in (_point(value[i:i + 2]) for i in range(0, len(value), 2)) if p]
    return [p for p in (_point(item) for item in value) if p]


def _confidence(value: Any) -> Optional[float]:
    try:
        confidence = float(value)
    except Exception:
        return None
    if confidence < 0.0 or confidence > 1.0:
        return None
    return confidence


def _evidence(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, list):
        return {"items": value}
    if value is None:
        return {}
    text = str(value).strip()
    return {"text": text} if text else {}


def _iter_spec_items(spec: Dict[str, Any]) -> Iterable[Tuple[str, Dict[str, Any]]]:
    for section in ("features", "geometry", "annotations", "tables"):
        raw = spec.get(section, [])
        if not isinstance(raw, list):
            continue
        for item in raw:
            if isinstance(item, dict):
                yield section, item


def _raw_items_for_section(spec: Dict[str, Any], section: str) -> List[Dict[str, Any]]:
    raw = spec.get(section, [])
    return [item for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []


def _load_trace(database: CADDatabase, image_id: str) -> Optional[Dict[str, Any]]:
    ensure_understanding_schema(database)
    scope = current_scope(database)
    with database._conn() as conn:
        row = conn.execute('''
            SELECT *
            FROM cad_image_traces
            WHERE image_id = ? AND workspace_id = ? AND drawing_id = ?
              AND conversation_id = ? AND thread_id = ?
        ''', (
            image_id,
            scope["workspace_id"],
            scope["drawing_id"],
            scope["conversation_id"],
            scope["thread_id"],
        )).fetchone()
    if not row:
        return None
    item = dict(row)
    item["calibration"] = decode_json(item.get("calibration"), {})
    item["spec"] = decode_json(item.get("spec_json"), {})
    item["warnings"] = decode_json(item.get("warnings"), [])
    return item


def _latest_trace(database: CADDatabase) -> Optional[Dict[str, Any]]:
    ensure_understanding_schema(database)
    scope = current_scope(database)
    with database._conn() as conn:
        row = conn.execute('''
            SELECT *
            FROM cad_image_traces
            WHERE workspace_id = ? AND drawing_id = ?
              AND conversation_id = ? AND thread_id = ?
            ORDER BY created_at DESC
            LIMIT 1
        ''', (
            scope["workspace_id"],
            scope["drawing_id"],
            scope["conversation_id"],
            scope["thread_id"],
        )).fetchone()
    if not row:
        return None
    item = dict(row)
    item["calibration"] = decode_json(item.get("calibration"), {})
    item["spec"] = decode_json(item.get("spec_json"), {})
    item["warnings"] = decode_json(item.get("warnings"), [])
    return item


def prepare_image_trace(image_path: str,
                        domain: str = "mechanical",
                        tile_size: int = 768,
                        tile_overlap: float = 0.2,
                        database: Optional[CADDatabase] = None) -> ToolResult:
    """Prepare a single external image for Agent-side VLM tracing."""
    db = get_db(database)
    ensure_understanding_schema(db)
    source = Path(image_path).expanduser().resolve()
    if not source.exists() or not source.is_file():
        return error_result(f"Image path does not exist: {source}")
    if source.suffix.lower() not in SUPPORTED_IMAGE_SUFFIXES:
        return error_result(
            f"Unsupported image type {source.suffix}.",
            data={"supported_suffixes": sorted(SUPPORTED_IMAGE_SUFFIXES)},
        )
    try:
        width, height = _image_size(source)
    except Exception as exc:
        return error_result(str(exc), next_tools=["prepare_image_trace"])
    image_id = stable_id("img", str(source), source.stat().st_mtime_ns, width, height)
    out_dir = Path.cwd() / "cad_image_traces"
    normalized_path = out_dir / f"{image_id}_normalized.png"
    normalized_image, warnings = _copy_or_normalize_image(source, normalized_path)
    normalized_width, normalized_height = width, height
    try:
        normalized_width, normalized_height = _image_size(Path(normalized_image))
    except Exception:
        warnings.append("Normalized image size could not be read; using source dimensions.")
    tiles = _build_tiles(
        Path(normalized_image),
        normalized_width,
        normalized_height,
        tile_size=max(128, min(int(tile_size or 768), 4096)),
        tile_overlap=max(0.0, min(float(tile_overlap or 0.2), 0.8)),
    )
    warnings.extend(tiles.get("warnings", []))
    scope = current_scope(db)
    with db._conn() as conn:
        conn.execute('''
            INSERT OR REPLACE INTO cad_image_traces
                (image_id, image_path, normalized_image_path, tile_index_path,
                 image_width, image_height, domain, units, calibration,
                 spec_json, warnings, workspace_id, drawing_id,
                 conversation_id, thread_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            image_id,
            str(source),
            normalized_image,
            tiles.get("tile_index_path", ""),
            normalized_width,
            normalized_height,
            str(domain or "mechanical"),
            "",
            json_text({}),
            json_text({}),
            json_text(warnings),
            scope["workspace_id"],
            scope["drawing_id"],
            scope["conversation_id"],
            scope["thread_id"],
        ))
    return ok_result(
        "Prepared image trace input for Agent-side VLM extraction.",
        data={
            "image_id": image_id,
            "image_path": str(source),
            "normalized_image_path": normalized_image,
            "tile_index_path": tiles.get("tile_index_path", ""),
            "image": {"width": normalized_width, "height": normalized_height},
            "domain": str(domain or "mechanical"),
            "tiles": tiles.get("tiles", []),
            "vlm_contract": "Use prompt copy_drawing_from_image and return ImageDrawingSpec/v1 JSON.",
        },
        warnings=warnings,
        next_tools=["copy_drawing_from_image", "validate_image_drawing_spec", "submit_image_drawing_spec"],
    )


def validate_image_drawing_spec(spec: Any,
                                image_id: Optional[str] = None,
                                database: Optional[CADDatabase] = None) -> ToolResult:
    """Validate ImageDrawingSpec/v1 before CADPlan compilation."""
    db = get_db(database)
    trace = _load_trace(db, image_id) if image_id else None
    if image_id and not trace:
        return error_result(f"Unknown image trace: {image_id}", next_tools=["prepare_image_trace"])
    if isinstance(spec, str):
        try:
            spec = json.loads(spec)
        except Exception as exc:
            return error_result(f"spec is not valid JSON: {exc}")
    if not isinstance(spec, dict):
        return error_result("spec must be a JSON object.")
    errors: List[Dict[str, Any]] = []
    warnings: List[str] = []
    if spec.get("schema_version") != "ImageDrawingSpec/v1":
        errors.append({"path": "schema_version", "message": "schema_version must be ImageDrawingSpec/v1."})
    domain = str(spec.get("domain") or "").strip().lower()
    if not domain:
        errors.append({"path": "domain", "message": "domain is required."})
    elif domain != "mechanical":
        warnings.append(f"Domain {domain!r} is accepted but v1 is optimized for mechanical drawings.")
    for section in ("features", "geometry", "annotations", "tables", "uncertainties"):
        if section not in spec:
            errors.append({"path": section, "message": f"{section} is required."})
        elif not isinstance(spec.get(section), list):
            errors.append({"path": section, "message": f"{section} must be a list."})
    width = int((trace or {}).get("image_width") or 0)
    height = int((trace or {}).get("image_height") or 0)
    ids = set()
    normalized_items: Dict[str, List[Dict[str, Any]]] = {
        "features": [],
        "geometry": [],
        "annotations": [],
        "tables": [],
    }
    for section, raw in _iter_spec_items(spec):
        item_errors: List[str] = []
        item_id = str(raw.get("id") or "").strip()
        kind = str(raw.get("kind") or raw.get("type") or "").strip().lower()
        if not item_id:
            item_errors.append("id is required")
        elif item_id in ids:
            item_errors.append(f"duplicate id {item_id}")
        ids.add(item_id)
        if kind not in SUPPORTED_KINDS:
            item_errors.append(f"kind must be one of {sorted(SUPPORTED_KINDS)}")
        if section == "features" and kind not in FEATURE_KINDS:
            item_errors.append(f"features item kind {kind!r} must describe a fidelity-critical feature")
        if section == "geometry" and kind not in GEOMETRY_KINDS:
            item_errors.append(f"geometry item kind {kind!r} is not geometric")
        if section == "annotations" and kind not in ANNOTATION_KINDS:
            item_errors.append(f"annotations item kind {kind!r} is not an annotation")
        if section == "tables" and kind not in TABLE_KINDS:
            item_errors.append(f"tables item kind {kind!r} must be table")
        confidence = _confidence(raw.get("confidence"))
        if confidence is None:
            item_errors.append("confidence must be a number in [0, 1]")
            confidence = 0.0
        bbox = _normalize_bbox(raw.get("pixel_bbox") or raw.get("bbox"))
        pixel_geometry = raw.get("pixel_geometry")
        if not bbox and not pixel_geometry:
            item_errors.append("one of pixel_bbox or pixel_geometry is required")
        if bbox and width > 0 and height > 0 and not _bbox_in_image(bbox, width, height):
            item_errors.append("pixel_bbox is outside image bounds")
        evidence = _evidence(raw.get("evidence"))
        if not evidence:
            item_errors.append("evidence is required")
        if kind == "chamfered_rectangle" and not (
            raw.get("chamfers") or raw.get("chamfer_points") or (isinstance(pixel_geometry, dict) and pixel_geometry.get("vertices"))
        ):
            item_errors.append("chamfered_rectangle requires chamfers, chamfer_points, or explicit vertices")
        if kind == "filleted_rectangle" and not (
            raw.get("fillets") or raw.get("radius") or raw.get("radii") or (isinstance(pixel_geometry, dict) and pixel_geometry.get("segments"))
        ):
            item_errors.append("filleted_rectangle requires fillets/radius/radii or explicit arc segments")
        if kind == "pattern":
            members = raw.get("members") or raw.get("member_ids") or raw.get("instances")
            if not members:
                item_errors.append("pattern requires members/member_ids/instances so repeated features are not flattened")
        if item_errors:
            errors.append({"path": f"{section}.{item_id or '<missing>'}", "errors": item_errors, "item": raw})
        else:
            normalized_items[section].append({
                **raw,
                "id": item_id,
                "kind": kind,
                "confidence": round(float(confidence), 4),
                "pixel_bbox": bbox,
                "evidence": evidence,
            })
    if errors:
        return error_result(
            f"ImageDrawingSpec validation failed for {len(errors)} item(s).",
            data={"errors": errors, "image_id": image_id, "valid_items": normalized_items},
            warnings=warnings,
            next_tools=["copy_drawing_from_image", "validate_image_drawing_spec"],
        )
    normalized = {
        **spec,
        "domain": domain,
        "features": normalized_items["features"],
        "geometry": normalized_items["geometry"],
        "annotations": normalized_items["annotations"],
        "tables": normalized_items["tables"],
    }
    return ok_result(
        "Validated ImageDrawingSpec/v1.",
        data={"spec": normalized, "image_id": image_id},
        warnings=warnings,
        next_tools=["submit_image_drawing_spec", "compile_image_spec_to_cad_plan"],
    )


def _dimension_calibration(spec: Dict[str, Any],
                           image_width: int,
                           scale_mode: str) -> Tuple[Dict[str, Any], List[str]]:
    warnings: List[str] = []
    if str(scale_mode or "").lower() == "default_width":
        scale = DEFAULT_TARGET_WIDTH / max(float(image_width or 1), 1.0)
        return {
            "scale": scale,
            "source": "default_width",
            "units": spec.get("units") or "mm",
            "target_width": DEFAULT_TARGET_WIDTH,
        }, [
            "No reliable dimension calibration was requested; image width maps to 1000 drawing units."
        ]
    candidates = spec.get("calibration_candidates") or []
    ratios: List[float] = []
    used = []
    if isinstance(candidates, list):
        for item in candidates:
            if not isinstance(item, dict):
                continue
            confidence = _confidence(item.get("confidence"))
            if confidence is not None and confidence < 0.65:
                continue
            value = item.get("value") or item.get("measured_value") or item.get("actual_value")
            pixel_distance = item.get("pixel_distance")
            if pixel_distance is None and isinstance(item.get("pixel_points"), list) and len(item["pixel_points"]) >= 2:
                p1 = _point(item["pixel_points"][0])
                p2 = _point(item["pixel_points"][1])
                if p1 and p2:
                    pixel_distance = math.hypot(p2[0] - p1[0], p2[1] - p1[1])
            try:
                value_f = float(value)
                pixel_f = float(pixel_distance)
            except Exception:
                continue
            if value_f > 0.0 and pixel_f > 0.0:
                ratios.append(value_f / pixel_f)
                used.append(item)
    if ratios:
        avg = sum(ratios) / len(ratios)
        max_delta = max(abs(ratio - avg) / avg for ratio in ratios) if avg > 0 else 0.0
        if max_delta <= 0.02:
            return {
                "scale": avg,
                "source": "dimension_first",
                "units": spec.get("units") or "mm",
                "candidate_count": len(used),
                "candidates": used,
            }, warnings
        warnings.append("Dimension calibration candidates conflict by more than 2%; using default image-width scale.")
    else:
        warnings.append("No reliable dimension calibration candidates found; using default image-width scale.")
    scale = DEFAULT_TARGET_WIDTH / max(float(image_width or 1), 1.0)
    return {
        "scale": scale,
        "source": "default_width",
        "units": spec.get("units") or "mm",
        "target_width": DEFAULT_TARGET_WIDTH,
    }, warnings


def submit_image_drawing_spec(image_id: str,
                              spec: Any,
                              source_model: str = "unknown",
                              prompt_version: str = "copy_drawing_from_image/v1",
                              database: Optional[CADDatabase] = None) -> ToolResult:
    """Validate and persist an Agent-side VLM ImageDrawingSpec."""
    db = get_db(database)
    trace = _load_trace(db, image_id)
    if not trace:
        return error_result(f"Unknown image trace: {image_id}", next_tools=["prepare_image_trace"])
    validation = validate_image_drawing_spec(spec, image_id=image_id, database=db)
    if not validation.get("ok"):
        return validation
    normalized = validation["data"]["spec"]
    calibration, calibration_warnings = _dimension_calibration(
        normalized,
        int(trace.get("image_width") or 0),
        "dimension_first",
    )
    warnings = sorted(set(validation.get("warnings", []) + calibration_warnings))
    payload = {
        **normalized,
        "source_model": source_model,
        "prompt_version": prompt_version,
        "submitted_at": now_iso(),
    }
    scope = current_scope(db)
    with db._conn() as conn:
        conn.execute('''
            UPDATE cad_image_traces
            SET units = ?, calibration = ?, spec_json = ?, warnings = ?
            WHERE image_id = ? AND workspace_id = ? AND drawing_id = ?
              AND conversation_id = ? AND thread_id = ?
        ''', (
            str(normalized.get("units") or calibration.get("units") or ""),
            json_text(calibration),
            json_text(payload),
            json_text(warnings),
            image_id,
            scope["workspace_id"],
            scope["drawing_id"],
            scope["conversation_id"],
            scope["thread_id"],
        ))
    return ok_result(
        "Stored ImageDrawingSpec for CADPlan compilation.",
        data={"image_id": image_id, "spec": payload, "calibration": calibration},
        warnings=warnings,
        next_tools=["compile_image_spec_to_cad_plan", "validate_image_fidelity_contract"],
    )


def _trace_context(database: CADDatabase,
                   image_id: Optional[str],
                   spec: Optional[Dict[str, Any]],
                   units: str,
                   scale_mode: str) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any], Dict[str, Any], List[str]]:
    trace = _load_trace(database, image_id) if image_id else _latest_trace(database)
    warnings: List[str] = []
    spec_data = spec or {}
    if not spec_data and trace:
        spec_data = trace.get("spec") or {}
    if not isinstance(spec_data, dict) or not spec_data:
        return trace, {}, {}, ["No ImageDrawingSpec is available; submit_image_drawing_spec first or pass spec."]
    if trace and trace.get("calibration"):
        calibration = dict(trace["calibration"])
    else:
        calibration, cal_warnings = _dimension_calibration(
            spec_data,
            int((trace or {}).get("image_width") or 0),
            scale_mode,
        )
        warnings.extend(cal_warnings)
    calibration["units"] = str(units or calibration.get("units") or spec_data.get("units") or "mm")
    return trace, spec_data, calibration, warnings


def _scale_point(point: Sequence[float],
                 image_height: float,
                 scale: float) -> List[float]:
    return [float(point[0]) * scale, (image_height - float(point[1])) * scale, 0.0]


def _scale_bbox(bbox: BBox,
                image_height: float,
                scale: float) -> Tuple[float, float, float, float]:
    x1, y1, x2, y2 = bbox
    p1 = _scale_point([x1, y2], image_height, scale)
    p2 = _scale_point([x2, y1], image_height, scale)
    return p1[0], p1[1], p2[0], p2[1]


def _pixel_geometry(item: Dict[str, Any]) -> Dict[str, Any]:
    raw = item.get("pixel_geometry")
    return raw if isinstance(raw, dict) else {}


def _layer_for(item: Dict[str, Any]) -> str:
    role = str(item.get("layer_role") or "").lower().strip()
    kind = str(item.get("kind") or "").lower()
    if role and role in DEFAULT_LAYERS:
        return DEFAULT_LAYERS[role]
    if kind in {"hole"}:
        return DEFAULT_LAYERS["hole"]
    if kind == "centerline":
        return DEFAULT_LAYERS["centerline"]
    if kind == "hatch":
        return DEFAULT_LAYERS["hatch"]
    if kind in {"dimension"}:
        return DEFAULT_LAYERS["dimension"]
    if kind in {"text", "leader"}:
        return DEFAULT_LAYERS["text"]
    if kind == "table":
        return DEFAULT_LAYERS["table"]
    return DEFAULT_LAYERS["object"]


def _unique_strings(values: Iterable[Any]) -> List[str]:
    seen = set()
    result = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def _refs_from_value(value: Any) -> List[str]:
    refs: List[str] = []
    if isinstance(value, str):
        refs.append(value)
    elif isinstance(value, dict):
        for key in (
            "id",
            "member_id",
            "feature_id",
            "geometry_id",
            "boundary_id",
            "ref",
            "ref_id",
            "source_id",
            "target_id",
        ):
            if key in value:
                refs.extend(_refs_from_value(value.get(key)))
                break
        for key in ("ids", "member_ids", "boundary_ids", "members", "boundaries"):
            if key in value:
                refs.extend(_refs_from_value(value.get(key)))
    elif isinstance(value, (list, tuple)):
        for item in value:
            refs.extend(_refs_from_value(item))
    return _unique_strings(refs)


def _refs_from_keys(item: Dict[str, Any], keys: Sequence[str]) -> List[str]:
    refs: List[str] = []
    geom = _pixel_geometry(item)
    for container in (item, geom):
        for key in keys:
            if key in container:
                refs.extend(_refs_from_value(container.get(key)))
    return _unique_strings(refs)


def _number_from_keys(item: Dict[str, Any], keys: Sequence[str]) -> Optional[float]:
    geom = _pixel_geometry(item)
    for container in (item, geom):
        for key in keys:
            value = container.get(key)
            if value is None:
                continue
            try:
                return float(value)
            except Exception:
                continue
    return None


def _point_from_keys(item: Dict[str, Any], keys: Sequence[str]) -> Optional[List[float]]:
    geom = _pixel_geometry(item)
    for container in (item, geom):
        for key in keys:
            point = _point(container.get(key))
            if point:
                return point
    return None


def _item_center(item: Dict[str, Any]) -> Optional[List[float]]:
    geom = _pixel_geometry(item)
    center = _point(geom.get("center") or item.get("center"))
    if center:
        return center
    bbox = item.get("pixel_bbox")
    if isinstance(bbox, (list, tuple)) and len(bbox) >= 4:
        try:
            return [(float(bbox[0]) + float(bbox[2])) / 2.0, (float(bbox[1]) + float(bbox[3])) / 2.0]
        except Exception:
            return None
    points = _points(geom.get("vertices") or geom.get("points") or item.get("points"))
    if points:
        return [sum(point[0] for point in points) / len(points), sum(point[1] for point in points) / len(points)]
    return None


def _cluster_values(values: Sequence[float], tolerance: float = 2.0) -> List[float]:
    if not values:
        return []
    clusters: List[List[float]] = []
    for value in sorted(float(v) for v in values):
        if not clusters or abs(value - clusters[-1][-1]) > tolerance:
            clusters.append([value])
        else:
            clusters[-1].append(value)
    return [sum(cluster) / len(cluster) for cluster in clusters]


def _median_spacing(values: Sequence[float]) -> Optional[float]:
    ordered = sorted(values)
    diffs = [ordered[i + 1] - ordered[i] for i in range(len(ordered) - 1) if ordered[i + 1] - ordered[i] > 1e-6]
    if not diffs:
        return None
    mid = len(diffs) // 2
    return sorted(diffs)[mid]


def _pattern_member_ids(item: Dict[str, Any]) -> List[str]:
    return _refs_from_keys(item, ("member_ids", "members", "instances"))


def _source_member_id(item: Dict[str, Any], member_ids: Sequence[str]) -> Optional[str]:
    explicit = _refs_from_keys(
        item,
        ("source_member_id", "seed_member_id", "prototype_id", "base_member_id", "prototype_member_id"),
    )
    for ref in explicit:
        if not member_ids or ref in member_ids:
            return ref
    return member_ids[0] if member_ids else None


def _pattern_mode(item: Dict[str, Any]) -> str:
    geom = _pixel_geometry(item)
    text = " ".join(
        str(value or "").lower()
        for value in (
            item.get("pattern_type"),
            item.get("array_type"),
            item.get("relationship"),
            item.get("layout"),
            geom.get("pattern_type"),
            geom.get("array_type"),
            geom.get("relationship"),
            geom.get("layout"),
        )
    )
    if any(token in text for token in ("polar", "circular", "radial", "bolt_circle", "bolt circle")):
        return "polar"
    if any(token in text for token in ("rect", "grid", "row", "column", "tube_bundle", "tube bundle")):
        return "rectangular"
    if _number_from_keys(item, ("rows", "row_count", "num_rows", "columns", "cols", "column_count", "num_columns")):
        return "rectangular"
    if _point_from_keys(item, ("center", "array_center", "pattern_center", "polar_center")):
        return "polar"
    return ""


def _relation_ref_maps(spec: Dict[str, Any]) -> Tuple[Dict[str, List[str]], Dict[str, List[str]]]:
    pattern_members: Dict[str, List[str]] = {}
    hatch_boundaries: Dict[str, List[str]] = {}
    relations = spec.get("relations", [])
    if not isinstance(relations, list):
        return pattern_members, hatch_boundaries
    for relation in relations:
        if not isinstance(relation, dict):
            continue
        rtype = str(relation.get("type") or relation.get("relation_type") or relation.get("kind") or "").lower()
        source_refs = _refs_from_value(
            relation.get("source")
            or relation.get("from")
            or relation.get("from_id")
            or relation.get("pattern_id")
            or relation.get("hatch_id")
        )
        target_refs = _refs_from_value(
            relation.get("target")
            or relation.get("to")
            or relation.get("to_id")
            or relation.get("member_id")
            or relation.get("boundary_id")
        )
        if not source_refs or not target_refs:
            continue
        if "boundary" in rtype or rtype in {"bounded_by", "hatch_boundary"}:
            for source in source_refs:
                hatch_boundaries.setdefault(source, [])
                hatch_boundaries[source].extend(target_refs)
        if "pattern" in rtype or "member" in rtype:
            for source in source_refs:
                pattern_members.setdefault(source, [])
                pattern_members[source].extend(target_refs)
    return (
        {key: _unique_strings(value) for key, value in pattern_members.items()},
        {key: _unique_strings(value) for key, value in hatch_boundaries.items()},
    )


def _member_centers(member_ids: Sequence[str], item_by_id: Dict[str, Dict[str, Any]]) -> List[List[float]]:
    centers = []
    for member_id in member_ids:
        item = item_by_id.get(member_id)
        if not item:
            continue
        center = _item_center(item)
        if center:
            centers.append(center)
    return centers


def _rectangular_pattern_args(item: Dict[str, Any],
                              member_ids: Sequence[str],
                              item_by_id: Dict[str, Dict[str, Any]],
                              scale: float) -> Optional[Dict[str, Any]]:
    rows = _number_from_keys(item, ("rows", "row_count", "num_rows"))
    columns = _number_from_keys(item, ("columns", "cols", "column_count", "num_columns"))
    row_spacing_world = _number_from_keys(item, ("row_spacing_world", "drawing_row_spacing"))
    column_spacing_world = _number_from_keys(item, ("column_spacing_world", "drawing_column_spacing"))
    row_spacing_px = _number_from_keys(item, ("row_spacing", "row_pitch", "pitch_y", "spacing_y", "y_spacing"))
    column_spacing_px = _number_from_keys(item, ("column_spacing", "col_spacing", "column_pitch", "pitch_x", "spacing_x", "x_spacing"))
    centers = _member_centers(member_ids, item_by_id)
    if (rows is None or columns is None or row_spacing_px is None or column_spacing_px is None) and len(centers) >= 2:
        xs = _cluster_values([point[0] for point in centers])
        ys = _cluster_values([point[1] for point in centers])
        if columns is None and len(xs) > 1:
            columns = float(len(xs))
        if rows is None and len(ys) > 1:
            rows = float(len(ys))
        if column_spacing_px is None:
            column_spacing_px = _median_spacing(xs)
        if row_spacing_px is None:
            row_spacing_px = _median_spacing(ys)
    if rows is None or columns is None:
        count = _number_from_keys(item, ("count", "member_count", "instance_count"))
        if count and columns is None:
            columns = count
            rows = rows or 1
    if rows is None or columns is None:
        return None
    rows_i = max(1, int(round(rows)))
    columns_i = max(1, int(round(columns)))
    if rows_i <= 1 and columns_i <= 1:
        return None
    if row_spacing_world is None:
        if row_spacing_px is None:
            row_spacing_px = 0.0 if rows_i <= 1 else None
        if row_spacing_px is None:
            return None
        row_spacing_world = -abs(float(row_spacing_px)) * scale
    if column_spacing_world is None:
        if column_spacing_px is None:
            column_spacing_px = 0.0 if columns_i <= 1 else None
        if column_spacing_px is None:
            return None
        column_spacing_world = abs(float(column_spacing_px)) * scale
    return {
        "rows": rows_i,
        "columns": columns_i,
        "row_spacing": row_spacing_world,
        "column_spacing": column_spacing_world,
    }


def _polar_pattern_args(item: Dict[str, Any],
                        member_ids: Sequence[str],
                        item_by_id: Dict[str, Dict[str, Any]],
                        image_height: float,
                        scale: float) -> Optional[Dict[str, Any]]:
    count = _number_from_keys(item, ("count", "member_count", "instance_count"))
    if count is None and member_ids:
        count = float(len(member_ids))
    if count is None or count < 2:
        return None
    center = _point_from_keys(item, ("center", "array_center", "pattern_center", "polar_center"))
    centers = _member_centers(member_ids, item_by_id)
    if center is None and centers:
        center = [sum(point[0] for point in centers) / len(centers), sum(point[1] for point in centers) / len(centers)]
    if center is None:
        return None
    fill_angle = _number_from_keys(item, ("fill_angle", "angle", "angle_deg", "angle_degrees", "sweep_angle")) or 360.0
    world_center = _scale_point(center, image_height, scale)
    return {
        "count": max(2, int(round(count))),
        "fill_angle": float(fill_angle),
        "center_x": world_center[0],
        "center_y": world_center[1],
        "center_z": world_center[2],
    }


def _pattern_plan_info(item: Dict[str, Any],
                       step_id: str,
                       id_to_step: Dict[str, str],
                       item_by_id: Dict[str, Dict[str, Any]],
                       extra_members: Sequence[str],
                       image_height: float,
                       scale: float) -> Tuple[List[Dict[str, Any]], List[str], set]:
    warnings: List[str] = []
    member_ids = _unique_strings([*_pattern_member_ids(item), *extra_members])
    source_id = _source_member_id(item, member_ids)
    if not source_id or source_id not in id_to_step:
        warnings.append(f"{item.get('id')}: pattern could not bind a source member handle.")
        return [], warnings, set()
    mode = _pattern_mode(item)
    if mode == "polar":
        op = "array_polar"
        args = _polar_pattern_args(item, member_ids, item_by_id, image_height, scale)
        expected_count = int(args["count"]) if args else None
    else:
        op = "array_rectangular"
        args = _rectangular_pattern_args(item, member_ids, item_by_id, scale)
        expected_count = int(args["rows"]) * int(args["columns"]) if args else None
    if not args:
        warnings.append(f"{item.get('id')}: pattern has members but no rectangular/polar array relationship that CADPlan can bind.")
        return [], warnings, set()
    source_step = id_to_step[source_id]
    step = {
        "step_id": f"{step_id}_array",
        "op": op,
        "args": {"handle": f"${source_step}", **args},
        "depends_on": [source_step],
        "save_as": f"${step_id}",
    }
    skip_ids = set()
    if expected_count == len(member_ids):
        skip_ids = {member_id for member_id in member_ids if member_id != source_id and member_id in item_by_id}
    elif member_ids:
        warnings.append(
            f"{item.get('id')}: array count does not match member id count; keeping explicit member geometry."
        )
    return [step], warnings, skip_ids


def _hatch_boundary_ids(item: Dict[str, Any], extra_boundaries: Sequence[str]) -> List[str]:
    keys = (
        "boundary_ids",
        "boundary_id",
        "boundary_handles",
        "related_boundary_ids",
        "outer_boundary_ids",
        "boundaries",
        "outer_boundaries",
    )
    return _unique_strings([*_refs_from_keys(item, keys), *extra_boundaries])


def _hatch_plan_steps(item: Dict[str, Any],
                      step_id: str,
                      id_to_step: Dict[str, str],
                      extra_boundaries: Sequence[str],
                      image_height: float,
                      scale: float) -> Tuple[List[Dict[str, Any]], List[str]]:
    warnings: List[str] = []
    steps: List[Dict[str, Any]] = []
    boundary_vars: List[str] = []
    boundary_deps: List[str] = []
    for boundary_id in _hatch_boundary_ids(item, extra_boundaries):
        boundary_step = id_to_step.get(boundary_id)
        if boundary_step:
            boundary_vars.append(f"${boundary_step}")
            boundary_deps.append(boundary_step)
        elif boundary_id.startswith("$"):
            boundary_vars.append(boundary_id)
        else:
            warnings.append(f"{item.get('id')}: hatch boundary id {boundary_id!r} was not compiled.")
    if not boundary_vars and item.get("pixel_bbox"):
        x1, y1, x2, y2 = _scale_bbox(item["pixel_bbox"], image_height, scale)
        boundary_step = f"{step_id}_boundary"
        steps.append({
            "step_id": boundary_step,
            "op": "draw_rectangle",
            "args": {"x1": x1, "y1": y1, "x2": x2, "y2": y2, "layer": DEFAULT_LAYERS["hatch"]},
            "save_as": f"${boundary_step}",
        })
        boundary_vars.append(f"${boundary_step}")
        boundary_deps.append(boundary_step)
    if not boundary_vars:
        warnings.append(f"{item.get('id')}: hatch could not bind a boundary handle.")
        return steps, warnings
    pattern_name = (
        item.get("pattern_name")
        or item.get("hatch_pattern")
        or _pixel_geometry(item).get("pattern_name")
        or _pixel_geometry(item).get("hatch_pattern")
        or item.get("pattern")
        or "ANSI31"
    )
    if not isinstance(pattern_name, str) or not pattern_name.strip():
        pattern_name = "ANSI31"
    add_step_id = step_id
    steps.append({
        "step_id": add_step_id,
        "op": "add_hatch",
        "args": {
            "pattern_name": pattern_name.strip(),
            "associativity": bool(item.get("associativity", True)),
            "layer": DEFAULT_LAYERS["hatch"],
        },
        "save_as": f"${step_id}",
    })
    steps.append({
        "step_id": f"{step_id}_add_boundary",
        "op": "hatch_add_boundary",
        "args": {"handle": f"${step_id}", "boundary_handles": boundary_vars},
        "depends_on": [*boundary_deps, add_step_id],
    })
    return steps, warnings


def _line_step(item: Dict[str, Any],
               image_height: float,
               scale: float,
               step_id: str) -> Optional[Dict[str, Any]]:
    geom = _pixel_geometry(item)
    start = _point(geom.get("start") or item.get("start"))
    end = _point(geom.get("end") or item.get("end"))
    if not start or not end:
        points = _points(geom.get("points") or item.get("points"))
        if len(points) >= 2:
            start, end = points[0], points[1]
    if not start or not end:
        return None
    p1 = _scale_point(start, image_height, scale)
    p2 = _scale_point(end, image_height, scale)
    return {
        "step_id": step_id,
        "op": "draw_line",
        "args": {
            "start_x": p1[0],
            "start_y": p1[1],
            "end_x": p2[0],
            "end_y": p2[1],
            "layer": _layer_for(item),
        },
        "save_as": f"${step_id}",
    }


def _circle_args(item: Dict[str, Any],
                 image_height: float,
                 scale: float) -> Optional[Dict[str, Any]]:
    geom = _pixel_geometry(item)
    center = _point(geom.get("center") or item.get("center"))
    radius = geom.get("radius") or item.get("radius")
    bbox = item.get("pixel_bbox")
    if center is None and bbox:
        center = [(bbox[0] + bbox[2]) / 2.0, (bbox[1] + bbox[3]) / 2.0]
    if radius is None and bbox:
        radius = min(abs(bbox[2] - bbox[0]), abs(bbox[3] - bbox[1])) / 2.0
    try:
        radius_f = float(radius) * scale
    except Exception:
        return None
    if not center or radius_f <= 0:
        return None
    c = _scale_point(center, image_height, scale)
    return {"center_x": c[0], "center_y": c[1], "radius": radius_f, "layer": _layer_for(item)}


def _geometry_step(item: Dict[str, Any],
                   image_height: float,
                   scale: float,
                   step_id: str) -> Tuple[List[Dict[str, Any]], List[str]]:
    kind = str(item.get("kind") or "").lower()
    warnings: List[str] = []
    steps: List[Dict[str, Any]] = []
    bbox = item.get("pixel_bbox")
    geom = _pixel_geometry(item)
    if kind in {"line", "centerline"}:
        step = _line_step(item, image_height, scale, step_id)
        if step:
            steps.append(step)
        else:
            warnings.append(f"{item.get('id')}: line geometry missing endpoints.")
    elif kind in {"circle", "hole"}:
        args = _circle_args(item, image_height, scale)
        if args:
            steps.append({"step_id": step_id, "op": "draw_circle", "args": args, "save_as": f"${step_id}"})
        else:
            warnings.append(f"{item.get('id')}: circle/hole geometry missing center or radius.")
    elif kind == "arc":
        center = _point(geom.get("center") or item.get("center"))
        radius = geom.get("radius") or item.get("radius")
        try:
            radius_f = float(radius) * scale
            start_angle = float(geom.get("start_angle", item.get("start_angle")))
            end_angle = float(geom.get("end_angle", item.get("end_angle")))
        except Exception:
            warnings.append(f"{item.get('id')}: arc requires center, radius, start_angle, and end_angle.")
            return steps, warnings
        if not center:
            warnings.append(f"{item.get('id')}: arc center is missing.")
            return steps, warnings
        c = _scale_point(center, image_height, scale)
        steps.append({
            "step_id": step_id,
            "op": "draw_arc",
            "args": {
                "center_x": c[0],
                "center_y": c[1],
                "radius": radius_f,
                "start_angle": start_angle,
                "end_angle": end_angle,
                "layer": _layer_for(item),
            },
            "save_as": f"${step_id}",
        })
    elif kind == "ellipse":
        center = _point(geom.get("center") or item.get("center"))
        major = _point(geom.get("major_axis") or item.get("major_axis"))
        ratio = geom.get("radius_ratio") or item.get("radius_ratio")
        if not center or not major or ratio is None:
            warnings.append(f"{item.get('id')}: ellipse requires center, major_axis, and radius_ratio.")
            return steps, warnings
        c = _scale_point(center, image_height, scale)
        steps.append({
            "step_id": step_id,
            "op": "draw_ellipse",
            "args": {
                "center_x": c[0],
                "center_y": c[1],
                "major_x": float(major[0]) * scale,
                "major_y": -float(major[1]) * scale,
                "radius_ratio": float(ratio),
                "layer": _layer_for(item),
            },
            "save_as": f"${step_id}",
        })
    elif kind in {"polyline", "chamfered_rectangle", "filleted_rectangle", "slot"}:
        points = _points(geom.get("vertices") or geom.get("points") or item.get("vertices") or item.get("points"))
        if not points and bbox and kind in {"chamfered_rectangle", "filleted_rectangle"}:
            warnings.append(f"{item.get('id')}: {kind} has no explicit vertices/segments; refusing to simplify to rectangle.")
            return steps, warnings
        if not points:
            warnings.append(f"{item.get('id')}: polyline-like geometry missing points.")
            return steps, warnings
        flat: List[float] = []
        for point in points:
            p = _scale_point(point, image_height, scale)
            flat.extend([p[0], p[1]])
        steps.append({
            "step_id": step_id,
            "op": "draw_polyline",
            "args": {"points": flat, "closed": bool(geom.get("closed", True)), "layer": _layer_for(item)},
            "save_as": f"${step_id}",
        })
        if kind == "filleted_rectangle" and (item.get("radius") or geom.get("radius")):
            steps.append({
                "step_id": f"{step_id}_fillet",
                "op": "fillet_polyline",
                "args": {"handle": f"${step_id}", "radius": float(item.get("radius") or geom.get("radius")) * scale},
                "depends_on": [step_id],
            })
        if kind == "chamfered_rectangle" and (item.get("chamfer_distance") or geom.get("chamfer_distance")):
            dist = float(item.get("chamfer_distance") or geom.get("chamfer_distance")) * scale
            steps.append({
                "step_id": f"{step_id}_chamfer",
                "op": "chamfer_polyline",
                "args": {"handle": f"${step_id}", "distance1": dist, "distance2": dist},
                "depends_on": [step_id],
            })
    elif kind == "rectangle":
        if not bbox:
            warnings.append(f"{item.get('id')}: rectangle requires pixel_bbox.")
            return steps, warnings
        x1, y1, x2, y2 = _scale_bbox(bbox, image_height, scale)
        steps.append({
            "step_id": step_id,
            "op": "draw_rectangle",
            "args": {"x1": x1, "y1": y1, "x2": x2, "y2": y2, "layer": _layer_for(item)},
            "save_as": f"${step_id}",
        })
    elif kind == "dimension":
        p1 = _point(geom.get("p1") or geom.get("start") or item.get("p1"))
        p2 = _point(geom.get("p2") or geom.get("end") or item.get("p2"))
        text_point = _point(geom.get("text_point") or item.get("text_point"))
        if not p1 or not p2:
            warnings.append(f"{item.get('id')}: dimension missing measurement points; not drawing fake text.")
            return steps, warnings
        if not text_point and bbox:
            text_point = [(bbox[0] + bbox[2]) / 2.0, (bbox[1] + bbox[3]) / 2.0]
        if not text_point:
            warnings.append(f"{item.get('id')}: dimension missing text point; not drawing fake text.")
            return steps, warnings
        wp1 = _scale_point(p1, image_height, scale)
        wp2 = _scale_point(p2, image_height, scale)
        wt = _scale_point(text_point, image_height, scale)
        steps.append({
            "step_id": step_id,
            "op": "add_linear_dimension",
            "args": {
                "x1": wp1[0],
                "y1": wp1[1],
                "x2": wp2[0],
                "y2": wp2[1],
                "text_x": wt[0],
                "text_y": wt[1],
                "layer": _layer_for(item),
            },
            "save_as": f"${step_id}",
        })
    elif kind == "text":
        point = _point(geom.get("insert") or geom.get("point") or item.get("point"))
        if not point and bbox:
            point = [bbox[0], bbox[3]]
        text = str(item.get("text") or geom.get("text") or item.get("label") or "").strip()
        if not point or not text:
            warnings.append(f"{item.get('id')}: text requires insertion point and text.")
            return steps, warnings
        p = _scale_point(point, image_height, scale)
        steps.append({
            "step_id": step_id,
            "op": "draw_text",
            "args": {"text": text, "insert_x": p[0], "insert_y": p[1], "height": float(item.get("height") or 2.5), "layer": _layer_for(item)},
            "save_as": f"${step_id}",
        })
    elif kind == "leader":
        points = _points(geom.get("points") or item.get("points"))
        text = str(item.get("text") or geom.get("text") or "").strip()
        if len(points) < 2 or not text:
            warnings.append(f"{item.get('id')}: leader requires at least two points and text.")
            return steps, warnings
        world_points = [_scale_point(point, image_height, scale) for point in points]
        steps.append({
            "step_id": step_id,
            "op": "add_mleader",
            "args": {"text": text, "points": world_points, "layer": _layer_for(item)},
            "save_as": f"${step_id}",
        })
    elif kind == "table":
        if not bbox:
            warnings.append(f"{item.get('id')}: table requires pixel_bbox.")
            return steps, warnings
        rows = item.get("rows") or geom.get("rows") or []
        row_count = len(rows) if isinstance(rows, list) and rows else int(item.get("row_count") or 1)
        col_count = max((len(row) for row in rows if isinstance(row, list)), default=int(item.get("column_count") or 1))
        x1, y1, x2, y2 = _scale_bbox(bbox, image_height, scale)
        row_height = abs(y2 - y1) / max(row_count, 1)
        col_width = abs(x2 - x1) / max(col_count, 1)
        steps.append({
            "step_id": step_id,
            "op": "add_table",
            "args": {
                "insert_x": min(x1, x2),
                "insert_y": max(y1, y2),
                "rows": row_count,
                "columns": col_count,
                "row_height": row_height,
                "column_width": col_width,
                "layer": _layer_for(item),
            },
            "save_as": f"${step_id}",
        })
        if isinstance(rows, list):
            for r_index, row in enumerate(rows):
                if not isinstance(row, list):
                    continue
                for c_index, cell in enumerate(row):
                    steps.append({
                        "step_id": f"{step_id}_cell_{r_index}_{c_index}",
                        "op": "edit_table_cell",
                        "args": {"table_handle": f"${step_id}", "row": r_index, "col": c_index, "text": str(cell)},
                        "depends_on": [step_id],
                    })
    elif kind in {"hatch", "pattern"}:
        warnings.append(f"{item.get('id')}: {kind} is preserved in spec but requires boundary/member handles after geometry creation.")
    else:
        warnings.append(f"{item.get('id')}: unsupported kind {kind}.")
    return steps, warnings


def compile_image_spec_to_cad_plan(image_id: Optional[str] = None,
                                   spec: Optional[Dict[str, Any]] = None,
                                   units: str = "mm",
                                   scale_mode: str = "dimension_first",
                                   database: Optional[CADDatabase] = None) -> ToolResult:
    """Compile an ImageDrawingSpec into a guarded CADPlan without modifying DWG."""
    db = get_db(database)
    trace, spec_data, calibration, context_warnings = _trace_context(db, image_id, spec, units, scale_mode)
    if not spec_data:
        return error_result(
            "No ImageDrawingSpec is available.",
            data={"image_id": image_id},
            warnings=context_warnings,
            next_tools=["prepare_image_trace", "submit_image_drawing_spec"],
        )
    image_height = float((trace or {}).get("image_height") or spec_data.get("image_height") or 1000)
    scale = float(calibration.get("scale") or 1.0)
    warnings: List[str] = list(context_warnings)
    steps: List[Dict[str, Any]] = []
    for layer in sorted(set(DEFAULT_LAYERS.values())):
        steps.append({
            "step_id": f"layer_{layer.lower().replace('-', '_')}",
            "op": "create_layer",
            "args": {"name": layer},
            "writes": True,
        })
    items: List[Tuple[str, Dict[str, Any]]] = []
    id_to_step: Dict[str, str] = {}
    item_by_id: Dict[str, Dict[str, Any]] = {}
    for section in ("geometry", "features", "annotations", "tables"):
        for item in _raw_items_for_section(spec_data, section):
            items.append((section, item))
    seen_ids = set()
    item_records: List[Tuple[str, str, Dict[str, Any]]] = []
    for index, (_section, item) in enumerate(items, start=1):
        raw_id = str(item.get("id") or f"item_{index}")
        safe_id = _safe_step_id(raw_id, f"item_{index}")
        if safe_id in seen_ids:
            safe_id = f"{safe_id}_{index}"
        seen_ids.add(safe_id)
        id_to_step[raw_id] = safe_id
        item_by_id[raw_id] = item
        item_records.append((raw_id, safe_id, item))

    relation_pattern_members, relation_hatch_boundaries = _relation_ref_maps(spec_data)
    pattern_steps: Dict[str, List[Dict[str, Any]]] = {}
    skip_item_ids = set()
    relation_warnings: List[str] = []
    for raw_id, safe_id, item in item_records:
        if str(item.get("kind") or "").lower() != "pattern":
            continue
        item_steps, item_warnings, pattern_skip_ids = _pattern_plan_info(
            item,
            safe_id,
            id_to_step,
            item_by_id,
            relation_pattern_members.get(raw_id, []),
            image_height,
            scale,
        )
        if item_steps:
            pattern_steps[raw_id] = item_steps
            skip_item_ids.update(pattern_skip_ids)
        relation_warnings.extend(item_warnings)

    hatch_steps: Dict[str, List[Dict[str, Any]]] = {}
    for raw_id, safe_id, item in item_records:
        if str(item.get("kind") or "").lower() != "hatch":
            continue
        item_steps, item_warnings = _hatch_plan_steps(
            item,
            safe_id,
            id_to_step,
            relation_hatch_boundaries.get(raw_id, []),
            image_height,
            scale,
        )
        if item_steps:
            hatch_steps[raw_id] = item_steps
        relation_warnings.extend(item_warnings)

    for raw_id, safe_id, item in item_records:
        kind = str(item.get("kind") or "").lower()
        if raw_id in skip_item_ids or kind in {"pattern", "hatch"}:
            continue
        item_steps, item_warnings = _geometry_step(item, image_height, scale, safe_id)
        steps.extend(item_steps)
        warnings.extend(item_warnings)
    for raw_id, _safe_id, item in item_records:
        kind = str(item.get("kind") or "").lower()
        if kind == "pattern":
            if raw_id in pattern_steps:
                steps.extend(pattern_steps[raw_id])
            elif _pattern_member_ids(item) or relation_pattern_members.get(raw_id):
                warnings.append(f"{item.get('id')}: pattern is preserved in spec but could not bind a CAD array handle.")
        elif kind == "hatch":
            if raw_id in hatch_steps:
                steps.extend(hatch_steps[raw_id])
            else:
                warnings.append(f"{item.get('id')}: hatch is preserved in spec but could not bind a CAD hatch boundary handle.")
    warnings.extend(relation_warnings)
    plan = {
        "plan_id": stable_id("plan", image_id or "", json.dumps(spec_data, sort_keys=True, default=str))[:24],
        "description": "Trace mechanical engineering drawing from ImageDrawingSpec/v1.",
        "units": units or calibration.get("units") or "mm",
        "risk_level": "medium",
        "requires_confirmation": True,
        "variables": {},
        "steps": steps,
        "constraints": [
            {
                "type": "image_trace_fidelity",
                "source": image_id or "inline_spec",
                "policy": "Do not silently downgrade chamfers, fillets, holes, slots, patterns, dimensions, hatches, or tables.",
            }
        ],
        "metadata": {
            "source": "image_trace",
            "image_id": image_id or (trace or {}).get("image_id"),
            "calibration": calibration,
            "feature_count": len(_raw_items_for_section(spec_data, "features")),
            "geometry_count": len(_raw_items_for_section(spec_data, "geometry")),
            "annotation_count": len(_raw_items_for_section(spec_data, "annotations")),
            "table_count": len(_raw_items_for_section(spec_data, "tables")),
        },
    }
    fidelity = validate_image_fidelity_contract(spec_data, plan, database=db)
    warnings.extend(fidelity.get("warnings", []))
    if not fidelity.get("ok"):
        return error_result(
            "Compiled CADPlan failed the image trace fidelity contract.",
            data={"plan": plan, "fidelity": fidelity.get("data", {}), "calibration": calibration},
            warnings=sorted(set(warnings)),
            next_tools=["validate_image_drawing_spec", "compile_image_spec_to_cad_plan"],
        )
    return ok_result(
        "Compiled ImageDrawingSpec to CADPlan.",
        data={"plan": plan, "fidelity": fidelity.get("data", {}), "calibration": calibration},
        warnings=sorted(set(warnings)),
        next_tools=["validate_image_fidelity_contract", "validate_cad_plan", "dry_run_cad_plan"],
    )


def _plan_ops_for_var(plan: Dict[str, Any], item_id: str) -> List[str]:
    safe = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in str(item_id).lower()).strip("_")
    ops = []
    for step in plan.get("steps", []) if isinstance(plan.get("steps"), list) else []:
        step_id = str(step.get("step_id") or "")
        if step_id == safe or step_id.startswith(f"{safe}_"):
            ops.append(str(step.get("op") or ""))
    return ops


def validate_image_fidelity_contract(spec: Dict[str, Any],
                                     cad_plan: Dict[str, Any],
                                     database: Optional[CADDatabase] = None) -> ToolResult:
    """Reject silent simplification of feature-critical image trace elements."""
    del database
    if not isinstance(spec, dict):
        return error_result("spec must be a JSON object.")
    if not isinstance(cad_plan, dict):
        return error_result("cad_plan must be a JSON object.")
    errors: List[Dict[str, Any]] = []
    warnings: List[str] = []
    relation_pattern_members, _relation_hatch_boundaries = _relation_ref_maps(spec)
    compiled_pattern_members = set()
    for section in ("features", "geometry"):
        for item in _raw_items_for_section(spec, section):
            item_id = str(item.get("id") or "")
            if str(item.get("kind") or "").lower() != "pattern":
                continue
            ops = _plan_ops_for_var(cad_plan, item_id)
            if any(op in {"array_rectangular", "array_polar", "insert_minsert_block"} for op in ops):
                compiled_pattern_members.update(_pattern_member_ids(item))
                compiled_pattern_members.update(relation_pattern_members.get(item_id, []))
    for section in ("features", "geometry"):
        for item in _raw_items_for_section(spec, section):
            item_id = str(item.get("id") or "")
            kind = str(item.get("kind") or "").lower()
            ops = _plan_ops_for_var(cad_plan, item_id)
            if kind == "chamfered_rectangle":
                if ops == ["draw_rectangle"] or "draw_rectangle" in ops and not {"draw_polyline", "chamfer_polyline"}.intersection(ops):
                    errors.append({
                        "id": item_id,
                        "kind": kind,
                        "message": "chamfered_rectangle cannot be compiled as a plain rectangle.",
                        "ops": ops,
                    })
                if not ops:
                    errors.append({"id": item_id, "kind": kind, "message": "chamfered_rectangle was not compiled.", "ops": ops})
            if kind == "filleted_rectangle":
                if not {"fillet_polyline", "draw_arc"}.intersection(ops):
                    errors.append({
                        "id": item_id,
                        "kind": kind,
                        "message": "filleted_rectangle must preserve radii or arc segments.",
                        "ops": ops,
                    })
                if not ops:
                    errors.append({"id": item_id, "kind": kind, "message": "filleted_rectangle was not compiled.", "ops": ops})
            if kind == "hole" and not ops and item_id not in compiled_pattern_members:
                errors.append({"id": item_id, "kind": kind, "message": "hole feature was not compiled.", "ops": ops})
            if kind == "slot" and not ops and item_id not in compiled_pattern_members:
                errors.append({"id": item_id, "kind": kind, "message": "slot feature was not compiled.", "ops": ops})
            if kind == "pattern":
                members = item.get("members") or item.get("member_ids") or item.get("instances")
                if not members:
                    errors.append({
                        "id": item_id,
                        "kind": kind,
                        "message": "pattern lacks members; repeated features may be flattened.",
                        "ops": ops,
                    })
                if not any(op in {"array_rectangular", "array_polar", "insert_minsert_block"} for op in ops):
                    warnings.append(f"Pattern {item_id} has no CAD array op yet; member relationship remains in the spec.")
            if kind == "hatch":
                if not ops:
                    warnings.append(f"Hatch {item_id} has no CAD hatch op yet; boundary relationship remains in the spec.")
                elif "hatch_add_boundary" not in ops:
                    warnings.append(f"Hatch {item_id} has no hatch_add_boundary op yet; boundary relationship remains in the spec.")
    for item in _raw_items_for_section(spec, "annotations"):
        item_id = str(item.get("id") or "")
        kind = str(item.get("kind") or "").lower()
        ops = _plan_ops_for_var(cad_plan, item_id)
        if kind == "dimension" and "draw_text" in ops:
            errors.append({
                "id": item_id,
                "kind": kind,
                "message": "dimension must use real dimension tools, not text.",
                "ops": ops,
            })
    if errors:
        return error_result(
            f"Image trace fidelity contract failed for {len(errors)} item(s).",
            data={"errors": errors, "valid": False},
            warnings=warnings,
            next_tools=["compile_image_spec_to_cad_plan", "validate_image_drawing_spec"],
        )
    return ok_result(
        "Image trace fidelity contract passed.",
        data={"valid": True, "checked_feature_count": len(_raw_items_for_section(spec, "features"))},
        warnings=warnings,
        next_tools=["validate_cad_plan", "dry_run_cad_plan"],
    )


__all__ = [
    "prepare_image_trace",
    "validate_image_drawing_spec",
    "submit_image_drawing_spec",
    "validate_image_fidelity_contract",
    "compile_image_spec_to_cad_plan",
]
