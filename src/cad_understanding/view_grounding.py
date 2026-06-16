"""View snapshot mapping and VLM region grounding helpers."""

from __future__ import annotations

import json
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
    current_scope,
    ensure_understanding_schema,
    get_db,
    now_iso,
    point_distance,
    stable_id,
)
from .result import ToolResult, error_result, ok_result

DEFAULT_IMAGE_SIZE = (1600, 1000)


def compute_plan_view_transform(view: Dict[str, Any],
                                image_width: int,
                                image_height: int) -> Dict[str, Any]:
    height = float(view.get("height") or 0.0)
    width = float(view.get("width") or 0.0)
    if height <= 0:
        height = 100.0
    if width <= 0:
        width = height * (float(image_width) / max(float(image_height), 1.0))
    center = view.get("center") or view.get("target") or [0.0, 0.0, 0.0]
    cx = float(center[0]) if len(center) > 0 else 0.0
    cy = float(center[1]) if len(center) > 1 else 0.0
    min_x = cx - width / 2.0
    max_y = cy + height / 2.0
    scale = min(float(image_width) / width, float(image_height) / height)
    content_width = width * scale
    content_height = height * scale
    offset_x = (float(image_width) - content_width) / 2.0
    offset_y = (float(image_height) - content_height) / 2.0
    world_to_pixel = [
        [scale, 0.0, -min_x * scale + offset_x],
        [0.0, -scale, max_y * scale + offset_y],
        [0.0, 0.0, 1.0],
    ]
    pixel_to_world = [
        [1.0 / scale, 0.0, min_x - offset_x / scale],
        [0.0, -1.0 / scale, max_y + offset_y / scale],
        [0.0, 0.0, 1.0],
    ]
    extent = (min_x, cy - height / 2.0, min_x + width, max_y)
    content_bbox = [
        offset_x,
        offset_y,
        offset_x + content_width,
        offset_y + content_height,
    ]
    warnings = []
    direction = view.get("direction") or view.get("view_direction") or [0, 0, 1]
    if len(direction) >= 3 and abs(float(direction[2] or 0.0)) < 0.9:
        warnings.append("Exact 3D/non-plan view mapping is not supported yet; using plan-view approximation.")
    if abs(float(view.get("twist") or 0.0)) > 1e-9:
        warnings.append("View twist is ignored by the first plan-view mapper.")
    return {
        "world_to_pixel": world_to_pixel,
        "pixel_to_world": pixel_to_world,
        "world_extent": extent,
        "content_bbox": content_bbox,
        "scale": scale,
        "warnings": warnings,
    }


def apply_matrix_2d(matrix: Sequence[Sequence[float]],
                    x: float,
                    y: float) -> List[float]:
    px = matrix[0][0] * x + matrix[0][1] * y + matrix[0][2]
    py = matrix[1][0] * x + matrix[1][1] * y + matrix[1][2]
    return [px, py]


def bbox_world_to_pixel(bbox: Tuple[float, float, float, float],
                        matrix: Sequence[Sequence[float]]) -> List[float]:
    p1 = apply_matrix_2d(matrix, bbox[0], bbox[1])
    p2 = apply_matrix_2d(matrix, bbox[2], bbox[3])
    return [min(p1[0], p2[0]), min(p1[1], p2[1]), max(p1[0], p2[0]), max(p1[1], p2[1])]


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
    return DEFAULT_IMAGE_SIZE


def _write_svg_overlay(path: Path,
                       image_width: int,
                       image_height: int,
                       bboxes: Dict[str, List[float]]) -> str:
    overlay_path = path.with_name(f"{path.stem}_overlay.svg")
    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{image_width}" height="{image_height}" viewBox="0 0 {image_width} {image_height}">',
        '<rect width="100%" height="100%" fill="white"/>',
    ]
    for index, (handle, bbox) in enumerate(bboxes.items(), start=1):
        x1, y1, x2, y2 = bbox
        lines.append(
            f'<rect x="{x1:.2f}" y="{y1:.2f}" width="{max(0.5, x2 - x1):.2f}" '
            f'height="{max(0.5, y2 - y1):.2f}" fill="none" stroke="#e11d48" stroke-width="2"/>'
        )
        lines.append(
            f'<text x="{x1:.2f}" y="{max(12.0, y1 - 3):.2f}" font-size="12" '
            f'font-family="Arial" fill="#111827">{index}</text>'
        )
        lines.append(f'<title>{index}: {handle}</title>')
    lines.append("</svg>")
    overlay_path.write_text("\n".join(lines), encoding="utf-8")
    return str(overlay_path)


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
            snapshot.get("image_path", ""),
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
        from src.cad_tools import file_tools, view_tools

        export_message = file_tools.export_view_image(str(path))
        try:
            view = json.loads(view_tools.get_current_view())
            if not isinstance(view, dict):
                raise ValueError("current view payload is not an object")
        except Exception as exc:
            view = {"center": [0, 0, 0], "height": 100, "width": 160, "target": [0, 0, 0], "direction": [0, 0, 1]}
            warnings.append(f"Could not read current AutoCAD view; used default mapping view: {exc}")
    except Exception as exc:
        view = {"center": [0, 0, 0], "height": 100, "width": 160, "target": [0, 0, 0], "direction": [0, 0, 1]}
        warnings.append(f"View export failed or AutoCAD is unavailable: {exc}")

    image_width, image_height = _image_size(str(path))
    transform = compute_plan_view_transform(view, image_width, image_height)
    warnings.extend(transform["warnings"])
    visible_handles: List[str] = []
    entity_screen_bboxes: Dict[str, List[float]] = {}
    if include_entity_bboxes:
        visible_handles, entity_screen_bboxes = _visible_entity_bboxes(
            db, transform["world_extent"], transform["world_to_pixel"]
        )

    overlay_path = ""
    if include_overlay:
        overlay_path = _write_svg_overlay(path, image_width, image_height, entity_screen_bboxes)

    snapshot = {
        "snapshot_id": stable_id("snapshot", str(path), now_iso()),
        "image_path": str(path),
        "overlay_image_path": overlay_path,
        "view": {
            "target": view.get("target") or view.get("center") or [0, 0, 0],
            "height": view.get("height"),
            "width": view.get("width"),
            "view_direction": view.get("direction") or view.get("view_direction") or [0, 0, 1],
            "twist": view.get("twist", 0.0),
            "center": view.get("center") or view.get("target") or [0, 0, 0],
        },
        "image": {"width": image_width, "height": image_height},
        "content_bbox": transform["content_bbox"],
        "world_to_pixel": transform["world_to_pixel"],
        "pixel_to_world": transform["pixel_to_world"],
        "visible_handles": visible_handles,
        "entity_screen_bboxes": entity_screen_bboxes,
        "context_json_path": "",
        "export_message": export_message,
    }
    context_path = path.with_name(f"{path.stem}_mapping.json")
    context_path.write_text(json.dumps(snapshot, indent=2, ensure_ascii=False), encoding="utf-8")
    snapshot["context_json_path"] = str(context_path)
    context_path.write_text(json.dumps(snapshot, indent=2, ensure_ascii=False), encoding="utf-8")
    _store_snapshot(db, snapshot)
    return ok_result(
        "Exported view image mapping snapshot.",
        data={"snapshot": snapshot},
        handles=visible_handles,
        warnings=warnings,
        next_tools=["ground_vlm_region", "get_visible_entities_in_view", "explain_entity"],
    )


def get_visible_entities_in_view(snapshot_id: str,
                                 database: Optional[CADDatabase] = None) -> ToolResult:
    snapshot = _load_snapshot(get_db(database), snapshot_id)
    if not snapshot:
        return error_result(f"Unknown view snapshot: {snapshot_id}")
    handles = snapshot.get("visible_handles", [])
    return ok_result(
        f"Snapshot {snapshot_id} has {len(handles)} visible entities.",
        data={"visible_handles": handles, "entity_screen_bboxes": snapshot.get("entity_screen_bboxes", {})},
        handles=handles,
        next_tools=["ground_vlm_region", "explain_entity"],
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
        data={"world": [world[0], world[1], 0.0], "pixel": [x, y], "snapshot_id": snapshot_id},
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
        data={"pixel": pixel, "world": [x, y, z], "snapshot_id": snapshot_id},
    )


def ground_vlm_region(snapshot_id: str,
                      bbox: List[float],
                      top_k: int = 10,
                      database: Optional[CADDatabase] = None) -> ToolResult:
    snapshot = _load_snapshot(get_db(database), snapshot_id)
    if not snapshot:
        return error_result(f"Unknown view snapshot: {snapshot_id}")
    if not isinstance(bbox, list) or len(bbox) < 4:
        return error_result("bbox must be [x1, y1, x2, y2]")
    query_bbox = [float(v) for v in bbox[:4]]
    query_center = [(query_bbox[0] + query_bbox[2]) / 2.0, (query_bbox[1] + query_bbox[3]) / 2.0]
    image = snapshot.get("image", {})
    diag = max(point_distance([0, 0], [image.get("width", 1), image.get("height", 1)]), 1.0)
    candidates = []
    for handle, ent_bbox in snapshot.get("entity_screen_bboxes", {}).items():
        ent_center = bbox_center(tuple(ent_bbox))
        iou = bbox_iou(query_bbox, ent_bbox)
        distance = point_distance(query_center, ent_center or query_center)
        distance_score = max(0.0, 1.0 - distance / diag)
        score = 0.75 * iou + 0.25 * distance_score
        if iou > 0.0 or score > 0.1:
            candidates.append({
                "handle": handle,
                "score": round(score, 4),
                "overlap_score": round(iou, 4),
                "distance_score": round(distance_score, 4),
                "screen_bbox": ent_bbox,
            })
    candidates.sort(key=lambda item: -item["score"])
    candidates = candidates[:max(1, min(int(top_k or 10), 100))]
    return ok_result(
        f"Grounded VLM region to {len(candidates)} candidate entities.",
        data={"candidates": candidates, "bbox": query_bbox, "snapshot_id": snapshot_id},
        handles=[candidate["handle"] for candidate in candidates],
        next_tools=["explain_entity", "validate_geometry"],
    )
