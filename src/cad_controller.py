"""
CAD Controller — Low-level COM bridge to AutoCAD Application.

Provides a singleton controller that wraps all AutoCAD COM automation calls.
Handles connection lifecycle, coordinate conversion, and error management.
Designed for AutoCAD 2020+ (also works with 2018+).

Architecture:
    This is the ONLY module that directly touches win32com.
    All MCP tool modules go through this controller — never COM directly.
    That gives us: testability, error isolation, and a clean migration path.
"""

import win32com.client
import pythoncom
import math
import logging
from typing import Optional, List, Tuple, Dict, Any, Union
from contextlib import contextmanager

logger = logging.getLogger(__name__)

# ── Coordinate helpers ──────────────────────────────────────────

def to_variant_point(x: float, y: float, z: float = 0.0):
    """Convert x,y,z to a COM-safe VARIANT double array."""
    return win32com.client.VARIANT(
        pythoncom.VT_ARRAY | pythoncom.VT_R8,
        [float(x), float(y), float(z)]
    )

def to_variant_array(values: List[float]):
    """Convert a flat list of floats to a COM-safe VARIANT double array."""
    return win32com.client.VARIANT(
        pythoncom.VT_ARRAY | pythoncom.VT_R8,
        [float(v) for v in values]
    )

def from_variant_point(variant) -> Tuple[float, float, float]:
    """Extract (x,y,z) from a COM variant point."""
    return (float(variant[0]), float(variant[1]), float(variant[2]))


# ── Decorators ──────────────────────────────────────────────────

def require_document(func):
    """Decorator ensuring an AutoCAD document is open before the tool runs."""
    def wrapper(self, *args, **kwargs):
        self._ensure_connected()
        if self.acad is None:
            return {"success": False,
                    "message": "错误：无法连接到 AutoCAD。请确保 AutoCAD 已启动。"}
        if self.acad.Documents.Count == 0:
            return {"success": False,
                    "message": "错误：没有打开的文档。请先创建或打开一个图纸。"}
        self.doc = self.acad.ActiveDocument
        return func(self, *args, **kwargs)
    return wrapper


# ── Controller ──────────────────────────────────────────────────

class CADController:
    """Singleton controller for all AutoCAD COM interactions."""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self.acad = None
        self.doc = None
        self._initialized = True
        logger.info("CAD控制器已初始化")

    # ── Connection ───────────────────────────────────────────

    def connect(self, visible: bool = True) -> bool:
        """Connect to AutoCAD. Returns True on success."""
        try:
            self.acad = win32com.client.Dispatch("AutoCAD.Application")
            if visible:
                self.acad.Visible = True
            if self.acad.Documents.Count > 0:
                self.doc = self.acad.ActiveDocument
            logger.info("已连接到 AutoCAD")
            return True
        except Exception as e:
            logger.error(f"连接AutoCAD失败: {e}")
            return False

    def _ensure_connected(self):
        """Lazy-connect if not already connected."""
        if self.acad is None:
            self.connect()

    @property
    def is_connected(self) -> bool:
        try:
            if self.acad is None:
                return False
            _ = self.acad.Name  # probe
            return True
        except Exception:
            self.acad = None
            self.doc = None
            return False

    @property
    def has_document(self) -> bool:
        try:
            return self.acad is not None and self.acad.Documents.Count > 0
        except Exception:
            return False

    # ── File Operations ──────────────────────────────────────

    @require_document
    def create_drawing(self, template: Optional[str] = None) -> Dict[str, Any]:
        if template:
            doc = self.acad.Documents.Add(template)
        else:
            doc = self.acad.Documents.Add()
        self.doc = doc
        return {"success": True, "message": "已创建新图纸", "name": doc.Name}

    @require_document
    def open_drawing(self, filepath: str, password: Optional[str] = None) -> Dict[str, Any]:
        try:
            if password:
                doc = self.acad.Documents.Open(filepath, password)
            else:
                doc = self.acad.Documents.Open(filepath)
            self.doc = doc
            return {"success": True, "message": f"已打开: {filepath}", "name": doc.Name}
        except Exception as e:
            return {"success": False, "message": f"打开失败: {e}"}

    @require_document
    def save_drawing(self, filepath: Optional[str] = None) -> Dict[str, Any]:
        try:
            if filepath:
                self.doc.SaveAs(filepath)
            else:
                self.doc.Save()
            return {"success": True, "message": f"已保存: {self.doc.Name}"}
        except Exception as e:
            return {"success": False, "message": f"保存失败: {e}"}

    @require_document
    def export_drawing(self, filepath: str, format_type: str = "PDF") -> Dict[str, Any]:
        format_type = format_type.upper()
        valid = {"DWG", "DXF", "PDF", "DWF", "DGN", "FBX", "IGS", "SAT", "STL", "WMF", "BMP"}
        if format_type not in valid:
            return {"success": False, "message": f"不支持格式: {format_type}。支持: {valid}"}
        try:
            if format_type == "DWG":
                self.doc.SaveAs(filepath)
            else:
                self.doc.Export(filepath, format_type)
            return {"success": True, "message": f"已导出为 {format_type}: {filepath}"}
        except Exception as e:
            return {"success": False, "message": f"导出失败: {e}"}

    @require_document
    def close_drawing(self, save: bool = False) -> Dict[str, Any]:
        try:
            self.doc.Close(save)
            if self.acad.Documents.Count > 0:
                self.doc = self.acad.ActiveDocument
            else:
                self.doc = None
            return {"success": True, "message": "已关闭图纸"}
        except Exception as e:
            return {"success": False, "message": f"关闭失败: {e}"}

    # ── Document Information ─────────────────────────────────

    @require_document
    def get_document_info(self) -> Dict[str, Any]:
        try:
            summary = self.doc.SummaryInfo
            return {
                "name": self.doc.Name,
                "path": self.doc.Path,
                "full_name": self.doc.FullName,
                "saved": self.doc.Saved,
                "read_only": self.doc.ReadOnly,
                "entity_count": self.doc.ModelSpace.Count,
                "blocks_count": self.doc.Blocks.Count,
                "layers_count": self.doc.Layers.Count,
                "text_styles_count": self.doc.TextStyles.Count,
                "dim_styles_count": self.doc.DimStyles.Count,
                "title": summary.Title if summary else "",
                "subject": summary.Subject if summary else "",
                "author": summary.Author if summary else "",
                "comments": summary.Comments if summary else "",
                "units": self.doc.GetVariable("INSUNITS") if hasattr(self.doc, 'GetVariable') else -1,
                "limits": self._get_limits(),
                "active_layer": self.doc.ActiveLayer.Name,
                "active_text_style": self.doc.ActiveTextStyle.Name,
                "active_dim_style": self.doc.ActiveDimStyle.Name,
            }
        except Exception as e:
            return {"error": str(e)}

    def _get_limits(self) -> Dict[str, List[float]]:
        try:
            return {
                "min": list(self.doc.Limits[:2]),
                "max": list(self.doc.Limits[2:]),
            }
        except Exception:
            return {"min": [0,0], "max": [0,0]}

    @require_document
    def get_variable(self, name: str) -> Any:
        try:
            return self.doc.GetVariable(name)
        except Exception as e:
            return f"获取变量失败: {e}"

    @require_document
    def set_variable(self, name: str, value: Any) -> Dict[str, Any]:
        try:
            self.doc.SetVariable(name, value)
            return {"success": True, "message": f"已设置 {name} = {value}"}
        except Exception as e:
            return {"success": False, "message": f"设置变量失败: {e}"}

    # ── Drawing Primitives ──────────────────────────────────

    @require_document
    def add_line(self, x1: float, y1: float, x2: float, y2: float,
                 z1: float = 0.0, z2: float = 0.0) -> Any:
        start = to_variant_point(x1, y1, z1)
        end = to_variant_point(x2, y2, z2)
        return self.doc.ModelSpace.AddLine(start, end)

    @require_document
    def add_circle(self, cx: float, cy: float, radius: float) -> Any:
        center = to_variant_point(cx, cy, 0)
        return self.doc.ModelSpace.AddCircle(center, radius)

    @require_document
    def add_arc(self, cx: float, cy: float, radius: float,
                start_angle_deg: float, end_angle_deg: float) -> Any:
        center = to_variant_point(cx, cy, 0)
        start_rad = start_angle_deg * math.pi / 180.0
        end_rad = end_angle_deg * math.pi / 180.0
        return self.doc.ModelSpace.AddArc(center, radius, start_rad, end_rad)

    @require_document
    def add_ellipse(self, cx: float, cy: float, major_axis: Tuple[float, float],
                    radius_ratio: float) -> Any:
        center = to_variant_point(cx, cy, 0)
        major = to_variant_point(major_axis[0], major_axis[1], 0)
        return self.doc.ModelSpace.AddEllipse(center, major, radius_ratio)

    @require_document
    def add_polyline(self, points: List[float], closed: bool = False) -> Any:
        pt_array = to_variant_array(points)
        pline = self.doc.ModelSpace.AddLightWeightPolyline(pt_array)
        pline.Closed = closed
        return pline

    @require_document
    def add_polyline_3d(self, points: List[float], closed: bool = False) -> Any:
        """points: [x1,y1,z1, x2,y2,z2, ...] (flat list of triplets)"""
        pt_array = to_variant_array(points)
        pline = self.doc.ModelSpace.Add3DPoly(pt_array)
        pline.Closed = closed
        return pline

    # ── Polyline Vertex Operations ─────────────────────────

    def _get_polyline(self, handle: str) -> Optional[Any]:
        """Get entity and verify it's a polyline."""
        ent = self._get_entity(handle)
        if ent is None:
            return None
        if ent.ObjectName not in ("AcDbPolyline", "AcDb2dPolyline", "AcDb3dPolyline"):
            return None
        return ent

    @require_document
    def polyline_set_bulge(self, handle: str, index: int, bulge: float) -> Dict[str, Any]:
        """Set the bulge factor at a vertex. bulge=0 means straight, positive=CCW arc, negative=CW arc."""
        pline = self._get_polyline(handle)
        if pline is None:
            return {"success": False, "message": f"未找到多段线: {handle}"}
        try:
            pline.SetBulge(index, float(bulge))
            return {"success": True, "message": f"已设置顶点 {index} 凸度={bulge}",
                    "handle": handle, "index": index, "bulge": bulge}
        except Exception as e:
            return {"success": False, "message": f"设置凸度失败: {e}"}

    @require_document
    def polyline_get_bulge(self, handle: str, index: int) -> Dict[str, Any]:
        """Get the bulge factor at a vertex."""
        pline = self._get_polyline(handle)
        if pline is None:
            return {"success": False, "message": f"未找到多段线: {handle}"}
        try:
            bulge = pline.GetBulge(index)
            return {"success": True, "handle": handle, "index": index, "bulge": bulge}
        except Exception as e:
            return {"success": False, "message": f"获取凸度失败: {e}"}

    @require_document
    def polyline_set_width(self, handle: str, seg_index: int,
                            start_width: float, end_width: float) -> Dict[str, Any]:
        """Set the start and end width of a polyline segment."""
        pline = self._get_polyline(handle)
        if pline is None:
            return {"success": False, "message": f"未找到多段线: {handle}"}
        try:
            pline.SetWidth(seg_index, float(start_width), float(end_width))
            return {"success": True,
                    "message": f"已设置段 {seg_index} 宽度: {start_width}→{end_width}",
                    "handle": handle}
        except Exception as e:
            return {"success": False, "message": f"设置宽度失败: {e}"}

    @require_document
    def polyline_get_width(self, handle: str, seg_index: int) -> Dict[str, Any]:
        """Get the start and end width of a polyline segment (returns start,end)."""
        pline = self._get_polyline(handle)
        if pline is None:
            return {"success": False, "message": f"未找到多段线: {handle}"}
        try:
            sw = pline.GetWidth(seg_index)  # returns tuple (startWidth, endWidth)
            return {"success": True, "handle": handle, "start_width": sw[0], "end_width": sw[1]}
        except Exception as e:
            return {"success": False, "message": f"获取宽度失败: {e}"}

    @require_document
    def polyline_add_vertex(self, handle: str, index: int,
                             x: float, y: float) -> Dict[str, Any]:
        """Add a vertex to an LWPOLYLINE at the given index."""
        pline = self._get_polyline(handle)
        if pline is None:
            return {"success": False, "message": f"未找到多段线: {handle}"}
        try:
            pt = to_variant_point(x, y, 0)
            pline.AddVertex(index, pt)
            return {"success": True,
                    "message": f"已在位置 {index} 添加顶点 ({x},{y})",
                    "handle": handle}
        except Exception as e:
            return {"success": False, "message": f"添加顶点失败: {e}"}

    @require_document
    def polyline_constant_width(self, handle: str,
                                  width: Optional[float] = None) -> Dict[str, Any]:
        """Get or set the polyline's constant width. Pass None to get, a float to set."""
        pline = self._get_polyline(handle)
        if pline is None:
            return {"success": False, "message": f"未找到多段线: {handle}"}
        try:
            if width is None:
                return {"success": True, "handle": handle,
                        "constant_width": pline.ConstantWidth}
            pline.ConstantWidth = float(width)
            return {"success": True,
                    "message": f"已设置多段线统一宽度={width}",
                    "handle": handle, "constant_width": width}
        except Exception as e:
            return {"success": False, "message": f"操作失败: {e}"}

    @require_document
    def polyline_num_vertices(self, handle: str) -> Dict[str, Any]:
        """Get the number of vertices in a polyline."""
        pline = self._get_polyline(handle)
        if pline is None:
            return {"success": False, "message": f"未找到多段线: {handle}"}
        try:
            nv = pline.NumberOfVertices
            return {"success": True, "handle": handle, "vertices": nv}
        except Exception as e:
            return {"success": False, "message": f"获取顶点数失败: {e}"}

    @require_document
    def polyline_get_point_at_param(self, handle: str,
                                       param: float) -> Dict[str, Any]:
        """Get 3D point on polyline at parameter."""
        pline = self._get_polyline(handle)
        if pline is None:
            return {"success": False, "message": f"未找到多段线: {handle}"}
        try:
            pt = pline.GetPointAtParam(float(param))
            return {"success": True, "point": [pt[0], pt[1], pt[2]]}
        except Exception as e:
            return {"success": False, "message": f"获取点失败: {e}"}

    @require_document
    def polyline_get_segment_type(self, handle: str,
                                     index: int) -> Dict[str, Any]:
        """Get segment type. 0=line, 1=arc."""
        pline = self._get_polyline(handle)
        if pline is None:
            return {"success": False, "message": f"未找到多段线: {handle}"}
        try:
            seg_type = pline.GetSegmentType(int(index))
            types = {0: "line", 1: "arc"}
            return {"success": True, "handle": handle, "index": index,
                    "type": types.get(seg_type, f"unknown({seg_type})")}
        except Exception as e:
            return {"success": False, "message": f"获取段类型失败: {e}"}

    @require_document
    def add_rectangle(self, x1: float, y1: float, x2: float, y2: float) -> Any:
        pts = [x1, y1, x2, y1, x2, y2, x1, y2]
        return self.add_polyline(pts, closed=True)

    @require_document
    def add_polygon(self, cx: float, cy: float, radius: float, sides: int) -> Any:
        pts = []
        for i in range(sides):
            angle = 2 * math.pi * i / sides - math.pi / 2
            pts.append(cx + radius * math.cos(angle))
            pts.append(cy + radius * math.sin(angle))
        return self.add_polyline(pts, closed=True)

    @require_document
    def add_spline(self, points: List[Tuple[float, float, float]],
                   start_tangent: Optional[Tuple[float,float,float]] = None,
                   end_tangent: Optional[Tuple[float,float,float]] = None) -> Any:
        # Flatten points into [x1,y1,z1, x2,y2,z2, ...]
        flat = []
        for p in points:
            flat.extend([float(p[0]), float(p[1]), float(p[2]) if len(p)>2 else 0.0])
        pts_array = to_variant_array(flat)
        st = to_variant_point(*start_tangent) if start_tangent else None
        et = to_variant_point(*end_tangent) if end_tangent else None
        if st and et:
            return self.doc.ModelSpace.AddSpline(pts_array, st, et)
        else:
            return self.doc.ModelSpace.AddSpline(pts_array,
                to_variant_point(0,0,0), to_variant_point(0,0,0))

    @require_document
    def add_point(self, x: float, y: float, z: float = 0.0) -> Any:
        pt = to_variant_point(x, y, z)
        return self.doc.ModelSpace.AddPoint(pt)

    @require_document
    def add_ray(self, origin: Tuple[float,float,float],
                direction: Tuple[float,float,float]) -> Any:
        o = to_variant_point(*origin)
        d = to_variant_point(*direction)
        return self.doc.ModelSpace.AddRay(o, d)

    @require_document
    def add_xline(self, pt1: Tuple[float,float,float],
                  pt2: Tuple[float,float,float]) -> Any:
        p1 = to_variant_point(*pt1)
        p2 = to_variant_point(*pt2)
        return self.doc.ModelSpace.AddXline(p1, p2)

    @require_document
    def add_mline(self, points: List[Tuple[float, float]]):
        """Add a multi-line. points: list of (x,y) tuples."""
        flat = []
        for p in points:
            flat.extend([float(p[0]), float(p[1]), 0.0])
        pts_array = to_variant_array(flat)
        return self.doc.ModelSpace.AddMLine(pts_array)

    @require_document
    def add_solid(self, pts: List[Tuple[float,float,float]]) -> Any:
        """Add a 2D solid (3 or 4 points)."""
        return self.doc.ModelSpace.AddSolid(
            to_variant_point(*pts[0]),
            to_variant_point(*pts[1]),
            to_variant_point(*pts[2]),
            to_variant_point(*pts[3]) if len(pts) > 3 else to_variant_point(*pts[2])
        )

    @require_document
    def add_donut(self, cx: float, cy: float, inner_radius: float,
                  outer_radius: float) -> List[Any]:
        """Add a donut (two arcs or a polyline). Returns list of entities."""
        center = to_variant_point(cx, cy, 0)
        inner = self.doc.ModelSpace.AddCircle(center, inner_radius)
        outer = self.doc.ModelSpace.AddCircle(center, outer_radius)
        return [inner, outer]

    # ── Text & Annotation ───────────────────────────────────

    @require_document
    def add_text(self, text: str, x: float, y: float, height: float = 2.5,
                 rotation_deg: float = 0.0, z: float = 0.0) -> Any:
        pt = to_variant_point(x, y, z)
        txt = self.doc.ModelSpace.AddText(text, pt, height)
        if rotation_deg != 0:
            txt.Rotation = rotation_deg * math.pi / 180.0
        return txt

    @require_document
    def add_mtext(self, text: str, x: float, y: float, width: float = 0.0,
                  height: float = 2.5, rotation_deg: float = 0.0) -> Any:
        pt = to_variant_point(x, y, 0)
        mtext = self.doc.ModelSpace.AddMText(pt, width, text)
        if height != 2.5:
            mtext.Height = height
        if rotation_deg != 0:
            mtext.Rotation = rotation_deg * math.pi / 180.0
        return mtext

    @require_document
    def add_leader(self, points: List[Tuple[float,float,float]],
                   annotation: Optional[str] = None,
                   arrowhead_type: int = 0) -> Any:
        """Add a leader line. points: list of (x,y,z) tuples."""
        flat = []
        for p in points:
            flat.extend([float(p[0]), float(p[1]), float(p[2]) if len(p)>2 else 0.0])
        pts_array = to_variant_array(flat)
        leader = self.doc.ModelSpace.AddLeader(pts_array, None, arrowhead_type)
        if annotation:
            leader.Annotation = annotation
        return leader

    @require_document
    def add_mleader(self, text: str, points: List[Tuple[float,float,float]]) -> Any:
        """Add a multileader."""
        flat = []
        for p in points:
            flat.extend([float(p[0]), float(p[1]), float(p[2]) if len(p)>2 else 0.0])
        pts_array = to_variant_array(flat)
        mleader = self.doc.ModelSpace.AddMLeader(pts_array, 0)
        mleader.ContentType = 2  # mtext content
        mleader.TextString = text
        return mleader

    @require_document
    def add_table(self, insert_point: Tuple[float,float,float],
                  rows: int, cols: int, row_height: float = 1.0,
                  col_width: float = 5.0) -> Any:
        pt = to_variant_point(*insert_point)
        table = self.doc.ModelSpace.AddTable(pt, rows, cols, row_height, col_width)
        return table

    # ── Dimensioning ────────────────────────────────────────

    @require_document
    def add_dimension_linear(self, pt1: Tuple[float,float,float],
                             pt2: Tuple[float,float,float],
                             text_pt: Tuple[float,float,float]) -> Any:
        return self.doc.ModelSpace.AddDimAligned(
            to_variant_point(*pt1),
            to_variant_point(*pt2),
            to_variant_point(*text_pt)
        )

    @require_document
    def add_dimension_rotated(self, pt1: Tuple[float,float,float],
                              pt2: Tuple[float,float,float],
                              text_pt: Tuple[float,float,float],
                              rotation_deg: float) -> Any:
        return self.doc.ModelSpace.AddDimRotated(
            to_variant_point(*pt1),
            to_variant_point(*pt2),
            to_variant_point(*text_pt),
            rotation_deg * math.pi / 180.0
        )

    @require_document
    def add_dimension_angular(self, center: Tuple[float,float,float],
                              pt1: Tuple[float,float,float],
                              pt2: Tuple[float,float,float],
                              text_pt: Tuple[float,float,float]) -> Any:
        return self.doc.ModelSpace.AddDimAngular(
            to_variant_point(*center),
            to_variant_point(*pt1),
            to_variant_point(*pt2),
            to_variant_point(*text_pt)
        )

    @require_document
    def add_dimension_radial(self, center: Tuple[float,float,float],
                             chord_pt: Tuple[float,float,float],
                             leader_len: float = 0.0) -> Any:
        return self.doc.ModelSpace.AddDimRadial(
            to_variant_point(*center),
            to_variant_point(*chord_pt),
            leader_len
        )

    @require_document
    def add_dimension_diametric(self, chord_pt1: Tuple[float,float,float],
                                chord_pt2: Tuple[float,float,float],
                                leader_len: float = 0.0) -> Any:
        return self.doc.ModelSpace.AddDimDiametric(
            to_variant_point(*chord_pt1),
            to_variant_point(*chord_pt2),
            leader_len
        )

    @require_document
    def add_dimension_ordinate(self, pt: Tuple[float,float,float],
                               leader_end: Tuple[float,float,float],
                               use_xaxis: bool) -> Any:
        return self.doc.ModelSpace.AddDimOrdinate(
            to_variant_point(*pt),
            to_variant_point(*leader_end),
            use_xaxis
        )

    @require_document
    def add_dimension_arc(self, center: Tuple[float,float,float],
                            start_pt: Tuple[float,float,float],
                            end_pt: Tuple[float,float,float],
                            text_pt: Tuple[float,float,float]) -> Any:
        """Add arc length dimension."""
        return self.doc.ModelSpace.AddDimArc(
            to_variant_point(*center),
            to_variant_point(*start_pt),
            to_variant_point(*end_pt),
            to_variant_point(*text_pt))

    @require_document
    def add_dimension_3point_angular(self, vertex: Tuple[float,float,float],
                                        x_ref1: Tuple[float,float,float],
                                        x_ref2: Tuple[float,float,float],
                                        text_pt: Tuple[float,float,float]) -> Any:
        """Add 3-point angular dimension."""
        return self.doc.ModelSpace.AddDim3PointAngular(
            to_variant_point(*vertex),
            to_variant_point(*x_ref1),
            to_variant_point(*x_ref2),
            to_variant_point(*text_pt))

    # ── Hatch ───────────────────────────────────────────────

    @require_document
    def add_hatch(self, pattern_type: int = 0, pattern_name: str = "ANSI31",
                  associativity: bool = True) -> Any:
        hatch = self.doc.ModelSpace.AddHatch(pattern_type, pattern_name, associativity)
        return hatch

    @require_document
    def hatch_boundary(self, hatch_obj, outer_loop: List[Any]) -> Any:
        """Append outer loop and evaluate hatch."""
        # Convert COM entity references
        outer = win32com.client.VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_DISPATCH,
                                        [ent for ent in outer_loop])
        hatch_obj.AppendOuterLoop(outer)
        hatch_obj.Evaluate()
        return hatch_obj

    @require_document
    def hatch_append_inner_loop(self, handle: str,
                                  inner_handles: List[str]) -> Dict[str, Any]:
        """Append inner (island) loop to an existing hatch."""
        hatch = self._get_entity(handle)
        if hatch is None or hatch.ObjectName != "AcDbHatch":
            return {"success": False, "message": f"未找到填充对象: {handle}"}
        try:
            entities = [self._get_entity(h) for h in inner_handles]
            entities = [e for e in entities if e is not None]
            if not entities:
                return {"success": False, "message": "未找到有效的边界实体"}
            inner = win32com.client.VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_DISPATCH, entities)
            hatch.AppendInnerLoop(inner)
            hatch.Evaluate()
            return {"success": True,
                    "message": f"已添加 {len(entities)} 个内部环（孤岛）",
                    "handle": handle}
        except Exception as e:
            return {"success": False, "message": f"添加内部环失败: {e}"}

    @require_document
    def hatch_set_properties(self, handle: str,
                               pattern_scale: Optional[float] = None,
                               pattern_angle: Optional[float] = None,
                               pattern_double: Optional[bool] = None,
                               hatch_style: Optional[int] = None) -> Dict[str, Any]:
        """Set common hatch properties.
        hatch_style: 0=Normal, 1=Outer, 2=Ignore."""
        hatch = self._get_entity(handle)
        if hatch is None or hatch.ObjectName != "AcDbHatch":
            return {"success": False, "message": f"未找到填充对象: {handle}"}
        changed = {}
        try:
            if pattern_scale is not None:
                hatch.PatternScale = float(pattern_scale)
                changed["pattern_scale"] = float(pattern_scale)
            if pattern_angle is not None:
                rad = float(pattern_angle) * math.pi / 180.0
                hatch.PatternAngle = rad
                changed["pattern_angle"] = pattern_angle
            if pattern_double is not None:
                hatch.PatternDouble = bool(pattern_double)
                changed["pattern_double"] = bool(pattern_double)
            if hatch_style is not None:
                hatch.HatchStyle = int(hatch_style)
                changed["hatch_style"] = int(hatch_style)
            hatch.Evaluate()
            return {"success": True, "message": f"已更新填充属性", "changed": changed}
        except Exception as e:
            return {"success": False, "message": f"设置填充属性失败: {e}"}

    @require_document
    def hatch_get_properties(self, handle: str) -> Dict[str, Any]:
        """Get all readable hatch properties."""
        hatch = self._get_entity(handle)
        if hatch is None or hatch.ObjectName != "AcDbHatch":
            return {"success": False, "message": f"未找到填充对象: {handle}"}
        try:
            return {"success": True, "handle": handle,
                    "pattern_name": hatch.PatternName,
                    "pattern_scale": hatch.PatternScale,
                    "pattern_angle": hatch.PatternAngle * 180.0 / math.pi,
                    "pattern_double": hatch.PatternDouble,
                    "hatch_style": hatch.HatchStyle,
                    "number_of_loops": hatch.NumberOfLoops,
                    "area": hatch.Area,
                    "associative": hatch.AssociativeHatch,
                    "island_detection_style": hatch.HatchStyle}
        except Exception as e:
            return {"success": False, "message": f"获取填充属性失败: {e}"}

    @require_document
    def hatch_set_gradient(self, handle: str, gradient_type: int = 0,
                             color1: str = "cyan", color2: str = "blue") -> Dict[str, Any]:
        """Set a gradient fill on a hatch. gradient_type: 0=Linear, 1=Cylinder, etc."""
        hatch = self._get_entity(handle)
        if hatch is None or hatch.ObjectName != "AcDbHatch":
            return {"success": False, "message": f"未找到填充对象: {handle}"}
        try:
            # Set to gradient pattern
            hatch.SetPattern(1, "SOLID")
            hatch.SetGradient(gradient_type, color1, color2)
            hatch.Evaluate()
            return {"success": True,
                    "message": f"已设置渐变填充 ({gradient_type})",
                    "handle": handle}
        except Exception as e:
            return {"success": False, "message": f"设置渐变填充失败: {e}"}

    # ── 3D Solid Primitives ────────────────────────────────

    @require_document
    def add_box(self, cx: float, cy: float, cz: float,
                length: float, width: float, height: float) -> Any:
        """Add a 3D box solid. length=X, width=Y, height=Z."""
        center = to_variant_point(cx, cy, cz)
        return self.doc.ModelSpace.AddBox(center, length, width, height)

    @require_document
    def add_cone(self, cx: float, cy: float, cz: float,
                 base_radius: float, height: float) -> Any:
        """Add a 3D cone solid."""
        center = to_variant_point(cx, cy, cz)
        return self.doc.ModelSpace.AddCone(center, base_radius, height)

    @require_document
    def add_cylinder(self, cx: float, cy: float, cz: float,
                     radius: float, height: float) -> Any:
        """Add a 3D cylinder solid."""
        center = to_variant_point(cx, cy, cz)
        return self.doc.ModelSpace.AddCylinder(center, radius, height)

    @require_document
    def add_elliptical_cone(self, cx: float, cy: float, cz: float,
                             major_radius: float, minor_radius: float,
                             height: float) -> Any:
        """Add a 3D elliptical cone solid."""
        center = to_variant_point(cx, cy, cz)
        return self.doc.ModelSpace.AddEllipticalCone(center, major_radius, minor_radius, height)

    @require_document
    def add_elliptical_cylinder(self, cx: float, cy: float, cz: float,
                                 major_radius: float, minor_radius: float,
                                 height: float) -> Any:
        """Add a 3D elliptical cylinder solid."""
        center = to_variant_point(cx, cy, cz)
        return self.doc.ModelSpace.AddEllipticalCylinder(center, major_radius, minor_radius, height)

    @require_document
    def add_sphere(self, cx: float, cy: float, cz: float, radius: float) -> Any:
        """Add a 3D sphere solid."""
        center = to_variant_point(cx, cy, cz)
        return self.doc.ModelSpace.AddSphere(center, radius)

    @require_document
    def add_torus(self, cx: float, cy: float, cz: float,
                  torus_radius: float, tube_radius: float) -> Any:
        """Add a 3D torus solid."""
        center = to_variant_point(cx, cy, cz)
        return self.doc.ModelSpace.AddTorus(center, torus_radius, tube_radius)

    @require_document
    def add_wedge(self, cx: float, cy: float, cz: float,
                  length: float, width: float, height: float) -> Any:
        """Add a 3D wedge solid."""
        center = to_variant_point(cx, cy, cz)
        return self.doc.ModelSpace.AddWedge(center, length, width, height)

    # ── Regions ────────────────────────────────────────────

    @require_document
    def add_region(self, entity_handles: List[str]) -> Any:
        """Create regions from closed curves. Original entities are consumed."""
        entities = []
        for h in entity_handles:
            ent = self._get_entity(h)
            if ent:
                entities.append(ent)
        if not entities:
            raise ValueError("No valid entities found for region creation")
        objs = win32com.client.VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_DISPATCH, entities)
        return self.doc.ModelSpace.AddRegion(objs)

    @require_document
    def add_extruded_solid(self, region_handle: str, height: float,
                           taper_angle_deg: float = 0.0) -> Any:
        """Extrude a region into a 3D solid."""
        region = self._get_entity(region_handle)
        if region is None:
            raise ValueError(f"Region not found: {region_handle}")
        taper_rad = taper_angle_deg * math.pi / 180.0
        return self.doc.ModelSpace.AddExtrudedSolid(region, height, taper_rad)

    @require_document
    def add_extruded_solid_along_path(self, region_handle: str,
                                       path_handle: str) -> Any:
        """Extrude a region along a path curve."""
        region = self._get_entity(region_handle)
        path = self._get_entity(path_handle)
        if region is None or path is None:
            raise ValueError(f"Region or path not found")
        return self.doc.ModelSpace.AddExtrudedSolidAlongPath(region, path)

    @require_document
    def add_revolved_solid(self, region_handle: str,
                           axis_point: Tuple[float, float, float],
                           axis_dir: Tuple[float, float, float],
                           angle_deg: float) -> Any:
        """Revolve a region around an axis to create a 3D solid."""
        region = self._get_entity(region_handle)
        if region is None:
            raise ValueError(f"Region not found: {region_handle}")
        pt = to_variant_point(*axis_point)
        direction = to_variant_point(*axis_dir)
        angle_rad = angle_deg * math.pi / 180.0
        return self.doc.ModelSpace.AddRevolvedSolid(region, pt, direction, angle_rad)

    # ── 3D Solid Editing ──────────────────────────────────

    def _get_3dsolid(self, handle: str) -> Optional[Any]:
        """Get an entity and verify it's a 3D solid or region."""
        ent = self._get_entity(handle)
        if ent is None:
            return None
        obj_name = ent.ObjectName
        if obj_name not in ("AcDb3dSolid", "AcDbRegion"):
            logger.warning(f"Entity {handle} is {obj_name}, not a 3D solid/region")
        return ent

    @require_document
    def solid_boolean(self, target_handle: str, tool_handle: str,
                       operation: int) -> Dict[str, Any]:
        """Boolean operation: 0=Union, 1=Intersect, 2=Subtract.
        Modifies target in-place."""
        target = self._get_3dsolid(target_handle)
        tool = self._get_3dsolid(tool_handle)
        if target is None or tool is None:
            return {"success": False, "message": "Solid not found"}
        try:
            target.Boolean(operation, tool)
            op_names = {0: "并集", 1: "交集", 2: "差集"}
            return {"success": True,
                    "message": f"已执行{op_names.get(operation, '布尔')}运算",
                    "target": target_handle, "tool": tool_handle}
        except Exception as e:
            return {"success": False, "message": f"布尔运算失败: {e}"}

    @require_document
    def solid_check_interference(self, handle1: str, handle2: str,
                                  create_solid: bool = True) -> Dict[str, Any]:
        """Check interference between two solids. Optionally create interference solid."""
        s1 = self._get_3dsolid(handle1)
        s2 = self._get_3dsolid(handle2)
        if s1 is None or s2 is None:
            return {"success": False, "message": "Solid not found"}
        try:
            result = s1.CheckInterference(s2, create_solid)
            interferes = bool(result)
            response = {"success": True, "interferes": interferes}
            if interferes and create_solid and hasattr(result, 'Handle'):
                response["interference_solid_handle"] = result.Handle
            return response
        except Exception as e:
            return {"success": False, "message": f"干涉检查失败: {e}"}

    @require_document
    def solid_slice(self, handle: str, p1: List[float], p2: List[float],
                     p3: List[float], negative_side_only: bool = False) -> Dict[str, Any]:
        """Slice a 3D solid with a plane defined by 3 points."""
        solid = self._get_3dsolid(handle)
        if solid is None:
            return {"success": False, "message": "Solid not found"}
        try:
            result = solid.SliceSolid(
                to_variant_point(*p1), to_variant_point(*p2),
                to_variant_point(*p3), negative_side_only)
            return {"success": True,
                    "message": f"已剖切实体 {handle}",
                    "result_handle": result.Handle if hasattr(result, 'Handle') else None}
        except Exception as e:
            return {"success": False, "message": f"剖切失败: {e}"}

    @require_document
    def solid_section(self, handle: str, p1: List[float], p2: List[float],
                       p3: List[float]) -> Dict[str, Any]:
        """Create a cross-section region from a 3D solid."""
        solid = self._get_3dsolid(handle)
        if solid is None:
            return {"success": False, "message": "Solid not found"}
        try:
            region = solid.SectionSolid(
                to_variant_point(*p1), to_variant_point(*p2), to_variant_point(*p3))
            return {"success": True,
                    "message": f"已创建截面区域",
                    "region_handle": region.Handle if hasattr(region, 'Handle') else None}
        except Exception as e:
            return {"success": False, "message": f"创建截面失败: {e}"}

    # ── 3D Mesh ───────────────────────────────────────────

    @require_document
    def add_3d_mesh(self, m_size: int, n_size: int,
                     points: List[float]) -> Any:
        """Add a 3D polygon mesh. points: flat list of M*N*3 coordinates."""
        pts_array = to_variant_array(points)
        return self.doc.ModelSpace.Add3DMesh(m_size, n_size, pts_array)

    @require_document
    def add_polyface_mesh(self, vertices: List[float],
                           face_list: List[int]) -> Any:
        """Add a polyface mesh."""
        verts = to_variant_array(vertices)
        faces = to_variant_array([float(f) for f in face_list])
        return self.doc.ModelSpace.AddPolyfaceMesh(verts, faces)

    # ── 3D Face / Additional Primitives ───────────────────

    @require_document
    def add_3d_face(self, p1: Tuple[float, float, float],
                    p2: Tuple[float, float, float],
                    p3: Tuple[float, float, float],
                    p4: Optional[Tuple[float, float, float]] = None) -> Any:
        """Add a 3D face (3 or 4 vertices)."""
        pt1 = to_variant_point(*p1)
        pt2 = to_variant_point(*p2)
        pt3 = to_variant_point(*p3)
        pt4 = to_variant_point(*p4) if p4 else pt3
        return self.doc.ModelSpace.Add3DFace(pt1, pt2, pt3, pt4)

    @require_document
    def add_tolerance(self, text: str, x: float, y: float, z: float = 0.0,
                       direction_x: float = 1.0, direction_y: float = 0.0,
                       direction_z: float = 0.0) -> Any:
        """Add a geometric tolerance (GD&T feature control frame)."""
        pt = to_variant_point(x, y, z)
        direction = to_variant_point(direction_x, direction_y, direction_z)
        return self.doc.ModelSpace.AddTolerance(text, pt, direction)

    @require_document
    def add_raster_image(self, filepath: str, x: float, y: float,
                          scale: float = 1.0, rotation_deg: float = 0.0,
                          z: float = 0.0) -> Any:
        """Add a raster image (PNG, JPG, BMP, TIFF, etc.)."""
        pt = to_variant_point(x, y, z)
        rotation_rad = rotation_deg * math.pi / 180.0
        return self.doc.ModelSpace.AddRaster(filepath, pt, scale, rotation_rad)

    @require_document
    def add_trace(self, points: List[float]) -> Any:
        """Add a trace (2D solid line with width). points: [x1,y1, x2,y2, x3,y3, x4,y4]."""
        flat = [float(v) for v in points]
        return self.doc.ModelSpace.AddTrace(to_variant_array(flat))

    @require_document
    def add_minert_block(self, block_name: str, x: float, y: float,
                           z: float = 0.0, x_scale: float = 1.0,
                           y_scale: float = 1.0, z_scale: float = 1.0,
                           rotation_deg: float = 0.0, rows: int = 1,
                           cols: int = 1, row_spacing: float = 0.0,
                           col_spacing: float = 0.0) -> Any:
        """Insert a rectangular array of block references (MInsert)."""
        pt = to_variant_point(x, y, z)
        rotation_rad = rotation_deg * math.pi / 180.0
        return self.doc.ModelSpace.AddMInsertBlock(
            pt, block_name, x_scale, y_scale, z_scale,
            rotation_rad, rows, cols, row_spacing, col_spacing)

    @require_document
    def add_shape(self, shape_name: str, x: float, y: float,
                   z: float = 0.0, scale: float = 1.0,
                   rotation_deg: float = 0.0) -> Any:
        """Add a shape entity (from loaded .shx shape file)."""
        pt = to_variant_point(x, y, z)
        rotation_rad = rotation_deg * math.pi / 180.0
        return self.doc.ModelSpace.AddShape(shape_name, pt, scale, rotation_rad)

    # ── TrueColor / Transparency / PlotStyle ──────────────

    @require_document
    def set_entity_truecolor(self, handle: str, red: int, green: int,
                              blue: int) -> Dict[str, Any]:
        """Set an entity's true color (RGB). Creates an AcCmColor object."""
        ent = self._get_entity(handle)
        if ent is None:
            return {"success": False, "message": f"未找到实体: {handle}"}
        try:
            tc = win32com.client.Dispatch("AutoCAD.AcCmColor")
            tc.SetRGB(red, green, blue)
            ent.TrueColor = tc
            return {"success": True,
                    "message": f"已设置真彩色 RGB({red},{green},{blue})",
                    "handle": handle}
        except Exception as e:
            return {"success": False, "message": f"设置真彩色失败: {e}"}

    @require_document
    def set_entity_plot_style(self, handle: str, plot_style: str) -> Dict[str, Any]:
        """Set an entity's plot style name."""
        ent = self._get_entity(handle)
        if ent is None:
            return {"success": False, "message": f"未找到实体: {handle}"}
        try:
            ent.PlotStyleName = plot_style
            return {"success": True,
                    "message": f"已设置打印样式 '{plot_style}'",
                    "handle": handle}
        except Exception as e:
            return {"success": False, "message": f"设置打印样式失败: {e}"}

    # ── Block Operations ───────────────────────────────────

    @require_document
    def create_block(self, name: str, base_pt: Tuple[float,float,float],
                     entities: List[Any] = None) -> Any:
        pt = to_variant_point(*base_pt)
        block = self.doc.Blocks.Add(pt, name)
        # Copy entities into block
        if entities:
            entity_handles = [e.Handle for e in entities]
            for h in entity_handles:
                ent = self.doc.HandleToObject(h)
                ent.Copy()
                # Move into block definition... this is complex in COM
                # Use CopyObjects for proper block creation
            try:
                objs = win32com.client.VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_DISPATCH, entities)
                self.doc.CopyObjects(objs, block, None)
            except Exception as e:
                logger.warning(f"Block entity copy: {e}")
        return block

    @require_document
    def insert_block(self, name: str, x: float, y: float, z: float = 0.0,
                     xscale: float = 1.0, yscale: float = 1.0, zscale: float = 1.0,
                     rotation_deg: float = 0.0) -> Any:
        pt = to_variant_point(x, y, z)
        return self.doc.ModelSpace.InsertBlock(pt, name, xscale, yscale, zscale,
                                               rotation_deg * math.pi / 180.0)

    @require_document
    def get_all_blocks(self) -> List[Dict[str, Any]]:
        blocks = []
        for i in range(self.doc.Blocks.Count):
            blk = self.doc.Blocks.Item(i)
            blocks.append({
                "name": blk.Name,
                "count": blk.Count,
                "is_layout": blk.IsLayout,
                "is_xref": blk.IsXRef,
                "origin": [blk.Origin[0], blk.Origin[1], blk.Origin[2]],
                "path": blk.Path if hasattr(blk, 'Path') else "",
            })
        return blocks

    @require_document
    def add_attribute(self, block_ref, tag: str, value: str = "",
                      height: float = 2.5, x: float = 0, y: float = 0,
                      mode: int = 0) -> Any:
        pt = to_variant_point(x, y, 0)
        return self.doc.ModelSpace.AddAttribute(height, mode, tag, pt, tag, value)

    @require_document
    def insert_block_with_attributes(self, name: str, x: float, y: float,
                                       z: float = 0.0, xscale: float = 1.0,
                                       yscale: float = 1.0, zscale: float = 1.0,
                                       rotation_deg: float = 0.0,
                                       attributes: Optional[List[Dict[str, str]]] = None) -> Any:
        """Insert a block reference with attribute values.
        attributes: list of {tag: value} dicts. Must match attribute definitions in block."""
        pt = to_variant_point(x, y, z)
        rotation_rad = rotation_deg * math.pi / 180.0
        # Insert the block reference
        block_ref = self.doc.ModelSpace.InsertBlock(
            pt, name, xscale, yscale, zscale, rotation_rad)
        # Set attribute values if provided
        if attributes and hasattr(block_ref, 'GetAttributes'):
            try:
                attrs = block_ref.GetAttributes()
                for attr_def in attrs:
                    for av in attributes:
                        if av.get("tag") == attr_def.TagString:
                            attr_def.TextString = str(av.get("value", ""))
                            break
            except Exception as e:
                logger.warning(f"设置属性值失败: {e}")
        return block_ref

    @require_document
    def get_block_attributes(self, handle: str) -> Dict[str, Any]:
        """Get attribute values from a block reference."""
        ent = self._get_entity(handle)
        if ent is None:
            return {"success": False, "message": f"未找到实体: {handle}"}
        if ent.ObjectName != "AcDbBlockReference":
            return {"success": False, "message": f"实体 {handle} 不是图块参照"}
        try:
            attrs = []
            if hasattr(ent, 'GetAttributes'):
                for attr in ent.GetAttributes():
                    attrs.append({
                        "tag": attr.TagString,
                        "value": attr.TextString,
                        "prompt": attr.PromptString,
                        "height": attr.Height,
                        "rotation": attr.Rotation * 180.0 / math.pi,
                        "invisible": attr.Invisible,
                        "constant": attr.Constant,
                        "style": attr.StyleName,
                    })
            return {"success": True, "handle": handle, "attributes": attrs,
                    "count": len(attrs)}
        except Exception as e:
            return {"success": False, "message": f"获取属性失败: {e}"}

    @require_document
    def set_block_attribute(self, handle: str, tag: str,
                              value: str) -> Dict[str, Any]:
        """Set a single attribute value on a block reference by tag name."""
        ent = self._get_entity(handle)
        if ent is None:
            return {"success": False, "message": f"未找到实体: {handle}"}
        try:
            if hasattr(ent, 'GetAttributes'):
                for attr in ent.GetAttributes():
                    if attr.TagString == tag:
                        attr.TextString = str(value)
                        return {"success": True,
                                "message": f"已设置属性 '{tag}' = '{value}'",
                                "handle": handle, "tag": tag, "value": value}
            return {"success": False, "message": f"未找到属性标签 '{tag}'"}
        except Exception as e:
            return {"success": False, "message": f"设置属性失败: {e}"}

    # ── Entity Manipulation ────────────────────────────────

    def _get_entity(self, handle: str) -> Optional[Any]:
        """Get a COM entity by handle."""
        try:
            return self.doc.HandleToObject(handle)
        except Exception:
            return None

    @require_document
    def move_entity(self, handle: str, from_pt: List[float],
                    to_pt: List[float]) -> Dict[str, Any]:
        ent = self._get_entity(handle)
        if ent is None:
            return {"success": False, "message": f"未找到实体: {handle}"}
        p1 = to_variant_point(*from_pt)
        p2 = to_variant_point(*to_pt)
        ent.Move(p1, p2)
        return {"success": True, "message": f"已移动实体 {handle}", "handle": handle}

    @require_document
    def rotate_entity(self, handle: str, base_pt: List[float],
                      angle_deg: float) -> Dict[str, Any]:
        ent = self._get_entity(handle)
        if ent is None:
            return {"success": False, "message": f"未找到实体: {handle}"}
        p = to_variant_point(*base_pt)
        ent.Rotate(p, angle_deg * math.pi / 180.0)
        return {"success": True, "message": f"已旋转实体 {handle} {angle_deg}°"}

    @require_document
    def copy_entity(self, handle: str) -> Dict[str, Any]:
        ent = self._get_entity(handle)
        if ent is None:
            return {"success": False, "message": f"未找到实体: {handle}"}
        new_ent = ent.Copy()
        return {"success": True, "message": f"已复制实体 {handle}",
                "handle": handle, "new_handle": new_ent.Handle}

    @require_document
    def delete_entity(self, handle: str) -> Dict[str, Any]:
        ent = self._get_entity(handle)
        if ent is None:
            return {"success": False, "message": f"未找到实体: {handle}"}
        ent.Delete()
        return {"success": True, "message": f"已删除实体 {handle}"}

    @require_document
    def delete_entities(self, handles: List[str]) -> Dict[str, Any]:
        deleted, failed = [], []
        for h in handles:
            r = self.delete_entity(h)
            if r["success"]:
                deleted.append(h)
            else:
                failed.append(h)
        return {"success": True, "deleted": deleted, "failed": failed,
                "message": f"已删除 {len(deleted)} 个实体，{len(failed)} 个失败"}

    @require_document
    def mirror_entity(self, handle: str, pt1: List[float],
                      pt2: List[float]) -> Dict[str, Any]:
        ent = self._get_entity(handle)
        if ent is None:
            return {"success": False, "message": f"未找到实体: {handle}"}
        p1 = to_variant_point(*pt1)
        p2 = to_variant_point(*pt2)
        mirrored = ent.Mirror(p1, p2)
        return {"success": True,
                "message": f"已镜像实体 {handle}",
                "new_handle": mirrored.Handle if mirrored else None}

    @require_document
    def scale_entity(self, handle: str, base_pt: List[float],
                     scale: float) -> Dict[str, Any]:
        ent = self._get_entity(handle)
        if ent is None:
            return {"success": False, "message": f"未找到实体: {handle}"}
        p = to_variant_point(*base_pt)
        ent.ScaleEntity(p, scale)
        return {"success": True, "message": f"已缩放实体 {handle} ({scale}x)"}

    @require_document
    def offset_entity(self, handle: str, distance: float) -> Dict[str, Any]:
        """Offset a polyline or curve. Returns the new entity handle."""
        ent = self._get_entity(handle)
        if ent is None:
            return {"success": False, "message": f"未找到实体: {handle}"}
        # Offset takes a distance. For direction, use positive for outside.
        try:
            offset_obj = ent.Offset(distance)
            if offset_obj:
                new_handles = []
                if hasattr(offset_obj, 'Count'):
                    for i in range(offset_obj.Count):
                        new_handles.append(offset_obj.Item(i).Handle)
                else:
                    new_handles = [offset_obj.Handle]
                return {"success": True,
                        "message": f"已偏移实体 {handle}，距离: {distance}",
                        "new_handles": new_handles}
        except Exception as e:
            return {"success": False, "message": f"偏移失败: {e}"}

    @require_document
    def array_rectangular(self, handle: str, rows: int, cols: int,
                          row_spacing: float, col_spacing: float) -> Dict[str, Any]:
        ent = self._get_entity(handle)
        if ent is None:
            return {"success": False, "message": f"未找到实体: {handle}"}
        try:
            result = ent.ArrayRectangular(rows, cols, rows + 1 if rows > 1 else 1,
                                          row_spacing, col_spacing)
            new_handles = []
            if hasattr(result, 'Count'):
                for i in range(result.Count):
                    new_handles.append(result.Item(i).Handle)
            elif result:
                new_handles.append(result.Handle)
            return {"success": True,
                    "message": f"已矩形阵列实体 {handle}: {rows}x{cols}",
                    "new_handles": new_handles}
        except Exception as e:
            return {"success": False, "message": f"阵列失败: {e}"}

    @require_document
    def array_polar(self, handle: str, count: int, angle_deg: float,
                    center: List[float]) -> Dict[str, Any]:
        ent = self._get_entity(handle)
        if ent is None:
            return {"success": False, "message": f"未找到实体: {handle}"}
        try:
            pt = to_variant_point(*center)
            result = ent.ArrayPolar(count, angle_deg * math.pi / 180.0, pt)
            new_handles = []
            if hasattr(result, 'Count'):
                for i in range(result.Count):
                    new_handles.append(result.Item(i).Handle)
            return {"success": True,
                    "message": f"已环形阵列 {count} 个，角度: {angle_deg}°",
                    "new_handles": new_handles}
        except Exception as e:
            return {"success": False, "message": f"阵列失败: {e}"}

    # ── 3D Entity Operations ─────────────────────────────

    @require_document
    def rotate_3d(self, handle: str, axis_p1: List[float],
                   axis_p2: List[float], angle_deg: float) -> Dict[str, Any]:
        """Rotate an entity around a 3D axis defined by two points."""
        ent = self._get_entity(handle)
        if ent is None:
            return {"success": False, "message": f"未找到实体: {handle}"}
        try:
            p1 = to_variant_point(*axis_p1)
            p2 = to_variant_point(*axis_p2)
            ent.Rotate3D(p1, p2, angle_deg * math.pi / 180.0)
            return {"success": True, "message": f"已3D旋转实体 {handle} {angle_deg}°"}
        except Exception as e:
            return {"success": False, "message": f"3D旋转失败: {e}"}

    @require_document
    def mirror_3d(self, handle: str, p1: List[float], p2: List[float],
                   p3: List[float]) -> Dict[str, Any]:
        """Mirror an entity across a 3D plane defined by 3 points."""
        ent = self._get_entity(handle)
        if ent is None:
            return {"success": False, "message": f"未找到实体: {handle}"}
        try:
            pt1 = to_variant_point(*p1)
            pt2 = to_variant_point(*p2)
            pt3 = to_variant_point(*p3)
            mirrored = ent.Mirror3D(pt1, pt2, pt3)
            return {"success": True,
                    "message": f"已3D镜像实体 {handle}",
                    "new_handle": mirrored.Handle if mirrored else None}
        except Exception as e:
            return {"success": False, "message": f"3D镜像失败: {e}"}

    # ── Advanced Entity Methods ───────────────────────────

    @require_document
    def get_bounding_box(self, handle: str) -> Dict[str, Any]:
        """Get the axis-aligned bounding box of an entity."""
        ent = self._get_entity(handle)
        if ent is None:
            return {"success": False, "message": f"未找到实体: {handle}"}
        try:
            min_pt = win32com.client.VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_R8, [0.0, 0.0, 0.0])
            max_pt = win32com.client.VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_R8, [0.0, 0.0, 0.0])
            ent.GetBoundingBox(min_pt, max_pt)
            return {"success": True,
                    "min": [min_pt[0], min_pt[1], min_pt[2]],
                    "max": [max_pt[0], max_pt[1], max_pt[2]],
                    "width": max_pt[0] - min_pt[0],
                    "height": max_pt[1] - min_pt[1],
                    "depth": max_pt[2] - min_pt[2]}
        except Exception as e:
            return {"success": False, "message": f"获取包围盒失败: {e}"}

    @require_document
    def intersect_with(self, handle1: str, handle2: str,
                        option: int = 0) -> Dict[str, Any]:
        """Find intersection points between two entities.
        option: 0=extendBoth, 1=extendThis, 2=extendOther, 3=extendNone"""
        ent1 = self._get_entity(handle1)
        ent2 = self._get_entity(handle2)
        if ent1 is None or ent2 is None:
            return {"success": False, "message": "未找到实体"}
        try:
            points = ent1.IntersectWith(ent2, option)
            if points is None:
                return {"success": True, "points": [], "count": 0}
            # Result is flat array [x1,y1,z1, x2,y2,z2, ...]
            pts = []
            for i in range(0, len(points), 3):
                pts.append([round(points[i], 6), round(points[i+1], 6), round(points[i+2], 6)])
            return {"success": True, "points": pts, "count": len(pts)}
        except Exception as e:
            return {"success": False, "message": f"求交失败: {e}"}

    @require_document
    def explode_entity(self, handle: str) -> Dict[str, Any]:
        ent = self._get_entity(handle)
        if ent is None:
            return {"success": False, "message": f"未找到实体: {handle}"}
        try:
            exploded = ent.Explode()
            new_handles = []
            if hasattr(exploded, 'Count'):
                for i in range(exploded.Count):
                    new_handles.append(exploded.Item(i).Handle)
            return {"success": True,
                    "message": f"已分解实体 {handle}，生成 {len(new_handles)} 个实体",
                    "new_handles": new_handles}
        except Exception as e:
            return {"success": False, "message": f"分解失败: {e}"}

    # ── Entity Properties ──────────────────────────────────

    @require_document
    def set_entity_properties(self, handle: str, **kwargs) -> Dict[str, Any]:
        """Set properties on any entity. Supported: color, layer, linetype,
        linetypescale, lineweight, visible, transparency."""
        ent = self._get_entity(handle)
        if ent is None:
            return {"success": False, "message": f"未找到实体: {handle}"}
        changed = {}
        for key, value in kwargs.items():
            try:
                if key == "color":
                    ent.Color = int(value)
                    changed[key] = int(value)
                elif key == "layer":
                    ent.Layer = str(value)
                    changed[key] = str(value)
                elif key == "linetype":
                    ent.Linetype = str(value)
                    changed[key] = str(value)
                elif key == "linetypescale":
                    ent.LinetypeScale = float(value)
                    changed[key] = float(value)
                elif key == "lineweight":
                    ent.Lineweight = float(value)
                    changed[key] = float(value)
                elif key == "visible":
                    ent.Visible = bool(value)
                    changed[key] = bool(value)
                elif key == "transparency":
                    ent.Transparency = float(value)
                    changed[key] = float(value)
                elif key == "material":
                    ent.Material = str(value)
                    changed[key] = str(value)
                elif key == "thickness":
                    ent.Thickness = float(value)
                    changed[key] = float(value)
                elif key == "elevation":
                    ent.Elevation = float(value)
                    changed[key] = float(value)
            except Exception as e:
                logger.warning(f"设置属性 {key} 失败: {e}")
        return {"success": True, "message": f"已修改 {len(changed)} 个属性",
                "changed": changed}

    @require_document
    def get_entity_properties(self, handle: str) -> Dict[str, Any]:
        """Get all readable properties of an entity."""
        ent = self._get_entity(handle)
        if ent is None:
            return {"success": False, "message": f"未找到实体: {handle}"}
        try:
            props = {
                "handle": ent.Handle,
                "object_name": ent.ObjectName,
                "object_id": ent.ObjectID,
                "layer": ent.Layer,
                "color": ent.Color,
                "linetype": ent.Linetype,
                "linetypescale": ent.LinetypeScale,
                "lineweight": ent.Lineweight,
                "visible": ent.Visible,
            }
            # Extended properties
            try:
                props["has_extension_dictionary"] = ent.HasExtensionDictionary
            except Exception:
                pass
            try:
                props["material"] = ent.Material
            except Exception:
                pass
            try:
                props["plot_style_name"] = ent.PlotStyleName
            except Exception:
                pass
            try:
                props["thickness"] = ent.Thickness
            except Exception:
                pass
            try:
                props["elevation"] = ent.Elevation
            except Exception:
                pass
            try:
                props["entity_transparency"] = ent.EntityTransparency
            except Exception:
                pass
            # TrueColor
            try:
                tc = ent.TrueColor
                props["true_color"] = {"red": tc.Red, "green": tc.Green, "blue": tc.Blue}
            except Exception:
                pass
            # Hyperlinks count
            try:
                props["hyperlink_count"] = ent.Hyperlinks.Count
            except Exception:
                pass
            # Type-specific properties
            obj_name = ent.ObjectName
            if obj_name == "AcDbLine":
                props.update({
                    "start_point": list(ent.StartPoint),
                    "end_point": list(ent.EndPoint),
                    "length": ent.Length,
                    "angle": ent.Angle,
                    "delta": list(ent.Delta),
                })
            elif obj_name == "AcDbCircle":
                props.update({
                    "center": list(ent.Center),
                    "radius": ent.Radius,
                    "diameter": ent.Diameter,
                    "circumference": ent.Circumference,
                    "area": ent.Area,
                })
            elif obj_name == "AcDbArc":
                props.update({
                    "center": list(ent.Center),
                    "radius": ent.Radius,
                    "start_angle": ent.StartAngle,
                    "end_angle": ent.EndAngle,
                    "arc_length": ent.ArcLength,
                    "area": ent.Area,
                })
            elif obj_name == "AcDbEllipse":
                props.update({
                    "center": list(ent.Center),
                    "major_axis": list(ent.MajorAxis),
                    "minor_axis": list(ent.MinorAxis),
                    "radius_ratio": ent.RadiusRatio,
                    "area": ent.Area,
                })
            elif obj_name in ("AcDbPolyline", "AcDb2dPolyline"):
                props.update({
                    "length": ent.Length,
                    "area": ent.Area,
                    "closed": ent.Closed,
                    "vertices": [[c[0],c[1]] for c in list(ent.Coordinates)],
                })
            elif obj_name == "AcDbSpline":
                props.update({
                    "degree": ent.Degree,
                    "number_of_fit_points": ent.NumberOfFitPoints,
                    "is_closed": ent.IsClosed,
                })
            elif obj_name in ("AcDbText", "AcDbMText"):
                props.update({
                    "text_string": ent.TextString,
                    "height": ent.Height,
                    "rotation": ent.Rotation,
                    "insertion_point": list(ent.InsertionPoint) if hasattr(ent, 'InsertionPoint') else None,
                })
            elif obj_name == "AcDb3dSolid":
                props.update({
                    "volume": ent.Volume,
                    "centroid": list(ent.Centroid) if hasattr(ent, 'Centroid') else None,
                    "moment_of_inertia": list(ent.MomentOfInertia) if hasattr(ent, 'MomentOfInertia') else None,
                    "principal_directions": list(ent.PrincipalDirections) if hasattr(ent, 'PrincipalDirections') else None,
                    "principal_moments": list(ent.PrincipalMoments) if hasattr(ent, 'PrincipalMoments') else None,
                    "product_of_inertia": list(ent.ProductOfInertia) if hasattr(ent, 'ProductOfInertia') else None,
                    "radius_of_gyration": list(ent.RadiusOfGyration) if hasattr(ent, 'RadiusOfGyration') else None,
                })
            elif obj_name == "AcDbRegion":
                props.update({
                    "area": ent.Area,
                    "perimeter": ent.Perimeter,
                    "centroid": list(ent.Centroid) if hasattr(ent, 'Centroid') else None,
                })
            elif obj_name == "AcDbRasterImage":
                props.update({
                    "image_file": ent.ImageFile,
                    "image_width": ent.ImageWidth,
                    "image_height": ent.ImageHeight,
                    "scale_factor": ent.ScaleFactor,
                    "rotation": ent.Rotation,
                    "fade": ent.Fade if hasattr(ent, 'Fade') else 0,
                    "contrast": ent.Contrast if hasattr(ent, 'Contrast') else 50,
                    "brightness": ent.Brightness if hasattr(ent, 'Brightness') else 50,
                })
            elif obj_name == "AcDbTable":
                props.update({
                    "rows": ent.Rows,
                    "columns": ent.Columns,
                })
            props["type"] = obj_name
            return props
        except Exception as e:
            return {"success": False, "message": f"获取属性失败: {e}", "handle": handle}

    # ── Hyperlinks ────────────────────────────────────────

    @require_document
    def add_hyperlink(self, handle: str, url: str,
                       description: str = "",
                       named_location: str = "") -> Dict[str, Any]:
        """Add a hyperlink to an entity."""
        ent = self._get_entity(handle)
        if ent is None:
            return {"success": False, "message": f"未找到实体: {handle}"}
        try:
            ent.Hyperlinks.Add(url, description, named_location)
            return {"success": True, "message": f"已添加超链接: {url}"}
        except Exception as e:
            return {"success": False, "message": f"添加超链接失败: {e}"}

    @require_document
    def get_hyperlinks(self, handle: str) -> Dict[str, Any]:
        """Get all hyperlinks on an entity."""
        ent = self._get_entity(handle)
        if ent is None:
            return {"success": False, "message": f"未找到实体: {handle}"}
        try:
            links = []
            for i in range(ent.Hyperlinks.Count):
                hl = ent.Hyperlinks.Item(i)
                links.append({
                    "url": hl.URL,
                    "description": hl.Description,
                    "named_location": hl.NamedLocation,
                })
            return {"success": True, "hyperlinks": links, "count": len(links)}
        except Exception as e:
            return {"success": False, "message": f"获取超链接失败: {e}"}

    @require_document
    def remove_hyperlink(self, handle: str, index: int = 0) -> Dict[str, Any]:
        """Remove a hyperlink from an entity by index."""
        ent = self._get_entity(handle)
        if ent is None:
            return {"success": False, "message": f"未找到实体: {handle}"}
        try:
            hl = ent.Hyperlinks.Item(index)
            hl.Delete()
            return {"success": True, "message": f"已删除超链接"}
        except Exception as e:
            return {"success": False, "message": f"删除超链接失败: {e}"}

    # ── XData (Extended Entity Data) ──────────────────────

    @require_document
    def get_xdata(self, handle: str, app_name: str = "") -> Dict[str, Any]:
        """Get extended data (XData) from an entity."""
        ent = self._get_entity(handle)
        if ent is None:
            return {"success": False, "message": f"未找到实体: {handle}"}
        try:
            xdata_type = win32com.client.VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_I2, [])
            xdata_value = win32com.client.VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_VARIANT, [])
            ent.GetXData(app_name or None, xdata_type, xdata_value)
            if not xdata_type or len(xdata_type) == 0:
                return {"success": True, "xdata": [], "count": 0,
                        "message": "无扩展数据"}
            pairs = []
            for i in range(len(xdata_type)):
                pairs.append({
                    "code": int(xdata_type[i]),
                    "value": str(xdata_value[i]),
                })
            return {"success": True, "xdata": pairs, "count": len(pairs)}
        except Exception as e:
            return {"success": False, "message": f"获取XData失败: {e}"}

    @require_document
    def set_xdata(self, handle: str, data_pairs: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Set extended data on an entity.
        data_pairs: list of {code: int, value: any} where code is DXF group code (1000-1071)."""
        ent = self._get_entity(handle)
        if ent is None:
            return {"success": False, "message": f"未找到实体: {handle}"}
        try:
            codes = [p["code"] for p in data_pairs]
            values = [p["value"] for p in data_pairs]
            xdata_type = win32com.client.VARIANT(
                pythoncom.VT_ARRAY | pythoncom.VT_I2, codes)
            xdata_value = win32com.client.VARIANT(
                pythoncom.VT_ARRAY | pythoncom.VT_VARIANT, values)
            ent.SetXData(xdata_type, xdata_value)
            return {"success": True, "message": f"已设置 {len(data_pairs)} 条扩展数据"}
        except Exception as e:
            return {"success": False, "message": f"设置XData失败: {e}"}

    # ── Extension Dictionary ──────────────────────────────

    @require_document
    def get_extension_dictionary(self, handle: str) -> Any:
        """Get or create the extension dictionary for an entity."""
        ent = self._get_entity(handle)
        if ent is None:
            return None
        try:
            return ent.GetExtensionDictionary()
        except Exception:
            return None

    # ── UCS Management ─────────────────────────────────━━

    @require_document
    def create_ucs(self, origin: List[float], x_axis_pt: List[float],
                    y_axis_pt: List[float], name: str) -> Dict[str, Any]:
        """Create a named UCS."""
        try:
            o = to_variant_point(*origin)
            x = to_variant_point(*x_axis_pt)
            y = to_variant_point(*y_axis_pt)
            ucs = self.doc.UserCoordinateSystems.Add(o, x, y, name)
            return {"success": True, "message": f"已创建UCS '{name}'", "name": name}
        except Exception as e:
            return {"success": False, "message": f"创建UCS失败: {e}"}

    @require_document
    def get_all_ucs(self) -> List[Dict[str, Any]]:
        """List all named UCS definitions."""
        ucss = []
        try:
            for i in range(self.doc.UserCoordinateSystems.Count):
                ucs = self.doc.UserCoordinateSystems.Item(i)
                ucss.append({
                    "name": ucs.Name,
                    "origin": list(ucs.Origin),
                    "x_vector": list(ucs.XVector),
                    "y_vector": list(ucs.YVector),
                })
        except Exception:
            pass
        return ucss

    @require_document
    def set_active_ucs(self, name: str) -> Dict[str, Any]:
        """Set the active UCS by name."""
        try:
            self.doc.ActiveUCS = self.doc.UserCoordinateSystems.Item(name)
            return {"success": True, "message": f"已激活UCS '{name}'"}
        except Exception as e:
            return {"success": False, "message": f"设置UCS失败: {e}"}

    @require_document
    def get_active_ucs(self) -> Dict[str, Any]:
        """Get the current active UCS."""
        try:
            ucs = self.doc.ActiveUCS
            return {
                "name": ucs.Name,
                "origin": list(ucs.Origin),
                "x_vector": list(ucs.XVector),
                "y_vector": list(ucs.YVector),
            }
        except Exception as e:
            return {"error": str(e)}

    # ── Selection ──────────────────────────────────────────

    @require_document
    def create_selection_set(self, name: str) -> Any:
        try:
            self.doc.SelectionSets.Item(name).Delete()
        except Exception:
            pass
        return self.doc.SelectionSets.Add(name)

    @require_document
    def select_by_window(self, pt1: List[float], pt2: List[float],
                         ss_name: str = "MCP_TEMP_SS") -> Dict[str, Any]:
        ss = self.create_selection_set(ss_name)
        ss.Select(0, to_variant_point(*pt1), to_variant_point(*pt2))
        handles = [ent.Handle for ent in ss]
        return {"success": True, "count": ss.Count, "handles": handles, "name": ss_name}

    @require_document
    def select_by_crossing(self, pt1: List[float], pt2: List[float],
                           ss_name: str = "MCP_TEMP_SS") -> Dict[str, Any]:
        ss = self.create_selection_set(ss_name)
        ss.Select(1, to_variant_point(*pt1), to_variant_point(*pt2))
        handles = [ent.Handle for ent in ss]
        return {"success": True, "count": ss.Count, "handles": handles, "name": ss_name}

    @require_document
    def select_all(self, ss_name: str = "MCP_TEMP_SS") -> Dict[str, Any]:
        ss = self.create_selection_set(ss_name)
        for i in range(self.doc.ModelSpace.Count):
            try:
                ss.AddItems([self.doc.ModelSpace.Item(i)])
            except Exception:
                pass
        handles = [ent.Handle for ent in ss]
        return {"success": True, "count": ss.Count, "handles": handles, "name": ss_name}

    # ── Layer Management ───────────────────────────────────

    @require_document
    def create_layer(self, name: str, color_idx: int = 7,
                     linetype: str = "Continuous") -> Dict[str, Any]:
        try:
            try:
                layer = self.doc.Layers.Item(name)
                if color_idx != 7:
                    layer.Color = color_idx
                return {"success": True, "message": f"图层 '{name}' 已存在", "existing": True}
            except Exception:
                layer = self.doc.Layers.Add(name)
                layer.Color = color_idx
                try:
                    layer.Linetype = linetype
                except Exception:
                    pass
                return {"success": True, "message": f"已创建图层 '{name}'", "existing": False}
        except Exception as e:
            return {"success": False, "message": f"创建图层失败: {e}"}

    @require_document
    def delete_layer(self, name: str) -> Dict[str, Any]:
        try:
            layer = self.doc.Layers.Item(name)
            if layer.Name == self.doc.ActiveLayer.Name:
                return {"success": False, "message": "不能删除当前图层"}
            layer.Delete()
            return {"success": True, "message": f"已删除图层 '{name}'"}
        except Exception as e:
            return {"success": False, "message": f"删除图层失败: {e}"}

    @require_document
    def rename_layer(self, old_name: str, new_name: str) -> Dict[str, Any]:
        try:
            layer = self.doc.Layers.Item(old_name)
            layer.Name = new_name
            return {"success": True, "message": f"图层已重命名: {old_name} → {new_name}"}
        except Exception as e:
            return {"success": False, "message": f"重命名失败: {e}"}

    @require_document
    def get_all_layers(self) -> List[Dict[str, Any]]:
        layers = []
        for i in range(self.doc.Layers.Count):
            layer = self.doc.Layers.Item(i)
            layers.append({
                "name": layer.Name,
                "color": layer.Color,
                "linetype": layer.Linetype,
                "lineweight": layer.Lineweight,
                "is_frozen": layer.Freeze,
                "is_locked": layer.Lock,
                "is_on": layer.LayerOn,
                "is_plottable": layer.Plottable,
                "description": layer.Description if hasattr(layer, 'Description') else "",
                "handle": layer.Handle,
            })
        return layers

    @require_document
    def set_layer_state(self, name: str, frozen: Optional[bool] = None,
                        locked: Optional[bool] = None, on: Optional[bool] = None) -> Dict[str, Any]:
        try:
            layer = self.doc.Layers.Item(name)
            if frozen is not None:
                layer.Freeze = frozen
            if locked is not None:
                layer.Lock = locked
            if on is not None:
                layer.LayerOn = on
            return {"success": True,
                    "message": f"图层 '{name}' 状态已更新",
                    "state": {"frozen": layer.Freeze, "locked": layer.Lock, "on": layer.LayerOn}}
        except Exception as e:
            return {"success": False, "message": f"设置图层状态失败: {e}"}

    @require_document
    def set_current_layer(self, name: str) -> Dict[str, Any]:
        try:
            self.doc.ActiveLayer = self.doc.Layers.Item(name)
            return {"success": True, "message": f"当前图层: {name}"}
        except Exception as e:
            return {"success": False, "message": f"设置图层失败: {e}"}

    # ── Style Management ───────────────────────────────────

    @require_document
    def get_text_styles(self) -> List[Dict[str, Any]]:
        styles = []
        for i in range(self.doc.TextStyles.Count):
            s = self.doc.TextStyles.Item(i)
            styles.append({
                "name": s.Name,
                "font_file": s.FontFile,
                "big_font_file": s.BigFontFile,
                "height": s.Height,
                "width": s.Width,
                "oblique_angle": s.ObliqueAngle,
                "is_vertical": s.IsVertical if hasattr(s, 'IsVertical') else False,
            })
        return styles

    @require_document
    def create_text_style(self, name: str, font: str = "Arial",
                          height: float = 0.0, width: float = 1.0) -> Dict[str, Any]:
        try:
            style = self.doc.TextStyles.Add(name)
            style.SetFont(font, False, False, 0, 0)
            style.Height = height
            style.Width = width
            return {"success": True, "message": f"已创建文字样式 '{name}'"}
        except Exception as e:
            return {"success": False, "message": f"创建文字样式失败: {e}"}

    @require_document
    def set_current_text_style(self, name: str) -> Dict[str, Any]:
        try:
            self.doc.ActiveTextStyle = self.doc.TextStyles.Item(name)
            return {"success": True, "message": f"当前文字样式: {name}"}
        except Exception as e:
            return {"success": False, "message": f"设置文字样式失败: {e}"}

    @require_document
    def get_dim_styles(self) -> List[Dict[str, Any]]:
        styles = []
        for i in range(self.doc.DimStyles.Count):
            s = self.doc.DimStyles.Item(i)
            styles.append({"name": s.Name, "handle": s.Handle})
        return styles

    @require_document
    def copy_dim_style(self, source_name: str, new_name: str) -> Dict[str, Any]:
        try:
            src = self.doc.DimStyles.Item(source_name)
            new_style = src.Copy(new_name)
            return {"success": True, "message": f"已复制标注样式: {source_name} → {new_name}"}
        except Exception as e:
            return {"success": False, "message": f"复制标注样式失败: {e}"}

    @require_document
    def set_current_dim_style(self, name: str) -> Dict[str, Any]:
        try:
            self.doc.ActiveDimStyle = self.doc.DimStyles.Item(name)
            return {"success": True, "message": f"当前标注样式: {name}"}
        except Exception as e:
            return {"success": False, "message": f"设置标注样式失败: {e}"}

    # ── View Operations ────────────────────────────────────

    @require_document
    def zoom_extents(self) -> Dict[str, Any]:
        self.doc.Application.ZoomExtents()
        return {"success": True, "message": "已缩放到全部范围"}

    @require_document
    def zoom_window(self, pt1: List[float], pt2: List[float]) -> Dict[str, Any]:
        self.doc.Application.ZoomWindow(
            to_variant_point(*pt1), to_variant_point(*pt2))
        return {"success": True, "message": "已缩放窗口"}

    @require_document
    def zoom_center(self, cx: float, cy: float, height: float) -> Dict[str, Any]:
        self.doc.Application.ZoomCenter(
            to_variant_point(cx, cy, 0), height)
        return {"success": True, "message": f"已居中缩放到 ({cx}, {cy})"}

    @require_document
    def zoom_scale(self, scale: float) -> Dict[str, Any]:
        self.doc.Application.ZoomScaled(scale, 0)
        return {"success": True, "message": f"已缩放 {scale}x"}

    @require_document
    def zoom_previous(self) -> Dict[str, Any]:
        self.doc.Application.ZoomPrevious()
        return {"success": True, "message": "已恢复前一视图"}

    @require_document
    def zoom_all(self) -> Dict[str, Any]:
        self.doc.Application.ZoomAll()
        return {"success": True, "message": "已缩放到全部"}

    @require_document
    def pan(self, dx: float, dy: float) -> Dict[str, Any]:
        try:
            # Get current view center, offset by (dx, dy), then re-center
            current_view = self.doc.ActiveView
            current_center = current_view.Center
            new_x = float(current_center[0]) + float(dx)
            new_y = float(current_center[1]) + float(dy)
            height = current_view.Height
            self.doc.Application.ZoomCenter(
                to_variant_point(new_x, new_y, 0), height)
            return {"success": True, "message": f"已平移 ({dx}, {dy})"}
        except Exception as e:
            return {"success": False, "message": f"平移失败: {e}"}

    @require_document
    def get_current_view(self) -> Dict[str, Any]:
        try:
            view = self.doc.ActiveView
            return {
                "center": list(view.Center),
                "height": view.Height,
                "width": view.Width,
                "target": list(view.Target),
                "direction": list(view.Direction),
            }
        except Exception as e:
            return {"error": str(e)}

    # ── Layout / Viewport ──────────────────────────────────

    @require_document
    def get_layouts(self) -> List[Dict[str, Any]]:
        layouts = []
        for i in range(self.doc.Layouts.Count):
            lay = self.doc.Layouts.Item(i)
            layouts.append({
                "name": lay.Name,
                "tab_order": lay.TabOrder,
                "model_type": lay.ModelType,
            })
        return layouts

    @require_document
    def set_active_layout(self, name: str) -> Dict[str, Any]:
        try:
            self.doc.ActiveLayout = self.doc.Layouts.Item(name)
            return {"success": True, "message": f"已切换到布局: {name}"}
        except Exception as e:
            return {"success": False, "message": f"切换布局失败: {e}"}

    @require_document
    def create_layout(self, name: str) -> Dict[str, Any]:
        try:
            self.doc.Layouts.Add(name)
            return {"success": True, "message": f"已创建布局: {name}"}
        except Exception as e:
            return {"success": False, "message": f"创建布局失败: {e}"}

    # ── Highlight ──────────────────────────────────────────

    @require_document
    def highlight_entity(self, handle: str, color: int = 1) -> Dict[str, Any]:
        ent = self._get_entity(handle)
        if ent is None:
            return {"success": False, "message": f"未找到实体: {handle}"}
        original = ent.Color
        ent.Color = color
        # Update needs to be pushed to the display
        ent.Update()
        return {"success": True, "message": f"已高亮实体 {handle}",
                "original_color": original, "new_color": color}

    @require_document
    def highlight_entities(self, handles: List[str], color: int = 1) -> Dict[str, Any]:
        results = []
        for h in handles:
            r = self.highlight_entity(h, color)
            results.append(r)
        count = sum(1 for r in results if r.get("success"))
        return {"success": True, "message": f"已高亮 {count}/{len(handles)} 个实体",
                "results": results}

    @require_document
    def reset_entity_color(self, handle: str, original_color: int) -> Dict[str, Any]:
        ent = self._get_entity(handle)
        if ent is None:
            return {"success": False, "message": f"未找到实体: {handle}"}
        ent.Color = original_color
        ent.Update()
        return {"success": True, "message": f"已重置颜色"}

    # ── Scanning ───────────────────────────────────────────

    @require_document
    def scan_model_space(self, max_entities: int = 10000) -> List[Dict[str, Any]]:
        """Scan all entities in model space and return their metadata."""
        entities = []
        count = min(self.doc.ModelSpace.Count, max_entities)
        type_stats = {}
        for i in range(count):
            try:
                ent = self.doc.ModelSpace.Item(i)
                obj_name = ent.ObjectName
                type_stats[obj_name] = type_stats.get(obj_name, 0) + 1
                info = {
                    "index": i,
                    "handle": ent.Handle,
                    "type": obj_name,
                    "name": obj_name.replace("AcDb", ""),
                    "layer": ent.Layer,
                    "color": ent.Color,
                    "linetype": ent.Linetype,
                }
                # Fast type-specific properties
                if obj_name == "AcDbLine":
                    info["start"] = [round(ent.StartPoint[0],4), round(ent.StartPoint[1],4), round(ent.StartPoint[2],4)]
                    info["end"] = [round(ent.EndPoint[0],4), round(ent.EndPoint[1],4), round(ent.EndPoint[2],4)]
                    info["length"] = round(ent.Length, 4)
                elif obj_name == "AcDbCircle":
                    info["center"] = [round(ent.Center[0],4), round(ent.Center[1],4), round(ent.Center[2],4)]
                    info["radius"] = round(ent.Radius, 4)
                elif obj_name == "AcDbArc":
                    info["center"] = [round(ent.Center[0],4), round(ent.Center[1],4), round(ent.Center[2],4)]
                    info["radius"] = round(ent.Radius, 4)
                    info["start_angle"] = round(ent.StartAngle, 4)
                    info["end_angle"] = round(ent.EndAngle, 4)
                elif obj_name in ("AcDbText", "AcDbMText"):
                    info["text"] = ent.TextString
                elif "Polyline" in obj_name:
                    info["length"] = round(ent.Length, 4) if hasattr(ent, 'Length') else 0
                    info["closed"] = ent.Closed if hasattr(ent, 'Closed') else False
                entities.append(info)
            except Exception as e:
                entities.append({"index": i, "error": str(e)})
        return {"entities": entities, "total": count, "type_stats": type_stats}

    @require_document
    def scan_entities_in_area(self, xmin: float, ymin: float,
                               xmax: float, ymax: float) -> List[Dict[str, Any]]:
        """Scan entities whose handle we can find within a bounding box.
        Uses selection window for efficiency."""
        ss = self.create_selection_set("MCP_AREA_SCAN")
        ss.Select(0, to_variant_point(xmin, ymin, 0), to_variant_point(xmax, ymax, 0))
        entities = []
        for ent in ss:
            try:
                entities.append({
                    "handle": ent.Handle,
                    "type": ent.ObjectName,
                    "layer": ent.Layer,
                    "color": ent.Color,
                })
            except Exception:
                pass
        return {"entities": entities, "count": ss.Count, "bbox": [xmin, ymin, xmax, ymax]}

    # ── Groups ─────────────────────────────────────────────

    @require_document
    def create_group(self, name: str, handles: List[str]) -> Dict[str, Any]:
        try:
            group = self.doc.Groups.Add(name)
            entities = []
            for h in handles:
                ent = self._get_entity(h)
                if ent:
                    entities.append(ent)
            if entities:
                varray = win32com.client.VARIANT(
                    pythoncom.VT_ARRAY | pythoncom.VT_DISPATCH, entities)
                group.AppendItems(varray)
            return {"success": True, "message": f"已创建组 '{name}'，包含 {len(entities)} 个实体"}
        except Exception as e:
            return {"success": False, "message": f"创建组失败: {e}"}

    @require_document
    def get_all_groups(self) -> List[Dict[str, Any]]:
        groups = []
        for i in range(self.doc.Groups.Count):
            g = self.doc.Groups.Item(i)
            groups.append({"name": g.Name, "count": g.Count})
        return groups

    # ── Send Command ──────────────────────────────────────

    @require_document
    def send_command(self, command: str) -> Dict[str, Any]:
        """Send a raw AutoCAD command string. Use cautiously."""
        try:
            self.doc.SendCommand(command + "\n")
            return {"success": True, "message": f"已发送命令: {command}"}
        except Exception as e:
            return {"success": False, "message": f"发送命令失败: {e}"}

    # ── Undo / Redo ───────────────────────────────────────

    @require_document
    def undo(self, count: int = 1) -> Dict[str, Any]:
        try:
            for _ in range(count):
                self.doc.SendCommand("U\n")
            return {"success": True, "message": f"已撤销 {count} 步"}
        except Exception as e:
            return {"success": False, "message": f"撤销失败: {e}"}

    @require_document
    def redo(self, count: int = 1) -> Dict[str, Any]:
        try:
            for _ in range(count):
                self.doc.SendCommand("REDO\n")
            return {"success": True, "message": f"已重做 {count} 步"}
        except Exception as e:
            return {"success": False, "message": f"重做失败: {e}"}

    # ── Measurements ──────────────────────────────────────

    def get_distance(self, pt1: List[float], pt2: List[float]) -> float:
        dx = pt1[0] - pt2[0]
        dy = pt1[1] - pt2[1]
        dz = (pt1[2] if len(pt1) > 2 else 0) - (pt2[2] if len(pt2) > 2 else 0)
        return math.sqrt(dx*dx + dy*dy + dz*dz)

    def get_angle(self, pt1: List[float], pt2: List[float]) -> float:
        dx = pt2[0] - pt1[0]
        dy = pt2[1] - pt1[1]
        return math.atan2(dy, dx) * 180.0 / math.pi

    # ── Purge / Audit ─────────────────────────────────────

    @require_document
    def purge_all(self) -> Dict[str, Any]:
        try:
            self.doc.PurgeAll()
            return {"success": True, "message": "已清理未使用的对象"}
        except Exception as e:
            return {"success": False, "message": f"清理失败: {e}"}

    @require_document
    def audit(self) -> Dict[str, Any]:
        try:
            self.doc.AuditInfo(True)
            return {"success": True, "message": "已审计图纸"}
        except Exception as e:
            return {"success": False, "message": f"审计失败: {e}"}

    # ── Named Views ───────────────────────────────────────

    @require_document
    def save_named_view(self, name: str) -> Dict[str, Any]:
        """Save the current view as a named view."""
        try:
            self.doc.Views.Add(name)
            return {"success": True, "message": f"已保存命名视图 '{name}'"}
        except Exception as e:
            return {"success": False, "message": f"保存视图失败: {e}"}

    @require_document
    def restore_named_view(self, name: str) -> Dict[str, Any]:
        """Restore a named view."""
        try:
            view = self.doc.Views.Item(name)
            self.doc.SetView(view)
            return {"success": True, "message": f"已恢复视图 '{name}'"}
        except Exception as e:
            return {"success": False, "message": f"恢复视图失败: {e}"}

    @require_document
    def get_named_views(self) -> List[Dict[str, Any]]:
        """List all named views."""
        views = []
        try:
            for i in range(self.doc.Views.Count):
                v = self.doc.Views.Item(i)
                views.append({
                    "name": v.Name,
                    "center": list(v.Center),
                    "width": v.Width,
                    "height": v.Height,
                    "target": list(v.Target),
                    "direction": list(v.Direction),
                })
        except Exception:
            pass
        return views

    @require_document
    def delete_named_view(self, name: str) -> Dict[str, Any]:
        """Delete a named view."""
        try:
            self.doc.Views.Item(name).Delete()
            return {"success": True, "message": f"已删除视图 '{name}'"}
        except Exception as e:
            return {"success": False, "message": f"删除视图失败: {e}"}

    # ── Viewports (Paper Space) ──────────────────────────

    @require_document
    def add_pviewport(self, cx: float, cy: float, width: float,
                       height: float) -> Any:
        """Add a paper space viewport."""
        center = to_variant_point(cx, cy, 0)
        return self.doc.PaperSpace.AddPViewport(center, width, height)

    @require_document
    def get_pviewports(self) -> List[Dict[str, Any]]:
        """List all paper space viewports."""
        vps = []
        try:
            for i in range(self.doc.PaperSpace.Count):
                ent = self.doc.PaperSpace.Item(i)
                if ent.ObjectName == "AcDbViewport":
                    vps.append({
                        "handle": ent.Handle,
                        "view_center": list(ent.ViewCenter) if hasattr(ent, 'ViewCenter') else None,
                        "display_locked": ent.DisplayLocked if hasattr(ent, 'DisplayLocked') else False,
                        "standard_scale": ent.StandardScale if hasattr(ent, 'StandardScale') else 0,
                        "custom_scale": ent.CustomScale if hasattr(ent, 'CustomScale') else 1.0,
                        "on": ent.ViewportOn if hasattr(ent, 'ViewportOn') else True,
                    })
        except Exception:
            pass
        return vps

    @require_document
    def set_pviewport_props(self, handle: str, **kwargs) -> Dict[str, Any]:
        """Set properties on a paper space viewport."""
        ent = self._get_entity(handle)
        if ent is None or ent.ObjectName != "AcDbViewport":
            return {"success": False, "message": "Not a viewport"}
        changed = {}
        for k, v in kwargs.items():
            try:
                if k == "display_locked":
                    ent.DisplayLocked = bool(v)
                elif k == "custom_scale":
                    ent.CustomScale = float(v)
                elif k == "standard_scale":
                    ent.StandardScale = int(v)
                elif k == "on":
                    ent.ViewportOn = bool(v)
                changed[k] = v
            except Exception as e:
                logger.warning(f"设置视口属性 {k} 失败: {e}")
        return {"success": True, "changed": changed}

    # ── Plot ──────────────────────────────────────────────

    @require_document
    def plot_to_device(self, plot_config: str = "") -> Dict[str, Any]:
        """Plot to a configured device."""
        try:
            self.doc.Plot.PlotToDevice(plot_config)
            return {"success": True, "message": "已发送到打印设备"}
        except Exception as e:
            return {"success": False, "message": f"打印失败: {e}"}

    @require_document
    def plot_to_file(self, filepath: str, plot_config: str = "") -> Dict[str, Any]:
        """Plot to a file (PLT)."""
        try:
            self.doc.Plot.PlotToFile(filepath, plot_config)
            return {"success": True, "message": f"已打印到文件: {filepath}"}
        except Exception as e:
            return {"success": False, "message": f"打印到文件失败: {e}"}

    @require_document
    def plot_preview(self, preview_type: int = 1) -> Dict[str, Any]:
        """Display plot preview. 0=Partial, 1=Full."""
        try:
            self.doc.Plot.DisplayPlotPreview(preview_type)
            return {"success": True, "message": "已显示打印预览"}
        except Exception as e:
            return {"success": False, "message": f"预览失败: {e}"}

    @require_document
    def set_plot_configs(self, layout_names: List[str]) -> Dict[str, Any]:
        """Set which layouts to plot."""
        try:
            layouts = win32com.client.VARIANT(
                pythoncom.VT_ARRAY | pythoncom.VT_VARIANT, layout_names)
            self.doc.Plot.SetLayoutsToPlot(layouts)
            return {"success": True, "message": f"已设置打印布局: {layout_names}"}
        except Exception as e:
            return {"success": False, "message": f"设置打印布局失败: {e}"}

    @require_document
    def get_plot_devices(self) -> List[str]:
        """Get available plot device names."""
        try:
            return list(self.doc.PlotConfigurations.GetPlotDeviceNames())
        except Exception:
            return []

    @require_document
    def get_plot_style_table_names(self) -> List[str]:
        """Get available plot style table names."""
        try:
            return list(self.doc.Plot.GetPlotStyleTableNames())
        except Exception:
            return []

    @require_document
    def get_plot_configurations(self) -> List[Dict[str, Any]]:
        """List all plot configurations (page setups)."""
        configs = []
        try:
            for i in range(self.doc.PlotConfigurations.Count):
                pc = self.doc.PlotConfigurations.Item(i)
                configs.append({
                    "name": pc.Name,
                    "device": pc.ConfigName,
                    "paper_size": pc.CanonicalMediaName,
                    "plot_type": pc.PlotType,
                    "style_sheet": pc.StyleSheet,
                    "center_plot": pc.CenterPlot,
                    "plot_rotation": pc.PlotRotation,
                })
        except Exception:
            pass
        return configs

    # ── Materials ─────────────────────────────────────────

    @require_document
    def create_material(self, name: str, description: str = "") -> Dict[str, Any]:
        """Create a new material."""
        try:
            mat = self.doc.Materials.Add(name)
            if description:
                mat.Description = description
            return {"success": True, "message": f"已创建材质 '{name}'"}
        except Exception as e:
            return {"success": False, "message": f"创建材质失败: {e}"}

    @require_document
    def get_materials(self) -> List[Dict[str, Any]]:
        """List all materials."""
        mats = []
        try:
            for i in range(self.doc.Materials.Count):
                m = self.doc.Materials.Item(i)
                mats.append({"name": m.Name, "description": m.Description})
        except Exception:
            pass
        return mats

    @require_document
    def set_entity_material(self, handle: str, material_name: str) -> Dict[str, Any]:
        """Assign a material to an entity."""
        ent = self._get_entity(handle)
        if ent is None:
            return {"success": False, "message": f"未找到实体: {handle}"}
        try:
            ent.Material = material_name
            return {"success": True, "message": f"已设置材质 '{material_name}'"}
        except Exception as e:
            return {"success": False, "message": f"设置材质失败: {e}"}

    @require_document
    def set_active_material(self, name: str) -> Dict[str, Any]:
        """Set the default material for new objects."""
        try:
            self.doc.ActiveMaterial = self.doc.Materials.Item(name)
            return {"success": True, "message": f"当前材质: {name}"}
        except Exception as e:
            return {"success": False, "message": f"设置材质失败: {e}"}

    # ── Linetypes ─────────────────────────────────────────

    @require_document
    def load_linetype(self, name: str, filename: str) -> Dict[str, Any]:
        """Load a linetype from a .lin file."""
        try:
            self.doc.Linetypes.Load(name, filename)
            return {"success": True, "message": f"已加载线型 '{name}'"}
        except Exception as e:
            return {"success": False, "message": f"加载线型失败: {e}"}

    @require_document
    def create_linetype(self, name: str) -> Dict[str, Any]:
        """Create a new (empty) linetype."""
        try:
            self.doc.Linetypes.Add(name)
            return {"success": True, "message": f"已创建线型 '{name}'"}
        except Exception as e:
            return {"success": False, "message": f"创建线型失败: {e}"}

    @require_document
    def get_linetypes(self) -> List[Dict[str, Any]]:
        """List all loaded linetypes."""
        lts = []
        try:
            for i in range(self.doc.Linetypes.Count):
                lt = self.doc.Linetypes.Item(i)
                lts.append({
                    "name": lt.Name,
                    "description": lt.Description,
                    "pattern_length": lt.PatternLength,
                })
        except Exception:
            pass
        return lts

    # ── Dictionaries ──────────────────────────────────────

    @require_document
    def create_dictionary(self, name: str) -> Dict[str, Any]:
        """Create a named dictionary."""
        try:
            self.doc.Dictionaries.Add(name)
            return {"success": True, "message": f"已创建字典 '{name}'"}
        except Exception as e:
            return {"success": False, "message": f"创建字典失败: {e}"}

    @require_document
    def get_dictionaries(self) -> List[str]:
        """List all named dictionaries."""
        names = []
        try:
            for i in range(self.doc.Dictionaries.Count):
                names.append(self.doc.Dictionaries.Item(i).Name)
        except Exception:
            pass
        return names

    # ── Registered Applications ───────────────────────────

    @require_document
    def create_registered_application(self, name: str) -> Dict[str, Any]:
        """Create a registered application ID (required for XData)."""
        try:
            self.doc.RegisteredApplications.Add(name)
            return {"success": True, "message": f"已注册应用 '{name}'"}
        except Exception as e:
            return {"success": False, "message": f"注册应用失败: {e}"}

    @require_document
    def get_registered_applications(self) -> List[str]:
        """List all registered application IDs."""
        names = []
        try:
            for i in range(self.doc.RegisteredApplications.Count):
                names.append(self.doc.RegisteredApplications.Item(i).Name)
        except Exception:
            pass
        return names

    # ── Preferences ───────────────────────────────────────

    def _get_pref_object(self, pref_path: str):
        """Navigate the Preferences object tree. e.g., 'Display.CursorSize'."""
        obj = self.acad.Preferences
        parts = pref_path.split('.')
        for part in parts[:-1]:
            obj = getattr(obj, part)
        return obj, parts[-1]

    def get_preference(self, pref_path: str) -> Any:
        """Read a single preference value. e.g., get_preference('Display.CursorSize')."""
        self._ensure_connected()
        try:
            obj, prop = self._get_pref_object(pref_path)
            return getattr(obj, prop)
        except Exception as e:
            return f"获取设置失败: {e}"

    def set_preference(self, pref_path: str, value: Any) -> Dict[str, Any]:
        """Set a single preference value. e.g., set_preference('Display.CursorSize', 100)."""
        self._ensure_connected()
        try:
            obj, prop = self._get_pref_object(pref_path)
            setattr(obj, prop, value)
            return {"success": True, "message": f"已设置 {pref_path} = {value}"}
        except Exception as e:
            return {"success": False, "message": f"设置失败: {e}"}

    def get_preferences_display(self) -> Dict[str, Any]:
        """Get display preferences."""
        self._ensure_connected()
        d = self.acad.Preferences.Display
        return {
            "cursor_size": d.CursorSize,
            "display_layout_tabs": d.DisplayLayoutTabs,
            "display_screen_menu": d.DisplayScreenMenu,
            "display_scroll_bars": d.DisplayScrollBars,
            "max_autocad_window": d.MaxAutoCADwindow,
        }

    def get_preferences_drafting(self) -> Dict[str, Any]:
        """Get drafting preferences."""
        self._ensure_connected()
        d = self.acad.Preferences.Drafting
        return {
            "auto_snap_marker": d.AutoSnapMarker,
            "auto_snap_magnet": d.AutoSnapMagnet,
            "auto_snap_tool_tip": d.AutoSnapToolTip,
            "auto_snap_aperture_size": d.AutoSnapApertureSize,
            "auto_snap_marker_size": d.AutoSnapMarkerSize,
            "auto_track_tool_tip": d.AutoTrackToolTip,
            "polar_tracking_vector": d.PolarTrackingVector,
        }

    def get_preferences_files(self) -> Dict[str, Any]:
        """Get file path preferences."""
        self._ensure_connected()
        f = self.acad.Preferences.Files
        return {
            "support_path": f.SupportPath,
            "auto_save_path": f.AutoSavePath,
            "log_file_path": f.LogFilePath,
            "temp_file_path": f.TempFilePath,
            "menu_file": f.MenuFile,
            "help_file_path": f.HelpFilePath,
            "print_spool_dir": f.PrintSpoolDir,
        }

    def get_preferences_opensave(self) -> Dict[str, Any]:
        """Get open/save preferences."""
        self._ensure_connected()
        o = self.acad.Preferences.OpenSave
        return {
            "auto_save_interval": o.AutoSaveInterval,
            "create_backup": o.CreateBackup,
            "incremental_save_percent": o.IncrementalSavePercent,
            "log_file_on": o.LogFileOn,
            "save_preview_thumbnail": o.SavePreviewThumbnail,
            "show_full_path_in_title": o.ShowFullPathInTitle,
        }

    def get_preferences_selection(self) -> Dict[str, Any]:
        """Get selection preferences."""
        self._ensure_connected()
        s = self.acad.Preferences.Selection
        return {
            "pick_first": s.Pickfirst,
            "pick_add": s.PickAdd,
            "pick_auto": s.PickAuto,
            "pick_drag": s.PickDrag,
            "pick_box_size": s.PickBoxSize,
            "display_grips": s.DisplayGrips,
            "display_grips_within_blocks": s.DisplayGripsWithinBlocks,
            "grip_size": s.GripSize,
            "pick_group": s.PickGroup,
        }

    def get_preferences_system(self) -> Dict[str, Any]:
        """Get system preferences."""
        self._ensure_connected()
        s = self.acad.Preferences.System
        return {
            "beep_on_error": s.BeepOnError,
            "single_document_mode": s.SingleDocumentMode,
            "enable_startup_dialog": s.EnableStartupDialog,
            "load_acad_lsp_in_all_docs": s.LoadAcadLspInAllDocuments,
        }

    def get_preferences_user(self) -> Dict[str, Any]:
        """Get user preferences."""
        self._ensure_connected()
        u = self.acad.Preferences.User
        return {
            "keyboard_priority": u.KeyboardPriority,
            "keyboard_accelerator": u.KeyboardAccelerator,
            "adc_insert_units_default_source": u.ADCInsertUnitsDefaultSource,
            "adc_insert_units_default_target": u.ADCInsertUnitsDefaultTarget,
        }

    # ── Utility / Geometry ────────────────────────────────

    @require_document
    def polar_point(self, x: float, y: float, z: float,
                     angle_deg: float, distance: float) -> List[float]:
        """Calculate point at angle and distance from origin (COM Utility method)."""
        pt = to_variant_point(x, y, z)
        angle_rad = angle_deg * math.pi / 180.0
        result = self.doc.Utility.PolarPoint(pt, angle_rad, distance)
        return [float(result[0]), float(result[1]), float(result[2])]

    @require_document
    def translate_coordinates(self, x: float, y: float, z: float,
                               from_cs: int = 0, to_cs: int = 1) -> List[float]:
        """Translate between coordinate systems. 0=WCS, 1=UCS, 2=DCS, 3=PSDCS, 4=OCS."""
        pt = to_variant_point(x, y, z)
        result = self.doc.Utility.TranslateCoordinates(pt, from_cs, to_cs, 0)
        return [float(result[0]), float(result[1]), float(result[2])]

    @require_document
    def angle_from_xaxis(self, p1: List[float], p2: List[float]) -> float:
        """Get angle between line p1-p2 and the X axis (in degrees)."""
        pt1 = to_variant_point(p1[0], p1[1], p1[2] if len(p1)>2 else 0)
        pt2 = to_variant_point(p2[0], p2[1], p2[2] if len(p2)>2 else 0)
        rads = self.doc.Utility.AngleFromXAxis(pt1, pt2)
        return rads * 180.0 / math.pi

    @require_document
    def angle_to_real(self, angle_str: str, unit: int = 0) -> float:
        """Parse angle string to radians. unit: 0=degrees, 1=deg/min/sec, 2=grads, 3=radians."""
        return self.doc.Utility.AngleToReal(angle_str, unit)

    @require_document
    def angle_to_string(self, angle_rad: float, unit: int = 0,
                         precision: int = 2) -> str:
        """Format angle in radians to string."""
        return self.doc.Utility.AngleToString(angle_rad, unit, precision)

    @require_document
    def distance_to_real(self, dist_str: str, unit: int = 0) -> float:
        """Parse distance string. unit: 0=decimal, 1=engineering, 2=architectural, 3=fractional."""
        return self.doc.Utility.DistanceToReal(dist_str, unit)

    @require_document
    def real_to_string(self, value: float, unit: int = 0,
                        precision: int = 2) -> str:
        """Format real number to string using current units."""
        return self.doc.Utility.RealToString(value, unit, precision)

    @require_document
    def create_typed_array(self, array_type: int,
                            values: List[Any]) -> Any:
        """Create a typed COM array."""
        return self.doc.Utility.CreateTypedArray(array_type, values)

    # ── Selection Enhancements ────────────────────────────

    @require_document
    def select_on_screen(self, ss_name: str = "MCP_SCR_SS",
                           filter_type: Optional[List[int]] = None,
                           filter_data: Optional[List[Any]] = None) -> Dict[str, Any]:
        """Select entities by prompting user on screen."""
        ss = self.create_selection_set(ss_name)
        try:
            if filter_type:
                ft = win32com.client.VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_I2, filter_type)
                fd = win32com.client.VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_VARIANT, filter_data)
                ss.SelectOnScreen(ft, fd)
            else:
                ss.SelectOnScreen()
            handles = [ent.Handle for ent in ss]
            return {"success": True, "count": ss.Count, "handles": handles}
        except Exception as e:
            return {"success": False, "message": f"屏幕选择失败: {e}"}

    @require_document
    def select_by_polygon(self, mode: int, points: List[float],
                            ss_name: str = "MCP_POLY_SS") -> Dict[str, Any]:
        """Select by polygon. Mode: 2=Fence, 6=WindowPolygon, 7=CrossingPolygon."""
        ss = self.create_selection_set(ss_name)
        try:
            pts = win32com.client.VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_R8, points)
            ss.SelectByPolygon(mode, pts)
            handles = [ent.Handle for ent in ss]
            return {"success": True, "count": ss.Count, "handles": handles}
        except Exception as e:
            return {"success": False, "message": f"多边形选择失败: {e}"}

    @require_document
    def select_at_point(self, x: float, y: float, z: float = 0.0,
                          ss_name: str = "MCP_PT_SS") -> Dict[str, Any]:
        """Select entities at a specific point."""
        ss = self.create_selection_set(ss_name)
        try:
            ss.SelectAtPoint(to_variant_point(x, y, z))
            handles = [ent.Handle for ent in ss]
            return {"success": True, "count": ss.Count, "handles": handles}
        except Exception as e:
            return {"success": False, "message": f"点选择失败: {e}"}

    @require_document
    def selection_erase(self, ss_name: str) -> Dict[str, Any]:
        """Erase all entities in a selection set."""
        try:
            ss = self.doc.SelectionSets.Item(ss_name)
            ss.Erase()
            return {"success": True, "message": f"已删除选择集 {ss_name} 中的实体"}
        except Exception as e:
            return {"success": False, "message": f"删除失败: {e}"}

    @require_document
    def selection_highlight(self, ss_name: str, state: bool) -> Dict[str, Any]:
        """Highlight or unhighlight all entities in a selection set."""
        try:
            ss = self.doc.SelectionSets.Item(ss_name)
            ss.Highlight(state)
            return {"success": True, "message": f"已{'高亮' if state else '取消高亮'}"}
        except Exception as e:
            return {"success": False, "message": f"高亮失败: {e}"}

    @require_document
    def selection_clear(self, ss_name: str) -> Dict[str, Any]:
        """Clear all items from a selection set."""
        try:
            ss = self.doc.SelectionSets.Item(ss_name)
            ss.Clear()
            return {"success": True, "message": "已清空选择集"}
        except Exception as e:
            return {"success": False, "message": f"清空失败: {e}"}

    # ── Application Methods ───────────────────────────────

    def get_acad_state(self) -> Dict[str, Any]:
        """Check if AutoCAD is idle."""
        self._ensure_connected()
        try:
            state = self.acad.GetAcadState()
            return {"is_quiescent": state.IsQuiescent,
                    "is_loaded": state.IsApplicationLoaded}
        except Exception as e:
            return {"error": str(e)}

    def get_app_info(self) -> Dict[str, Any]:
        """Get AutoCAD application information."""
        self._ensure_connected()
        try:
            return {
                "caption": self.acad.Caption,
                "full_name": self.acad.FullName,
                "path": self.acad.Path,
                "version": self.acad.Version,
                "locale_id": self.acad.LocaleId,
            }
        except Exception as e:
            return {"error": str(e)}

    def load_arx(self, module_name: str) -> Dict[str, Any]:
        """Load an ObjectARX application."""
        self._ensure_connected()
        try:
            self.acad.LoadArx(module_name)
            return {"success": True, "message": f"已加载ARX: {module_name}"}
        except Exception as e:
            return {"success": False, "message": f"加载ARX失败: {e}"}

    def unload_arx(self, module_name: str) -> Dict[str, Any]:
        """Unload an ObjectARX application."""
        self._ensure_connected()
        try:
            self.acad.UnloadArx(module_name)
            return {"success": True, "message": f"已卸载ARX: {module_name}"}
        except Exception as e:
            return {"success": False, "message": f"卸载ARX失败: {e}"}

    def list_arx(self) -> List[str]:
        """List loaded ObjectARX applications."""
        self._ensure_connected()
        try:
            return list(self.acad.ListArx())
        except Exception:
            return []

    # ── Security ──────────────────────────────────────────

    @require_document
    def set_drawing_password(self, password: str) -> Dict[str, Any]:
        """Set a password on the current drawing."""
        try:
            self.doc.SecurityOptions.Password = password
            return {"success": True, "message": "已设置图纸密码"}
        except Exception as e:
            return {"success": False, "message": f"设置密码失败: {e}"}

    # ── File Dependencies ─────────────────────────────────

    @require_document
    def get_file_dependencies(self) -> List[Dict[str, Any]]:
        """List file dependencies of the drawing."""
        deps = []
        try:
            for i in range(self.doc.FileDependencies.Count):
                dep = self.doc.FileDependencies.Item(i)
                deps.append({
                    "name": dep.Name,
                    "full_path": dep.FullPathName,
                    "found_path": dep.FoundPath,
                    "type": dep.Type,
                })
        except Exception:
            pass
        return deps

    # ── SummaryInfo Write ─────────────────────────────────

    @require_document
    def set_summary_info(self, **kwargs) -> Dict[str, Any]:
        """Set summary info fields (title, subject, author, keywords, comments)."""
        try:
            si = self.doc.SummaryInfo
            for key, value in kwargs.items():
                if hasattr(si, key):
                    setattr(si, key, str(value))
            return {"success": True, "message": "已更新图纸属性"}
        except Exception as e:
            return {"success": False, "message": f"更新属性失败: {e}"}

    # ── Active Space ──────────────────────────────────────

    @require_document
    def get_active_space(self) -> Dict[str, Any]:
        """Get active space (0=Model, 1=Paper)."""
        try:
            space = self.doc.ActiveSpace
            return {"active_space": space,
                    "name": "模型空间" if space == 0 else "图纸空间"}
        except Exception as e:
            return {"error": str(e)}

    @require_document
    def set_active_space(self, space: int) -> Dict[str, Any]:
        """Set active space. 0=Model, 1=Paper."""
        try:
            self.doc.ActiveSpace = space
            return {"success": True,
                    "message": f"已切换到{'模型空间' if space==0 else '图纸空间'}"}
        except Exception as e:
            return {"success": False, "message": f"切换空间失败: {e}"}

    @require_document
    def get_mspace(self) -> bool:
        """Check if editing model space through floating viewport."""
        return self.doc.MSpace

    # ── Transform ─────────────────────────────────────────

    @require_document
    def transform_entity(self, handle: str,
                           matrix: List[List[float]]) -> Dict[str, Any]:
        """Apply a 4x4 transformation matrix to an entity."""
        ent = self._get_entity(handle)
        if ent is None:
            return {"success": False, "message": f"未找到实体: {handle}"}
        try:
            flat = []
            for row in matrix:
                flat.extend(row)
            mat = to_variant_array(flat)
            ent.TransformBy(mat)
            return {"success": True, "message": f"已变换实体 {handle}"}
        except Exception as e:
            return {"success": False, "message": f"变换失败: {e}"}

    @require_document
    def regen(self, which: str = "all") -> Dict[str, Any]:
        """Regenerate the drawing. which: 'all' or 'active'."""
        try:
            if which == "active":
                self.doc.Regen(0)
            else:
                self.doc.Regen(1)
            return {"success": True, "message": "已重生成"}
        except Exception as e:
            return {"success": False, "message": f"重生成失败: {e}"}


# ── Module-level singleton ──────────────────────────────────────

_controller = None

def get_controller() -> CADController:
    global _controller
    if _controller is None:
        _controller = CADController()
    return _controller
