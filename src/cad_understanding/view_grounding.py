"""View snapshot mapping, overlay artifacts, and VLM region grounding."""

from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
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


def _read_wmf_size(filepath: str) -> Optional[Tuple[int, int]]:
    """Read pixel dimensions from a Placeable WMF (APM) header."""
    try:
        with open(filepath, "rb") as fh:
            header = fh.read(22)
        if len(header) < 22:
            return None
        magic = int.from_bytes(header[:4], "little")
        if magic != 0x9AC6CDD7:
            return None
        left = int.from_bytes(header[2:4], "little", signed=True)
        top = int.from_bytes(header[4:6], "little", signed=True)
        right = int.from_bytes(header[6:8], "little", signed=True)
        bottom = int.from_bytes(header[8:10], "little", signed=True)
        units_per_inch = int.from_bytes(header[10:12], "little") or 96
        width = int(abs(right - left) * 96 / units_per_inch)
        height = int(abs(bottom - top) * 96 / units_per_inch)
        if width > 0 and height > 0:
            return width, height
    except Exception:
        pass
    return None


def _try_convert_wmf_to_raster(wmf_path: Path) -> Optional[Path]:
    """Convert a WMF file to PNG using available system tools.

    Tries ImageMagick (magick/convert), then wand, then Inkscape, then
    LibreOffice (soffice). Returns the PNG path on success, None if all
    attempts fail.
    """
    png_path = wmf_path.with_suffix(".png")

    magick = shutil.which("magick") or shutil.which("convert")
    if magick:
        try:
            result = subprocess.run(
                [magick, str(wmf_path), str(png_path)],
                capture_output=True,
                timeout=30,
            )
            if result.returncode == 0 and png_path.exists() and png_path.stat().st_size > 0:
                return png_path
        except Exception:
            pass

    try:
        from wand.image import Image as WandImage

        with WandImage(filename=str(wmf_path)) as img:
            img.format = "png"
            img.save(filename=str(png_path))
        if png_path.exists() and png_path.stat().st_size > 0:
            return png_path
    except Exception:
        pass

    inkscape = shutil.which("inkscape")
    if inkscape:
        try:
            result = subprocess.run(
                [inkscape, str(wmf_path), "--export-type=png", f"--export-filename={png_path}"],
                capture_output=True,
                timeout=30,
            )
            if result.returncode == 0 and png_path.exists() and png_path.stat().st_size > 0:
                return png_path
        except Exception:
            pass

    # LibreOffice is commonly installed and can rasterize WMF headlessly.
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if soffice:
        try:
            result = subprocess.run(
                [soffice, "--headless", "--convert-to", "png", "--outdir",
                 str(png_path.parent), str(wmf_path)],
                capture_output=True,
                timeout=45,
            )
            if result.returncode == 0 and png_path.exists() and png_path.stat().st_size > 0:
                return png_path
        except Exception:
            pass

    return None


def _image_size(filepath: str) -> Tuple[int, int]:
    suffix = Path(filepath).suffix.lower()
    if suffix == ".bmp":
        size = _read_bmp_size(filepath)
        if size:
            return size
    if suffix == ".wmf":
        size = _read_wmf_size(filepath)
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


def _pixel_bbox_with_min_size(bbox: Sequence[float],
                              min_size: float = 10.0) -> List[float]:
    x1, y1, x2, y2 = [float(v) for v in list(bbox)[:4]]
    if x2 < x1:
        x1, x2 = x2, x1
    if y2 < y1:
        y1, y2 = y2, y1
    width = x2 - x1
    height = y2 - y1
    if width < min_size:
        pad = (min_size - width) / 2.0
        x1 -= pad
        x2 += pad
    if height < min_size:
        pad = (min_size - height) / 2.0
        y1 -= pad
        y2 += pad
    return [x1, y1, x2, y2]


def _overlay_colors(item: Dict[str, Any]) -> Tuple[str, str, str]:
    kind = str(item.get("item_kind") or "entity")
    if kind == "primitive":
        return "#2563eb", "rgba(37, 99, 235, 0.10)", "#1e3a8a"
    if kind == "semantic":
        return "#059669", "rgba(5, 150, 105, 0.10)", "#064e3b"
    return "#e11d48", "rgba(225, 29, 72, 0.08)", "#111827"


def _write_svg_overlay(path: Path,
                       image_width: int,
                       image_height: int,
                       items: List[Dict[str, Any]],
                       warnings: Optional[List[str]] = None,
                       overlay_style: str = "bbox") -> str:
    overlay_path = path.with_name(f"{path.stem}_overlay.svg")
    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{image_width}" height="{image_height}" viewBox="0 0 {image_width} {image_height}">',
        '<rect width="100%" height="100%" fill="white"/>',
    ]
    for item in items:
        bbox = item.get("pixel_bbox") or [0, 0, 0, 0]
        x1, y1, x2, y2 = _pixel_bbox_with_min_size(bbox, min_size=8.0)
        label = item.get("overlay_id", "?")
        stroke, fill, text_color = _overlay_colors(item)
        center_x = (x1 + x2) / 2.0
        center_y = (y1 + y2) / 2.0
        fill_value = fill if overlay_style == "som" else "none"
        lines.append(
            f'<rect x="{x1:.2f}" y="{y1:.2f}" width="{max(0.5, x2 - x1):.2f}" '
            f'height="{max(0.5, y2 - y1):.2f}" fill="{fill_value}" stroke="{stroke}" stroke-width="2"/>'
        )
        if overlay_style == "som":
            radius = max(9.0, min(18.0, (len(str(label)) * 4.2) + 6.0))
            lines.append(
                f'<circle cx="{center_x:.2f}" cy="{center_y:.2f}" r="{radius:.2f}" '
                f'fill="{stroke}" fill-opacity="0.92" stroke="white" stroke-width="2"/>'
            )
            text_x = center_x
            text_y = center_y + 4.0
            anchor = "middle"
            text_color = "white"
        else:
            text_x = x1
            text_y = max(12.0, y1 - 3)
            anchor = "start"
        lines.append(
            f'<text x="{text_x:.2f}" y="{text_y:.2f}" font-size="13" '
            f'font-family="Arial" font-weight="700" text-anchor="{anchor}" '
            f'fill="{text_color}">{_svg_escape(label)}</text>'
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
                         items: List[Dict[str, Any]],
                         overlay_style: str = "bbox") -> Optional[str]:
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
            x1, y1, x2, y2 = _pixel_bbox_with_min_size(item.get("pixel_bbox") or [0, 0, 0, 0])
            label = str(item.get("overlay_id") or "?")
            kind = str(item.get("item_kind") or "entity")
            if kind == "primitive":
                outline = (37, 99, 235, 255)
                fill = (37, 99, 235, 26)
            elif kind == "semantic":
                outline = (5, 150, 105, 255)
                fill = (5, 150, 105, 26)
            else:
                outline = (225, 29, 72, 255)
                fill = (225, 29, 72, 20)
            draw.rectangle([x1, y1, x2, y2], outline=outline, fill=fill if overlay_style == "som" else None, width=3)
            text_box = draw.textbbox((0, 0), label, font=font)
            tw = text_box[2] - text_box[0] + 8
            th = text_box[3] - text_box[1] + 6
            if overlay_style == "som":
                cx = (x1 + x2) / 2.0
                cy = (y1 + y2) / 2.0
                radius = max(10, int(max(tw, th) / 2 + 4))
                draw.ellipse([cx - radius, cy - radius, cx + radius, cy + radius], fill=outline, outline=(255, 255, 255, 255), width=2)
                draw.text((cx - tw / 2 + 4, cy - th / 2 + 3), label, fill=(255, 255, 255, 255), font=font)
            else:
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
            "item_kind": "entity",
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


def _build_primitive_overlay_items(database: CADDatabase,
                                   entity_items: List[Dict[str, Any]],
                                   view_extent: BBox,
                                   matrix: Sequence[Sequence[float]]) -> List[Dict[str, Any]]:
    primitive_items: List[Dict[str, Any]] = []
    for entity_item in entity_items:
        handle = str(entity_item.get("handle") or "")
        parent_id = str(entity_item.get("overlay_id") or "")
        topology = topology_for_handle(database, handle)
        for primitive_index, primitive in enumerate(topology.get("primitives", []), start=1):
            world_bbox = _primitive_bbox(primitive)
            if not world_bbox or not bbox_intersects(world_bbox, view_extent):
                continue
            primitive_items.append({
                "overlay_id": f"{parent_id}.P{primitive_index:02d}",
                "item_kind": "primitive",
                "handle": handle,
                "native_handle": entity_item.get("native_handle") or handle,
                "parent_overlay_id": parent_id,
                "entity_type": entity_item.get("entity_type"),
                "layer": entity_item.get("layer"),
                "primitive_key": primitive.get("primitive_key"),
                "primitive_type": primitive.get("primitive_type"),
                "role": primitive.get("role"),
                "pixel_bbox": bbox_world_to_pixel(world_bbox, matrix),
                "world_bbox": bbox_dict(world_bbox),
                "semantic_tags": entity_item.get("semantic_tags", []),
                "confidence": 0.9,
            })
    return primitive_items


def _overlay_items_for_granularity(database: CADDatabase,
                                   visible_handles: List[str],
                                   screen_bboxes: Dict[str, List[float]],
                                   view_extent: BBox,
                                   matrix: Sequence[Sequence[float]],
                                   overlay_granularity: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    entity_items = _build_overlay_items(database, visible_handles, screen_bboxes)
    primitive_items = _build_primitive_overlay_items(database, entity_items, view_extent, matrix)
    granularity = (overlay_granularity or "entity").lower().strip()
    if granularity == "primitive":
        return primitive_items, primitive_items
    if granularity in {"both", "entity+primitive", "all"}:
        return entity_items + primitive_items, primitive_items
    return entity_items, primitive_items


def _build_tile_index(clean_image_path: str,
                      overlay_image_path: str,
                      image_width: int,
                      image_height: int,
                      overlay_items: List[Dict[str, Any]],
                      tile_size: int = 640,
                      tile_overlap: float = 0.2) -> Dict[str, Any]:
    size = max(128, min(int(tile_size or 640), 4096))
    overlap = max(0.0, min(float(tile_overlap or 0.0), 0.8))
    step = max(1, int(size * (1.0 - overlap)))
    tiles: List[Dict[str, Any]] = []
    raster_suffixes = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff"}
    clean_path = Path(clean_image_path)
    overlay_path = Path(overlay_image_path) if overlay_image_path else Path("")
    can_crop_clean = clean_path.exists() and clean_path.suffix.lower() in raster_suffixes
    can_crop_overlay = overlay_path.exists() and overlay_path.suffix.lower() in raster_suffixes
    clean_image = None
    overlay_image = None
    try:
        if can_crop_clean or can_crop_overlay:
            from PIL import Image

            clean_image = Image.open(clean_path) if can_crop_clean else None
            overlay_image = Image.open(overlay_path) if can_crop_overlay else None
        tile_dir = clean_path.with_name(f"{clean_path.stem}_tiles")
        if clean_image or overlay_image:
            tile_dir.mkdir(parents=True, exist_ok=True)
        tile_index = 1
        y = 0
        while y < image_height:
            x = 0
            y2 = min(image_height, y + size)
            y1 = max(0, y2 - size)
            while x < image_width:
                x2 = min(image_width, x + size)
                x1 = max(0, x2 - size)
                tile_bbox = [float(x1), float(y1), float(x2), float(y2)]
                visible_items = [
                    item for item in overlay_items
                    if bbox_intersects(tile_bbox, item.get("pixel_bbox"))
                ]
                tile: Dict[str, Any] = {
                    "tile_id": f"T{tile_index:03d}",
                    "pixel_bbox": tile_bbox,
                    "overlay_ids": [item.get("overlay_id") for item in visible_items],
                    "item_count": len(visible_items),
                }
                if clean_image:
                    clean_tile_path = tile_dir / f"{clean_path.stem}_{tile['tile_id']}.png"
                    clean_image.crop((x1, y1, x2, y2)).save(clean_tile_path)
                    tile["clean_tile_path"] = str(clean_tile_path)
                if overlay_image:
                    overlay_tile_path = tile_dir / f"{clean_path.stem}_{tile['tile_id']}_overlay.png"
                    overlay_image.crop((x1, y1, x2, y2)).save(overlay_tile_path)
                    tile["overlay_tile_path"] = str(overlay_tile_path)
                tiles.append(tile)
                tile_index += 1
                if x2 >= image_width:
                    break
                x += step
            if y2 >= image_height:
                break
            y += step
    finally:
        for image in (clean_image, overlay_image):
            if image is not None:
                image.close()
    sidecar_path = clean_path.with_name(f"{clean_path.stem}_tiles.json")
    tile_index_payload = {
        "tile_size": size,
        "tile_overlap": overlap,
        "tiles": tiles,
        "warnings": [] if (can_crop_clean or can_crop_overlay) else [
            "Source view artifact was not a supported raster image; tile index contains metadata only."
        ],
    }
    sidecar_path.write_text(json.dumps(tile_index_payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    return {
        "tile_index_path": str(sidecar_path),
        **tile_index_payload,
    }


def create_overlay_artifact(clean_image_path: str,
                            entity_screen_bboxes: Any,
                            context: Dict[str, Any],
                            overlay_style: str = "bbox") -> Dict[str, Any]:
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

    style = "som" if str(overlay_style or "").lower().strip() in {"som", "set_of_mark", "set-of-mark"} else "bbox"
    overlay_path = _draw_raster_overlay(path, image_width, image_height, items, overlay_style=style)
    overlay_vlm_ready = bool(overlay_path)
    if overlay_path:
        artifact_warnings = []
    else:
        # SVG is a useful human/record artifact but NO VLM API accepts SVG as
        # image input. Emit it, but flag clearly that it must not be sent to a
        # VLM; coordinate grounding still works via ground_vlm_region.
        artifact_warnings = [
            "Raster overlay unavailable (Pillow not installed or source is not a raster image); "
            "wrote an SVG overlay for human review only. Do NOT send the SVG to a VLM API — "
            "install Pillow for a PNG overlay, or use ground_vlm_region / map_pixel_region_to_world_bbox "
            "for coordinate-based grounding without an overlay image."
        ]
        overlay_path = _write_svg_overlay(path, image_width, image_height, items, warnings=artifact_warnings, overlay_style=style)

    sidecar = {
        "clean_image_path": str(path),
        "overlay_image_path": overlay_path,
        "overlay_vlm_ready": overlay_vlm_ready,
        "overlay_items": items,
        "overlay_style": style,
        "image": {"width": image_width, "height": image_height},
        "warnings": warnings + artifact_warnings,
    }
    sidecar_path = path.with_name(f"{path.stem}_overlay_items.json")
    sidecar_path.write_text(json.dumps(sidecar, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    return {
        "overlay_image_path": overlay_path,
        "overlay_items_path": str(sidecar_path),
        "overlay_vlm_ready": overlay_vlm_ready,
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
                                   overlay_granularity: str = "entity",
                                   overlay_style: str = "bbox",
                                   include_tiles: bool = False,
                                   tile_size: int = 640,
                                   tile_overlap: float = 0.2,
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

    # Attempt WMF→PNG conversion so VLMs can receive a raster image.
    # Overlay, tile, and coordinate mapping all use the raster path when available.
    raster_path = path
    vlm_ready = False
    vlm_blocked_reason = ""
    if path.suffix.lower() == ".wmf" and path.exists():
        converted = _try_convert_wmf_to_raster(path)
        if converted:
            raster_path = converted
            vlm_ready = True
        else:
            vlm_blocked_reason = (
                "AutoCAD exported WMF and no WMF→PNG converter is installed "
                "(ImageMagick, wand, Inkscape, or LibreOffice). VLM APIs cannot read WMF."
            )
            warnings.append(
                vlm_blocked_reason
                + " Install one of those tools to enable VLM image input, or export a PDF and "
                "render it externally. Coordinate grounding (ground_vlm_region / "
                "map_pixel_region_to_world_bbox) still works without a VLM-readable image."
            )
    elif path.suffix.lower() in {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"}:
        vlm_ready = path.exists()
        if not vlm_ready:
            vlm_blocked_reason = f"Expected raster image was not produced at {path}."

    image_width, image_height = _image_size(str(raster_path))
    # Degraded mode: if the image dimensions had to fall back to the hardcoded
    # default (e.g. unreadable WMF header and no PIL), the aspect ratio is wrong
    # and every pixel↔world transform would be skewed. Recover the aspect ratio
    # from the scanned entity extent so grounding stays usable.
    image_size_source = "image_file" if vlm_ready else "wmf_header_or_file"
    if (image_width, image_height) == DEFAULT_IMAGE_SIZE:
        recovered = _scanned_entity_extent(db)
        if recovered:
            ex_w = float(recovered[2]) - float(recovered[0])
            ex_h = float(recovered[3]) - float(recovered[1])
            if ex_w > 0 and ex_h > 0:
                image_height = max(1, int(round(DEFAULT_IMAGE_SIZE[0] * ex_h / ex_w)))
                image_width = DEFAULT_IMAGE_SIZE[0]
                image_size_source = "estimated_from_scanned_extent"
                warnings.append(
                    "Image dimensions were unreadable; estimated the aspect ratio from scanned "
                    "entity extents. Pixel mapping is approximate (transform_confidence=low)."
                )
        else:
            image_size_source = "default_fallback"
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

    overlay_items, primitive_overlay_items = _overlay_items_for_granularity(
        db,
        visible_handles,
        entity_screen_bboxes,
        transform["world_extent"],
        transform["world_to_pixel"],
        overlay_granularity,
    )
    overlay_path = ""
    overlay_items_path = ""
    overlay_vlm_ready = False
    if include_overlay:
        overlay = create_overlay_artifact(
            str(raster_path),
            overlay_items,
            {**context, "warnings": warnings, "image": {"width": image_width, "height": image_height}},
            overlay_style=overlay_style,
        )
        overlay_path = overlay["overlay_image_path"]
        overlay_items_path = overlay["overlay_items_path"]
        overlay_vlm_ready = bool(overlay.get("overlay_vlm_ready"))
        warnings.extend(overlay.get("warnings", []))
    tile_index = {
        "tile_index_path": "",
        "tiles": [],
        "warnings": [],
        "tile_size": tile_size,
        "tile_overlap": tile_overlap,
    }
    if include_tiles:
        tile_index = _build_tile_index(
            str(raster_path),
            overlay_path,
            image_width,
            image_height,
            overlay_items,
            tile_size=tile_size,
            tile_overlap=tile_overlap,
        )
        warnings.extend(tile_index.get("warnings", []))

    snapshot = {
        "snapshot_id": stable_id("snapshot", str(path), now_iso()),
        "clean_image_path": str(raster_path),
        "image_path": str(raster_path),
        "autocad_export_path": str(path),
        "vlm_ready": vlm_ready,
        "vlm_blocked_reason": vlm_blocked_reason,
        "vlm_image_path": str(raster_path) if vlm_ready else "",
        "image_size_source": image_size_source,
        "transform_confidence": "low" if image_size_source.startswith("estimated") or image_size_source == "default_fallback" else "normal",
        "overlay_image_path": overlay_path,
        "overlay_vlm_ready": overlay_vlm_ready,
        "context_json_path": "",
        "overlay_items_path": overlay_items_path,
        "overlay_items": overlay_items,
        "primitive_overlay_items": primitive_overlay_items,
        "overlay_granularity": (overlay_granularity or "entity").lower().strip(),
        "overlay_style": "som" if str(overlay_style or "").lower().strip() in {"som", "set_of_mark", "set-of-mark"} else "bbox",
        "tile_index_path": tile_index.get("tile_index_path", ""),
        "tiles": tile_index.get("tiles", []),
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
    if vlm_ready:
        readiness = f" VLM-ready image at {snapshot['vlm_image_path']} (send THIS file to the VLM)."
    else:
        readiness = (
            " NOT VLM-ready: do not send the exported file to a VLM. "
            + (vlm_blocked_reason or "No VLM-readable raster was produced.")
            + " Use ground_vlm_region/map_pixel_region_to_world_bbox for coordinate grounding instead."
        )
    next_tools = ["get_snapshot_image", "ground_vlm_region", "ground_vlm_overlay_id", "get_visible_entities_in_view", "explain_entity"]
    if not vlm_ready:
        next_tools = ["check_runtime_environment"] + next_tools
    return ok_result(
        "Exported view image mapping snapshot." + readiness,
        data={"snapshot": snapshot},
        handles=visible_handles,
        warnings=sorted(set(warnings)),
        next_tools=next_tools,
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
            "primitive_overlay_items": snapshot.get("primitive_overlay_items", []),
            "tiles": snapshot.get("tiles", []),
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
    if item.get("primitive_key"):
        primitive_matches = [{
            "primitive_key": item.get("primitive_key"),
            "primitive_type": item.get("primitive_type"),
            "role": item.get("role"),
            "score": 1.0,
            "evidence": {
                "reason": "VLM referenced a primitive overlay item directly.",
                "overlay_id": item.get("overlay_id"),
                "pixel_bbox": ent_bbox,
                "world_bbox": item.get("world_bbox"),
            },
        }] + [
            primitive for primitive in primitive_matches
            if primitive.get("primitive_key") != item.get("primitive_key")
        ]
    warnings = list(snapshot.get("limitations", [])) + primitive_warnings
    return {
        "handle": item.get("handle"),
        "native_handle": item.get("native_handle") or item.get("handle"),
        "entity_type": item.get("entity_type"),
        "overlay_id": item.get("overlay_id"),
        "item_kind": item.get("item_kind", "entity"),
        "parent_overlay_id": item.get("parent_overlay_id"),
        "primitive_key": item.get("primitive_key"),
        "primitive_type": item.get("primitive_type"),
        "role": item.get("role"),
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
