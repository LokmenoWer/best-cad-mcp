"""Typed, JSON-serializable CAD intermediate representation structures."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class BBox2D:
    min_x: Optional[float] = None
    min_y: Optional[float] = None
    max_x: Optional[float] = None
    max_y: Optional[float] = None


@dataclass
class BBox3D:
    min_x: Optional[float] = None
    min_y: Optional[float] = None
    min_z: Optional[float] = None
    max_x: Optional[float] = None
    max_y: Optional[float] = None
    max_z: Optional[float] = None


@dataclass
class CadEntityIR:
    handle: str
    native_handle: str = ""
    object_name: str = ""
    entity_type: str = "Unknown"
    layer: str = "0"
    color: Any = 256
    linetype: str = "ByLayer"
    visible: bool = True
    bbox: Dict[str, Any] = field(default_factory=dict)
    geometry: Dict[str, Any] = field(default_factory=dict)
    properties: Dict[str, Any] = field(default_factory=dict)
    topology_refs: List[str] = field(default_factory=list)
    semantic_tags: List[str] = field(default_factory=list)
    source: str = "cad_entities"
    confidence: float = 1.0


@dataclass
class LayerIR:
    name: str
    color: Any = 7
    linetype: str = "Continuous"
    lineweight: Any = -1.0
    is_frozen: bool = False
    is_locked: bool = False
    is_on: bool = True
    is_plottable: bool = True
    description: str = ""
    handle: str = ""
    entity_count: int = 0


@dataclass
class BlockIR:
    name: str
    entity_count: int = 0
    is_layout: bool = False
    is_xref: bool = False
    origin: List[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    path: str = ""


@dataclass
class TopologyPrimitiveIR:
    entity_handle: str
    primitive_key: str
    primitive_type: str
    role: str = ""
    sequence_index: int = 0
    parent_key: str = ""
    x: Optional[float] = None
    y: Optional[float] = None
    z: Optional[float] = None
    x2: Optional[float] = None
    y2: Optional[float] = None
    z2: Optional[float] = None
    radius: Optional[float] = None
    length: Optional[float] = None
    area: Optional[float] = None
    is_closed: bool = False
    source: str = "derived"
    properties: Dict[str, Any] = field(default_factory=dict)


@dataclass
class TopologyRelationIR:
    entity_handle: str
    from_key: str
    to_key: str
    relation_type: str
    sequence_index: int = 0
    properties: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SemanticObjectIR:
    object_id: str
    object_type: str
    label: str
    source: str = "rule"
    confidence: float = 0.0
    bbox: Dict[str, Any] = field(default_factory=dict)
    entity_handles: List[str] = field(default_factory=list)
    properties: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SemanticRelationIR:
    relation_id: str
    from_object_id: str
    to_object_id: str
    relation_type: str
    confidence: float = 0.0
    evidence: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ConstraintIR:
    constraint_id: str
    constraint_type: str
    source: str
    target_handles: List[str] = field(default_factory=list)
    target_object_ids: List[str] = field(default_factory=list)
    value: Optional[float] = None
    actual: Optional[float] = None
    tolerance: Optional[float] = None
    unit: str = ""
    confidence: float = 0.0
    status: str = "unknown"
    evidence: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ViewSnapshotIR:
    snapshot_id: str
    image_path: str = ""
    overlay_image_path: str = ""
    view: Dict[str, Any] = field(default_factory=dict)
    image: Dict[str, Any] = field(default_factory=dict)
    world_to_pixel: List[List[float]] = field(default_factory=list)
    pixel_to_world: List[List[float]] = field(default_factory=list)
    visible_handles: List[str] = field(default_factory=list)
    entity_screen_bboxes: Dict[str, List[float]] = field(default_factory=dict)
    context_json_path: str = ""


@dataclass
class ValidationIssueIR:
    issue_id: str
    severity: str
    issue_type: str
    message: str
    handles: List[str] = field(default_factory=list)
    object_ids: List[str] = field(default_factory=list)
    expected: Any = None
    actual: Any = None
    bbox: Dict[str, Any] = field(default_factory=dict)
    evidence: Dict[str, Any] = field(default_factory=dict)
    repair_hint: str = ""
    suggested_tools: List[str] = field(default_factory=list)


@dataclass
class ValidationReportIR:
    passed: bool = True
    score: float = 100.0
    issue_count: int = 0
    issues: List[Dict[str, Any]] = field(default_factory=list)
    generated_at: str = ""
    recommended_next_tools: List[str] = field(default_factory=list)


@dataclass
class DrawingIR:
    drawing_id: str
    drawing_name: str = "active"
    drawing_path: str = ""
    units: str = "unknown"
    extents: Dict[str, Any] = field(default_factory=dict)
    entity_count: int = 0
    layers: List[Dict[str, Any]] = field(default_factory=list)
    blocks: List[Dict[str, Any]] = field(default_factory=list)
    entities: List[Dict[str, Any]] = field(default_factory=list)
    topology: Dict[str, Any] = field(default_factory=dict)
    semantic_objects: List[Dict[str, Any]] = field(default_factory=list)
    semantic_relations: List[Dict[str, Any]] = field(default_factory=list)
    constraints: List[Dict[str, Any]] = field(default_factory=list)
    validation: Dict[str, Any] = field(default_factory=dict)
    views: List[Dict[str, Any]] = field(default_factory=list)
    generated_at: str = ""


def to_dict(value: Any) -> Dict[str, Any]:
    return asdict(value)


__all__ = [
    "BBox2D",
    "BBox3D",
    "CadEntityIR",
    "LayerIR",
    "BlockIR",
    "TopologyPrimitiveIR",
    "TopologyRelationIR",
    "SemanticObjectIR",
    "SemanticRelationIR",
    "ConstraintIR",
    "ViewSnapshotIR",
    "DrawingIR",
    "ValidationIssueIR",
    "ValidationReportIR",
    "to_dict",
]
