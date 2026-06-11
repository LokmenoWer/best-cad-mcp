"""
CAD Utilities — shared helpers for the MCP server.

Includes coordinate conversion, color helpers, error formatting,
and common geometry calculations.
"""

import math
import random
import string
from typing import List, Tuple, Optional, Dict, Any


# ── Color Helpers ───────────────────────────────────────────────

# AutoCAD Color Index (ACI) — common named colors
ACI_COLORS = {
    "red": 1,          "yellow": 2,      "green": 3,
    "cyan": 4,         "blue": 5,        "magenta": 6,
    "white": 7,        "black": 0,       "byblock": 0,
    "dark_red": 8,     "dark_blue": 170, "orange": 30,
    "brown": 42,       "purple": 200,    "pink": 220,
    "gray": 8,         "dark_gray": 252, "light_gray": 254,
    "bylayer": 256,
}

def resolve_color(color) -> int:
    """Resolve a color name or index to an ACI integer."""
    if isinstance(color, int):
        return max(0, min(256, color))
    if isinstance(color, str):
        return ACI_COLORS.get(color.lower(), 7)
    return 7

def color_name(aci: int) -> str:
    """Get the name of an ACI color index."""
    for name, idx in ACI_COLORS.items():
        if idx == aci:
            return name
    return f"ACI_{aci}"


# ── Geometry Helpers ───────────────────────────────────────────

def distance_2d(x1: float, y1: float, x2: float, y2: float) -> float:
    return math.sqrt((x2 - x1)**2 + (y2 - y1)**2)

def distance_3d(p1: List[float], p2: List[float]) -> float:
    dx = p1[0] - p2[0]
    dy = p1[1] - p2[1]
    dz = (p1[2] if len(p1) > 2 else 0) - (p2[2] if len(p2) > 2 else 0)
    return math.sqrt(dx*dx + dy*dy + dz*dz)

def angle_2d(x1: float, y1: float, x2: float, y2: float) -> float:
    """Angle in degrees from (x1,y1) to (x2,y2)."""
    return math.atan2(y2 - y1, x2 - x1) * 180.0 / math.pi

def midpoint(x1: float, y1: float, x2: float, y2: float) -> Tuple[float, float]:
    return ((x1 + x2) / 2, (y1 + y2) / 2)

def polar_point(x: float, y: float, distance: float, angle_deg: float) -> Tuple[float, float]:
    """Calculate point at distance and angle from origin."""
    rad = angle_deg * math.pi / 180.0
    return (x + distance * math.cos(rad), y + distance * math.sin(rad))

def polygon_vertices(cx: float, cy: float, radius: float, sides: int,
                     start_angle_deg: float = 0) -> List[float]:
    """Generate polygon vertex coordinates as flat list [x1,y1, x2,y2, ...]."""
    vertices = []
    for i in range(sides):
        angle = start_angle_deg + 360 * i / sides
        px, py = polar_point(cx, cy, radius, angle)
        vertices.extend([px, py])
    return vertices

def bbox_from_points(points: List[Tuple[float, float]]) -> Tuple[float, float, float, float]:
    """Calculate bounding box from a list of (x,y) points."""
    if not points:
        return (0, 0, 0, 0)
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return (min(xs), min(ys), max(xs), max(ys))

def is_point_in_bbox(x: float, y: float,
                     bbox: Tuple[float, float, float, float]) -> bool:
    return bbox[0] <= x <= bbox[2] and bbox[1] <= y <= bbox[3]

def bboxes_overlap(bbox1: Tuple[float, float, float, float],
                   bbox2: Tuple[float, float, float, float]) -> bool:
    return not (bbox1[2] < bbox2[0] or bbox1[0] > bbox2[2] or
                bbox1[3] < bbox2[1] or bbox1[1] > bbox2[3])


# ── Formatting Helpers ──────────────────────────────────────────

def format_entity_list(entities: List[Dict], max_items: int = 50) -> str:
    """Format a list of entity dicts into an AI-readable summary."""
    if not entities:
        return "无实体。"
    total = len(entities)
    lines = [f"共 {total} 个实体:"]
    for i, ent in enumerate(entities[:max_items]):
        lines.append(f"  [{i}] {ent.get('type','?')} | "
                     f"Handle: {ent.get('handle','?')} | "
                     f"Layer: {ent.get('layer','?')} | "
                     f"Color: {ent.get('color','?')}")
    if total > max_items:
        lines.append(f"  ... 以及其他 {total - max_items} 个实体")
    return "\n".join(lines)

def format_success(message: str, **kwargs) -> str:
    """Format a success result string."""
    parts = [f"✓ {message}"]
    for k, v in kwargs.items():
        parts.append(f"  {k}: {v}")
    return "\n".join(parts)

def format_error(message: str) -> str:
    return f"✗ 错误: {message}"


# ── Validation Helpers ─────────────────────────────────────────

def validate_coordinate(x: Any, y: Any, z: Any = None) -> Tuple[float, float, Optional[float]]:
    """Validate and convert coordinates to floats."""
    try:
        fx = float(x)
        fy = float(y)
        fz = float(z) if z is not None else None
        return (fx, fy, fz)
    except (ValueError, TypeError):
        raise ValueError(f"无效坐标: ({x}, {y}, {z})")

def validate_positive(value: float, name: str = "value") -> float:
    value = float(value)
    if value <= 0:
        raise ValueError(f"{name} 必须为正数，得到 {value}")
    return value

def validate_handle(handle: str) -> str:
    if not handle or not isinstance(handle, str):
        raise ValueError("句柄不能为空")
    return handle.strip().upper()


# ── ID Generation ───────────────────────────────────────────────

def generate_name(prefix: str = "MCP", length: int = 6) -> str:
    """Generate a readable random name for selection sets, groups, etc."""
    chars = string.ascii_uppercase + string.digits
    suffix = ''.join(random.choices(chars, k=length))
    return f"{prefix}_{suffix}"


# ── Level of Detail ────────────────────────────────────────────

class DetailLevel:
    """Controls how much detail to include in scan results."""
    MINIMAL = "minimal"     # handle + type + layer only
    STANDARD = "standard"   # + color, basic geometry
    FULL = "full"           # all properties
