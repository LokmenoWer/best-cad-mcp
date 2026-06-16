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
import os
import time
from typing import Optional, List, Tuple, Dict, Any, Union
from contextlib import contextmanager
from src.cad_utils import DetailLevel, com_get, com_set

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
                    "message": "Unable to connect to AutoCAD. Make sure AutoCAD is running."}
        if self.acad.Documents.Count == 0:
            return {"success": False,
                    "message": "No drawing is open. Create or open a drawing first."}
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
        deadline = time.time() + 6.0
        last_error = None
        while True:
            try:
                self.acad = win32com.client.GetActiveObject("AutoCAD.Application")
                if visible:
                    self.acad.Visible = True
                if self.acad.Documents.Count > 0:
                    self.doc = self.acad.ActiveDocument
                logger.info("已连接到 AutoCAD")
                return True
            except Exception as e:
                last_error = e
                if time.time() >= deadline:
                    break
                time.sleep(0.5)
        logger.error(f"连接AutoCAD失败: {last_error}")
        return False

    def _ensure_connected(self):
        """Lazy-connect if not already connected."""
        if self.acad is None:
            self.connect()
        if self.acad is not None:
            try:
                if self.acad.Documents.Count > 0:
                    self.doc = self.acad.ActiveDocument
            except Exception:
                self.acad = None
                self.doc = None

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

    def create_drawing(self, template: Optional[str] = None) -> Dict[str, Any]:
        self._ensure_connected()
        if self.acad is None:
            return {"success": False,
                    "message": "Unable to connect to AutoCAD. Please make sure AutoCAD is running."}
        try:
            if template:
                doc = self.acad.Documents.Add(template)
            else:
                doc = self.acad.Documents.Add()
            self.doc = doc
            try:
                name = doc.Name
            except Exception:
                try:
                    self.doc = self.acad.ActiveDocument
                    name = self.doc.Name
                except Exception:
                    name = ""
            return {"success": True, "message": "Created new drawing", "name": name}
        except Exception as e:
            return {"success": False, "message": f"Create failed: {e}"}

    def open_drawing(self, filepath: str, password: Optional[str] = None) -> Dict[str, Any]:
        """Open a drawing even when AutoCAD is only showing the Start tab."""
        self._ensure_connected()
        if self.acad is None:
            return {"success": False,
                    "message": "Unable to connect to AutoCAD. Please make sure AutoCAD is running."}
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
            if format_type == "BMP":
                raise RuntimeError("BMP export through AutoCAD COM can block in this environment; use WMF instead.")
            if format_type == "DWG":
                self.doc.SaveAs(filepath)
            elif format_type == "DXF":
                self._export_with_selection_set(filepath, "DXF")
            elif format_type == "PDF":
                self._export_pdf(filepath)
            elif format_type == "DWF":
                self._export_dwf(filepath)
            else:
                self._export_with_selection_set(filepath, format_type)
            return {"success": True, "message": f"已导出为 {format_type}: {filepath}"}
        except Exception as e:
            return {"success": False, "message": f"导出失败: {e}"}

    def _export_pdf(self, filepath: str) -> None:
        plotters = [
            "DWG To PDF.pc3",
            "AutoCAD PDF (General Documentation).pc3",
            "AutoCAD PDF (High Quality Print).pc3",
        ]
        layout = getattr(self.doc, "ActiveLayout", None)
        previous_config = None
        has_previous_config = False
        if layout is not None:
            try:
                previous_config = layout.ConfigName
                has_previous_config = True
            except Exception:
                pass

        old_vars = {}
        for name, value in {"BACKGROUNDPLOT": 0, "FILEDIA": 0, "CMDDIA": 0}.items():
            try:
                old_vars[name] = self.doc.GetVariable(name)
                self.doc.SetVariable(name, value)
            except Exception:
                pass

        last_error = None
        try:
            for plotter in plotters:
                try:
                    if layout is not None:
                        try:
                            layout.ConfigName = plotter
                        except Exception:
                            pass
                    try:
                        if os.path.exists(filepath):
                            os.remove(filepath)
                    except Exception:
                        pass
                    ok = self.doc.Plot.PlotToFile(filepath, plotter)
                    if ok is False:
                        raise RuntimeError(f"PlotToFile returned False for {plotter}")
                    for _ in range(10):
                        if os.path.exists(filepath) and os.path.getsize(filepath) > 0:
                            return
                        time.sleep(0.2)
                    return
                except Exception as e:
                    last_error = e
            raise RuntimeError(last_error or "no PDF plotter available")
        finally:
            for name, value in old_vars.items():
                try:
                    self.doc.SetVariable(name, value)
                except Exception:
                    pass
            if layout is not None and has_previous_config:
                try:
                    layout.ConfigName = previous_config
                except Exception:
                    pass

    def _export_dwf(self, filepath: str) -> None:
        plotters = [
            "DWF6 ePlot.pc3",
            "DWFx ePlot (XPS Compatible).pc3",
        ]
        root, ext = os.path.splitext(filepath)
        if ext.lower() in {".dwf", ".dwfx"}:
            candidates = [filepath]
        else:
            candidates = [filepath + ".dwf", filepath]

        layout = getattr(self.doc, "ActiveLayout", None)
        previous_config = None
        has_previous_config = False
        if layout is not None:
            try:
                previous_config = layout.ConfigName
                has_previous_config = True
            except Exception:
                pass

        old_vars = {}
        for name, value in {"BACKGROUNDPLOT": 0, "FILEDIA": 0, "CMDDIA": 0}.items():
            try:
                old_vars[name] = self.doc.GetVariable(name)
                self.doc.SetVariable(name, value)
            except Exception:
                pass

        last_error = None
        try:
            for path in dict.fromkeys(candidates):
                for plotter in plotters:
                    try:
                        if layout is not None:
                            try:
                                layout.ConfigName = plotter
                                layout.RefreshPlotDeviceInfo()
                            except Exception:
                                pass
                        try:
                            if os.path.exists(path):
                                os.remove(path)
                        except Exception:
                            pass
                        for attempt in range(5):
                            try:
                                ok = self.doc.Plot.PlotToFile(path)
                                break
                            except Exception as e:
                                if attempt == 4:
                                    raise e
                                time.sleep(1.0 * (attempt + 1))
                        else:
                            ok = self.doc.Plot.PlotToFile(path, plotter)
                        if ok is False:
                            raise RuntimeError(f"PlotToFile returned False for {plotter}")
                        for _ in range(3):
                            if os.path.exists(path) and os.path.getsize(path) > 0:
                                return
                            time.sleep(0.2)
                        return
                    except Exception as e:
                        last_error = e
            raise RuntimeError(last_error or "no DWF plotter available")
        finally:
            for name, value in old_vars.items():
                try:
                    self.doc.SetVariable(name, value)
                except Exception:
                    pass
            if layout is not None and has_previous_config:
                try:
                    layout.ConfigName = previous_config
                except Exception:
                    pass

    def _export_with_selection_set(self, filepath: str, format_type: str) -> None:
        export_path = self._strip_export_extension(filepath, format_type)
        ss_name = "MCP_EXPORT_EMPTY_SS"
        selection_set = None
        try:
            try:
                self.doc.SelectionSets.Item(ss_name).Delete()
            except Exception:
                pass
            selection_set = self.doc.SelectionSets.Add(ss_name)
            if format_type in {"WMF", "BMP"} and self.doc.ModelSpace.Count > 0:
                item = self.doc.ModelSpace.Item(self.doc.ModelSpace.Count - 1)
                selection_set.AddItems(win32com.client.VARIANT(
                    pythoncom.VT_ARRAY | pythoncom.VT_DISPATCH, [item]
                ))
            self.doc.Export(export_path, format_type, selection_set)
        finally:
            if selection_set is not None:
                try:
                    selection_set.Delete()
                except Exception:
                    pass

    def _strip_export_extension(self, filepath: str, format_type: str) -> str:
        root, ext = os.path.splitext(filepath)
        if ext.lower() == f".{format_type.lower()}":
            return root
        return filepath

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
            pt = win32com.client.VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_R8,
                                         [float(x), float(y)])
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
            coords = list(com_get(pline, "Coordinates", []))
            step = 3 if com_get(pline, "ObjectName", "") == "AcDb3dPolyline" else 2
            nv = len(coords) // step
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
            try:
                pt = pline.GetPointAtParam(float(param))
                return {"success": True, "point": [pt[0], pt[1], pt[2]]}
            except Exception:
                coords = list(com_get(pline, "Coordinates", []))
                step = 3 if com_get(pline, "ObjectName", "") == "AcDb3dPolyline" else 2
                index = max(0, min(int(float(param)), (len(coords) // step) - 1))
                start = index * step
                z = coords[start + 2] if step == 3 else 0.0
                return {"success": True, "point": [coords[start], coords[start + 1], z]}
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
            try:
                seg_type = pline.GetSegmentType(int(index))
                types = {0: "line", 1: "arc"}
                seg_name = types.get(seg_type, f"unknown({seg_type})")
            except Exception:
                bulge = 0.0
                try:
                    bulge = float(pline.GetBulge(int(index)))
                except Exception:
                    pass
                seg_name = "arc" if abs(bulge) > 1e-12 else "line"
            return {"success": True, "handle": handle, "index": index, "type": seg_name}
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
        try:
            flat = []
            for p in points:
                flat.extend([float(p[0]), float(p[1]), float(p[2]) if len(p)>2 else 0.0])
            pts_array = to_variant_array(flat)
            mleader = self.doc.ModelSpace.AddMLeader(pts_array, 0)
            if isinstance(mleader, (list, tuple)):
                mleader = next((item for item in mleader if hasattr(item, "Handle")), mleader[0])
            mleader.ContentType = 2  # MText content
            mleader.TextString = text
            return mleader
        except Exception as e:
            return {"success": False, "message": f"多重引线失败: {e}"}

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
        faces = win32com.client.VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_I2,
                                        [int(f) for f in face_list])
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
            tc = None
            candidates = []
            try:
                version = str(com_get(self.acad, "Version", ""))
                major = int(float(version.split()[0]))
                candidates.extend([
                    f"AutoCAD.AcCmColor.{major}",
                    f"AutoCAD.AcCmColor.{major - 1}",
                ])
            except Exception:
                pass
            candidates.extend([f"AutoCAD.AcCmColor.{n}" for n in range(30, 15, -1)])
            candidates.append("AutoCAD.AcCmColor")
            last_error = None
            for prog_id in dict.fromkeys(candidates):
                try:
                    tc = self.acad.GetInterfaceObject(prog_id)
                    break
                except Exception as e:
                    last_error = e
            if tc is None:
                raise last_error or RuntimeError("Unable to create AcCmColor object")
            tc.SetRGB(red, green, blue)
            com_set(ent, "TrueColor", tc)
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
        block_collection = self.doc.Blocks
        count = int(com_get(block_collection, "Count", 0) or 0)
        for i in range(count):
            try:
                blk = block_collection.Item(i)
            except Exception as e:
                logger.warning(f"读取图块定义 {i} 失败: {e}")
                continue
            is_xref = bool(com_get(blk, "IsXRef", False))
            origin = com_get(blk, "Origin", [0, 0, 0])
            try:
                origin = [origin[0], origin[1], origin[2]]
            except Exception:
                origin = [0, 0, 0]
            blocks.append({
                "name": com_get(blk, "Name", ""),
                "count": 0 if is_xref else com_get(blk, "Count", 0),
                "is_layout": bool(com_get(blk, "IsLayout", False)),
                "is_xref": is_xref,
                "origin": origin,
                "path": com_get(blk, "Path", "") if is_xref else "",
            })
        return blocks

    @require_document
    def get_xrefs(self) -> List[Dict[str, Any]]:
        """List external reference block definitions without full block expansion."""
        xrefs = []
        block_collection = self.doc.Blocks
        count = int(com_get(block_collection, "Count", 0) or 0)
        for i in range(count):
            try:
                blk = block_collection.Item(i)
            except Exception as e:
                logger.warning(f"读取外部参照定义 {i} 失败: {e}")
                continue
            if not bool(com_get(blk, "IsXRef", False)):
                continue
            xrefs.append({
                "name": com_get(blk, "Name", ""),
                "path": com_get(blk, "Path", ""),
            })
        return xrefs

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
                        "tag": com_get(attr, "TagString", ""),
                        "value": com_get(attr, "TextString", ""),
                        "prompt": com_get(attr, "PromptString", ""),
                        "height": com_get(attr, "Height", 0),
                        "rotation": com_get(attr, "Rotation", 0) * 180.0 / math.pi,
                        "invisible": com_get(attr, "Invisible", False),
                        "constant": com_get(attr, "Constant", False),
                        "style": com_get(attr, "StyleName", ""),
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
        if self.doc is None:
            self._ensure_connected()
        if self.doc is None:
            return None
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
                new_handles = self._collect_object_handles(offset_obj)
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
            if rows < 1 or cols < 1:
                return {"success": False, "message": "行数和列数必须大于等于 1"}
            result = ent.ArrayRectangular(rows, cols, 1,
                                          row_spacing, col_spacing, 0.0)
            new_handles = self._collect_object_handles(result)
            return {"success": True,
                    "message": f"已矩形阵列实体 {handle}: {rows}x{cols}",
                    "new_handles": new_handles}
        except Exception as e:
            return {"success": False, "message": f"阵列失败: {e}"}

    def _collect_object_handles(self, result: Any) -> List[str]:
        if result is None:
            return []
        objects = []
        if isinstance(result, (list, tuple)):
            objects = list(result)
        else:
            count = getattr(result, "Count", None)
            if isinstance(count, int):
                objects = [result.Item(i) for i in range(count)]
            else:
                objects = [result]

        handles = []
        for obj in objects:
            handle = getattr(obj, "Handle", None)
            if handle:
                handles.append(handle)
        return handles

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
            min_pt, max_pt = ent.GetBoundingBox()
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
                    com_set(ent, "Color", int(value))
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
                "handle": com_get(ent, "Handle", ""),
                "object_name": com_get(ent, "ObjectName", ""),
                "object_id": com_get(ent, "ObjectID", None),
                "layer": com_get(ent, "Layer", "0"),
                "color": com_get(ent, "Color", 256),
                "linetype": com_get(ent, "Linetype", "ByLayer"),
                "linetypescale": com_get(ent, "LinetypeScale", None),
                "lineweight": com_get(ent, "Lineweight", None),
                "visible": com_get(ent, "Visible", None),
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
            result = ent.GetXData(app_name or None)
            if result is None:
                xdata_type, xdata_value = [], []
            else:
                xdata_type, xdata_value = result
            xdata_type = list(xdata_type or [])
            xdata_value = list(xdata_value or [])
            if not xdata_type:
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
        except BaseException:
            try:
                ent.CreateExtensionDictionary()
                return ent.GetExtensionDictionary()
            except BaseException:
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

    def _retry_com_call(self, func, attempts: int = 10, delay: float = 0.35):
        last_error = None
        for attempt in range(attempts):
            try:
                return func()
            except Exception as e:
                last_error = e
                if attempt == attempts - 1:
                    break
                time.sleep(delay)
        raise last_error

    @require_document
    def create_selection_set(self, name: str) -> Any:
        try:
            self._retry_com_call(lambda: self.doc.SelectionSets.Item(name).Delete())
        except Exception:
            pass
        return self._retry_com_call(lambda: self.doc.SelectionSets.Add(name))

    @require_document
    def select_by_window(self, pt1: List[float], pt2: List[float],
                         ss_name: str = "MCP_TEMP_SS") -> Dict[str, Any]:
        ss = self.create_selection_set(ss_name)
        self._retry_com_call(lambda: ss.Select(0, to_variant_point(*pt1), to_variant_point(*pt2)))
        handles = [ent.Handle for ent in ss]
        return {"success": True, "count": ss.Count, "handles": handles, "name": ss_name}

    @require_document
    def select_by_crossing(self, pt1: List[float], pt2: List[float],
                           ss_name: str = "MCP_TEMP_SS") -> Dict[str, Any]:
        ss = self.create_selection_set(ss_name)
        self._retry_com_call(lambda: ss.Select(1, to_variant_point(*pt1), to_variant_point(*pt2)))
        handles = [ent.Handle for ent in ss]
        return {"success": True, "count": ss.Count, "handles": handles, "name": ss_name}

    @require_document
    def select_all(self, ss_name: str = "MCP_TEMP_SS",
                   max_handles: int = 200,
                   max_com_selection: int = 1000) -> Dict[str, Any]:
        model_space = self.doc.ModelSpace
        entity_count = int(com_get(model_space, "Count", 0) or 0)
        if entity_count > max_com_selection:
            handles = []
            sample_count = min(entity_count, max_handles)
            for i in range(sample_count):
                try:
                    ent = model_space.Item(i)
                    handle = com_get(ent, "Handle", "")
                    if handle:
                        handles.append(handle)
                except Exception as e:
                    logger.warning(f"读取模型空间实体 {i} 失败: {e}")
            return {
                "success": True,
                "count": entity_count,
                "handles": handles,
                "name": None,
                "selected": False,
                "truncated": entity_count > len(handles),
                "message": "Drawing is large; returned handle sample without creating a global selection set.",
            }

        ss = self.create_selection_set(ss_name)
        self._retry_com_call(lambda: ss.Select(5))
        handles = []
        for i, ent in enumerate(ss):
            if i >= max_handles:
                break
            handles.append(com_get(ent, "Handle", ""))
        return {
            "success": True,
            "count": ss.Count,
            "handles": handles,
            "name": ss_name,
            "selected": True,
            "truncated": ss.Count > len(handles),
        }

    # ── Layer Management ───────────────────────────────────

    def _set_layer_color(self, layer, color_idx: int) -> Tuple[bool, Optional[str]]:
        """Set a layer ACI color with fallbacks for older AutoCAD COM bindings."""
        color_idx = int(color_idx)
        variants = [color_idx]
        for vt in (getattr(pythoncom, "VT_I2", None), getattr(pythoncom, "VT_I4", None)):
            if vt is not None:
                try:
                    variants.append(win32com.client.VARIANT(vt, color_idx))
                except Exception:
                    pass

        last_error = None
        for value in variants:
            try:
                layer.Color = value
                return True, None
            except Exception as e:
                last_error = e

        return False, str(last_error)

    @require_document
    def create_layer(self, name: str, color_idx: int = 7,
                     linetype: str = "Continuous") -> Dict[str, Any]:
        try:
            try:
                layer = self.doc.Layers.Item(name)
                color_set = True
                color_warning = None
                if color_idx != 7:
                    color_set, color_warning = self._set_layer_color(layer, color_idx)
                return {"success": True, "message": f"图层 '{name}' 已存在",
                        "existing": True, "color_set": color_set,
                        "color_warning": color_warning}
            except Exception:
                layer = self.doc.Layers.Add(name)
                color_set, color_warning = self._set_layer_color(layer, color_idx)
                try:
                    layer.Linetype = linetype
                except Exception:
                    pass
                return {"success": True, "message": f"已创建图层 '{name}'",
                        "existing": False, "color_set": color_set,
                        "color_warning": color_warning}
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
                "name": com_get(layer, "Name", ""),
                "color": com_get(layer, "Color", 7),
                "linetype": com_get(layer, "Linetype", "Continuous"),
                "lineweight": com_get(layer, "Lineweight", None),
                "is_frozen": com_get(layer, "Freeze", False),
                "is_locked": com_get(layer, "Lock", False),
                "is_on": com_get(layer, "LayerOn", True),
                "is_plottable": com_get(layer, "Plottable", True),
                "description": com_get(layer, "Description", ""),
                "handle": com_get(layer, "Handle", ""),
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

    def _lisp_string(self, value: str) -> str:
        return str(value).replace("\\", "\\\\").replace('"', '\\"')

    @require_document
    def isolate_layer(self, name: str) -> Dict[str, Any]:
        try:
            self.doc.Layers.Item(name)
        except Exception as e:
            return {"success": False, "message": f"图层不存在或不可访问: {e}"}

        current = self.set_current_layer(name)
        if not current.get("success", False):
            return current

        target = self._lisp_string(name)
        lisp = (
            '(vl-load-com)'
            '(setq doc (vla-get-ActiveDocument (vlax-get-acad-object)))'
            f'(setq target (strcase "{target}"))'
            '(vlax-for lay (vla-get-Layers doc)'
            '  (if (/= (strcase (vla-get-Name lay)) target)'
            '    (vl-catch-all-apply \'vla-put-LayerOn (list lay :vlax-false))'
            '    (vl-catch-all-apply \'vla-put-LayerOn (list lay :vlax-true))'
            '  )'
            ')'
            '(princ)'
        )
        result = self.run_lisp(lisp)
        if not result.get("success", False):
            return {"success": False, "message": result.get("message", "隔离图层失败")}
        return {"success": True, "message": f"已隔离图层 '{name}'", "name": name}

    @require_document
    def unisolate_layers(self) -> Dict[str, Any]:
        lisp = (
            '(vl-load-com)'
            '(setq doc (vla-get-ActiveDocument (vlax-get-acad-object)))'
            '(vlax-for lay (vla-get-Layers doc)'
            '  (if (= (vla-get-Freeze lay) :vlax-false)'
            '    (vl-catch-all-apply \'vla-put-LayerOn (list lay :vlax-true))'
            '  )'
            ')'
            '(princ)'
        )
        result = self.run_lisp(lisp)
        if not result.get("success", False):
            return {"success": False, "message": result.get("message", "取消隔离失败")}
        return {"success": True, "message": "已打开所有未冻结图层"}

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
                "name": com_get(s, "Name", ""),
                "font_file": com_get(s, "FontFile", ""),
                "big_font_file": com_get(s, "BigFontFile", ""),
                "height": com_get(s, "Height", 0),
                "width": com_get(s, "Width", 1),
                "oblique_angle": com_get(s, "ObliqueAngle", 0),
                "is_vertical": com_get(s, "IsVertical", False),
            })
        return styles

    @staticmethod
    def _looks_like_font_file(font: str) -> bool:
        clean = str(font or "").strip().strip('"')
        _, ext = os.path.splitext(clean)
        return ext.lower() in {".shx", ".ttf", ".ttc", ".otf", ".fon"}

    @staticmethod
    def _font_file_candidates(font: str) -> List[str]:
        clean = str(font or "").strip().strip('"')
        if not clean:
            return []
        candidates = [clean]
        _, ext = os.path.splitext(clean)
        if not ext and "\\" not in clean and "/" not in clean:
            candidates.append(f"{clean}.shx")
        return list(dict.fromkeys(candidates))

    def _get_text_style_font_defaults(self, style) -> Tuple[str, bool, bool, int, int]:
        for candidate in (style, getattr(self.doc, "ActiveTextStyle", None)):
            if candidate is None:
                continue
            try:
                font_args = candidate.GetFont()
            except Exception:
                continue
            if isinstance(font_args, (list, tuple)) and len(font_args) >= 5:
                typeface, bold, italic, charset, pitch_and_family = font_args[:5]
                return (
                    str(typeface or "Arial"),
                    bool(bold),
                    bool(italic),
                    int(charset or 0),
                    int(pitch_and_family or 34),
                )
        return ("Arial", False, False, 0, 34)

    def _set_text_style_font_file(self, style, font: str) -> str:
        last_error = None
        for candidate in self._font_file_candidates(font):
            try:
                style.FontFile = candidate
                return candidate
            except Exception as exc:
                last_error = exc
        raise RuntimeError(last_error or "no font file candidate")

    def _apply_text_style_font(self, style, font: str) -> Tuple[str, str]:
        clean = str(font or "").strip()
        if not clean:
            return ("unchanged", "")
        if self._looks_like_font_file(clean):
            return ("FontFile", self._set_text_style_font_file(style, clean))

        _, bold, italic, charset, pitch_and_family = self._get_text_style_font_defaults(style)
        try:
            style.SetFont(clean, bold, italic, charset, pitch_and_family)
            return ("SetFont", clean)
        except Exception as setfont_error:
            try:
                font_file = self._set_text_style_font_file(style, clean)
                return ("FontFile", font_file)
            except Exception as fontfile_error:
                raise RuntimeError(
                    f"SetFont failed: {setfont_error}; FontFile fallback failed: {fontfile_error}"
                ) from setfont_error

    @require_document
    def create_text_style(self, name: str, font: str = "Arial",
                          height: float = 0.0, width: float = 1.0) -> Dict[str, Any]:
        try:
            name = str(name or "").strip()
            if not name:
                return {"success": False, "message": "创建文字样式失败: 样式名称不能为空"}
            height = float(height)
            width = float(width)
            if height < 0:
                return {"success": False, "message": "创建文字样式失败: height 不能为负数"}
            if width <= 0:
                return {"success": False, "message": "创建文字样式失败: width 必须大于 0"}
            style = self.doc.TextStyles.Add(name)
            font_method, font_value = self._apply_text_style_font(style, font)
            style.Height = height
            style.Width = width
            message = f"已创建文字样式 '{name}'"
            if font_method != "unchanged":
                message += f" ({font_method}: {font_value})"
            return {"success": True, "message": message}
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
            new_style = self.doc.DimStyles.Add(new_name)
            new_style.CopyFrom(src)
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
            # Get current view center, offset by (dx, dy), then ZoomCenter.
            current_view = com_get(self.doc, "ActiveView", None)
            if current_view is None:
                current_view = com_get(self.doc, "ActiveViewport", None)
            if current_view is None:
                return {"success": False, "message": "No active view or viewport is available"}
            current_center = com_get(current_view, "Center", [0, 0])
            new_x = float(current_center[0]) + float(dx)
            new_y = float(current_center[1]) + float(dy)
            height = com_get(current_view, "Height", 100)
            self.doc.Application.ZoomCenter(
                to_variant_point(new_x, new_y, 0), height)
            return {"success": True, "message": f"已平移 ({dx}, {dy})"}
        except Exception as e:
            return {"success": False, "message": f"平移失败: {e}"}

    @require_document
    def get_current_view(self) -> Dict[str, Any]:
        try:
            view = com_get(self.doc, "ActiveView", None)
            if view is None:
                view = com_get(self.doc, "ActiveViewport", None)
            if view is None:
                return {"error": "No active view or viewport is available"}
            return {
                "center": list(com_get(view, "Center", [])),
                "height": com_get(view, "Height", None),
                "width": com_get(view, "Width", None),
                "target": list(com_get(view, "Target", [])),
                "direction": list(com_get(view, "Direction", [])),
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
        original = com_get(ent, "Color", 256)
        com_set(ent, "Color", int(color))
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
        com_set(ent, "Color", int(original_color))
        ent.Update()
        return {"success": True, "message": f"已重置颜色"}

    # ── Scanning ───────────────────────────────────────────

    @staticmethod
    def _scan_point(value: Any) -> Optional[List[float]]:
        try:
            return [
                round(float(value[0]), 4),
                round(float(value[1]), 4),
                round(float(value[2]) if len(value) > 2 else 0.0, 4),
            ]
        except Exception:
            return None

    @staticmethod
    def _scan_bbox(ent: Any) -> Optional[List[float]]:
        try:
            min_pt, max_pt = ent.GetBoundingBox()
            return [
                round(float(min_pt[0]), 4),
                round(float(min_pt[1]), 4),
                round(float(max_pt[0]), 4),
                round(float(max_pt[1]), 4),
            ]
        except Exception:
            return None

    @require_document
    def scan_model_space(self, max_entities: int = 10000,
                         detail_level: str = DetailLevel.MINIMAL,
                         include_bounding_boxes: bool = True) -> Dict[str, Any]:
        """Scan model space with a large-drawing friendly default.

        minimal: handle/type/layer, optionally bbox.
        standard/full: also read common properties and simple geometry.
        """
        level = (detail_level or DetailLevel.MINIMAL).lower()
        if level not in {DetailLevel.MINIMAL, DetailLevel.STANDARD, DetailLevel.FULL}:
            level = DetailLevel.MINIMAL

        model_space = self.doc.ModelSpace
        total_available = int(com_get(model_space, "Count", 0) or 0)
        limit = total_available if max_entities is None else max(0, int(max_entities))
        count = min(total_available, limit)
        entities = []
        type_stats = {}
        read_common_properties = level in {DetailLevel.STANDARD, DetailLevel.FULL}
        read_geometry = level in {DetailLevel.STANDARD, DetailLevel.FULL}

        for i in range(count):
            try:
                ent = model_space.Item(i)
                obj_name = com_get(ent, "ObjectName", "Unknown")
                type_stats[obj_name] = type_stats.get(obj_name, 0) + 1
                info = {
                    "index": i,
                    "handle": com_get(ent, "Handle", ""),
                    "type": obj_name,
                    "name": obj_name.replace("AcDb", ""),
                    "layer": com_get(ent, "Layer", "0"),
                }
                if read_common_properties:
                    info["color"] = com_get(ent, "Color", 256)
                    info["linetype"] = com_get(ent, "Linetype", "ByLayer")
                if include_bounding_boxes:
                    bbox = self._scan_bbox(ent)
                    if bbox is not None:
                        info["bbox"] = bbox
                if read_geometry:
                    typed_ent = ent
                    if level == DetailLevel.FULL:
                        try:
                            typed_ent = win32com.client.Dispatch(ent)
                        except Exception:
                            pass
                    if obj_name == "AcDbLine":
                        start = self._scan_point(com_get(typed_ent, "StartPoint", None))
                        end = self._scan_point(com_get(typed_ent, "EndPoint", None))
                        if start:
                            info["start"] = start
                        if end:
                            info["end"] = end
                        info["length"] = round(float(com_get(typed_ent, "Length", 0) or 0), 4)
                    elif obj_name == "AcDbCircle":
                        center = self._scan_point(com_get(typed_ent, "Center", None))
                        if center:
                            info["center"] = center
                        info["radius"] = round(float(com_get(typed_ent, "Radius", 0) or 0), 4)
                    elif obj_name == "AcDbArc":
                        center = self._scan_point(com_get(typed_ent, "Center", None))
                        if center:
                            info["center"] = center
                        info["radius"] = round(float(com_get(typed_ent, "Radius", 0) or 0), 4)
                        info["start_angle"] = round(float(com_get(typed_ent, "StartAngle", 0) or 0), 4)
                        info["end_angle"] = round(float(com_get(typed_ent, "EndAngle", 0) or 0), 4)
                    elif obj_name in ("AcDbText", "AcDbMText"):
                        info["text"] = com_get(typed_ent, "TextString", "")
                    elif "Polyline" in obj_name:
                        info["length"] = round(float(com_get(typed_ent, "Length", 0) or 0), 4)
                        info["closed"] = bool(com_get(typed_ent, "Closed", False))
                entities.append(info)
            except Exception as e:
                entities.append({"index": i, "error": str(e)})
        return {
            "entities": entities,
            "total": count,
            "total_available": total_available,
            "scanned": count,
            "truncated": count < total_available,
            "detail_level": level,
            "type_stats": type_stats,
        }

    @require_document
    def scan_entities_in_area(self, xmin: float, ymin: float,
                               xmax: float, ymax: float) -> List[Dict[str, Any]]:
        """Scan entities whose handle we can find within a bounding box.
        Uses selection window for efficiency."""
        ss = self.create_selection_set("MCP_AREA_SCAN")
        self._retry_com_call(
            lambda: ss.Select(0, to_variant_point(xmin, ymin, 0), to_variant_point(xmax, ymax, 0))
        )
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

    def _wait_quiescent(self, timeout: float = 5.0) -> bool:
        """Block until AutoCAD is idle (no command in progress), or timeout."""
        import time
        deadline = time.time() + timeout
        try:
            state = self.acad.GetAcadState()
        except Exception:
            time.sleep(0.2)
            return True
        while time.time() < deadline:
            try:
                if state.IsQuiescent:
                    return True
            except Exception:
                return True
            time.sleep(0.05)
        return False

    @require_document
    def run_lisp(self, lisp: str, wait: bool = True) -> Dict[str, Any]:
        """Execute an AutoLISP expression via SendCommand and wait for idle.

        Unlike send_command, this is meant for (command ...) sequences that
        select entities by handle using (handent "..."). Waits for AutoCAD to
        return to a quiescent state so callers can safely query results after.
        """
        import time
        try:
            self._wait_quiescent(timeout=5.0)
            if not lisp.endswith("\n"):
                lisp += "\n"
            self.doc.SendCommand(lisp)
            if wait:
                # Give the command queue a beat, then wait for idle.
                time.sleep(0.1)
                if not self._wait_quiescent(timeout=10.0):
                    return {"success": False, "message": "AutoCAD command did not return to idle state"}
            return {"success": True, "message": "已执行 LISP 表达式"}
        except Exception as e:
            return {"success": False, "message": f"执行LISP失败: {e}"}

    @require_document
    def fillet(self, handle1: str, handle2: str, radius: float) -> Dict[str, Any]:
        """Fillet two entities by handle. Returns handle of the new arc if created."""
        ms = self.doc.ModelSpace
        try:
            before = ms.Count
        except Exception:
            before = -1
        lisp = (f'(setvar "FILLETRAD" {radius})'
                f'(command "._FILLET" (handent "{handle1}") (handent "{handle2}"))')
        r = self.run_lisp(lisp)
        if not r["success"]:
            return r
        # Detect a newly created arc (the fillet arc), if any.
        new_handle = None
        try:
            if before >= 0 and ms.Count > before:
                ent = ms.Item(ms.Count - 1)
                new_handle = ent.Handle
        except Exception:
            pass
        return {"success": True, "message": f"已圆角 (R={radius})",
                "new_handle": new_handle}

    @require_document
    def chamfer(self, handle1: str, handle2: str,
                dist1: float, dist2: float) -> Dict[str, Any]:
        """Chamfer two entities by handle using distance/distance method."""
        lisp = (f'(setvar "CHAMMODE" 0)'
                f'(setvar "CHAMFERA" {dist1})(setvar "CHAMFERB" {dist2})'
                f'(command "._CHAMFER" (handent "{handle1}") (handent "{handle2}"))')
        return self.run_lisp(lisp)

    @require_document
    def trim(self, trim_handles: List[str],
             cutting_handles: List[str]) -> Dict[str, Any]:
        """Trim entities using cutting edges, all selected by handle."""
        cut = "".join(f'(ssadd (handent "{h}") ss)' for h in cutting_handles)
        # Build a selection set of cutting edges, run TRIM, pick each target.
        picks = "".join(f'(handent "{h}") ' for h in trim_handles)
        lisp = (f'(setq ss (ssadd))'
                f'{cut}'
                f'(command "._TRIM" ss "" {picks}"")')
        return self.run_lisp(lisp)

    @require_document
    def extend(self, extend_handles: List[str],
               boundary_handles: List[str]) -> Dict[str, Any]:
        """Extend entities to boundary edges, all selected by handle."""
        bnd = "".join(f'(ssadd (handent "{h}") ss)' for h in boundary_handles)
        picks = "".join(f'(handent "{h}") ' for h in extend_handles)
        lisp = (f'(setq ss (ssadd))'
                f'{bnd}'
                f'(command "._EXTEND" ss "" {picks}"")')
        return self.run_lisp(lisp)

    @require_document
    def break_at(self, handle: str, p1: List[float],
                 p2: Optional[List[float]] = None) -> Dict[str, Any]:
        """Break an entity at one point (split) or between two points (gap)."""
        def pt(p):
            z = p[2] if len(p) > 2 else 0.0
            return f'(list {p[0]} {p[1]} {z})'
        if p2 is None:
            # Split in place: pick first point, then "@" for same point.
            lisp = (f'(command "._BREAK" (handent "{handle}") "_F" {pt(p1)} "@")')
        else:
            lisp = (f'(command "._BREAK" (handent "{handle}") "_F" {pt(p1)} {pt(p2)})')
        return self.run_lisp(lisp)

    @require_document
    def join(self, handles: List[str]) -> Dict[str, Any]:
        """Join multiple collinear/contiguous entities into one."""
        source = f'(handent "{handles[0]}")'
        rest = "".join(f'(handent "{h}") ' for h in handles[1:])
        lisp = f'(command "._JOIN" {source} {rest}"")'
        return self.run_lisp(lisp)

    @require_document
    def lengthen(self, handle: str, mode: str, value: float,
                 end: str = "both") -> Dict[str, Any]:
        """Lengthen/shorten an entity. mode: delta|percent|total."""
        mode_code = {"delta": "_DE", "percent": "_P", "total": "_T"}.get(mode, "_DE")
        # LENGTHEN picks the object near an end to choose which side grows.
        lisp = (f'(command "._LENGTHEN" {mode_code} {value} '
                f'(handent "{handle}") "")')
        return self.run_lisp(lisp)

    @require_document
    def divide(self, handle: str, segments: int, block_name: str = "") -> Dict[str, Any]:
        """Divide an entity into a number of equal segments, placing points or blocks."""
        if block_name:
            lisp = f'(command "._DIVIDE" (handent "{handle}") "_B" "{block_name}" "_Y" {segments})'
        else:
            lisp = f'(command "._DIVIDE" (handent "{handle}") {segments})'
        return self.run_lisp(lisp)

    @require_document
    def measure(self, handle: str, length: float, block_name: str = "") -> Dict[str, Any]:
        """Measure an entity: place points or blocks at specified intervals."""
        if block_name:
            lisp = f'(command "._MEASURE" (handent "{handle}") "_B" "{block_name}" "_Y" {length})'
        else:
            lisp = f'(command "._MEASURE" (handent "{handle}") {length})'
        return self.run_lisp(lisp)

    @require_document
    def align(self, handles: List[str], points: List[List[float]]) -> Dict[str, Any]:
        """Align entities using pairs of source and destination points.
        points should be structured as: [[src1, dest1], [src2, dest2], [src3, dest3] (optional)]
        """
        sel = "".join(f'(ssadd (handent "{h}") ss)' for h in handles)
        lisp = f'(setq ss (ssadd)){sel}(command "._ALIGN" ss "" '
        for pair in points:
            if len(pair) == 2:
                src, dst = pair
                sz, dz = src[2] if len(src)>2 else 0, dst[2] if len(dst)>2 else 0
                lisp += f"(list {src[0]} {src[1]} {sz}) (list {dst[0]} {dst[1]} {dz}) "
        lisp += '"" "_Y")'
        return self.run_lisp(lisp)

    @require_document
    def chamfer_poly(self, handle: str, dist1: float, dist2: float) -> Dict[str, Any]:
        """Chamfer all vertices of a polyline."""
        lisp = (f'(setvar "CHAMMODE" 0)'
                f'(setvar "CHAMFERA" {dist1})(setvar "CHAMFERB" {dist2})'
                f'(command "._CHAMFER" "_P" (handent "{handle}"))')
        return self.run_lisp(lisp)

    @require_document
    def fillet_poly(self, handle: str, radius: float) -> Dict[str, Any]:
        """Fillet all vertices of a polyline."""
        lisp = (f'(setvar "FILLETRAD" {radius})'
                f'(command "._FILLET" "_P" (handent "{handle}"))')
        return self.run_lisp(lisp)

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
            viewport = com_get(self.doc, "ActiveViewport", None)
            if viewport is not None:
                for prop in ("Center", "Height", "Width", "Target", "Direction"):
                    value = com_get(view, prop, None)
                    if value is not None:
                        try:
                            com_set(viewport, prop, value)
                        except Exception:
                            pass
                self.doc.ActiveViewport = viewport
            else:
                self.doc.Application.ZoomCenter(
                    to_variant_point(*list(com_get(view, "Center", [0, 0, 0]))[:2], 0),
                    com_get(view, "Height", 100))
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
        def _point(value):
            if value is None:
                return None
            try:
                values = list(value)
            except Exception:
                return None
            result = []
            for item in values:
                try:
                    result.append(float(item))
                except Exception:
                    result.append(item)
            return result

        def _float(value, default=None):
            if value is None:
                return default
            try:
                return float(value)
            except Exception:
                return default

        def _int(value, default=None):
            if value is None:
                return default
            try:
                return int(value)
            except Exception:
                return default

        def _bool(value, default=False):
            if value is None:
                return default
            try:
                return bool(value)
            except Exception:
                return default

        def _bounds(center, width, height):
            if center is None or len(center) < 2 or width is None or height is None:
                return None
            try:
                cx, cy = float(center[0]), float(center[1])
                z = float(center[2]) if len(center) > 2 else 0.0
                half_w = float(width) / 2.0
                half_h = float(height) / 2.0
                return {
                    "min": [cx - half_w, cy - half_h, z],
                    "max": [cx + half_w, cy + half_h, z],
                }
            except Exception:
                return None

        vps = []
        active_layout = com_get(self.doc, "ActiveLayout", None)
        layout_name = com_get(active_layout, "Name", None) if active_layout is not None else None
        try:
            paper_space = self.doc.PaperSpace
            count = int(com_get(paper_space, "Count", 0) or 0)
            for i in range(count):
                try:
                    ent = paper_space.Item(i)
                except Exception as e:
                    logger.warning(f"读取图纸空间实体 {i} 失败: {e}")
                    continue

                object_name = com_get(ent, "ObjectName", "")
                if object_name == "AcDbViewport":
                    center = _point(com_get(ent, "Center", None))
                    target = _point(com_get(ent, "Target", None))
                    view_center = _point(com_get(ent, "ViewCenter", None)) or target
                    width = _float(com_get(ent, "Width", None))
                    height = _float(com_get(ent, "Height", None))
                    custom_scale = _float(com_get(ent, "CustomScale", None), 1.0)
                    vps.append({
                        "handle": com_get(ent, "Handle", ""),
                        "object_name": object_name,
                        "layout": layout_name,
                        "layer": com_get(ent, "Layer", "0"),
                        "center": center,
                        "paper_center": center,
                        "width": width,
                        "height": height,
                        "paper_bounds": _bounds(center, width, height),
                        "view_center": view_center,
                        "target": target,
                        "direction": _point(com_get(ent, "Direction", None)),
                        "twist_angle": _float(com_get(ent, "TwistAngle", None), 0.0),
                        "display_locked": _bool(com_get(ent, "DisplayLocked", None), False),
                        "standard_scale": _int(com_get(ent, "StandardScale", None), 0),
                        "standard_scale2": _int(com_get(ent, "StandardScale2", None), None),
                        "custom_scale": custom_scale,
                        "on": _bool(com_get(ent, "ViewportOn", None), True),
                        "visible": _bool(com_get(ent, "Visible", None), True),
                        "clipped": _bool(com_get(ent, "Clipped", None), False),
                    })
        except Exception as e:
            logger.warning(f"读取图纸空间视口失败: {e}")
        return vps

    @require_document
    def set_pviewport_props(self, handle: str, **kwargs) -> Dict[str, Any]:
        """Set properties on a paper space viewport."""
        ent = self._get_entity(handle)
        if ent is None or com_get(ent, "ObjectName", "") != "AcDbViewport":
            return {"success": False, "message": "Not a viewport"}
        changed = {}
        for k, v in kwargs.items():
            try:
                if k == "display_locked":
                    if not com_set(ent, "DisplayLocked", bool(v)):
                        raise RuntimeError("DisplayLocked property is not writable")
                elif k == "custom_scale":
                    if not com_set(ent, "CustomScale", float(v)):
                        raise RuntimeError("CustomScale property is not writable")
                elif k == "standard_scale":
                    if not com_set(ent, "StandardScale", int(v)):
                        raise RuntimeError("StandardScale property is not writable")
                elif k == "on":
                    state = bool(v)
                    displayed = False
                    try:
                        ent.Display(state)
                        displayed = True
                    except Exception:
                        pass
                    if not displayed and not com_set(ent, "ViewportOn", state):
                        raise RuntimeError("ViewportOn property is not writable")
                changed[k] = v
            except Exception as e:
                logger.warning(f"设置视口属性 {k} 失败: {e}")
        if kwargs and not changed:
            return {"success": False,
                    "message": "No viewport properties were updated",
                    "changed": changed}
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
            try:
                self.doc.Linetypes.Item(name)
                return {"success": True, "message": f"线型已加载 '{name}'"}
            except Exception:
                pass
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
            return com_get(obj, prop, None)
        except Exception as e:
            return f"获取设置失败: {e}"

    def set_preference(self, pref_path: str, value: Any) -> Dict[str, Any]:
        """Set a single preference value. e.g., set_preference('Display.CursorSize', 100)."""
        self._ensure_connected()
        try:
            obj, prop = self._get_pref_object(pref_path)
            com_set(obj, prop, value)
            return {"success": True, "message": f"已设置 {pref_path} = {value}"}
        except Exception as e:
            return {"success": False, "message": f"设置失败: {e}"}

    def get_preferences_display(self) -> Dict[str, Any]:
        """Get display preferences."""
        self._ensure_connected()
        d = self.acad.Preferences.Display
        return {
            "cursor_size": com_get(d, "CursorSize", None),
            "display_layout_tabs": com_get(d, "DisplayLayoutTabs", None),
            "display_screen_menu": com_get(d, "DisplayScreenMenu", None),
            "display_scroll_bars": com_get(d, "DisplayScrollBars", None),
            "max_autocad_window": com_get(d, "MaxAutoCADWindow", None),
        }

    def get_preferences_drafting(self) -> Dict[str, Any]:
        """Get drafting preferences."""
        self._ensure_connected()
        d = self.acad.Preferences.Drafting
        return {
            "auto_snap_marker": com_get(d, "AutoSnapMarker", None),
            "auto_snap_magnet": com_get(d, "AutoSnapMagnet", None),
            "auto_snap_tool_tip": com_get(d, "AutoSnapToolTip", None),
            "auto_snap_aperture_size": com_get(d, "AutoSnapApertureSize", None),
            "auto_snap_marker_size": com_get(d, "AutoSnapMarkerSize", None),
            "auto_track_tool_tip": com_get(d, "AutoTrackToolTip", None),
            "polar_tracking_vector": com_get(d, "PolarTrackingVector", None),
        }

    def get_preferences_files(self) -> Dict[str, Any]:
        """Get file path preferences."""
        self._ensure_connected()
        f = self.acad.Preferences.Files
        return {
            "support_path": com_get(f, "SupportPath", None),
            "auto_save_path": com_get(f, "AutoSavePath", None),
            "log_file_path": com_get(f, "LogFilePath", None),
            "temp_file_path": com_get(f, "TempFilePath", None),
            "menu_file": com_get(f, "MenuFile", None),
            "help_file_path": com_get(f, "HelpFilePath", None),
            "print_spool_dir": com_get(f, "PrintSpoolDir", None),
        }

    def get_preferences_opensave(self) -> Dict[str, Any]:
        """Get open/save preferences."""
        self._ensure_connected()
        o = self.acad.Preferences.OpenSave
        return {
            "auto_save_interval": com_get(o, "AutoSaveInterval", None),
            "create_backup": com_get(o, "CreateBackup", None),
            "incremental_save_percent": com_get(o, "IncrementalSavePercent", None),
            "log_file_on": com_get(o, "LogFileOn", None),
            "save_preview_thumbnail": com_get(o, "SavePreviewThumbnail", None),
            "show_full_path_in_title": com_get(o, "ShowFullPathInTitle", None),
        }

    def get_preferences_selection(self) -> Dict[str, Any]:
        """Get selection preferences."""
        self._ensure_connected()
        s = self.acad.Preferences.Selection
        return {
            "pick_first": com_get(s, "PickFirst", None),
            "pick_add": com_get(s, "PickAdd", None),
            "pick_auto": com_get(s, "PickAuto", None),
            "pick_drag": com_get(s, "PickDrag", None),
            "pick_box_size": com_get(s, "PickBoxSize", None),
            "display_grips": com_get(s, "DisplayGrips", None),
            "display_grips_within_blocks": com_get(s, "DisplayGripsWithinBlocks", None),
            "grip_size": com_get(s, "GripSize", None),
            "pick_group": com_get(s, "PickGroup", None),
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
            self._retry_com_call(lambda: ss.SelectByPolygon(mode, pts))
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
            self._retry_com_call(lambda: ss.SelectAtPoint(to_variant_point(x, y, z)))
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
            if "未找到主键" in str(e) or "key not found" in str(e).lower():
                return {"success": True, "message": f"选择集 {ss_name} 不存在或已为空"}
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
            mat = win32com.client.VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_R8,
                                          [[float(v) for v in row] for row in matrix])
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
