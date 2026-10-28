"""
CAD Utilities — shared helpers for the MCP server.

Includes coordinate conversion, color helpers, error formatting,
and common geometry calculations.
"""

import math
import random
import re
import string
from typing import Any, Dict, List, Optional, Sequence, Tuple


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


# COM Helpers

def com_get(obj: Any, name: str, default: Any = None) -> Any:
    """Read an AutoCAD COM property across pywin32 case variants."""
    candidates = [name]
    lower = name[:1].lower() + name[1:]
    upper = name[:1].upper() + name[1:]
    for candidate in (lower, upper):
        if candidate not in candidates:
            candidates.append(candidate)
    for candidate in candidates:
        try:
            return getattr(obj, candidate)
        except Exception:
            continue
    return default


def com_set(obj: Any, name: str, value: Any) -> bool:
    """Set an AutoCAD COM property across pywin32 case variants."""
    candidates = [name]
    lower = name[:1].lower() + name[1:]
    upper = name[:1].upper() + name[1:]
    for candidate in (lower, upper):
        if candidate not in candidates:
            candidates.append(candidate)
    for candidate in candidates:
        try:
            setattr(obj, candidate, value)
            return True
        except Exception:
            continue
    return False


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
    parts = [f"OK: {message}"]
    for k, v in kwargs.items():
        parts.append(f"  {k}: {v}")
    return "\n".join(parts)

def format_error(message: str) -> str:
    return f"ERROR: {message}"


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


_LISP_HANDLE_RE = re.compile(r"^[0-9A-F]+$")
_LISP_NUMBER_RE = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?")


def lisp_string(value: str) -> str:
    """Escape a value for placement inside an AutoLISP double-quoted string."""
    return str(value).replace("\\", "\\\\").replace('"', '\\"')


def lisp_quoted(value: str, name: str = "text") -> str:
    """Return a quoted AutoLISP string that cannot close the surrounding literal."""
    text = str(value)
    if any(ord(char) < 32 for char in text):
        raise ValueError(f"非法文本 {name}: {value!r}")
    return lisp_string(text)


def lisp_handle(handle: str) -> str:
    """Return an AutoCAD handle safe to embed in `(handent "...")`."""
    text = str(handle or "").strip().upper()
    if not _LISP_HANDLE_RE.fullmatch(text):
        raise ValueError(f"非法实体句柄: {handle!r}")
    return text


def lisp_handent(handle: str) -> str:
    """Return a `(handent "...")` form for a validated handle."""
    return f'(handent "{lisp_handle(handle)}")'


def lisp_number(value: Any, name: str = "value") -> str:
    """Return a finite decimal literal safe to interpolate into AutoLISP."""
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError(f"非法数值 {name}: {value!r}")
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped or any(ord(char) < 32 or char.isspace() for char in stripped):
            raise ValueError(f"非法数值 {name}: {value!r}")
        value = stripped
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"非法数值 {name}: {value!r}") from exc
    if not math.isfinite(number):
        raise ValueError(f"非法数值 {name}: {value!r}")
    text = format(number, ".12g")
    if not _LISP_NUMBER_RE.fullmatch(text):
        raise ValueError(f"非法数值 {name}: {value!r}")
    return text


def lisp_point(point: Sequence[Any], name: str = "point") -> str:
    """Return an AutoLISP point list built from finite coordinates."""
    if isinstance(point, (str, bytes)) or not isinstance(point, Sequence) or len(point) < 2:
        raise ValueError(f"非法坐标 {name}: {point!r}")
    x = lisp_number(point[0], f"{name}.x")
    y = lisp_number(point[1], f"{name}.y")
    z = lisp_number(point[2] if len(point) > 2 else 0.0, f"{name}.z")
    return f"(list {x} {y} {z})"


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
