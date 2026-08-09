"""View snapshot mapping, overlay artifacts, and VLM region grounding."""

from __future__ import annotations

import base64
import json
import math
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

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
    entity_geometry,
    entity_type,
    ensure_understanding_schema,
    get_db,
    point_list,
    now_iso,
    stable_id,
    topology_for_handle,
)
from .result import ToolResult, error_result, ok_result

DEFAULT_IMAGE_SIZE = (1600, 1000)
AUTOCAD_WMF_SELECTION_FRAME_PADDING_RATIO = 0.00375
SNAPSHOT_SCHEMA_VERSION = "cad-view-snapshot/v3"
OVERLAY_SIDECAR_SCHEMA_VERSION = "cad-overlay-items/v2"
TILE_INDEX_SCHEMA_VERSION = "cad-view-tiles/v2"
GROUNDING_GEOMETRY_VERSION = "path-polygon/v1"
MIN_REGION_GROUNDING_SCORE = 0.35
MIN_RESOLVABLE_EXTENT_PX = 1.0
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
        # Aldus Placeable Metafile header layout:
        # key[0:4], hmf[4:6], bbox left/top/right/bottom[6:14], inch[14:16].
        left = int.from_bytes(header[6:8], "little", signed=True)
        top = int.from_bytes(header[8:10], "little", signed=True)
        right = int.from_bytes(header[10:12], "little", signed=True)
        bottom = int.from_bytes(header[12:14], "little", signed=True)
        units_per_inch = int.from_bytes(header[14:16], "little") or 96
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
    LibreOffice (soffice), and finally Windows GDI+ through PowerShell.
    Returns the PNG path on success, None if all attempts fail.
    """
    png_path = wmf_path.with_suffix(".png")

    magick = shutil.which("magick")
    if not magick:
        convert_candidate = shutil.which("convert")
        # Windows ships system32\convert.exe for filesystem conversion; it is
        # unrelated to ImageMagick and must never receive image paths.
        if convert_candidate and "system32" not in convert_candidate.lower():
            magick = convert_candidate
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

    # AutoCAD's COM export is most reliable as WMF on Windows.  GDI+ can
    # rasterize that native vector artifact without requiring a separate
    # graphics package, so keep it as the final Windows-only fallback.
    powershell = shutil.which("powershell.exe") or shutil.which("powershell")
    if powershell:
        script = r"""
& {
param([string]$SourcePath, [string]$TargetPath)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing
$metafile = [System.Drawing.Imaging.Metafile]::FromFile($SourcePath)
try {
    $sourceWidth = [Math]::Max(1, [int]$metafile.Width)
    $sourceHeight = [Math]::Max(1, [int]$metafile.Height)
    # Placeable WMFs report logical-unit dimensions that can be tens of
    # thousands of units wide.  Scale both up and down to a bounded raster.
    $scale = [Math]::Max(0.01, [Math]::Min(4.0, 2400.0 / [Math]::Max($sourceWidth, $sourceHeight)))
    $width = [Math]::Max(1, [int][Math]::Round($sourceWidth * $scale))
    $height = [Math]::Max(1, [int][Math]::Round($sourceHeight * $scale))
    $bitmap = New-Object System.Drawing.Bitmap($width, $height, [System.Drawing.Imaging.PixelFormat]::Format32bppArgb)
    try {
        $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
        try {
            $graphics.Clear([System.Drawing.Color]::White)
            $graphics.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::HighQuality
            $graphics.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
            $destination = [System.Drawing.Rectangle]::FromLTRB(0, 0, $width, $height)
            $graphics.DrawImage($metafile, $destination)
        }
        finally {
            $graphics.Dispose()
        }
        $bitmap.Save($TargetPath, [System.Drawing.Imaging.ImageFormat]::Png)
    }
    finally {
        $bitmap.Dispose()
    }
}
finally {
    $metafile.Dispose()
}
}
"""
        try:
            source_literal = str(wmf_path.resolve()).replace("'", "''")
            target_literal = str(png_path.resolve()).replace("'", "''")
            invocation = script.rstrip() + f" '{source_literal}' '{target_literal}'"
            encoded_command = base64.b64encode(
                invocation.encode("utf-16-le")
            ).decode("ascii")
            result = subprocess.run(
                [
                    powershell,
                    "-NoLogo",
                    "-NoProfile",
                    "-NonInteractive",
                    "-EncodedCommand",
                    encoded_command,
                ],
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


def _path_midpoint(path: Sequence[Sequence[float]]) -> Optional[List[float]]:
    safe_path = _bounded_finite_path(path)
    segments: List[Tuple[List[float], List[float], float]] = []
    total = 0.0
    for start, end in zip(safe_path, safe_path[1:]):
        length = math.hypot(end[0] - start[0], end[1] - start[1])
        if not math.isfinite(length) or length <= 1e-12:
            continue
        segments.append((start, end, length))
        total += length
    if not segments or not math.isfinite(total):
        return safe_path[0] if safe_path else None
    target = total / 2.0
    traversed = 0.0
    for start, end, length in segments:
        if traversed + length >= target:
            parameter = (target - traversed) / length
            return [
                start[0] + parameter * (end[0] - start[0]),
                start[1] + parameter * (end[1] - start[1]),
            ]
        traversed += length
    return list(segments[-1][1])


def _polygon_interior_anchor(polygon: Sequence[Sequence[float]]) -> Optional[List[float]]:
    safe_polygon = _bounded_finite_path(polygon)
    bbox = _finite_pixel_bbox(_path_bbox(safe_polygon))
    if len(safe_polygon) < 3 or not bbox:
        return None
    mean_x, mean_y = safe_polygon[0]
    for index, point in enumerate(safe_polygon[1:], start=2):
        mean_x += (point[0] - mean_x) / index
        mean_y += (point[1] - mean_y) / index
    candidates = [
        [mean_x, mean_y],
        [(bbox[0] + bbox[2]) / 2.0, (bbox[1] + bbox[3]) / 2.0],
    ]
    for row in range(1, 8):
        for column in range(1, 8):
            candidates.append([
                bbox[0] + (bbox[2] - bbox[0]) * column / 8.0,
                bbox[1] + (bbox[3] - bbox[1]) * row / 8.0,
            ])
    edge_step = max(1, len(safe_polygon) // 512)
    sampled_polygon = safe_polygon[::edge_step]
    if len(sampled_polygon) < 3:
        sampled_polygon = safe_polygon
    best: Optional[List[float]] = None
    best_clearance = -1.0
    for candidate in candidates:
        if not _point_in_polygon(candidate, safe_polygon, tolerance=1e-6):
            continue
        clearance = min((
            _point_to_segment_distance(candidate, start, end)
            for start, end in zip(
                sampled_polygon,
                [*sampled_polygon[1:], sampled_polygon[0]],
            )
        ), default=0.0)
        if clearance > best_clearance:
            best = candidate
            best_clearance = clearance
    return best or list(safe_polygon[0])


def _overlay_anchor(item: Dict[str, Any], bbox: Sequence[float]) -> List[float]:
    polygon_anchor = _polygon_interior_anchor(item.get("pixel_polygon") or [])
    if polygon_anchor:
        return polygon_anchor
    path_anchor = _path_midpoint(item.get("pixel_path") or [])
    if path_anchor:
        return path_anchor
    safe_bbox = _finite_pixel_bbox(bbox) or [0.0, 0.0, 0.0, 0.0]
    return [
        (safe_bbox[0] + safe_bbox[2]) / 2.0,
        (safe_bbox[1] + safe_bbox[3]) / 2.0,
    ]


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
        pixel_polygon = _bounded_finite_path(item.get("pixel_polygon") or [])
        pixel_path = _bounded_finite_path(item.get("pixel_path") or [])
        bbox = (
            _finite_pixel_bbox(item.get("pixel_bbox"))
            or _finite_pixel_bbox(_path_bbox(pixel_polygon or pixel_path))
            or [0.0, 0.0, 0.0, 0.0]
        )
        x1, y1, x2, y2 = _pixel_bbox_with_min_size(bbox, min_size=8.0)
        label = item.get("overlay_id", "?")
        stroke, fill, text_color = _overlay_colors(item)
        center_x, center_y = _overlay_anchor(item, bbox)
        fill_value = fill if overlay_style == "som" else "none"
        if (
            str(item.get("item_kind") or "").lower() == "semantic"
            and len(pixel_polygon) >= 3
        ):
            polygon_points = " ".join(
                f"{float(point[0]):.2f},{float(point[1]):.2f}"
                for point in pixel_polygon
                if isinstance(point, (list, tuple)) and len(point) >= 2
            )
            lines.append(
                f'<polygon points="{polygon_points}" fill="{fill_value}" '
                f'stroke="{stroke}" stroke-width="3" stroke-linejoin="round"/>'
            )
        elif len(pixel_path) >= 2:
            path_points = " ".join(
                f"{float(point[0]):.2f},{float(point[1]):.2f}"
                for point in pixel_path
                if isinstance(point, (list, tuple)) and len(point) >= 2
            )
            lines.append(
                f'<polyline points="{path_points}" fill="none" stroke="{stroke}" '
                f'stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>'
            )
        else:
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
            safe_polygon = _bounded_finite_path(item.get("pixel_polygon") or [])
            safe_path = _bounded_finite_path(item.get("pixel_path") or [])
            safe_bbox = (
                _finite_pixel_bbox(item.get("pixel_bbox"))
                or _finite_pixel_bbox(_path_bbox(safe_polygon or safe_path))
                or [0.0, 0.0, 0.0, 0.0]
            )
            x1, y1, x2, y2 = _pixel_bbox_with_min_size(safe_bbox)
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
            pixel_polygon = [
                (float(point[0]), float(point[1]))
                for point in safe_polygon
                if isinstance(point, (list, tuple)) and len(point) >= 2
            ]
            pixel_path = [
                (float(point[0]), float(point[1]))
                for point in safe_path
                if isinstance(point, (list, tuple)) and len(point) >= 2
            ]
            if kind == "semantic" and len(pixel_polygon) >= 3:
                if overlay_style == "som":
                    draw.polygon(pixel_polygon, fill=fill)
                draw.line(
                    [*pixel_polygon, pixel_polygon[0]],
                    fill=outline,
                    width=3,
                    joint="curve",
                )
            elif len(pixel_path) >= 2:
                draw.line(
                    pixel_path,
                    fill=outline,
                    width=3,
                    joint="curve",
                )
            else:
                draw.rectangle([x1, y1, x2, y2], outline=outline, fill=fill if overlay_style == "som" else None, width=3)
            text_box = draw.textbbox((0, 0), label, font=font)
            tw = text_box[2] - text_box[0] + 8
            th = text_box[3] - text_box[1] + 6
            if overlay_style == "som":
                cx, cy = _overlay_anchor(item, safe_bbox)
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


def _build_semantic_overlay_items(database: CADDatabase,
                                  view_extent: BBox,
                                  matrix: Sequence[Sequence[float]]) -> List[Dict[str, Any]]:
    """Expose multi-handle semantic shapes instead of flattening them to tags."""
    ensure_understanding_schema(database)
    scope = current_scope(database)
    with database._conn() as conn:
        rows = conn.execute('''
            SELECT object_id, object_type, label, source, confidence,
                   bbox_min_x, bbox_min_y, bbox_max_x, bbox_max_y,
                   entity_handles, properties
            FROM cad_semantic_objects
            WHERE workspace_id = ? AND drawing_id = ?
              AND conversation_id = ? AND thread_id = ?
            ORDER BY confidence DESC, object_type, object_id
        ''', (
            scope["workspace_id"], scope["drawing_id"],
            scope["conversation_id"], scope["thread_id"],
        )).fetchall()
    entities = {
        str(entity.get("handle") or ""): entity
        for entity in all_entities(database)
    }
    items: List[Dict[str, Any]] = []
    for row in rows:
        if any(row[key] is None for key in (
            "bbox_min_x", "bbox_min_y", "bbox_max_x", "bbox_max_y",
        )):
            continue
        world_bbox = (
            float(row["bbox_min_x"]), float(row["bbox_min_y"]),
            float(row["bbox_max_x"]), float(row["bbox_max_y"]),
        )
        if not bbox_intersects(world_bbox, view_extent):
            continue
        handles = [
            str(handle) for handle in decode_json(row["entity_handles"], [])
            if str(handle)
        ]
        properties = decode_json(row["properties"], {})
        raw_vertices = properties.get("vertices") if isinstance(properties, dict) else None
        world_polygon: List[List[float]] = []
        if isinstance(raw_vertices, list):
            if len(raw_vertices) > 8192:
                last_index = len(raw_vertices) - 1
                vertex_values = [
                    raw_vertices[round(index * last_index / 8191)]
                    for index in range(8192)
                ]
            else:
                vertex_values = raw_vertices
            for raw_point in vertex_values:
                if not isinstance(raw_point, (list, tuple)) or len(raw_point) < 2:
                    continue
                try:
                    point = [float(raw_point[0]), float(raw_point[1])]
                except (TypeError, ValueError):
                    continue
                if all(math.isfinite(value) for value in point):
                    world_polygon.append(point)
        pixel_polygon = (
            _transform_finite_path(matrix, world_polygon)
            if len(world_polygon) >= 3 else []
        )
        world_path = (
            _entity_world_path(entities.get(handles[0], {}))
            if len(handles) == 1 and len(world_polygon) < 3
            else []
        )
        pixel_path = _transform_finite_path(matrix, world_path)
        items.append({
            "overlay_id": f"S{len(items) + 1:03d}",
            "item_kind": "semantic",
            "object_id": str(row["object_id"] or ""),
            "object_type": str(row["object_type"] or "semantic_object"),
            "label": str(row["label"] or row["object_type"] or "semantic object"),
            "source": str(row["source"] or "semantic"),
            "handle": handles[0] if handles else "",
            "handles": handles,
            "native_handle": handles[0] if handles else "",
            "entity_type": str(row["object_type"] or "semantic_object"),
            "pixel_bbox": _finite_pixel_bbox(
                bbox_world_to_pixel(world_bbox, matrix)
            ) or [],
            "pixel_polygon": pixel_polygon,
            "pixel_path": pixel_path,
            "world_bbox": bbox_dict(world_bbox),
            "world_polygon": world_polygon,
            "world_path": world_path,
            "semantic_tags": [str(row["object_type"] or "semantic_object")],
            "properties": properties,
            "confidence": round(float(row["confidence"] or 0.0), 4),
        })
    return items


def _bounded_finite_path(value: Any,
                         max_points: int = 8192) -> List[List[float]]:
    """Return a deterministic, finite 2D path without unbounded sidecars."""
    points = [
        [float(point[0]), float(point[1])]
        for point in point_list(value)
        if len(point) >= 2
        and math.isfinite(float(point[0]))
        and math.isfinite(float(point[1]))
    ]
    compact: List[List[float]] = []
    for point in points:
        if not compact or point != compact[-1]:
            compact.append(point)
    if len(compact) <= max_points:
        return compact
    last_index = len(compact) - 1
    return [
        compact[round(index * last_index / (max_points - 1))]
        for index in range(max_points)
    ]


def _transform_finite_path(matrix: Sequence[Sequence[float]],
                           path: Sequence[Sequence[float]]) -> List[List[float]]:
    transformed: List[List[float]] = []
    for point in path:
        try:
            pixel = apply_matrix_2d(matrix, float(point[0]), float(point[1]))
        except (TypeError, ValueError, OverflowError, IndexError):
            continue
        if len(pixel) >= 2 and all(math.isfinite(float(value)) for value in pixel[:2]):
            transformed.append([float(pixel[0]), float(pixel[1])])
    return _bounded_finite_path(transformed)


def _path_bbox(path: Sequence[Sequence[float]]) -> Optional[BBox]:
    finite = _bounded_finite_path(path)
    if not finite:
        return None
    bbox = (
        min(point[0] for point in finite),
        min(point[1] for point in finite),
        max(point[0] for point in finite),
        max(point[1] for point in finite),
    )
    width = bbox[2] - bbox[0]
    height = bbox[3] - bbox[1]
    if (
        not all(math.isfinite(component) for component in bbox)
        or not math.isfinite(width)
        or not math.isfinite(height)
    ):
        return None
    return bbox


def _finite_point2(value: Any) -> Optional[List[float]]:
    if not isinstance(value, (list, tuple)) or len(value) < 2:
        return None
    try:
        point = [float(value[0]), float(value[1])]
    except (TypeError, ValueError, OverflowError):
        return None
    return point if all(math.isfinite(component) for component in point) else None


def _curve_parameter(geometry: Dict[str, Any],
                     primary_key: str,
                     legacy_key: str) -> Optional[float]:
    value = geometry.get(primary_key)
    unit = str(geometry.get("parameter_unit") or "").lower()
    if value is None:
        value = geometry.get(legacy_key)
        unit = str(geometry.get("angle_unit") or unit).lower()
    try:
        parameter = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(parameter):
        return None
    if unit.startswith("deg"):
        return math.radians(parameter)
    if primary_key in geometry or unit.startswith("rad"):
        return parameter
    return None


def _sample_parametric_path(point_at: Any,
                            start: float,
                            sweep: float) -> List[List[float]]:
    if not all(math.isfinite(value) for value in (start, sweep)) or abs(sweep) <= 1e-12:
        return []
    segment_count = max(8, min(256, int(math.ceil(abs(sweep) / (math.pi / 48.0)))))
    return _bounded_finite_path([
        point_at(start + sweep * index / segment_count)
        for index in range(segment_count + 1)
    ])


def _polyline_world_path(geometry: Dict[str, Any]) -> List[List[float]]:
    vertices = _bounded_finite_path(
        geometry.get("vertices") or geometry.get("points")
    )
    if len(vertices) < 2:
        return vertices
    closed = bool(geometry.get("closed") or geometry.get("is_closed"))
    bulges = geometry.get("bulges")
    if not isinstance(bulges, (list, tuple)) or not bulges:
        if closed and vertices[0] != vertices[-1]:
            vertices.append(list(vertices[0]))
        return vertices
    segment_pairs = list(zip(vertices, vertices[1:]))
    if closed and vertices[0] != vertices[-1]:
        segment_pairs.append((vertices[-1], vertices[0]))
    path: List[List[float]] = [list(segment_pairs[0][0])] if segment_pairs else []
    for index, (start, end) in enumerate(segment_pairs):
        try:
            bulge = float(bulges[index]) if index < len(bulges) else 0.0
        except (TypeError, ValueError, OverflowError):
            bulge = 0.0
        dx = end[0] - start[0]
        dy = end[1] - start[1]
        chord = math.hypot(dx, dy)
        if not math.isfinite(bulge) or abs(bulge) <= 1e-12 or chord <= 1e-12:
            path.append(list(end))
            continue
        sweep = 4.0 * math.atan(bulge)
        center_offset = chord * (1.0 - bulge * bulge) / (4.0 * bulge)
        center = [
            (start[0] + end[0]) / 2.0 - dy / chord * center_offset,
            (start[1] + end[1]) / 2.0 + dx / chord * center_offset,
        ]
        radius = math.hypot(start[0] - center[0], start[1] - center[1])
        start_parameter = math.atan2(start[1] - center[1], start[0] - center[0])
        arc = _sample_parametric_path(
            lambda parameter: [
                center[0] + radius * math.cos(parameter),
                center[1] + radius * math.sin(parameter),
            ],
            start_parameter,
            sweep,
        )
        path.extend(arc[1:] if len(arc) >= 2 else [list(end)])
    return _bounded_finite_path(path)


def _analytic_entity_world_path(entity: Dict[str, Any]) -> List[List[float]]:
    geometry = entity_geometry(entity)
    kind = entity_type(entity)
    center = _finite_point2(geometry.get("center"))
    if center and ("circle" in kind or ("arc" in kind and "ellipse" not in kind)):
        try:
            radius = abs(float(geometry.get("radius")))
        except (TypeError, ValueError, OverflowError):
            radius = 0.0
        if not math.isfinite(radius) or radius <= 1e-12:
            return []
        if "arc" in kind:
            start = _curve_parameter(geometry, "start_parameter", "start_angle")
            end = _curve_parameter(geometry, "end_parameter", "end_angle")
            if start is None or end is None:
                return []
            try:
                explicit_sweep = float(geometry.get("sweep"))
            except (TypeError, ValueError, OverflowError):
                explicit_sweep = 0.0
            sweep = (
                explicit_sweep
                if math.isfinite(explicit_sweep) and abs(explicit_sweep) > 1e-12
                else (end - start) % (2.0 * math.pi)
            )
            if abs(sweep) <= 1e-12:
                return []
        else:
            start, sweep = 0.0, 2.0 * math.pi
        return _sample_parametric_path(
            lambda parameter: [
                center[0] + radius * math.cos(parameter),
                center[1] + radius * math.sin(parameter),
            ],
            start,
            sweep,
        )
    if center and "ellipse" in kind:
        major = _finite_point2(geometry.get("major_axis"))
        minor = _finite_point2(geometry.get("minor_axis"))
        if major and not minor:
            try:
                ratio = abs(float(geometry.get("radius_ratio")))
            except (TypeError, ValueError, OverflowError):
                ratio = 0.0
            minor = [-major[1] * ratio, major[0] * ratio] if ratio > 0.0 else None
        if not major or not minor:
            return []
        is_arc = bool(geometry.get("is_arc"))
        if is_arc:
            start = _curve_parameter(geometry, "start_parameter", "start_angle")
            end = _curve_parameter(geometry, "end_parameter", "end_angle")
            if start is None or end is None:
                return []
            try:
                explicit_sweep = float(geometry.get("sweep"))
            except (TypeError, ValueError, OverflowError):
                explicit_sweep = 0.0
            sweep = (
                explicit_sweep
                if math.isfinite(explicit_sweep) and abs(explicit_sweep) > 1e-12
                else (end - start) % (2.0 * math.pi)
            )
            if abs(sweep) <= 1e-12:
                return []
        else:
            start, sweep = 0.0, 2.0 * math.pi
        return _sample_parametric_path(
            lambda parameter: [
                center[0] + major[0] * math.cos(parameter) + minor[0] * math.sin(parameter),
                center[1] + major[1] * math.cos(parameter) + minor[1] * math.sin(parameter),
            ],
            start,
            sweep,
        )
    return []


def _entity_world_path(entity: Dict[str, Any]) -> List[List[float]]:
    """Extract authored stroke geometry; never invent a bbox diagonal."""
    geometry = entity_geometry(entity)
    kind = entity_type(entity)
    if "polyline" in kind:
        visual_path = _bounded_finite_path(geometry.get("visual_path"))
        if len(visual_path) >= 2:
            return visual_path
        return _polyline_world_path(geometry)
    if "line" in kind:
        start = geometry.get("start_point") or geometry.get("start")
        end = geometry.get("end_point") or geometry.get("end")
        return _bounded_finite_path([start, end]) if start is not None and end is not None else []
    analytic_path = _analytic_entity_world_path(entity)
    if len(analytic_path) >= 2:
        return analytic_path
    for key in ("samples", "sample_points", "sampled_points", "fit_points"):
        path = _bounded_finite_path(geometry.get(key))
        if len(path) >= 2:
            return path
    return []


def _primitive_world_path(primitive: Dict[str, Any]) -> List[List[float]]:
    properties = primitive.get("properties")
    if not isinstance(properties, dict):
        properties = {}
    for key in (
        "samples", "sample_points", "sampled_points", "fit_points",
        "vertices", "points",
    ):
        path = _bounded_finite_path(
            primitive.get(key) if primitive.get(key) is not None else properties.get(key)
        )
        if len(path) >= 2:
            return path
    primitive_role = str(primitive.get("role") or "").lower()
    curve_kind = str(properties.get("curve_kind") or "").lower()
    if any(token in f"{primitive_role} {curve_kind}" for token in (
        "circle", "arc", "ellipse",
    )):
        geometry = dict(properties)
        center = _finite_point2([primitive.get("x"), primitive.get("y")])
        if center:
            geometry["center"] = center
        if primitive.get("radius") is not None:
            geometry["radius"] = primitive.get("radius")
        if "ellipse" in primitive_role or "ellipse" in curve_kind:
            analytic_type = "AcDbEllipse"
            geometry["is_arc"] = bool(
                geometry.get("is_arc")
                or "ellipse_arc" in curve_kind
            )
        elif "arc" in primitive_role or "arc" in curve_kind:
            analytic_type = "AcDbArc"
        else:
            analytic_type = "AcDbCircle"
        analytic_path = _analytic_entity_world_path({
            "type": analytic_type,
            "geometry": geometry,
        })
        if len(analytic_path) >= 2:
            return analytic_path
    values = [
        primitive.get("x"), primitive.get("y"),
        primitive.get("x2"), primitive.get("y2"),
    ]
    try:
        if all(value is not None and math.isfinite(float(value)) for value in values):
            return [
                [float(values[0]), float(values[1])],
                [float(values[2]), float(values[3])],
            ]
    except (TypeError, ValueError):
        pass
    return []


def _build_overlay_items(database: CADDatabase,
                         visible_handles: List[str],
                         screen_bboxes: Dict[str, List[float]],
                         matrix: Sequence[Sequence[float]]) -> List[Dict[str, Any]]:
    entities = {str(entity.get("handle")): entity for entity in all_entities(database)}
    semantic_tags = _entity_semantic_tags(database)
    items: List[Dict[str, Any]] = []
    for index, handle in enumerate(visible_handles, start=1):
        entity = entities.get(handle, {})
        bbox = bbox_from_row(entity)
        world_path = _entity_world_path(entity)
        pixel_path = _transform_finite_path(matrix, world_path)
        items.append({
            "overlay_id": f"E{index:03d}",
            "item_kind": "entity",
            "handle": handle,
            "native_handle": str(entity.get("native_handle") or handle),
            "entity_type": entity.get("type") or entity.get("entity_type") or entity.get("name") or "Unknown",
            "layer": entity.get("layer") or "0",
            "pixel_bbox": _finite_pixel_bbox(screen_bboxes.get(handle)) or [],
            "pixel_path": pixel_path,
            "world_bbox": bbox_dict(bbox),
            "world_path": world_path,
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
            world_path = _primitive_world_path(primitive)
            world_bbox = _path_bbox(world_path) or _primitive_bbox(primitive)
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
                "pixel_bbox": _finite_pixel_bbox(
                    bbox_world_to_pixel(world_bbox, matrix)
                ) or [],
                "pixel_path": _transform_finite_path(matrix, world_path),
                "world_bbox": bbox_dict(world_bbox),
                "world_path": world_path,
                "semantic_tags": entity_item.get("semantic_tags", []),
                "confidence": 0.9,
            })
    return primitive_items


def _overlay_items_for_granularity(database: CADDatabase,
                                   visible_handles: List[str],
                                   screen_bboxes: Dict[str, List[float]],
                                   view_extent: BBox,
                                   matrix: Sequence[Sequence[float]],
                                   overlay_granularity: str) -> Tuple[
                                       List[Dict[str, Any]],
                                       List[Dict[str, Any]],
                                       List[Dict[str, Any]],
                                       List[Dict[str, Any]],
                                   ]:
    entity_items = _build_overlay_items(database, visible_handles, screen_bboxes, matrix)
    primitive_items = _build_primitive_overlay_items(database, entity_items, view_extent, matrix)
    semantic_items = _build_semantic_overlay_items(database, view_extent, matrix)
    granularity = (overlay_granularity or "entity").lower().strip()
    if granularity == "primitive":
        return primitive_items, primitive_items, semantic_items, entity_items
    if granularity in {"semantic", "shape", "group"}:
        return semantic_items, primitive_items, semantic_items, entity_items
    if granularity in {"both", "entity+primitive", "all"}:
        items = entity_items + primitive_items
        if granularity == "all":
            items += semantic_items
        return items, primitive_items, semantic_items, entity_items
    if granularity in {"adaptive", "hierarchical"}:
        # Dense sheets benefit from object/group marks; sparse sheets retain
        # exact entity marks. This avoids covering every fine stroke with SOM.
        if semantic_items and len(entity_items) + len(primitive_items) > 120:
            group_items = [
                item for item in semantic_items
                if len({str(handle) for handle in item.get("handles", []) if handle}) >= 2
            ]
            covered_handles = {
                str(handle)
                for item in group_items
                for handle in item.get("handles", [])
                if handle
            }
            if group_items and covered_handles:
                uncovered_entities = [
                    item for item in entity_items
                    if str(item.get("handle") or "") not in covered_handles
                ]
                return (
                    [*group_items, *uncovered_entities],
                    primitive_items,
                    semantic_items,
                    entity_items,
                )
        return entity_items, primitive_items, semantic_items, entity_items
    return entity_items, primitive_items, semantic_items, entity_items


def _overlay_item_intersects_region(item: Dict[str, Any],
                                    region: Sequence[float]) -> bool:
    if not bbox_intersects(region, item.get("pixel_bbox")):
        return False
    pixel_path = item.get("pixel_path") or []
    if len(pixel_path) >= 2:
        return _pixel_path_intersects_bbox(pixel_path, region, padding=2.0)
    polygon = item.get("pixel_polygon") or []
    if (
        str(item.get("item_kind") or "").lower() != "semantic"
        or len(polygon) < 3
    ):
        return True
    # A semantic bbox can cover a large concave notch. Clip the actual contour
    # so tile metadata never claims that a shape is visible in empty bbox area.
    return len(_clip_polygon_to_bbox(polygon, region)) >= 2


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
                    if _overlay_item_intersects_region(item, tile_bbox)
                ]
                tile: Dict[str, Any] = {
                    "tile_id": f"T{tile_index:03d}",
                    "pixel_bbox": tile_bbox,
                    "global_pixel_bbox": tile_bbox,
                    "local_pixel_bbox": [0.0, 0.0, float(x2 - x1), float(y2 - y1)],
                    "coordinate_space": "tile_local",
                    "image": {"width": int(x2 - x1), "height": int(y2 - y1)},
                    "local_to_global": [
                        [1.0, 0.0, float(x1)],
                        [0.0, 1.0, float(y1)],
                        [0.0, 0.0, 1.0],
                    ],
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
        "schema_version": TILE_INDEX_SCHEMA_VERSION,
        "grounding_geometry_version": GROUNDING_GEOMETRY_VERSION,
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
        "schema_version": OVERLAY_SIDECAR_SCHEMA_VERSION,
        "grounding_geometry_version": GROUNDING_GEOMETRY_VERSION,
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


def _expand_extent_proportionally(extent: BBox,
                                  padding_ratio: float) -> BBox:
    """Expand each axis by its own size, preserving the source aspect ratio."""
    min_x, min_y, max_x, max_y = [float(value) for value in extent]
    ratio = max(float(padding_ratio), 0.0)
    pad_x = max(max_x - min_x, 1.0) * ratio
    pad_y = max(max_y - min_y, 1.0) * ratio
    return min_x - pad_x, min_y - pad_y, max_x + pad_x, max_y + pad_y


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
                "AutoCAD exported WMF and no WMF-to-PNG conversion path succeeded "
                "(Windows GDI+, ImageMagick, wand, Inkscape, or LibreOffice). "
                "VLM APIs cannot read WMF."
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
        # Document.Export(..., "WMF", selection_set) fits the selected entity
        # extents into a frame with a small proportional margin.  The previous
        # generic 8% viewport padding caused the large radial VLM offset; zero
        # padding still leaves a 3-4 px residual at README resolution.
        wmf_extent = _expand_extent_proportionally(
            scanned_extent,
            AUTOCAD_WMF_SELECTION_FRAME_PADDING_RATIO,
        )
        context["view"] = _view_from_extent(
            wmf_extent,
            image_width,
            image_height,
            padding_ratio=0.0,
        )
        mapping_view_source = "scanned_entity_extent_for_wmf_export"
        warnings.append(
            "WMF selection-set export mapping was derived from scanned entity extents with only AutoCAD's calibrated proportional frame margin."
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
            fallback_extent = (
                _expand_extent_proportionally(
                    scanned_extent,
                    AUTOCAD_WMF_SELECTION_FRAME_PADDING_RATIO,
                )
                if path.suffix.lower() == ".wmf"
                else scanned_extent
            )
            context["view"] = _view_from_extent(
                fallback_extent,
                image_width,
                image_height,
                padding_ratio=0.0 if path.suffix.lower() == ".wmf" else 0.08,
            )
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

    (
        overlay_items,
        primitive_overlay_items,
        semantic_overlay_items,
        entity_overlay_items,
    ) = _overlay_items_for_granularity(
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
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "grounding_geometry_version": GROUNDING_GEOMETRY_VERSION,
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
        "entity_overlay_items": entity_overlay_items,
        "primitive_overlay_items": primitive_overlay_items,
        "semantic_overlay_items": semantic_overlay_items,
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
            "entity_overlay_items": snapshot.get("entity_overlay_items", []),
            "primitive_overlay_items": snapshot.get("primitive_overlay_items", []),
            "semantic_overlay_items": snapshot.get("semantic_overlay_items", []),
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
    try:
        values = [float(v) for v in bbox[:4]]
    except (TypeError, ValueError, OverflowError):
        return error_result("bbox values must be finite numbers")
    if not all(math.isfinite(value) for value in values):
        return error_result("bbox values must be finite numbers")
    x1, y1, x2, y2 = _normalized_pixel_bbox(values)
    width = x2 - x1
    height = y2 - y1
    if not math.isfinite(width) or not math.isfinite(height) or width <= 0.0 or height <= 0.0:
        return error_result("bbox must have finite positive width and height")
    image = snapshot.get("image") or {}
    image_width = float(image.get("width") or 0.0)
    image_height = float(image.get("height") or 0.0)
    if (
        image_width > 0.0 and image_height > 0.0
        and (x1 < 0.0 or y1 < 0.0 or x2 > image_width or y2 > image_height)
    ):
        return error_result("bbox must be fully inside the snapshot image bounds")
    try:
        corners = [
            apply_matrix_2d(snapshot["pixel_to_world"], x1, y1),
            apply_matrix_2d(snapshot["pixel_to_world"], x2, y1),
            apply_matrix_2d(snapshot["pixel_to_world"], x2, y2),
            apply_matrix_2d(snapshot["pixel_to_world"], x1, y2),
        ]
    except (KeyError, TypeError, ValueError, OverflowError, IndexError):
        return error_result("snapshot pixel-to-world transform is invalid")
    world_bbox = (
        min(point[0] for point in corners),
        min(point[1] for point in corners),
        max(point[0] for point in corners),
        max(point[1] for point in corners),
    )
    if not all(math.isfinite(value) for value in world_bbox):
        return error_result("bbox mapping produced non-finite world coordinates")
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
            candidate = (float(x) - r, float(y) - r, float(x) + r, float(y) + r)
        elif x is not None and y is not None and x2 is not None and y2 is not None:
            candidate = (min(float(x), float(x2)), min(float(y), float(y2)),
                         max(float(x), float(x2)), max(float(y), float(y2)))
        elif x is not None and y is not None:
            candidate = (float(x), float(y), float(x), float(y))
        else:
            return None
    except Exception:
        return None
    finite = _finite_pixel_bbox(candidate)
    return tuple(finite) if finite is not None else None


def _normalized_pixel_bbox(value: Sequence[float]) -> List[float]:
    x1, y1, x2, y2 = [float(v) for v in list(value)[:4]]
    return [min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)]


def _finite_pixel_bbox(value: Any) -> Optional[List[float]]:
    if not isinstance(value, (list, tuple)) or len(value) < 4:
        return None
    try:
        bbox = _normalized_pixel_bbox(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not all(math.isfinite(component) for component in bbox):
        return None
    width = bbox[2] - bbox[0]
    height = bbox[3] - bbox[1]
    if not math.isfinite(width) or not math.isfinite(height):
        return None
    return bbox


def _inflate_thin_bbox(bbox: Sequence[float], padding: float) -> List[float]:
    x1, y1, x2, y2 = _normalized_pixel_bbox(bbox)
    if x2 - x1 < padding * 2.0:
        center_x = x1 + (x2 - x1) / 2.0
        x1, x2 = center_x - padding, center_x + padding
    if y2 - y1 < padding * 2.0:
        center_y = y1 + (y2 - y1) / 2.0
        y1, y2 = center_y - padding, center_y + padding
    return [x1, y1, x2, y2]


def _safe_image_diagonal(snapshot: Dict[str, Any]) -> float:
    image = snapshot.get("image") or {}
    dimensions: List[float] = []
    for key in ("width", "height"):
        try:
            value = float(image.get(key) or 1.0)
        except (TypeError, ValueError, OverflowError):
            value = 1.0
        dimensions.append(value if math.isfinite(value) and value > 0.0 else 1.0)
    diagonal = math.hypot(*dimensions)
    return diagonal if math.isfinite(diagonal) and diagonal >= 1.0 else 1.0


def _bbox_intersection_area(a: Sequence[float], b: Sequence[float]) -> float:
    ax1, ay1, ax2, ay2 = _normalized_pixel_bbox(a)
    bx1, by1, bx2, by2 = _normalized_pixel_bbox(b)
    return max(0.0, min(ax2, bx2) - max(ax1, bx1)) * max(0.0, min(ay2, by2) - max(ay1, by1))


def _point_to_bbox_distance(point: Sequence[float], bbox: Sequence[float]) -> float:
    x1, y1, x2, y2 = _normalized_pixel_bbox(bbox)
    dx = max(x1 - float(point[0]), 0.0, float(point[0]) - x2)
    dy = max(y1 - float(point[1]), 0.0, float(point[1]) - y2)
    return math.hypot(dx, dy)


def _pixel_bbox_support(query_bbox: Sequence[float],
                        candidate_bbox: Sequence[float],
                        snapshot: Dict[str, Any]) -> Dict[str, float]:
    """Score visible support for boxes, line strokes, and point marks.

    Raw IoU is undefined for zero-area CAD lines/points. Inflate only the
    degenerate screen dimension by a few pixels (the perceptual stroke width),
    then combine query coverage with local—not whole-sheet—distance.
    """
    query = _finite_pixel_bbox(query_bbox)
    candidate_base = _finite_pixel_bbox(candidate_bbox)
    if query is None or candidate_base is None:
        return _empty_pixel_support(snapshot)
    image_diag = _safe_image_diagonal(snapshot)
    stroke_padding = max(2.0, min(8.0, image_diag * 0.0025))
    candidate = _inflate_thin_bbox(candidate_base, stroke_padding)
    query_for_area = _inflate_thin_bbox(query, 0.5)
    intersection = _bbox_intersection_area(query_for_area, candidate)
    query_area = max(
        (query_for_area[2] - query_for_area[0]) * (query_for_area[3] - query_for_area[1]),
        1e-9,
    )
    candidate_area = max(
        (candidate[2] - candidate[0]) * (candidate[3] - candidate[1]),
        1e-9,
    )
    query_coverage = min(1.0, intersection / query_area)
    candidate_coverage = min(1.0, intersection / candidate_area)
    iou = bbox_iou(query_for_area, candidate)
    overlap_score = max(iou, 0.8 * query_coverage + 0.2 * candidate_coverage)
    query_center = [
        query[0] + (query[2] - query[0]) / 2.0,
        query[1] + (query[3] - query[1]) / 2.0,
    ]
    candidate_center = bbox_center(tuple(candidate)) or query_center
    distance = _point_to_bbox_distance(query_center, candidate)
    local_scale = max(
        math.hypot(query[2] - query[0], query[3] - query[1]),
        stroke_padding * 4.0,
    )
    distance_score = 1.0 / (1.0 + distance / local_scale)
    query_center_on_candidate = 1.0 if distance <= 1e-9 else 0.0
    candidate_center_in_query = 1.0 if (
        query[0] <= candidate_center[0] <= query[2]
        and query[1] <= candidate_center[1] <= query[3]
    ) else 0.0
    center_support = max(query_center_on_candidate, 0.65 * candidate_center_in_query)
    score = 0.58 * overlap_score + 0.27 * distance_score + 0.15 * center_support
    if intersection <= 0.0:
        score *= 0.7
    return {
        "score": round(min(max(score, 0.0), 1.0), 4),
        "iou_score": round(iou, 4),
        "overlap_score": round(overlap_score, 4),
        "query_coverage": round(query_coverage, 4),
        "candidate_coverage": round(candidate_coverage, 4),
        "distance_score": round(distance_score, 4),
        "center_support": round(center_support, 4),
        "stroke_padding_px": round(stroke_padding, 4),
        "pixel_distance": round(distance, 4),
        "support_mode": "bbox",
    }


def _empty_pixel_support(snapshot: Dict[str, Any],
                         mode: str = "invalid_geometry") -> Dict[str, Any]:
    image_diag = _safe_image_diagonal(snapshot)
    stroke_padding = max(2.0, min(8.0, image_diag * 0.0025))
    return {
        "score": 0.0,
        "iou_score": 0.0,
        "overlap_score": 0.0,
        "query_coverage": 0.0,
        "candidate_coverage": 0.0,
        "distance_score": 0.0,
        "center_support": 0.0,
        "stroke_padding_px": round(stroke_padding, 4),
        "pixel_distance": None,
        "support_mode": mode,
    }


def _segment_fraction_inside_bbox(start: Sequence[float],
                                  end: Sequence[float],
                                  bbox: Sequence[float]) -> float:
    """Liang-Barsky clipping fraction for one finite segment."""
    try:
        values = [
            float(start[0]), float(start[1]),
            float(end[0]), float(end[1]),
            *_normalized_pixel_bbox(bbox),
        ]
    except (TypeError, ValueError, OverflowError, IndexError):
        return 0.0
    if not all(math.isfinite(value) for value in values):
        return 0.0
    scale = max(1.0, *(abs(value) for value in values))
    sx, sy, ex, ey, x1, y1, x2, y2 = [value / scale for value in values]
    x0, y0 = sx, sy
    dx = ex - sx
    dy = ey - sy
    lower = 0.0
    upper = 1.0
    for coefficient, offset in (
        (-dx, x0 - x1),
        (dx, x2 - x0),
        (-dy, y0 - y1),
        (dy, y2 - y0),
    ):
        if abs(coefficient) <= 1e-300:
            if offset < 0.0:
                return 0.0
            continue
        parameter = offset / coefficient
        if coefficient < 0.0:
            lower = max(lower, parameter)
        else:
            upper = min(upper, parameter)
        if lower > upper:
            return 0.0
    return min(1.0, max(0.0, upper - lower))


def _pixel_path_intersects_bbox(path: Sequence[Sequence[float]],
                                bbox: Sequence[float],
                                padding: float = 0.0) -> bool:
    region = _finite_pixel_bbox(bbox)
    if region is None or not math.isfinite(float(padding)):
        return False
    expanded = [
        region[0] - padding, region[1] - padding,
        region[2] + padding, region[3] + padding,
    ]
    for start, end in zip(path, path[1:]):
        if _segment_fraction_inside_bbox(start, end, expanded) > 0.0:
            return True
        try:
            point = [float(start[0]), float(start[1])]
        except (TypeError, ValueError, OverflowError, IndexError):
            continue
        if all(math.isfinite(value) for value in point):
            if (
                expanded[0] <= point[0] <= expanded[2]
                and expanded[1] <= point[1] <= expanded[3]
            ):
                return True
    return False


def _pixel_path_support(query_bbox: Sequence[float],
                        path: Sequence[Sequence[float]],
                        snapshot: Dict[str, Any]) -> Dict[str, Any]:
    """Score actual visible stroke support instead of the path's AABB."""
    query = _finite_pixel_bbox(query_bbox)
    if query is None:
        return _empty_pixel_support(snapshot)
    finite_path: List[List[float]] = []
    for point in path:
        if not isinstance(point, (list, tuple)) or len(point) < 2:
            continue
        try:
            numeric = [float(point[0]), float(point[1])]
        except (TypeError, ValueError):
            continue
        if all(math.isfinite(value) for value in numeric):
            finite_path.append(numeric)
    image_diag = _safe_image_diagonal(snapshot)
    stroke_padding = max(2.0, min(8.0, image_diag * 0.0025))
    expanded_query = [
        query[0] - stroke_padding,
        query[1] - stroke_padding,
        query[2] + stroke_padding,
        query[3] + stroke_padding,
    ]
    metric_cap = max(image_diag * 1e6, 1e6)
    total_length = 0.0
    inside_length = 0.0
    query_center = [
        query[0] + (query[2] - query[0]) / 2.0,
        query[1] + (query[3] - query[1]) / 2.0,
    ]
    distances: List[float] = []
    for start, end in zip(finite_path, finite_path[1:]):
        length = math.hypot(end[0] - start[0], end[1] - start[1])
        if not math.isfinite(length) or length <= 1e-12:
            continue
        clipped_length = length * _segment_fraction_inside_bbox(
            start, end, expanded_query
        )
        total_length = min(metric_cap, total_length + min(length, metric_cap))
        if math.isfinite(clipped_length) and clipped_length > 0.0:
            inside_length = min(
                metric_cap,
                inside_length + min(clipped_length, metric_cap),
            )
        distances.append(_point_to_segment_distance(query_center, start, end))
    distance = min(distances, default=float("inf"))
    query_for_area = _inflate_thin_bbox(query, 0.5)
    query_area = max(
        (query_for_area[2] - query_for_area[0])
        * (query_for_area[3] - query_for_area[1]),
        1e-9,
    )
    cap_area = math.pi * stroke_padding * stroke_padding
    intersection_area = min(
        query_area,
        max(0.0, inside_length * stroke_padding * 2.0 + (
            cap_area if inside_length > 0.0 else 0.0
        )),
    )
    candidate_area = max(total_length * stroke_padding * 2.0 + cap_area, 1e-9)
    query_coverage = min(1.0, intersection_area / query_area)
    candidate_coverage = min(1.0, inside_length / max(total_length, 1e-9))
    union_area = max(query_area + candidate_area - intersection_area, 1e-9)
    iou = min(1.0, intersection_area / union_area)
    overlap_score = max(iou, 0.8 * query_coverage + 0.2 * candidate_coverage)
    path_extent = _finite_pixel_bbox(_path_bbox(finite_path))
    extent_iou = bbox_iou(query_for_area, path_extent) if path_extent else 0.0
    # A detector often returns the bounding box of a whole stroked object.  In
    # that case the query center may be empty (a circle is the canonical
    # example), so center-to-stroke distance alone would incorrectly lose to a
    # large enclosing filled profile.  Require both an extent match and actual
    # path coverage; a small box in a diagonal line's empty AABB corner still
    # receives no such boost.
    # Two-point LINEs are not boosted by their rectangular extent: the extent
    # of a diagonal is mostly empty space and would otherwise overwhelm a
    # genuine multi-edge profile sharing the same AABB.  Curves and authored
    # multi-segment paths can use whole-object extent evidence.
    extent_support = (
        extent_iou * candidate_coverage if len(finite_path) > 2 else 0.0
    )
    local_scale = max(
        math.hypot(query[2] - query[0], query[3] - query[1]),
        stroke_padding * 4.0,
    )
    distance_score = (
        1.0 / (1.0 + distance / local_scale)
        if math.isfinite(distance) else 0.0
    )
    center_support = (
        1.0 / (1.0 + (distance / max(stroke_padding, 1e-9)) ** 2)
        if math.isfinite(distance) else 0.0
    )
    score = 0.58 * overlap_score + 0.27 * distance_score + 0.15 * center_support
    score = max(
        score,
        0.72 * extent_support
        + 0.18 * candidate_coverage
        + 0.10 * distance_score,
    )
    if inside_length <= 0.0:
        score *= 0.7
    return {
        "score": round(min(max(score, 0.0), 1.0), 4),
        "iou_score": round(iou, 4),
        "overlap_score": round(overlap_score, 4),
        "query_coverage": round(query_coverage, 4),
        "candidate_coverage": round(candidate_coverage, 4),
        "distance_score": round(distance_score, 4),
        "center_support": round(center_support, 4),
        "extent_iou": round(extent_iou, 4),
        "extent_support": round(extent_support, 4),
        "stroke_padding_px": round(stroke_padding, 4),
        "pixel_distance": round(distance, 4) if math.isfinite(distance) else None,
        "path_distance_px": round(distance, 4) if math.isfinite(distance) else None,
        "path_length_px": round(total_length, 4),
        "path_length_in_query_px": round(inside_length, 4),
        "support_mode": "path",
    }


def _polygon_area(points: Sequence[Sequence[float]]) -> float:
    finite = _bounded_finite_path(points)
    if len(finite) < 3:
        return 0.0
    scale = max(
        1.0,
        *(abs(component) for point in finite for component in point[:2]),
    )
    normalized = [[point[0] / scale, point[1] / scale] for point in finite]
    anchor_x, anchor_y = normalized[0]
    normalized_area = abs(math.fsum(
        (normalized[index][0] - anchor_x)
        * (normalized[(index + 1) % len(normalized)][1] - anchor_y)
        - (normalized[(index + 1) % len(normalized)][0] - anchor_x)
        * (normalized[index][1] - anchor_y)
        for index in range(len(normalized))
    )) / 2.0
    if normalized_area <= 0.0:
        return 0.0
    # Pixel artifacts cannot meaningfully approach this ceiling. Cap instead
    # of emitting JSON Infinity when legacy/corrupt sidecars contain enormous
    # but individually finite coordinates.
    if scale > math.sqrt(1e300 / normalized_area):
        return 1e300
    return normalized_area * scale * scale


def _point_to_segment_distance(point: Sequence[float],
                               start: Sequence[float],
                               end: Sequence[float]) -> float:
    try:
        px, py = float(point[0]), float(point[1])
        sx, sy = float(start[0]), float(start[1])
        ex, ey = float(end[0]), float(end[1])
    except (TypeError, ValueError, OverflowError, IndexError):
        return float("inf")
    if not all(math.isfinite(value) for value in (px, py, sx, sy, ex, ey)):
        return float("inf")
    scale = max(1.0, *(abs(value) for value in (px, py, sx, sy, ex, ey)))
    px, py, sx, sy, ex, ey = (
        px / scale, py / scale, sx / scale, sy / scale,
        ex / scale, ey / scale,
    )
    dx = ex - sx
    dy = ey - sy
    length_sq = dx * dx + dy * dy
    if length_sq <= 1e-300:
        normalized_distance = math.hypot(px - sx, py - sy)
        distance = normalized_distance * scale
        return distance if math.isfinite(distance) else float("inf")
    parameter = min(1.0, max(0.0, (
        (px - sx) * dx
        + (py - sy) * dy
    ) / length_sq))
    normalized_distance = math.hypot(
        px - (sx + parameter * dx),
        py - (sy + parameter * dy),
    )
    distance = normalized_distance * scale
    return distance if math.isfinite(distance) else float("inf")


def _point_in_polygon(point: Sequence[float],
                      polygon: Sequence[Sequence[float]],
                      tolerance: float = 1e-9) -> bool:
    if len(polygon) < 3:
        return False
    for start, end in zip(polygon, [*polygon[1:], polygon[0]]):
        if _point_to_segment_distance(point, start, end) <= tolerance:
            return True
    x = float(point[0])
    y = float(point[1])
    inside = False
    previous = polygon[-1]
    for current in polygon:
        x0, y0 = float(previous[0]), float(previous[1])
        x1, y1 = float(current[0]), float(current[1])
        if (y0 > y) != (y1 > y):
            crossing_x = x0 + (y - y0) * (x1 - x0) / (y1 - y0)
            if crossing_x >= x:
                inside = not inside
        previous = current
    return inside


def _clip_polygon_to_bbox(polygon: Sequence[Sequence[float]],
                          bbox: Sequence[float]) -> List[List[float]]:
    x1, y1, x2, y2 = _normalized_pixel_bbox(bbox)
    points = [[float(point[0]), float(point[1])] for point in polygon]

    def clip(points_to_clip: List[List[float]],
             inside: Any,
             intersect: Any) -> List[List[float]]:
        if not points_to_clip:
            return []
        output: List[List[float]] = []
        previous = points_to_clip[-1]
        previous_inside = inside(previous)
        for current in points_to_clip:
            current_inside = inside(current)
            if current_inside:
                if not previous_inside:
                    output.append(intersect(previous, current))
                output.append(current)
            elif previous_inside:
                output.append(intersect(previous, current))
            previous = current
            previous_inside = current_inside
        return output

    def vertical(start: Sequence[float], end: Sequence[float], x: float) -> List[float]:
        dx = float(end[0]) - float(start[0])
        parameter = 0.0 if abs(dx) <= 1e-300 else (x - float(start[0])) / dx
        return [x, float(start[1]) + parameter * (float(end[1]) - float(start[1]))]

    def horizontal(start: Sequence[float], end: Sequence[float], y: float) -> List[float]:
        dy = float(end[1]) - float(start[1])
        parameter = 0.0 if abs(dy) <= 1e-300 else (y - float(start[1])) / dy
        return [float(start[0]) + parameter * (float(end[0]) - float(start[0])), y]

    points = clip(points, lambda point: point[0] >= x1, lambda a, b: vertical(a, b, x1))
    points = clip(points, lambda point: point[0] <= x2, lambda a, b: vertical(a, b, x2))
    points = clip(points, lambda point: point[1] >= y1, lambda a, b: horizontal(a, b, y1))
    return clip(points, lambda point: point[1] <= y2, lambda a, b: horizontal(a, b, y2))


def _pixel_polygon_support(query_bbox: Sequence[float],
                           polygon: Sequence[Sequence[float]],
                           stroke_padding: float) -> Dict[str, Any]:
    query = _finite_pixel_bbox(query_bbox)
    finite_polygon = _bounded_finite_path(polygon)
    coordinate_limit = max(
        1e9,
        *(abs(component) * 1e6 for component in (query or [1.0, 1.0, 1.0, 1.0])),
    )
    if (
        query is None
        or len(finite_polygon) < 3
        or any(
            abs(component) > coordinate_limit
            for point in finite_polygon
            for component in point[:2]
        )
    ):
        return {
            "supported": False,
            "score": 0.0,
            "query_coverage": 0.0,
            "candidate_coverage": 0.0,
            "center_inside": False,
            "boundary_distance_px": None,
            "boundary_support": 0.0,
            "extent_iou": 0.0,
            "clipped_area_px2": 0.0,
            "polygon_area_px2": 0.0,
        }
    polygon = finite_polygon
    query_area = max((query[2] - query[0]) * (query[3] - query[1]), 1e-9)
    polygon_area = max(_polygon_area(polygon), 1e-9)
    clipped_area = _polygon_area(_clip_polygon_to_bbox(polygon, query))
    query_coverage = min(1.0, clipped_area / query_area)
    candidate_coverage = min(1.0, clipped_area / polygon_area)
    polygon_bbox = _finite_pixel_bbox(_path_bbox(polygon))
    extent_iou = bbox_iou(query, polygon_bbox) if polygon_bbox else 0.0
    center = [
        query[0] + (query[2] - query[0]) / 2.0,
        query[1] + (query[3] - query[1]) / 2.0,
    ]
    center_inside = _point_in_polygon(
        center, polygon, tolerance=max(1e-9, stroke_padding * 0.05)
    )
    boundary_distance = min((
        _point_to_segment_distance(center, start, end)
        for start, end in zip(polygon, [*polygon[1:], polygon[0]])
    ), default=float("inf"))
    boundary_support = 1.0 / (
        1.0 + boundary_distance / max(stroke_padding, 1e-9)
    )
    supported = bool(
        clipped_area > 1e-9
        or center_inside
        or boundary_distance <= stroke_padding * 1.5
    )
    # A query contained by several nested profiles is common in dense CAD
    # drawings.  Query coverage alone makes every enclosing loop look almost
    # identical.  Candidate coverage and extent IoU encode the VLM's observed
    # scale, so an exact inner contour outranks a much larger enclosing loop;
    # center/boundary support still keeps a localized issue inside a lone
    # profile actionable.
    score = (
        0.45 * query_coverage
        + 0.15 * candidate_coverage
        + 0.20 * float(center_inside)
        + 0.15 * extent_iou
        + 0.05 * boundary_support
    )
    return {
        "supported": supported,
        "score": round(min(1.0, max(0.0, score)), 4),
        "query_coverage": round(query_coverage, 4),
        "candidate_coverage": round(candidate_coverage, 4),
        "center_inside": center_inside,
        "boundary_distance_px": round(boundary_distance, 4),
        "boundary_support": round(boundary_support, 4),
        "extent_iou": round(extent_iou, 4),
        "clipped_area_px2": round(clipped_area, 4),
        "polygon_area_px2": round(polygon_area, 4),
    }


def _primitive_candidates(database: CADDatabase,
                          handle: str,
                          query_bbox: List[float],
                          snapshot: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], List[str]]:
    topology = topology_for_handle(database, handle)
    primitives = topology.get("primitives", [])
    if not primitives:
        return [], ["Primitive topology is unavailable; grounding is entity-level only."]
    ranked = []
    for primitive in primitives:
        world_path = _primitive_world_path(primitive)
        world_bbox = _path_bbox(world_path) or _primitive_bbox(primitive)
        if not world_bbox:
            continue
        pixel_bbox = _finite_pixel_bbox(
            bbox_world_to_pixel(world_bbox, snapshot["world_to_pixel"])
        ) or []
        pixel_path = _transform_finite_path(
            snapshot["world_to_pixel"], world_path
        )
        support = (
            _pixel_path_support(query_bbox, pixel_path, snapshot)
            if len(pixel_path) >= 2
            else (
                _pixel_bbox_support(query_bbox, pixel_bbox, snapshot)
                if pixel_bbox else _empty_pixel_support(snapshot)
            )
        )
        score = support["score"]
        if support["overlap_score"] > 0.0 or score > 0.1:
            ranked.append({
                "primitive_key": primitive.get("primitive_key"),
                "primitive_type": primitive.get("primitive_type"),
                "role": primitive.get("role"),
                "score": round(min(score, 1.0), 4),
                "evidence": {
                    **support,
                    "pixel_bbox": pixel_bbox,
                    "pixel_path": pixel_path,
                    "world_bbox": bbox_dict(world_bbox),
                    "world_path": world_path,
                },
            })
    ranked.sort(key=lambda item: -item["score"])
    return ranked[:10], []


def _runtime_entity_path(database: CADDatabase,
                         handle: str,
                         snapshot: Dict[str, Any]) -> Tuple[
                             List[List[float]], List[List[float]], str
                         ]:
    """Compatibility bridge for snapshots created before paths were persisted."""
    cache_key = "_runtime_entity_path_cache"
    cache = snapshot.get(cache_key)
    if not isinstance(cache, dict):
        cache = {}
        snapshot[cache_key] = cache
    if handle not in cache:
        entity_map = snapshot.get("_runtime_entity_map")
        if not isinstance(entity_map, dict):
            entity_map = {
                str(entity.get("handle") or ""): entity
                for entity in all_entities(database)
            }
            snapshot["_runtime_entity_map"] = entity_map
        entity = entity_map.get(handle, {})
        world_path = _entity_world_path(entity)
        pixel_path = _transform_finite_path(
            snapshot["world_to_pixel"], world_path
        ) if snapshot.get("world_to_pixel") else []
        cache[handle] = {
            "world_path": world_path,
            "pixel_path": pixel_path,
            "entity_type": str(
                entity.get("type")
                or entity.get("entity_type")
                or entity.get("name")
                or ""
            ),
        }
    cached = cache.get(handle) or {}
    return (
        list(cached.get("world_path") or []),
        list(cached.get("pixel_path") or []),
        str(cached.get("entity_type") or ""),
    )


def _candidate_from_overlay_item(database: CADDatabase,
                                 item: Dict[str, Any],
                                 query_bbox: List[float],
                                 snapshot: Dict[str, Any],
                                 direct_reference: bool = False) -> Dict[str, Any]:
    ent_bbox = _finite_pixel_bbox(item.get("pixel_bbox")) or []
    pixel_path = item.get("pixel_path") or []
    world_path = item.get("world_path") or []
    resolved_entity_type = str(item.get("entity_type") or "")
    reconstructed_path = False
    is_semantic_item = str(item.get("item_kind") or "").lower() == "semantic"
    semantic_handles = [handle for handle in item.get("handles", []) if handle]
    item_kind = str(item.get("item_kind") or "entity").lower()
    can_reconstruct_member_path = (
        item_kind == "entity"
        or (
            is_semantic_item
            and len(semantic_handles) == 1
            and len(item.get("pixel_polygon") or []) < 3
        )
    )
    if len(pixel_path) < 2 and item.get("handle") and can_reconstruct_member_path:
        runtime_world_path, runtime_pixel_path, runtime_entity_type = _runtime_entity_path(
            database, str(item.get("handle")), snapshot
        )
        if len(runtime_pixel_path) >= 2:
            world_path = runtime_world_path
            pixel_path = runtime_pixel_path
            reconstructed_path = True
        if not resolved_entity_type:
            resolved_entity_type = runtime_entity_type
    support = (
        _pixel_path_support(query_bbox, pixel_path, snapshot)
        if len(pixel_path) >= 2
        else (
            _pixel_bbox_support(query_bbox, ent_bbox, snapshot)
            if ent_bbox else _empty_pixel_support(snapshot)
        )
    )
    if reconstructed_path:
        support["support_mode"] = "reconstructed_path"
    score = support["score"]
    is_semantic = str(item.get("item_kind") or "").lower() == "semantic"
    primitive_matches: List[Dict[str, Any]] = []
    primitive_warnings: List[str] = []
    if not is_semantic and item.get("handle"):
        primitive_matches, primitive_warnings = _primitive_candidates(
            database,
            str(item.get("handle")),
            query_bbox,
            snapshot,
        )
    if item.get("primitive_key"):
        direct_primitive = next((
            primitive for primitive in primitive_matches
            if primitive.get("primitive_key") == item.get("primitive_key")
        ), None)
        primitive_score = 1.0 if direct_reference else float(
            (direct_primitive or {}).get("score") or score
        )
        primitive_reason = (
            "VLM referenced a primitive overlay item directly."
            if direct_reference
            else "Primitive overlay item was ranked by region-to-geometry support."
        )
        primitive_matches = [{
            "primitive_key": item.get("primitive_key"),
            "primitive_type": item.get("primitive_type"),
            "role": item.get("role"),
            "score": round(primitive_score, 4),
            "evidence": {
                **((direct_primitive or {}).get("evidence") or {}),
                "reason": primitive_reason,
                "overlay_id": item.get("overlay_id"),
                "pixel_bbox": ent_bbox,
                "world_bbox": item.get("world_bbox"),
            },
        }] + [
            primitive for primitive in primitive_matches
            if primitive.get("primitive_key") != item.get("primitive_key")
        ]
    warnings = list(snapshot.get("limitations", [])) + primitive_warnings
    if item_kind == "primitive":
        path_capable = str(item.get("primitive_type") or "").lower() in {
            "line", "line_segment", "segment", "curve", "arc", "spline",
        }
    else:
        path_capable = any(token in resolved_entity_type.lower() for token in (
            "line", "polyline", "spline",
        ))
    if reconstructed_path:
        warnings.append(
            "Legacy snapshot lacked pixel-path geometry; path support was reconstructed from the current scoped scan. Re-export before an edit if the drawing may have changed."
        )
    elif path_capable and len(pixel_path) < 2:
        score = min(score, 0.25)
        support["score"] = round(score, 4)
        support["support_mode"] = "bbox_fallback_conservative"
        warnings.append(
            "Overlay item lacks authored pixel-path geometry; bbox fallback was conservatively downweighted."
        )
    raster_extent_px = (
        max(ent_bbox[2] - ent_bbox[0], ent_bbox[3] - ent_bbox[1])
        if len(ent_bbox) >= 4 else 0.0
    )
    # Only an authored POINT entity has an independently rendered point style.
    # Endpoint primitives attached to a subpixel LINE are topology metadata,
    # not visible point markers, and must remain subject to the resolution gate.
    point_marker = resolved_entity_type.lower().replace("acdb", "") == "point"
    raster_resolvable = bool(
        direct_reference
        or point_marker
        or raster_extent_px >= MIN_RESOLVABLE_EXTENT_PX
    )
    if not raster_resolvable:
        score = min(score, 0.25)
        support["score"] = round(score, 4)
        support["support_mode"] = "subpixel_unresolvable"
        warnings.append(
            "Candidate footprint is below one pixel in this snapshot; region grounding abstains because the raster cannot distinguish it reliably."
        )
    item_confidence = float(item.get("confidence", 1.0) or 0.0)
    return {
        "handle": item.get("handle"),
        "native_handle": item.get("native_handle") or item.get("handle"),
        "entity_type": resolved_entity_type or item.get("entity_type"),
        "overlay_id": item.get("overlay_id"),
        "item_kind": item.get("item_kind", "entity"),
        "parent_overlay_id": item.get("parent_overlay_id"),
        "primitive_key": item.get("primitive_key"),
        "primitive_type": item.get("primitive_type"),
        "role": item.get("role"),
        "score": round(score, 4),
        "iou_score": support["iou_score"],
        "overlap_score": support["overlap_score"],
        "query_coverage": support["query_coverage"],
        "candidate_coverage": support["candidate_coverage"],
        "distance_score": support["distance_score"],
        "center_support": support["center_support"],
        "support_mode": support.get("support_mode", "bbox"),
        "raster_extent_px": round(raster_extent_px, 4),
        "raster_resolvable": raster_resolvable,
        "pixel_bbox": ent_bbox,
        "pixel_path": pixel_path,
        "world_path": world_path,
        "world_bbox": item.get("world_bbox"),
        "layer": item.get("layer"),
        "semantic_tags": item.get("semantic_tags", []),
        "handles": item.get("handles") or ([item.get("handle")] if item.get("handle") else []),
        "object_id": item.get("object_id"),
        "object_type": item.get("object_type"),
        "label": item.get("label"),
        "candidate_primitives": primitive_matches,
        "confidence": round(min(
            1.0,
            score * float(snapshot.get("confidence", 0.5) or 0.5) * item_confidence,
        ), 4),
        "limitations": warnings,
        "evidence": {
            "query_bbox": query_bbox,
            "entity_pixel_bbox": ent_bbox,
            "entity_pixel_path": pixel_path,
            "snapshot_confidence": snapshot.get("confidence", 0.5),
            "spatial_support": support,
        },
    }


def _semantic_shape_candidates(database: CADDatabase,
                               query_bbox: List[float],
                               snapshot: Dict[str, Any],
                               top_k: int) -> List[Dict[str, Any]]:
    items = [
        item for item in snapshot.get("semantic_overlay_items", [])
        if isinstance(item, dict) and item.get("pixel_bbox")
    ]
    # Snapshots produced before semantic overlays were introduced can still use
    # the current scoped graph when a transform and view extent are available.
    if not items and snapshot.get("world_to_pixel"):
        transform = compute_view_transform(
            snapshot.get("view") or {},
            int((snapshot.get("image") or {}).get("width") or DEFAULT_IMAGE_SIZE[0]),
            int((snapshot.get("image") or {}).get("height") or DEFAULT_IMAGE_SIZE[1]),
            ucs=snapshot.get("ucs"),
            viewport=snapshot.get("viewport"),
        )
        items = _build_semantic_overlay_items(
            database, transform["world_extent"], snapshot["world_to_pixel"]
        )
    needs_geometry_refresh = any(
        len(item.get("pixel_polygon") or []) < 3
        and len(item.get("pixel_path") or []) < 2
        for item in items
    )
    refreshed_by_id: Dict[str, Dict[str, Any]] = {}
    if needs_geometry_refresh and snapshot.get("world_to_pixel"):
        transform = compute_view_transform(
            snapshot.get("view") or {},
            int((snapshot.get("image") or {}).get("width") or DEFAULT_IMAGE_SIZE[0]),
            int((snapshot.get("image") or {}).get("height") or DEFAULT_IMAGE_SIZE[1]),
            ucs=snapshot.get("ucs"),
            viewport=snapshot.get("viewport"),
        )
        refreshed_by_id = {
            str(item.get("object_id") or ""): item
            for item in _build_semantic_overlay_items(
                database, transform["world_extent"], snapshot["world_to_pixel"]
            )
        }
    candidates: List[Dict[str, Any]] = []
    for stored_item in items:
        item = dict(stored_item)
        geometry_refreshed = False
        if (
            len(item.get("pixel_polygon") or []) < 3
            and len(item.get("pixel_path") or []) < 2
        ):
            refreshed = refreshed_by_id.get(str(item.get("object_id") or ""))
            if refreshed and (
                len(refreshed.get("pixel_polygon") or []) >= 3
                or len(refreshed.get("pixel_path") or []) >= 2
            ):
                item.update({
                    key: refreshed.get(key)
                    for key in (
                        "pixel_bbox", "pixel_polygon", "pixel_path",
                        "world_bbox", "world_polygon", "world_path",
                    )
                })
                geometry_refreshed = True
        object_type = str(item.get("object_type") or "").lower()
        handles = [handle for handle in item.get("handles", []) if handle]
        contour_dependent = (
            len(handles) > 1
            or any(token in object_type for token in (
                "profile", "region", "component", "part", "pattern",
            ))
        )
        if (
            contour_dependent
            and len(item.get("pixel_polygon") or []) < 3
            and len(item.get("pixel_path") or []) < 2
        ):
            continue
        candidate = _candidate_from_overlay_item(database, item, query_bbox, snapshot)
        bbox_spatial_score = float(candidate.get("score") or 0.0)
        spatial_score = bbox_spatial_score
        pixel_polygon = item.get("pixel_polygon") or []
        polygon_support: Optional[Dict[str, Any]] = None
        if len(pixel_polygon) >= 3:
            polygon_support = _pixel_polygon_support(
                query_bbox,
                pixel_polygon,
                float(
                    ((candidate.get("evidence") or {}).get("spatial_support") or {}).get(
                        "stroke_padding_px", 2.0
                    )
                ),
            )
            if not polygon_support["supported"]:
                continue
            spatial_score = min(
                1.0,
                0.70 * bbox_spatial_score
                + 0.30 * float(polygon_support["score"]),
            )
        semantic_confidence = float(item.get("confidence") or 0.0)
        closure_prior = 1.0 if any(token in object_type for token in (
            "profile", "part", "component", "pattern", "region", "block",
        )) else 0.0
        score = min(1.0, 0.88 * spatial_score + 0.07 * semantic_confidence + 0.05 * closure_prior)
        if not candidate.get("raster_resolvable", True):
            score = min(score, 0.25)
        candidate.update({
            "candidate_type": "semantic_shape",
            "score": round(score, 4),
            "spatial_score": round(spatial_score, 4),
            "bbox_spatial_score": round(bbox_spatial_score, 4),
            "polygon_support": polygon_support,
            "semantic_confidence": round(semantic_confidence, 4),
            "closure_prior": closure_prior,
            "geometry_refreshed_from_current_graph": geometry_refreshed,
            "confidence": round(min(
                1.0,
                score * float(snapshot.get("confidence", 0.5) or 0.5) * semantic_confidence,
            ), 4),
        })
        if geometry_refreshed:
            candidate["limitations"] = sorted(set([
                *(candidate.get("limitations") or []),
                "Legacy snapshot lacked semantic contour/path geometry; it was reconstructed from the current scoped semantic graph. Re-export before editing if the drawing may have changed.",
            ]))
        # Semantic priors may rank a spatially plausible group, but must never
        # manufacture localization in a blank/distant region.
        if candidate["overlap_score"] > 0.0 or spatial_score > 0.1:
            candidates.append(candidate)
    candidates.sort(key=lambda item: (-float(item["score"]), str(item.get("object_id") or "")))
    return candidates[:max(1, min(int(top_k or 10), 100))]


def _candidate_handle_group(candidate: Dict[str, Any]) -> Tuple[str, ...]:
    values = candidate.get("handles")
    if not isinstance(values, list) or not values:
        values = [candidate.get("handle")]
    return tuple(sorted({str(handle) for handle in values if handle}))


def _distinct_handle_group_candidates(candidates: List[Dict[str, Any]],
                                      limit: int = 2) -> List[Dict[str, Any]]:
    distinct: List[Dict[str, Any]] = []
    seen = set()
    for candidate in candidates:
        group = _candidate_handle_group(candidate)
        # Candidates without any entity membership cannot establish a
        # competing CAD selection.
        if not group or group in seen:
            continue
        seen.add(group)
        distinct.append(candidate)
        if len(distinct) >= max(1, limit):
            break
    return distinct


def _candidate_has_spatial_support(candidate: Dict[str, Any]) -> bool:
    polygon_support = candidate.get("polygon_support")
    if isinstance(polygon_support, dict):
        return bool(polygon_support.get("supported"))
    spatial = (candidate.get("evidence") or {}).get("spatial_support") or {}
    support_mode = str(spatial.get("support_mode") or candidate.get("support_mode") or "")
    if support_mode in {"path", "reconstructed_path"}:
        return float(spatial.get("path_length_in_query_px") or 0.0) > 0.0
    return (
        float(spatial.get("overlap_score") or candidate.get("overlap_score") or 0.0) > 0.0
        and float(spatial.get("query_coverage") or candidate.get("query_coverage") or 0.0) > 0.0
    )


def _normalized_semantic_type(value: Any) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")
    aliases = {
        "profile": "closed_profile",
        "outer_profile": "closed_profile",
        "inner_profile": "closed_profile",
        "closed_contour": "closed_profile",
        "closed_loop": "closed_profile",
        "closed_shape": "closed_profile",
    }
    return aliases.get(normalized, normalized)


def _apply_semantic_intent(candidates: List[Dict[str, Any]],
                           semantic_type: str) -> bool:
    """Use type intent only when a spatially supported exact shape exists."""
    expected = _normalized_semantic_type(semantic_type)
    if not expected:
        return False
    exact_shapes = [
        candidate for candidate in candidates
        if str(candidate.get("candidate_type") or "") == "semantic_shape"
        and _normalized_semantic_type(candidate.get("object_type")) == expected
        and float(candidate.get("spatial_score") or candidate.get("score") or 0.0) > 0.1
    ]
    if not exact_shapes:
        return False
    for candidate in candidates:
        original_score = float(candidate.get("score") or 0.0)
        matches = candidate in exact_shapes
        candidate["unadjusted_score"] = round(original_score, 4)
        candidate["semantic_intent"] = expected
        candidate["semantic_intent_match"] = matches
        candidate["score"] = round(
            original_score + 0.04 * (1.0 - original_score)
            if matches else original_score * 0.80,
            4,
        )
    return True


def ground_vlm_region(snapshot_id: str,
                      bbox: List[float],
                      top_k: int = 10,
                      database: Optional[CADDatabase] = None,
                      semantic_type: Optional[str] = None) -> ToolResult:
    db = get_db(database)
    snapshot = _load_snapshot(db, snapshot_id)
    if not snapshot:
        return error_result(f"Unknown view snapshot: {snapshot_id}")
    if not isinstance(bbox, list) or len(bbox) < 4:
        return error_result("bbox must be [x1, y1, x2, y2]")
    try:
        bbox_values = [float(v) for v in bbox[:4]]
    except (TypeError, ValueError):
        return error_result("bbox values must be finite numbers")
    if not all(math.isfinite(value) for value in bbox_values):
        return error_result("bbox values must be finite numbers")
    query_bbox = _normalized_pixel_bbox(bbox_values)
    query_width = query_bbox[2] - query_bbox[0]
    query_height = query_bbox[3] - query_bbox[1]
    if (
        not math.isfinite(query_width)
        or not math.isfinite(query_height)
        or query_width <= 0.0
        or query_height <= 0.0
    ):
        return error_result("bbox must have positive width and height")
    image = snapshot.get("image") or {}
    image_width = float(image.get("width") or 0.0)
    image_height = float(image.get("height") or 0.0)
    if (
        image_width > 0.0 and image_height > 0.0
        and (
            query_bbox[0] < 0.0 or query_bbox[1] < 0.0
            or query_bbox[2] > image_width or query_bbox[3] > image_height
        )
    ):
        return error_result("bbox must be fully inside the snapshot image bounds")
    raw_candidates = []
    for item in snapshot.get("overlay_items", []):
        if str(item.get("item_kind") or "").lower() == "semantic":
            continue
        candidate = _candidate_from_overlay_item(db, item, query_bbox, snapshot)
        if candidate["overlap_score"] > 0.0 or candidate["score"] > 0.1:
            raw_candidates.append(candidate)
    if snapshot.get("entity_screen_bboxes"):
        fallback_items = list(snapshot.get("entity_overlay_items") or [])
        if not fallback_items:
            fallback_items = [
                {
                    "overlay_id": "",
                    "handle": handle,
                    "native_handle": handle,
                    "pixel_bbox": ent_bbox,
                }
                for handle, ent_bbox in snapshot.get("entity_screen_bboxes", {}).items()
            ]
        for item in fallback_items:
            candidate = _candidate_from_overlay_item(db, item, query_bbox, snapshot)
            if candidate["overlap_score"] > 0.0 or candidate["score"] > 0.1:
                raw_candidates.append(candidate)
    # Entity+primitive overlays may repeat one handle many times. Keep the best
    # localized representation per handle so a dense polyline cannot consume
    # every top-k slot.
    best_by_handle: Dict[str, Dict[str, Any]] = {}
    for candidate in raw_candidates:
        handle = str(candidate.get("handle") or "")
        if not handle:
            continue
        existing = best_by_handle.get(handle)
        if existing is None or float(candidate["score"]) > float(existing["score"]):
            best_by_handle[handle] = candidate
    all_entity_candidates = sorted(
        best_by_handle.values(),
        key=lambda item: (-float(item["score"]), str(item.get("handle") or "")),
    )
    requested_top_k = max(1, min(int(top_k or 10), 100))
    # Decision candidates must not disappear when callers request top_k=1.
    # Search a bounded but broad pool so repeated semantic representations of
    # one handle group cannot hide a genuine runner-up.
    decision_top_k = 100
    all_shape_candidates = _semantic_shape_candidates(
        db, query_bbox, snapshot, decision_top_k
    )
    semantic_intent_applied = _apply_semantic_intent(
        [*all_shape_candidates, *all_entity_candidates],
        str(semantic_type or ""),
    )
    all_shape_candidates.sort(
        key=lambda item: (-float(item["score"]), str(item.get("object_id") or ""))
    )
    all_entity_candidates.sort(
        key=lambda item: (-float(item["score"]), str(item.get("handle") or ""))
    )
    combined = sorted(
        [*all_shape_candidates, *all_entity_candidates],
        key=lambda item: (-float(item.get("score") or 0.0), str(item.get("object_id") or item.get("handle") or "")),
    )
    acceptable_candidates = [
        candidate for candidate in combined
        if float(candidate.get("score") or 0.0) >= MIN_REGION_GROUNDING_SCORE
        and _candidate_has_spatial_support(candidate)
    ]
    semantic_decision_pool = [
        candidate for candidate in acceptable_candidates
        if semantic_intent_applied
        and candidate.get("candidate_type") == "semantic_shape"
        and candidate.get("semantic_intent_match") is True
    ]
    # Once the requested semantic family is known to exist, member entities
    # are supporting evidence rather than alternative object selections.  If
    # every matching semantic shape is below the safety floor (for example a
    # subpixel contour), abstain instead of silently falling back to an edge or
    # endpoint from that unresolved shape.
    decision_pool = (
        semantic_decision_pool
        if semantic_intent_applied else acceptable_candidates
    )
    decision_candidates = _distinct_handle_group_candidates(
        decision_pool, limit=2
    )
    recommended = decision_candidates[0] if decision_candidates else None
    runner_up = decision_candidates[1] if len(decision_candidates) > 1 else None
    margin = (
        float(recommended.get("score") or 0.0) - float(runner_up.get("score") or 0.0)
        if recommended and runner_up else (float(recommended.get("score") or 0.0) if recommended else 0.0)
    )
    ambiguous = bool(recommended and runner_up and margin < 0.08)
    candidates = all_entity_candidates[:requested_top_k]
    shape_candidates = all_shape_candidates[:requested_top_k]
    world_region = map_pixel_region_to_world_bbox(snapshot_id, query_bbox, database=db)
    return ok_result(
        f"Grounded VLM region to {len(candidates)} entity and {len(shape_candidates)} shape candidate(s).",
        data={
            "candidates": candidates,
            "shape_candidates": shape_candidates,
            "recommended_candidate": recommended,
            "selection": {
                "strategy": "coarse_to_fine_shape_then_entity",
                "semantic_intent": _normalized_semantic_type(semantic_type),
                "semantic_intent_applied": semantic_intent_applied,
                "score_margin": round(margin, 4),
                "ambiguous": ambiguous,
                "ambiguity_threshold": 0.08,
                "minimum_grounding_score": MIN_REGION_GROUNDING_SCORE,
                "acceptable_candidate_count": len(acceptable_candidates),
                "decision_pool": (
                    "semantic_shape_matches"
                    if semantic_decision_pool
                    else (
                        "semantic_shape_matches_below_threshold"
                        if semantic_intent_applied else "all_spatial_candidates"
                    )
                ),
                "recommended_handle_group": list(_candidate_handle_group(recommended or {})),
                "runner_up_handle_group": list(_candidate_handle_group(runner_up or {})),
                "decision_candidates": decision_candidates,
            },
            "bbox": query_bbox,
            "world_region": world_region["data"].get("world_bbox") if world_region["ok"] else None,
            "snapshot_id": snapshot_id,
            "confidence": snapshot.get("confidence", 0.5),
            "limitations": snapshot.get("limitations", []),
        },
        handles=sorted({
            handle
            for candidate in [*(shape_candidates or []), *(candidates or [])]
            for handle in _candidate_handle_group(candidate)
        }),
        warnings=sorted(set(snapshot.get("limitations", []) + (
            ["Top grounding candidates are close in score; keep the result ambiguous until semantic or handle evidence confirms one."]
            if ambiguous else []
        ))),
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
    candidate = _candidate_from_overlay_item(
        db, item, bbox, snapshot, direct_reference=True
    )
    candidate["score"] = 1.0
    candidate["iou_score"] = 1.0
    candidate["overlap_score"] = 1.0
    candidate["distance_score"] = 1.0
    candidate["confidence"] = round(min(
        1.0,
        float(snapshot.get("confidence", 0.5) or 0.5)
        * float(item.get("confidence", 1.0) or 0.0),
    ), 4)
    grounded_handles = [
        str(handle) for handle in (item.get("handles") or [item.get("handle")]) if handle
    ]
    return ok_result(
        f"Grounded overlay_id {overlay_id} to handle {item.get('handle')}.",
        data={"candidate": candidate, "snapshot_id": snapshot_id},
        handles=grounded_handles,
        warnings=candidate.get("limitations", []),
        next_tools=["explain_entity", "validate_geometry"],
    )


def overlay_id_sort_key(value: str) -> Tuple[int, str]:
    match = re.search(r"(\d+)$", value or "")
    return (int(match.group(1)) if match else 0, value or "")
