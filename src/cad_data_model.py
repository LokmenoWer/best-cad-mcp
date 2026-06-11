"""
CAD Data Model — AI-understandable CAD document representation.

Provides structured, typed data classes that model the full CAD domain:
  - Entities (lines, circles, arcs, polylines, text, dimensions, blocks, etc.)
  - Layers, styles, layouts, views
  - Selection sets, groups
  - Document metadata

These models act as the "translation layer" between raw COM objects and
the MCP tools. Claude/LLMs receive these structured representations so
they can reason about the drawing without needing to understand COM internals.

Design principles:
  - Every model is JSON-serializable (via .to_dict() / .to_json())
  - Properties are flattened where possible for AI readability
  - Bounding boxes and spatial metadata are always included
  - Handles link back to the live COM entity for modifications
"""

from dataclasses import dataclass, field, asdict
from typing import Optional, List, Tuple, Dict, Any
from enum import Enum, auto
import json
import math


# ── Enums ──────────────────────────────────────────────────────

class EntityType(str, Enum):
    """AutoCAD entity type constants (AI-friendly names)."""
    LINE = "Line"
    CIRCLE = "Circle"
    ARC = "Arc"
    ELLIPSE = "Ellipse"
    SPLINE = "Spline"
    POLYLINE = "Polyline"
    POLYLINE_3D = "3DPolyline"
    POINT = "Point"
    RAY = "Ray"
    XLINE = "XLine"
    MLINE = "MLine"
    SOLID = "Solid"
    TEXT = "Text"
    MTEXT = "MText"
    DIMENSION = "Dimension"
    LEADER = "Leader"
    MLEADER = "MLeader"
    HATCH = "Hatch"
    TABLE = "Table"
    BLOCK_REF = "BlockReference"
    IMAGE = "Image"
    XREF = "ExternalReference"
    REGION = "Region"
    MESH = "Mesh"
    SURFACE = "Surface"
    UNKNOWN = "Unknown"

    @classmethod
    def from_object_name(cls, name: str) -> 'EntityType':
        mapping = {
            "AcDbLine": cls.LINE,
            "AcDbCircle": cls.CIRCLE,
            "AcDbArc": cls.ARC,
            "AcDbEllipse": cls.ELLIPSE,
            "AcDbSpline": cls.SPLINE,
            "AcDbPolyline": cls.POLYLINE,
            "AcDb2dPolyline": cls.POLYLINE,
            "AcDb3dPolyline": cls.POLYLINE_3D,
            "AcDbPoint": cls.POINT,
            "AcDbRay": cls.RAY,
            "AcDbXline": cls.XLINE,
            "AcDbMLine": cls.MLINE,
            "AcDbSolid": cls.SOLID,
            "AcDbText": cls.TEXT,
            "AcDbMText": cls.MTEXT,
            "AcDbAlignedDimension": cls.DIMENSION,
            "AcDbRotatedDimension": cls.DIMENSION,
            "AcDbRadialDimension": cls.DIMENSION,
            "AcDbDiametricDimension": cls.DIMENSION,
            "AcDbAngularDimension": cls.DIMENSION,
            "AcDbOrdinateDimension": cls.DIMENSION,
            "AcDbLeader": cls.LEADER,
            "AcDbMLeader": cls.MLEADER,
            "AcDbHatch": cls.HATCH,
            "AcDbTable": cls.TABLE,
            "AcDbBlockReference": cls.BLOCK_REF,
            "AcDbRasterImage": cls.IMAGE,
            "AcDbXref": cls.XREF,
            "AcDbRegion": cls.REGION,
            "AcDbSubDMesh": cls.MESH,
        }
        return mapping.get(name, cls.UNKNOWN)


class CoordinateSystem(str, Enum):
    WORLD = "WCS"
    USER = "UCS"
    DISPLAY = "DCS"


class Units(int, Enum):
    """AutoCAD INSUNITS values."""
    INCHES = 1
    FEET = 2
    MILES = 3
    MILLIMETERS = 4
    CENTIMETERS = 5
    METERS = 6
    KILOMETERS = 7
    MICROINCHES = 8
    MILS = 9
    YARDS = 10
    ANGSTROMS = 11
    NANOMETERS = 12
    MICRONS = 13
    DECIMETERS = 14
    DECAMETERS = 15
    HECTOMETERS = 16
    GIGAMETERS = 17
    ASTRONOMICAL = 18
    LIGHT_YEARS = 19
    PARSECS = 20


# ── Base Models ────────────────────────────────────────────────

@dataclass
class Point3D:
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0

    def to_tuple(self) -> Tuple[float, float, float]:
        return (self.x, self.y, self.z)

    def to_list(self) -> List[float]:
        return [self.x, self.y, self.z]

    def distance_to(self, other: 'Point3D') -> float:
        return math.sqrt((self.x - other.x)**2 + (self.y - other.y)**2 + (self.z - other.z)**2)

    def midpoint(self, other: 'Point3D') -> 'Point3D':
        return Point3D((self.x + other.x) / 2, (self.y + other.y) / 2, (self.z + other.z) / 2)

    @classmethod
    def from_list(cls, lst: List[float]) -> 'Point3D':
        if len(lst) >= 3:
            return cls(lst[0], lst[1], lst[2])
        elif len(lst) == 2:
            return cls(lst[0], lst[1], 0.0)
        return cls()


@dataclass
class BoundingBox:
    min: Point3D = field(default_factory=Point3D)
    max: Point3D = field(default_factory=Point3D)

    @property
    def width(self) -> float:
        return self.max.x - self.min.x

    @property
    def height(self) -> float:
        return self.max.y - self.min.y

    @property
    def center(self) -> Point3D:
        return Point3D((self.min.x + self.max.x) / 2,
                       (self.min.y + self.max.y) / 2,
                       (self.min.z + self.max.z) / 2)

    def contains(self, pt: Point3D) -> bool:
        return (self.min.x <= pt.x <= self.max.x and
                self.min.y <= pt.y <= self.max.y)


@dataclass
class CADEntity:
    """Base representation of any AutoCAD entity."""
    handle: str
    entity_type: EntityType = EntityType.UNKNOWN
    layer: str = "0"
    color: int = 256  # ByLayer
    linetype: str = "ByLayer"
    linetype_scale: float = 1.0
    lineweight: float = -1.0  # ByLayer
    visible: bool = True
    bounds: Optional[BoundingBox] = None

    # Type-specific properties serialized as dict
    geometry: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["entity_type"] = self.entity_type.value if isinstance(self.entity_type, EntityType) else self.entity_type
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, default=str)


@dataclass
class LineEntity(CADEntity):
    entity_type: EntityType = EntityType.LINE

    @property
    def start_point(self) -> Point3D:
        return Point3D.from_list(self.geometry.get("start_point", [0, 0, 0]))

    @property
    def end_point(self) -> Point3D:
        return Point3D.from_list(self.geometry.get("end_point", [0, 0, 0]))

    @property
    def length(self) -> float:
        return self.start_point.distance_to(self.end_point)

    @property
    def angle_deg(self) -> float:
        dx = self.end_point.x - self.start_point.x
        dy = self.end_point.y - self.start_point.y
        return math.atan2(dy, dx) * 180.0 / math.pi


@dataclass
class CircleEntity(CADEntity):
    entity_type: EntityType = EntityType.CIRCLE

    @property
    def center(self) -> Point3D:
        return Point3D.from_list(self.geometry.get("center", [0, 0, 0]))

    @property
    def radius(self) -> float:
        return self.geometry.get("radius", 0.0)

    @property
    def diameter(self) -> float:
        return self.radius * 2

    @property
    def area(self) -> float:
        return math.pi * self.radius ** 2

    @property
    def circumference(self) -> float:
        return 2 * math.pi * self.radius


@dataclass
class ArcEntity(CADEntity):
    entity_type: EntityType = EntityType.ARC

    @property
    def center(self) -> Point3D:
        return Point3D.from_list(self.geometry.get("center", [0, 0, 0]))

    @property
    def radius(self) -> float:
        return self.geometry.get("radius", 0.0)

    @property
    def start_angle_deg(self) -> float:
        return self.geometry.get("start_angle", 0.0) * 180.0 / math.pi

    @property
    def end_angle_deg(self) -> float:
        return self.geometry.get("end_angle", 0.0) * 180.0 / math.pi


@dataclass
class PolylineEntity(CADEntity):
    entity_type: EntityType = EntityType.POLYLINE

    @property
    def vertices(self) -> List[Point3D]:
        verts = self.geometry.get("vertices", [])
        return [Point3D.from_list(v) for v in verts]

    @property
    def is_closed(self) -> bool:
        return self.geometry.get("closed", False)

    @property
    def area(self) -> float:
        return self.geometry.get("area", 0.0)


@dataclass
class TextEntity(CADEntity):
    entity_type: EntityType = EntityType.TEXT

    @property
    def text(self) -> str:
        return self.geometry.get("text_string", "")

    @property
    def insertion_point(self) -> Point3D:
        return Point3D.from_list(self.geometry.get("insertion_point", [0, 0, 0]))

    @property
    def height(self) -> float:
        return self.geometry.get("height", 2.5)


# ── Layer Model ────────────────────────────────────────────────

@dataclass
class CADLayer:
    name: str
    color: int = 7
    linetype: str = "Continuous"
    lineweight: float = -1.0
    is_frozen: bool = False
    is_locked: bool = False
    is_on: bool = True
    is_plottable: bool = True
    transparency: float = 0.0
    description: str = ""
    handle: str = ""
    entity_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ── Block Model ────────────────────────────────────────────────

@dataclass
class CADBlock:
    name: str
    entity_count: int = 0
    is_layout: bool = False
    is_xref: bool = False
    origin: Point3D = field(default_factory=Point3D)
    path: str = ""
    entities: List[CADEntity] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["origin"] = self.origin.to_list()
        return d


# ── Style Models ───────────────────────────────────────────────

@dataclass
class CADTextStyle:
    name: str
    font_file: str = ""
    big_font_file: str = ""
    height: float = 0.0
    width: float = 1.0
    oblique_angle: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class CADDimStyle:
    name: str
    handle: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ── View Model ─────────────────────────────────────────────────

@dataclass
class CADView:
    center: Point3D = field(default_factory=Point3D)
    height: float = 0.0
    width: float = 0.0
    target: Point3D = field(default_factory=Point3D)
    direction: Point3D = field(default_factory=Point3D)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "center": self.center.to_list(),
            "height": self.height,
            "width": self.width,
            "target": self.target.to_list(),
            "direction": self.direction.to_list(),
        }


# ── Document Model ─────────────────────────────────────────────

@dataclass
class CADDocument:
    """Top-level document representing the current CAD drawing.
    This is the primary data structure AI agents reason about."""
    name: str = ""
    path: str = ""
    saved: bool = False
    read_only: bool = False
    units: str = "Millimeters"
    measurement: str = "Metric"

    # Content counts
    entity_count: int = 0
    block_count: int = 0
    layer_count: int = 0
    text_style_count: int = 0
    dim_style_count: int = 0
    layout_count: int = 0
    group_count: int = 0

    # Type statistics
    entity_type_stats: Dict[str, int] = field(default_factory=dict)

    # Collections
    layers: List[CADLayer] = field(default_factory=list)
    blocks: List[CADBlock] = field(default_factory=list)
    layouts: List[str] = field(default_factory=list)
    groups: List[str] = field(default_factory=list)
    active_layer: str = "0"
    active_text_style: str = "Standard"
    active_dim_style: str = "Standard"
    limits: Tuple[float, float, float, float] = (0, 0, 420, 297)
    view: Optional[CADView] = None

    # Metadata
    title: str = ""
    subject: str = ""
    author: str = ""
    comments: str = ""
    keywords: str = ""

    # Sampled entities (not all, just those queried/scanned)
    entities: List[CADEntity] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "path": self.path,
            "saved": self.saved,
            "read_only": self.read_only,
            "units": self.units,
            "measurement": self.measurement,
            "entity_count": self.entity_count,
            "block_count": self.block_count,
            "layer_count": self.layer_count,
            "text_style_count": self.text_style_count,
            "dim_style_count": self.dim_style_count,
            "layout_count": self.layout_count,
            "group_count": self.group_count,
            "entity_type_stats": self.entity_type_stats,
            "active_layer": self.active_layer,
            "active_text_style": self.active_text_style,
            "active_dim_style": self.active_dim_style,
            "limits": list(self.limits),
            "title": self.title,
            "subject": self.subject,
            "author": self.author,
            "comments": self.comments,
            "keywords": self.keywords,
            "layers": [l.to_dict() for l in self.layers],
            "blocks": [b.to_dict() for b in self.blocks],
            "layouts": self.layouts,
            "groups": self.groups,
            "view": self.view.to_dict() if self.view else None,
            "entities": [e.to_dict() for e in self.entities],
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent, default=str)


# ── Selection Model ────────────────────────────────────────────

@dataclass
class CADSelection:
    name: str
    count: int = 0
    handles: List[str] = field(default_factory=list)
    entities: List[CADEntity] = field(default_factory=list)
    filter_type: Optional[str] = None
    bbox: Optional[Tuple[float, float, float, float]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "count": self.count,
            "handles": self.handles,
            "filter_type": self.filter_type,
            "bbox": list(self.bbox) if self.bbox else None,
        }


# ── Spatial Query Model ────────────────────────────────────────

@dataclass
class SpatialQuery:
    """Represents a spatial search in the drawing."""
    bbox: Optional[BoundingBox] = None
    radius: Optional[float] = None
    center: Optional[Point3D] = None
    entity_types: List[EntityType] = field(default_factory=list)
    layer_filter: List[str] = field(default_factory=list)
    color_filter: Optional[int] = None
    text_filter: Optional[str] = None
    limit: int = 100

    def to_dict(self) -> Dict[str, Any]:
        return {
            "bbox": [self.bbox.min.to_list(), self.bbox.max.to_list()] if self.bbox else None,
            "radius": self.radius,
            "center": self.center.to_list() if self.center else None,
            "entity_types": [t.value for t in self.entity_types],
            "layer_filter": self.layer_filter,
            "color_filter": self.color_filter,
            "text_filter": self.text_filter,
            "limit": self.limit,
        }
