"""View snapshot mapping, overlay artifacts, and VLM region grounding."""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from src.cad_database import CADDatabase

from .common import (
    all_entities,
    bbox_center,
    bbox_dict,
    bbox_from_row,
    bbox_intersects,
    bbox_iou,
    bbox_union,
    current_scope,
    decode_json,
    ensure_understanding_schema,
    get_db,
    now_iso,
    point_distance,
    stable_id,
    topology_for_handle,
)
from .result import ToolResult, error_result, ok_result

DEFAULT_IMAGE_SIZE = (1600, 1000)
BBox = Tuple[float, float, float, float]


def _point(value: Any, default: Optional[List[float]] = None) -> List[float]:
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        try:
            return [
                float(value[0]),
                float(value[1]),
                float(value[2]) if len(value) > 2 else 0.0,
            ]
        except Exception:
            pass
    return list(default or [0.0, 0.0, 0.0])


def _normalize_direction(value: Any) -> List[float]:
    direction = _point(value, [0.0, 0.0, 1.0])
    length = math.sqrt(sum(component * component for component in direction))
    if length <= 1e-12:
        return [0.0, 0.0, 1.0]
    return [component / length for component in direction]


def _matrix_inverse_2d(matrix: Sequence[Sequence[float]]) -> List[List[float]]:
    a, b, c = matrix[0]
    d, e, f = matrix[1]
    det = a * e - b * d
    if abs(det) <= 1e-18:
        raise ValueError("2D transform matrix is singular")
    inv_det = 1.0 / det
    return [
        [e * inv_det, -b * inv_det, (b * f - e * c) * inv_det],
        [-d * inv_det, a * inv_det, (d * c - a * f) * inv_det],
        [0.0, 0.0, 1.0],
    ]


def _compose_2d(first: Sequence[Sequence[float]],
                second: Sequence[Sequence[float]]) -> List[List[float]]:
    return [
        [
            first[row][0] * second[0][col]
            + first[row][1] * second[1][col]
            + first[row][2] * second[2][col]
            for col in range(3)
        ]
        for row in range(3)
    ]


def _world_to_ucs_matrix(ucs: Dict[str, Any]) -> List[List[float]]:
    origin = _point(ucs.get("origin"), [0.0, 0.0, 0.0])
    x_axis = _point(ucs.get("x_axis") or ucs.get("xaxis"), [1.0, 0.0, 0.0])
    y_axis = _point(ucs.get("y_axis") or ucs.get("yaxis"), [0.0, 1.0, 0.0])
    x_len = math.hypot(x_axis[0], x_axis[1]) or 1.0
    y_len = math.hypot(y_axis[0], y_axis[1]) or 1.0
    ux = [x_axis[0] / x_len, x_axis[1] / x_len]
    uy = [y_axis[0] / y_len, y_axis[1] / y_len]
    return [
        [ux[0], ux[1], -(ux[0] * origin[0] + ux[1] * origin[1])],
        [uy[0], uy[1], -(uy[0] * origin[0] + uy[1] * origin[1])],
        [0.0, 0.0, 1.0],
    ]


def _view_dimensions(view: Dict[str, Any],
                     image_width: int,
                     image_height: int) -> Tuple[float, float]:
    height = float(view.get("height") or view.get("view_height") or 0.0)
    width = float(view.get("width") or view.get("view_width") or 0.0)
    if height <= 0:
        height = 100.0
    if width <= 0:
        width = height * (float(image_width) / max(float(image_height), 1.0))
    return width, height


def compute_view_transform(view: Dict[str, Any],
                           image_width: int,
                           image_height: int,
                           ucs: Optional[Dict[str, Any]] = None,
                           viewport: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Compute WCS/world <-> image pixel transforms for a 2D AutoCAD view.

    The exact path for top/plan views is:
    WCS -> optional UCS plane -> view DCS with twist -> image content box -> pixel.
    Non-plan directions still return a usable approximation with lower
    confidence and explicit limitations.
    """
    del viewport
    view = dict(view or {})
    warnings: List[str] = []
    limitations: List[str] = []
    direction = _normalize_direction(view.get("direction") or view.get("view_direction"))
    is_plan = abs(direction[0]) <= 1e-9 and abs(direction[1]) <= 1e-9 and abs(abs(direction[2]) - 1.0) <= 1e-9
    confidence = 0.98 if is_plan else 0.45
    if not is_plan:
        limitations.append("non_plan_view")
        warnings.append("Exact 3D/non-plan projection is unavailable; using a 2D plan-view approximation.")

    width, height = _view_dimensions(view, image_width, image_height)
    center = _point(view.get("center") or view.get("target"), [0.0, 0.0, 0.0])
    twist = float(view.get("twist") or view.get("twist_angle") or view.get("view_twist") or 0.0)

    scale = min(float(image_width) / width, float(image_height) / height)
    content_width = width * scale
    content_height = height * scale
    offset_x = (float(image_width) - content_width) / 2.0
    offset_y = (float(image_height) - content_height) / 2.0

    cos_t = math.cos(twist)
    sin_t = math.sin(twist)

    world_to_work = _world_to_ucs_matrix(ucs or {}) if ucs else [
        [1.0, 0.0, 0.0],
        [0.0, 1.0, 0.0],
        [0.0, 0.0, 1.0],
    ]
    center_work = apply_matrix_2d(world_to_work, center[0], center[1])

    # World/UCS work coordinates -> DCS view coordinates.
    work_to_view = [
        [cos_t, sin_t, -(cos_t * center_work[0] + sin_t * center_work[1])],
        [-sin_t, cos_t, sin_t * center_work[0] - cos_t * center_work[1]],
        [0.0, 0.0, 1.0],
    ]
    view_to_pixel = [
        [scale, 0.0, offset_x + width * scale / 2.0],
        [0.0, -scale, offset_y + height * scale / 2.0],
        [0.0, 0.0, 1.0],
    ]
    world_to_view = _compose_2d(work_to_view, world_to_work)
    world_to_pixel = _compose_2d(view_to_pixel, world_to_view)
    pixel_to_world = _matrix_inverse_2d(world_to_pixel)

    view_corners = [
        [-width / 2.0, -height / 2.0],
        [width / 2.0, -height / 2.0],
        [width / 2.0, height / 2.0],
        [-width / 2.0, height / 2.0],
    ]
    view_to_work = _matrix_inverse_2d(work_to_view)
    work_to_world = _matrix_inverse_2d(world_to_work)
    world_corners = [
        apply_matrix_2d(work_to_world, *apply_matrix_2d(view_to_work, vx, vy))
        for vx, vy in view_corners
    ]
    extent = (
        min(point[0] for point in world_corners),
        min(point[1] for point in world_corners),
        max(point[0] for point in world_corners),
        max(point[1] for point in world_corners),
    )
    if abs(twist) > 1e-12:
        warnings.append(f"View twist {twist:.6g} radians was included in the mapping.")
    if ucs:
        warnings.append("UCS axes were included in the mapping when supplied by the view context.")
    content_bbox = [
        offset_x,
        offset_y,
        offset_x + content_width,
        offset_y + content_height,
    ]
    return {
        "world_to_pixel": world_to_pixel,
        "pixel_to_world": pixel_to_world,
        "world_extent": extent,
        "content_bbox": content_bbox,
        "scale": scale,
        "confidence": round(confidence, 3),
        "warnings": warnings,
        "limitations": limitations,
        "transform_chain": {
            "wcs_to_ucs": world_to_work,
            "ucs_to_dcs": work_to_view,
            "dcs_to_pixel": view_to_pixel,
            "world_to_pixel": world_to_pixel,
            "pixel_to_world": pixel_to_world,
        },
    }


def compute_plan_view_transform(view: Dict[str, Any],
                                image_width: int,
                                image_height: int) -> Dict[str, Any]:
    """Backward-compatible wrapper for the improved view transform."""
    transform = compute_view_transform(view, image_width, image_height)
    # Preserve the original no-warning behavior for exact untwisted plan views.
    if not transform["limitations"] and abs(float((view or {}).get("twist") or 0.0)) <= 1e-12:
        transform["warnings"] = []
    return transform


def apply_matrix_2d(matrix: Sequence[Sequence[float]],
                    x: float,
                    y: float) -> List[float]:
    px = matrix[0][0] * x + matrix[0][1] * y + matrix[0][2]
    py = matrix[1][0] * x + matrix[1][1] * y + matrix[1][2]
    return [px, py]


def bbox_world_to_pixel(bbox: Tuple[float, float, float, float],
                        matrix: Sequence[Sequence[float]]) -> List[float]:
    corners = [
        apply_matrix_2d(matrix, bbox[0], bbox[1]),
        apply_matrix_2d(matrix, bbox[2], bbox[1]),
        apply_matrix_2d(matrix, bbox[2], bbox[3]),
        apply_matrix_2d(matrix, bbox[0], bbox[3]),
    ]
    return [
        min(point[0] for point in corners),
        min(point[1] for point in corners),
        max(point[0] for point in corners),
        max(point[1] for point in corners),
    ]


def _read_bmp_size(filepath: str) -> Optional[Tuple[int, int]]:
    try:
        with open(filepath, "rb") as fh:
            header = fh.read(26)
        if header[:2] != b"BM":
            return None
        width = int.from_bytes(header[18:22], "little", signed=True)
        height = abs(int.from_bytes(header[22:26], "little", signed=True))
        return width, height
    except Exception:
        return None


def _image_size(filepath: str) -> Tuple[int, int]:
    suffix = Path(filepath).suffix.lower()
    if suffix == ".bmp":
        size = _read_bmp_size(filepath)
        if size:
            return size
    try:
        from PIL import Image

        with Image.open(filepath) as image:
            return int(image.width), int(image.height)
    except Exception:
        return DEFAULT_IMAGE_SIZE


def _svg_escape(value: Any) -> str:
    return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _write_svg_overlay(path: Path,
                       image_width: int,
                       image_height: int,
                       items: List[Dict[str, Any]],
                       warnings: Optional[List[str]] = None) -> str:
    overlay_path = path.with_name(f"{path.stem}_overlay.svg")
    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{image_width}" height="{image_height}" viewBox="0 0 {image_width} {image_height}">',
        '<rect width="100%" height="100%" fill="white"/>',
    ]
    for item in items:
        bbox = item.get("pixel_bbox") or [0, 0, 0, 0]
        x1, y1, x2, y2 = [float(v) for v in bbox[:4]]
        label = item.get("overlay_id", "?")
        lines.append(
            f'<rect x="{x1:.2f}" y="{y1:.2f}" width="{max(0.5, x2 - x1):.2f}" '
            f'height="{max(0.5, y2 - y1):.2f}" fill="none" stroke="#e11d48" stroke-width="2"/>'
        )
        lines.append(
            f'<text x="{x1:.2f}" y="{max(12.0, y1 - 3):.2f}" font-size="13" '
            f'font-family="Arial" font-weight="700" fill="#111827">{_svg_escape(label)}</text>'
        )
        lines.append(f'<title>{_svg_escape(label)}: {_svg_escape(item.get("handle", ""))}</title>')
    for index, warning in enumerate(warnings or [], start=1):
        lines.append(
            f'<text x="8" y="{image_height - 8 - 14 * (index - 1)}" font-size="11" '
            f'font-family="Arial" fill="#92400e">{_svg_escape(warning)}</text>'
        )
    lines.append("</svg>")
    overlay_path.write_text("\n".join(lines), encoding="utf-8")
    return str(overlay_path)


def _draw_raster_overlay(path: Path,
                         image_width: int,
                         image_height: int,
                         items: List[Dict[str, Any]]) -> Optional[str]:
    if path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff"}:
        return None
    if not path.exists():
        return None
    try:
        from PIL import Image, ImageDraw, ImageFont

        with Image.open(path) as source:
            image = source.convert("RGBA")
        draw = ImageDraw.Draw(image, "RGBA")
        font = ImageFont.load_default()
        for item in items:
            x1, y1, x2, y2 = [float(v) for v in (item.get("pixel_bbox") or [0, 0, 0, 0])[:4]]
            label = str(item.get("overlay_id") or "?")
            draw.rectangle([x1, y1, x2, y2], outline=(225, 29, 72, 255), width=3)
            text_box = draw.textbbox((0, 0), label, font=font)
            tw = text_box[2] - text_box[0] + 8
            th = text_box[3] - text_box[1] + 6
            label_y = max(0, y1 - th - 2)
            draw.rectangle([x1, label_y, x1 + tw, label_y + th], fill=(17, 24, 39, 220))
            draw.text((x1 + 4, label_y + 3), label, fill=(255, 255, 255, 255), font=font)
        overlay_path = path.with_name(f"{path.stem}_overlay.png")
        if image.width != image_width or image.height != image_height:
            image_width, image_height = image.width, image.height
        del image_width, image_height
        image.save(overlay_path)
        return str(overlay_path)
    except Exception:
        return None


def _entity_semantic_tags(database: CADDatabase) -> Dict[str, List[str]]:
    ensure_understanding_schema(database)
    scope = current_scope(database)
    tags: Dict[str, List[str]] = {}
    with database._conn() as conn:
        rows = conn.execute('''
            SELECT object_type, entity_handles
            FROM cad_semantic_objects
            WHERE workspace_id = ? AND drawing_id = ?
              AND conversation_id = ? AND thread_id = ?
        ''', (
            scope["workspace_id"], scope["drawing_id"],
            scope["conversation_id"], scope["thread_id"],
        )).fetchall()
    for row in rows:
        handles = decode_json(row["entity_handles"], [])
        for handle in handles if isinstance(handles, list) else []:
            tag = str(row["object_type"] or "")
            if tag:
                tags.setdefault(str(handle), []).append(tag)
    return {handle: sorted(set(values)) for handle, values in tags.items()}


def _build_overlay_items(database: CADDatabase,
                         visible_handles: List[str],
                         screen_bboxes: Dict[str, List[float]]) -> List[Dict[str, Any]]:
    entities = {str(entity.get("handle")): entity for entity in all_entities(database)}
    semantic_tags = _entity_semantic_tags(database)
    items: List[Dict[str, Any]] = []
    for index, handle in enumerate(visible_handles, start=1):
        entity = entities.get(handle, {})
        bbox = bbox_from_row(entity)
        items.append({
            "overlay_id": f"E{index:03d}",
            "handle": handle,
            "native_handle": str(entity.get("native_handle") or handle),
            "entity_type": entity.get("type") or entity.get("entity_type") or entity.get("name") or "Unknown",
            "layer": entity.get("layer") or "0",
            "pixel_bbox": screen_bboxes.get(handle, []),
            "world_bbox": bbox_dict(bbox),
            "semantic_tags": semantic_tags.get(handle, []),
            "confidence": 0.95 if bbox else 0.45,
        })
    return items


def create_overlay_artifact(clean_image_path: str,
                            entity_screen_bboxes: Any,
                            context: Dict[str, Any]) -> Dict[str, Any]:
    """Create a real raster overlay when possible, otherwise SVG fallback."""
    path = Path(clean_image_path)
    image = context.get("image", {})
    image_width = int(image.get("width") or DEFAULT_IMAGE_SIZE[0])
    image_height = int(image.get("height") or DEFAULT_IMAGE_SIZE[1])
    warnings = list(context.get("warnings") or [])
    if isinstance(entity_screen_bboxes, list):
        items = entity_screen_bboxes
    else:
        items = [
            {"overlay_id": f"E{index:03d}", "handle": handle, "pixel_bbox": bbox}
            for index, (handle, bbox) in enumerate(dict(entity_screen_bboxes or {}).items(), start=1)
        ]

    overlay_path = _draw_raster_overlay(path, image_width, image_height, items)
    if overlay_path:
        artifact_warnings = []
    else:
        artifact_warnings = [
            "Raster overlay was unavailable; generated an external SVG overlay fallback."
        ]
        overlay_path = _write_svg_overlay(path, image_width, image_height, items, warnings=artifact_warnings)

    sidecar = {
        "clean_image_path": str(path),
        "overlay_image_path": overlay_path,
        "overlay_items": items,
        "image": {"width": image_width, "height": image_height},
        "warnings": warnings + artifact_warnings,
    }
    sidecar_path = path.with_name(f"{path.stem}_overlay_items.json")
    sidecar_path.write_text(json.dumps(sidecar, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    return {
        "overlay_image_path": overlay_path,
        "overlay_items_path": str(sidecar_path),
        "overlay_items": items,
        "warnings": artifact_warnings,
    }


def _store_snapshot(database: CADDatabase, snapshot: Dict[str, Any]) -> None:
    ensure_understanding_schema(database)
    scope = current_scope(database)
    with database._conn() as conn:
        conn.execute('''
            INSERT OR REPLACE INTO cad_view_snapshots
                (snapshot_id, image_path, overlay_image_path, context_json_path,
                 snapshot_data, workspace_id, drawing_id, conversation_id,
                 thread_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            snapshot["snapshot_id"],
            snapshot.get("clean_image_path") or snapshot.get("image_path", ""),
            snapshot.get("overlay_image_path", ""),
            snapshot.get("context_json_path", ""),
            json.dumps(snapshot, ensure_ascii=False, default=str),
            scope["workspace_id"], scope["drawing_id"],
            scope["conversation_id"], scope["thread_id"],
        ))


def _load_snapshot(database: CADDatabase, snapshot_id: str) -> Optional[Dict[str, Any]]:
    ensure_understanding_schema(database)
    scope = current_scope(database)
    with database._conn() as conn:
        row = conn.execute('''
            SELECT snapshot_data
            FROM cad_view_snapshots
            WHERE snapshot_id = ? AND workspace_id = ? AND drawing_id = ?
              AND conversation_id = ? AND thread_id = ?
        ''', (
            snapshot_id,
            scope["workspace_id"], scope["drawing_id"],
            scope["conversation_id"], scope["thread_id"],
        )).fetchone()
    if not row:
        return None
    return json.loads(row["snapshot_data"] or "{}")


def _visible_entity_bboxes(database: CADDatabase,
                           view_extent: Tuple[float, float, float, float],
                           matrix: Sequence[Sequence[float]]) -> Tuple[List[str], Dict[str, List[float]]]:
    visible = []
    screen_bboxes: Dict[str, List[float]] = {}
    for entity in all_entities(database):
        bbox = bbox_from_row(entity)
        if bbox is None or not bbox_intersects(bbox, view_extent):
            continue
        handle = str(entity.get("handle"))
        visible.append(handle)
        screen_bboxes[handle] = bbox_world_to_pixel(bbox, matrix)
    return visible, screen_bboxes


def _scanned_entity_extent(database: CADDatabase) -> Optional[BBox]:
    return bbox_union(bbox_from_row(entity) for entity in all_entities(database))


def _view_from_extent(extent: BBox,
                      image_width: int,
                      image_height: int,
                      padding_ratio: float = 0.08) -> Dict[str, Any]:
    min_x, min_y, max_x, max_y = extent
    width = max(max_x - min_x, 1.0)
    height = max(max_y - min_y, 1.0)
    padding = max(width, height, 1.0) * max(float(padding_ratio), 0.0)
    width += padding * 2.0
    height += padding * 2.0
    aspect = max(float(image_width), 1.0) / max(float(image_height), 1.0)
    if width / max(height, 1e-9) > aspect:
        height = width / aspect
    else:
        width = height * aspect
    return {
        "center": [(min_x + max_x) / 2.0, (min_y + max_y) / 2.0, 0.0],
        "target": [(min_x + max_x) / 2.0, (min_y + max_y) / 2.0, 0.0],
        "height": height,
        "width": width,
        "direction": [0.0, 0.0, 1.0],
        "view_direction": [0.0, 0.0, 1.0],
        "twist": 0.0,
    }


def get_current_view_context(filepath: Optional[str] = None,
                             image_size: Optional[Tuple[int, int]] = None) -> Dict[str, Any]:
    """Read the current AutoCAD view through tool/controller layers when available."""
    warnings: List[str] = []
    view: Dict[str, Any]
    try:
        from src.cad_tools import file_tools, view_tools

        if not getattr(view_tools.ctrl, "has_document", False):
            raise RuntimeError("AutoCAD controller has no active document")
        raw = view_tools.get_current_view()
        view = json.loads(raw) if isinstance(raw, str) else dict(raw or {})
        if not isinstance(view, dict) or view.get("error"):
            raise ValueError(view.get("error") if isinstance(view, dict) else "invalid view payload")
        try:
            info = json.loads(file_tools.get_document_info())
        except Exception:
            info = {}
        active_space = str(info.get("active_space") or view.get("active_space") or "model").lower()
    except Exception as exc:
        view = {"center": [0, 0, 0], "height": 100, "width": 160, "target": [0, 0, 0], "direction": [0, 0, 1]}
        active_space = "model"
        warnings.append(f"Could not read current AutoCAD view; used default mapping view: {exc}")

    image_width, image_height = image_size or (_image_size(filepath) if filepath else DEFAULT_IMAGE_SIZE)
    context = {
        "space": "paper" if active_space in {"paper", "1"} else "model",
        "ucs": view.get("ucs") or {},
        "view": {
            "target": view.get("target") or view.get("center") or [0, 0, 0],
            "height": view.get("height"),
            "width": view.get("width"),
            "view_direction": view.get("direction") or view.get("view_direction") or [0, 0, 1],
            "direction": view.get("direction") or view.get("view_direction") or [0, 0, 1],
            "twist": view.get("twist") or view.get("twist_angle") or view.get("view_twist") or 0.0,
            "center": view.get("center") or view.get("target") or [0, 0, 0],
        },
        "viewport": view.get("viewport") or {},
        "image": {"width": image_width, "height": image_height},
        "transform_chain": {},
        "limitations": [],
        "warnings": warnings,
    }
    transform = compute_view_transform(
        context["view"],
        image_width,
        image_height,
        ucs=context["ucs"],
        viewport=context["viewport"],
    )
    context["transform_chain"] = transform["transform_chain"]
    context["limitations"] = transform["limitations"]
    context["warnings"].extend(transform["warnings"])
    context["confidence"] = transform["confidence"]
    return context


def export_view_image_with_mapping(filepath: Optional[str] = None,
                                   include_overlay: bool = True,
                                   include_entity_bboxes: bool = True,
                                   database: Optional[CADDatabase] = None) -> ToolResult:
    db = get_db(database)
    if filepath is None or not str(filepath).strip():
        out_dir = Path.cwd() / "cad_visual_exports"
        out_dir.mkdir(parents=True, exist_ok=True)
        filepath = str(out_dir / f"cad_view_mapped_{stable_id('shot', now_iso())}.wmf")
    path = Path(filepath)

    warnings: List[str] = []
    export_message = ""
    try:
        from src.cad_tools import file_tools

        if getattr(file_tools.ctrl, "has_document", False):
            export_message = file_tools.export_view_image(str(path))
        else:
            warnings.append("AutoCAD controller has no active document; skipped live view export and used metadata-only mapping.")
    except Exception as exc:
        warnings.append(f"View export failed or AutoCAD is unavailable: {exc}")

    image_width, image_height = _image_size(str(path))
    context = get_current_view_context(str(path), (image_width, image_height))
    warnings.extend(context.get("warnings", []))
    scanned_extent = _scanned_entity_extent(db)
    mapping_view_source = "current_autocad_view"
    if path.suffix.lower() == ".wmf" and scanned_extent:
        context["view"] = _view_from_extent(scanned_extent, image_width, image_height)
        mapping_view_source = "scanned_entity_extent_for_wmf_export"
        warnings.append(
            "WMF export uses AutoCAD selection-set extents; mapping was derived from scanned entity extents."
        )
    transform = compute_view_transform(
        context["view"],
        image_width,
        image_height,
        ucs=context.get("ucs"),
        viewport=context.get("viewport"),
    )
    warnings.extend(w for w in transform["warnings"] if w not in warnings)
    visible_handles: List[str] = []
    entity_screen_bboxes: Dict[str, List[float]] = {}
    if include_entity_bboxes:
        visible_handles, entity_screen_bboxes = _visible_entity_bboxes(
            db, transform["world_extent"], transform["world_to_pixel"]
        )
        if not visible_handles and scanned_extent:
            context["view"] = _view_from_extent(scanned_extent, image_width, image_height)
            transform = compute_view_transform(
                context["view"],
                image_width,
                image_height,
                ucs=context.get("ucs"),
                viewport=context.get("viewport"),
            )
            visible_handles, entity_screen_bboxes = _visible_entity_bboxes(
                db, transform["world_extent"], transform["world_to_pixel"]
            )
            mapping_view_source = "scanned_entity_extent_fallback"
            warnings.append(
                "Current AutoCAD view contained no scanned entities; mapping fell back to scanned entity extents."
            )

    overlay_items = _build_overlay_items(db, visible_handles, entity_screen_bboxes)
    overlay_path = ""
    overlay_items_path = ""
    if include_overlay:
        overlay = create_overlay_artifact(
            str(path),
            overlay_items,
            {**context, "warnings": warnings, "image": {"width": image_width, "height": image_height}},
        )
        overlay_path = overlay["overlay_image_path"]
        overlay_items_path = overlay["overlay_items_path"]
        warnings.extend(overlay.get("warnings", []))

    snapshot = {
        "snapshot_id": stable_id("snapshot", str(path), now_iso()),
        "clean_image_path": str(path),
        "image_path": str(path),
        "overlay_image_path": overlay_path,
        "context_json_path": "",
        "overlay_items_path": overlay_items_path,
        "overlay_items": overlay_items,
        "view": context["view"],
        "ucs": context.get("ucs", {}),
        "viewport": context.get("viewport", {}),
        "space": context.get("space", "model"),
        "image": {"width": image_width, "height": image_height},
        "content_bbox": transform["content_bbox"],
        "world_to_pixel": transform["world_to_pixel"],
        "pixel_to_world": transform["pixel_to_world"],
        "transform_chain": transform["transform_chain"],
        "confidence": transform["confidence"],
        "limitations": transform["limitations"],
        "mapping_view_source": mapping_view_source,
        "scanned_entity_extent": bbox_dict(scanned_extent),
        "visible_handles": visible_handles,
        "entity_screen_bboxes": entity_screen_bboxes,
        "export_message": export_message,
    }
    context_path = path.with_name(f"{path.stem}_mapping.json")
    snapshot["context_json_path"] = str(context_path)
    context_path.write_text(json.dumps(snapshot, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    _store_snapshot(db, snapshot)
    return ok_result(
        "Exported view image mapping snapshot.",
        data={"snapshot": snapshot},
        handles=visible_handles,
        warnings=sorted(set(warnings)),
        next_tools=["ground_vlm_region", "ground_vlm_overlay_id", "get_visible_entities_in_view", "explain_entity"],
    )


def get_visible_entities_in_view(snapshot_id: str,
                                 database: Optional[CADDatabase] = None) -> ToolResult:
    snapshot = _load_snapshot(get_db(database), snapshot_id)
    if not snapshot:
        return error_result(f"Unknown view snapshot: {snapshot_id}")
    handles = snapshot.get("visible_handles", [])
    return ok_result(
        f"Snapshot {snapshot_id} has {len(handles)} visible entities.",
        data={
            "visible_handles": handles,
            "entity_screen_bboxes": snapshot.get("entity_screen_bboxes", {}),
            "overlay_items": snapshot.get("overlay_items", []),
        },
        handles=handles,
        next_tools=["ground_vlm_region", "ground_vlm_overlay_id", "explain_entity"],
    )


def map_pixel_to_world(snapshot_id: str,
                       x: float,
                       y: float,
                       database: Optional[CADDatabase] = None) -> ToolResult:
    snapshot = _load_snapshot(get_db(database), snapshot_id)
    if not snapshot:
        return error_result(f"Unknown view snapshot: {snapshot_id}")
    world = apply_matrix_2d(snapshot["pixel_to_world"], float(x), float(y))
    return ok_result(
        "Mapped pixel to world coordinates.",
        data={
            "world": [world[0], world[1], 0.0],
            "pixel": [x, y],
            "snapshot_id": snapshot_id,
            "confidence": snapshot.get("confidence", 0.5),
            "limitations": snapshot.get("limitations", []),
        },
        warnings=snapshot.get("limitations", []),
    )


def map_world_to_pixel(snapshot_id: str,
                       x: float,
                       y: float,
                       z: float = 0.0,
                       database: Optional[CADDatabase] = None) -> ToolResult:
    snapshot = _load_snapshot(get_db(database), snapshot_id)
    if not snapshot:
        return error_result(f"Unknown view snapshot: {snapshot_id}")
    pixel = apply_matrix_2d(snapshot["world_to_pixel"], float(x), float(y))
    return ok_result(
        "Mapped world coordinates to pixel.",
        data={
            "pixel": pixel,
            "world": [x, y, z],
            "snapshot_id": snapshot_id,
            "confidence": snapshot.get("confidence", 0.5),
            "limitations": snapshot.get("limitations", []),
        },
        warnings=snapshot.get("limitations", []),
    )


def map_pixel_region_to_world_bbox(snapshot_id: str,
                                   bbox: List[float],
                                   database: Optional[CADDatabase] = None) -> ToolResult:
    snapshot = _load_snapshot(get_db(database), snapshot_id)
    if not snapshot:
        return error_result(f"Unknown view snapshot: {snapshot_id}")
    if not isinstance(bbox, list) or len(bbox) < 4:
        return error_result("bbox must be [x1, y1, x2, y2]")
    x1, y1, x2, y2 = [float(v) for v in bbox[:4]]
    corners = [
        apply_matrix_2d(snapshot["pixel_to_world"], x1, y1),
        apply_matrix_2d(snapshot["pixel_to_world"], x2, y1),
        apply_matrix_2d(snapshot["pixel_to_world"], x2, y2),
        apply_matrix_2d(snapshot["pixel_to_world"], x1, y2),
    ]
    world_bbox = (
        min(point[0] for point in corners),
        min(point[1] for point in corners),
        max(point[0] for point in corners),
        max(point[1] for point in corners),
    )
    return ok_result(
        "Mapped pixel region to world bbox.",
        data={
            "snapshot_id": snapshot_id,
            "pixel_bbox": [x1, y1, x2, y2],
            "world_bbox": bbox_dict(world_bbox),
            "confidence": snapshot.get("confidence", 0.5),
            "limitations": snapshot.get("limitations", []),
        },
        warnings=snapshot.get("limitations", []),
    )


def _primitive_bbox(primitive: Dict[str, Any]) -> Optional[BBox]:
    x = primitive.get("x")
    y = primitive.get("y")
    x2 = primitive.get("x2")
    y2 = primitive.get("y2")
    radius = primitive.get("radius")
    try:
        if x is not None and y is not None and radius is not None:
            r = abs(float(radius))
            return (float(x) - r, float(y) - r, float(x) + r, float(y) + r)
        if x is not None and y is not None and x2 is not None and y2 is not None:
            return (min(float(x), float(x2)), min(float(y), float(y2)),
                    max(float(x), float(x2)), max(float(y), float(y2)))
        if x is not None and y is not None:
            return (float(x), float(y), float(x), float(y))
    except Exception:
        return None
    return None


def _primitive_candidates(database: CADDatabase,
                          handle: str,
                          query_bbox: List[float],
                          snapshot: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], List[str]]:
    topology = topology_for_handle(database, handle)
    primitives = topology.get("primitives", [])
    if not primitives:
        return [], ["Primitive topology is unavailable; grounding is entity-level only."]
    query_center = [(query_bbox[0] + query_bbox[2]) / 2.0, (query_bbox[1] + query_bbox[3]) / 2.0]
    image = snapshot.get("image", {})
    diag = max(point_distance([0, 0], [image.get("width", 1), image.get("height", 1)]), 1.0)
    ranked = []
    for primitive in primitives:
        world_bbox = _primitive_bbox(primitive)
        if not world_bbox:
            continue
        pixel_bbox = bbox_world_to_pixel(world_bbox, snapshot["world_to_pixel"])
        center = bbox_center(tuple(pixel_bbox))
        iou = bbox_iou(query_bbox, pixel_bbox)
        distance = point_distance(query_center, center or query_center)
        distance_score = max(0.0, 1.0 - distance / diag)
        score = max(iou, 0.65 * iou + 0.35 * distance_score)
        if iou > 0.0 or score > 0.1:
            ranked.append({
                "primitive_key": primitive.get("primitive_key"),
                "primitive_type": primitive.get("primitive_type"),
                "role": primitive.get("role"),
                "score": round(min(score, 1.0), 4),
                "evidence": {
                    "iou_score": round(iou, 4),
                    "distance_score": round(distance_score, 4),
                    "pixel_bbox": pixel_bbox,
                    "world_bbox": bbox_dict(world_bbox),
                },
            })
    ranked.sort(key=lambda item: -item["score"])
    return ranked[:10], []


def _candidate_from_overlay_item(database: CADDatabase,
                                 item: Dict[str, Any],
                                 query_bbox: List[float],
                                 snapshot: Dict[str, Any]) -> Dict[str, Any]:
    ent_bbox = [float(v) for v in (item.get("pixel_bbox") or [0, 0, 0, 0])[:4]]
    query_center = [(query_bbox[0] + query_bbox[2]) / 2.0, (query_bbox[1] + query_bbox[3]) / 2.0]
    image = snapshot.get("image", {})
    diag = max(point_distance([0, 0], [image.get("width", 1), image.get("height", 1)]), 1.0)
    ent_center = bbox_center(tuple(ent_bbox))
    iou = bbox_iou(query_bbox, ent_bbox)
    distance = point_distance(query_center, ent_center or query_center)
    distance_score = max(0.0, 1.0 - distance / diag)
    score = 0.75 * iou + 0.25 * distance_score
    primitive_matches, primitive_warnings = _primitive_candidates(
        database,
        str(item.get("handle")),
        query_bbox,
        snapshot,
    )
    warnings = list(snapshot.get("limitations", [])) + primitive_warnings
    return {
        "handle": item.get("handle"),
        "native_handle": item.get("native_handle") or item.get("handle"),
        "entity_type": item.get("entity_type"),
        "overlay_id": item.get("overlay_id"),
        "score": round(score, 4),
        "iou_score": round(iou, 4),
        "distance_score": round(distance_score, 4),
        "pixel_bbox": ent_bbox,
        "world_bbox": item.get("world_bbox"),
        "candidate_primitives": primitive_matches,
        "confidence": round(min(1.0, score * float(snapshot.get("confidence", 0.5) or 0.5)), 4),
        "limitations": warnings,
        "evidence": {
            "query_bbox": query_bbox,
            "entity_pixel_bbox": ent_bbox,
            "snapshot_confidence": snapshot.get("confidence", 0.5),
        },
    }


def ground_vlm_region(snapshot_id: str,
                      bbox: List[float],
                      top_k: int = 10,
                      database: Optional[CADDatabase] = None) -> ToolResult:
    db = get_db(database)
    snapshot = _load_snapshot(db, snapshot_id)
    if not snapshot:
        return error_result(f"Unknown view snapshot: {snapshot_id}")
    if not isinstance(bbox, list) or len(bbox) < 4:
        return error_result("bbox must be [x1, y1, x2, y2]")
    query_bbox = [float(v) for v in bbox[:4]]
    candidates = []
    for item in snapshot.get("overlay_items", []):
        candidate = _candidate_from_overlay_item(db, item, query_bbox, snapshot)
        if candidate["iou_score"] > 0.0 or candidate["score"] > 0.1:
            candidates.append(candidate)
    if not candidates and snapshot.get("entity_screen_bboxes"):
        fallback_items = [
            {"overlay_id": "", "handle": handle, "native_handle": handle, "pixel_bbox": ent_bbox}
            for handle, ent_bbox in snapshot.get("entity_screen_bboxes", {}).items()
        ]
        candidates = [
            _candidate_from_overlay_item(db, item, query_bbox, snapshot)
            for item in fallback_items
        ]
    candidates.sort(key=lambda item: -item["score"])
    candidates = candidates[:max(1, min(int(top_k or 10), 100))]
    world_region = map_pixel_region_to_world_bbox(snapshot_id, query_bbox, database=db)
    return ok_result(
        f"Grounded VLM region to {len(candidates)} candidate entities.",
        data={
            "candidates": candidates,
            "bbox": query_bbox,
            "world_region": world_region["data"].get("world_bbox") if world_region["ok"] else None,
            "snapshot_id": snapshot_id,
            "confidence": snapshot.get("confidence", 0.5),
            "limitations": snapshot.get("limitations", []),
        },
        handles=[str(candidate["handle"]) for candidate in candidates if candidate.get("handle")],
        warnings=snapshot.get("limitations", []),
        next_tools=["ground_vlm_overlay_id", "explain_entity", "validate_geometry"],
    )


def ground_vlm_overlay_id(snapshot_id: str,
                          overlay_id: str,
                          database: Optional[CADDatabase] = None) -> ToolResult:
    db = get_db(database)
    snapshot = _load_snapshot(db, snapshot_id)
    if not snapshot:
        return error_result(f"Unknown view snapshot: {snapshot_id}")
    overlay_norm = str(overlay_id or "").strip().upper()
    item = next(
        (entry for entry in snapshot.get("overlay_items", [])
         if str(entry.get("overlay_id", "")).upper() == overlay_norm),
        None,
    )
    if item is None:
        return error_result(
            f"Unknown overlay_id {overlay_id} for snapshot {snapshot_id}.",
            data={"available_overlay_ids": [entry.get("overlay_id") for entry in snapshot.get("overlay_items", [])]},
        )
    bbox = [float(v) for v in (item.get("pixel_bbox") or [0, 0, 0, 0])[:4]]
    candidate = _candidate_from_overlay_item(db, item, bbox, snapshot)
    candidate["score"] = 1.0
    candidate["iou_score"] = 1.0
    candidate["distance_score"] = 1.0
    return ok_result(
        f"Grounded overlay_id {overlay_id} to handle {item.get('handle')}.",
        data={"candidate": candidate, "snapshot_id": snapshot_id},
        handles=[str(item.get("handle"))],
        warnings=candidate.get("limitations", []),
        next_tools=["explain_entity", "validate_geometry"],
    )


def overlay_id_sort_key(value: str) -> Tuple[int, str]:
    match = re.search(r"(\d+)$", value or "")
    return (int(match.group(1)) if match else 0, value or "")
