"""CAD MCP Tools — Preferences, Plot, UCS, Views, Utility, Materials, Linetypes, Hyperlinks, XData, and Advanced Entity Operations."""
from typing import Optional, List, Dict, Any
import json
from src.cad_controller import get_controller
from src.cad_database import get_database
from src.cad_utils import format_success

ctrl = get_controller()
db = get_database()


# ══════════════════════════════════════════════════════════════════
#  HYPERLINKS
# ══════════════════════════════════════════════════════════════════

def add_hyperlink(handle: str, url: str, description: str = "",
                   named_location: str = "") -> str:
    """为实体添加超链接。

    Args:
        handle:          实体句柄
        url:             链接URL（如 https://example.com 或本地文件路径）
        description:     链接描述文字
        named_location:  目标中的命名位置（可选）
    """
    r = ctrl.add_hyperlink(handle, url, description, named_location)
    return r["message"]


def get_hyperlinks(handle: str) -> str:
    """获取实体上所有的超链接。

    Args:
        handle: 实体句柄
    """
    r = ctrl.get_hyperlinks(handle)
    return json.dumps(r, indent=2, ensure_ascii=False)


def remove_hyperlink(handle: str, index: int = 0) -> str:
    """删除实体上的指定超链接。

    Args:
        handle: 实体句柄
        index:  超链接索引（默认0=第一个）
    """
    r = ctrl.remove_hyperlink(handle, index)
    return r["message"]


# ══════════════════════════════════════════════════════════════════
#  XDATA (EXTENDED ENTITY DATA)
# ══════════════════════════════════════════════════════════════════

def get_xdata(handle: str, app_name: str = "") -> str:
    """获取实体上的扩展数据 (XData)。

    扩展数据是附着在实体上的自定义数据，可用于存储AI生成的元数据、
    分析结果、分类标签等。

    Args:
        handle:   实体句柄
        app_name: 注册应用名称（空字符串=获取所有应用的XData）
    """
    r = ctrl.get_xdata(handle, app_name)
    return json.dumps(r, indent=2, ensure_ascii=False, default=str)


def _normalize_xdata_pairs(app_name: str,
                           data_pairs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not isinstance(app_name, str) or not app_name.strip():
        raise ValueError("app_name is required for XData")
    if not isinstance(data_pairs, list):
        raise ValueError("data_pairs must be a list")

    supported_codes = {1000, 1002, 1003, 1005, 1040, 1041, 1042, 1070, 1071}
    float_codes = {1040, 1041, 1042}
    int_codes = {1070, 1071}
    normalized: List[Dict[str, Any]] = []

    for index, pair in enumerate(data_pairs):
        if not isinstance(pair, dict) or "code" not in pair or "value" not in pair:
            raise ValueError(f"data_pairs[{index}] must contain code and value")
        try:
            code = int(pair["code"])
        except (TypeError, ValueError):
            raise ValueError(f"data_pairs[{index}].code must be an integer")
        if code == 1001:
            raise ValueError("Do not include DXF code 1001; app_name is added automatically")
        if code not in supported_codes:
            raise ValueError(f"Unsupported XData DXF code: {code}")

        value = pair["value"]
        if isinstance(value, (dict, list, tuple)):
            raise ValueError(f"data_pairs[{index}].value must be scalar")
        if code in float_codes:
            value = float(value)
        elif code in int_codes:
            if isinstance(value, bool):
                raise ValueError(f"data_pairs[{index}].value must be an integer")
            value = int(value)
        else:
            value = str(value)
        normalized.append({"code": code, "value": value})

    return normalized


def set_xdata(handle: str, app_name: str,
               data_pairs: List[Dict[str, Any]]) -> str:
    """为实体设置扩展数据 (XData)。

    扩展数据由 DXF 组码和数据值对组成。
    常用组码:
      1000: ASCII字符串 (最长255字符)
      1001: 注册应用名称
      1040: 实数 (double)
      1070: 16位整数
      1071: 32位整数

    数据对格式: [{"code": 1000, "value": "my_string"}, {"code": 1040, "value": 12.5}]

    Args:
        handle:     实体句柄
        app_name:   注册应用名称（必须先在RegisteredApplications中注册）
        data_pairs: 数据对列表，每项包含 code (int) 和 value
    """
    normalized_pairs = _normalize_xdata_pairs(app_name, data_pairs)
    # Ensure app_name is registered
    ctrl.create_registered_application(app_name)
    # First pair must be 1001 with the app name
    full_pairs = [{"code": 1001, "value": app_name}] + normalized_pairs
    r = ctrl.set_xdata(handle, full_pairs)
    return r["message"]


# ══════════════════════════════════════════════════════════════════
#  UCS MANAGEMENT
# ══════════════════════════════════════════════════════════════════

def create_ucs(origin_x: float, origin_y: float, origin_z: float,
               x_axis_x: float, x_axis_y: float, x_axis_z: float,
               y_axis_x: float, y_axis_y: float, y_axis_z: float,
               name: str) -> str:
    """创建命名用户坐标系 (UCS)。

    UCS 定义了一个自定义的坐标系统，用于在3D空间中的特定平面上绘图。

    Args:
        origin_x,y,z:  UCS原点（WCS坐标）
        x_axis_x,y,z:  X轴正方向点（定义X轴方向）
        y_axis_x,y,z:  Y轴正方向点（定义Y轴方向，必须与X轴垂直）
        name:          UCS名称
    """
    r = ctrl.create_ucs(
        [origin_x, origin_y, origin_z],
        [x_axis_x, x_axis_y, x_axis_z],
        [y_axis_x, y_axis_y, y_axis_z],
        name)
    return r["message"]


def get_all_ucs() -> str:
    """列出所有命名 UCS。"""
    ucss = ctrl.get_all_ucs()
    if not ucss:
        return "无命名 UCS"
    lines = [f"共 {len(ucss)} 个 UCS:"]
    for i, u in enumerate(ucss):
        lines.append(f"  [{i}] {u['name']:<20s} 原点:{u['origin']}")
    return "\n".join(lines)


def set_active_ucs(name: str) -> str:
    """激活指定 UCS。

    Args:
        name: UCS名称
    """
    r = ctrl.set_active_ucs(name)
    return r["message"]


def get_active_ucs() -> str:
    """获取当前活动的 UCS 信息。"""
    info = ctrl.get_active_ucs()
    return json.dumps(info, indent=2, ensure_ascii=False)


# ══════════════════════════════════════════════════════════════════
#  NAMED VIEWS
# ══════════════════════════════════════════════════════════════════

def save_named_view(name: str) -> str:
    """将当前视图保存为命名视图。

    Args:
        name: 视图名称
    """
    r = ctrl.save_named_view(name)
    return r["message"]


def restore_named_view(name: str) -> str:
    """恢复命名视图。

    Args:
        name: 视图名称
    """
    r = ctrl.restore_named_view(name)
    return r["message"]


def get_named_views() -> str:
    """列出所有命名视图。"""
    views = ctrl.get_named_views()
    if not views:
        return "无命名视图"
    import json
    return json.dumps(views, indent=2, ensure_ascii=False)


def delete_named_view(name: str) -> str:
    """删除命名视图。

    Args:
        name: 视图名称
    """
    r = ctrl.delete_named_view(name)
    return r["message"]


# ══════════════════════════════════════════════════════════════════
#  PAPER SPACE VIEWPORTS
# ══════════════════════════════════════════════════════════════════

def add_viewport(center_x: float, center_y: float, width: float,
                  height: float, layer: Optional[str] = None) -> str:
    """在布局中创建图纸空间视口。

    注意：此工具需要在图纸空间布局中使用（非模型空间）。

    Args:
        center_x, center_y: 视口中心点
        width:              视口宽度
        height:             视口高度
        layer:              图层名称
    """
    if layer:
        ctrl.create_layer(layer)
        ctrl.set_current_layer(layer)
    try:
        vp = ctrl.add_pviewport(center_x, center_y, width, height)
        vp.ViewportOn = True
        return format_success(f"已创建图纸空间视口", handle=vp.Handle,
                              size=f"{width}×{height}")
    except Exception as e:
        return f"创建视口失败: {e}"


def get_viewports() -> str:
    """列出所有图纸空间视口。"""
    vps = ctrl.get_pviewports()
    if not vps:
        return "无图纸空间视口"
    import json
    return json.dumps(vps, indent=2, ensure_ascii=False)


def set_viewport_properties(handle: str,
                             display_locked: Optional[bool] = None,
                             custom_scale: Optional[float] = None,
                             on: Optional[bool] = None) -> str:
    """设置图纸空间视口属性。

    Args:
        handle:         视口句柄
        display_locked: 是否锁定视口显示
        custom_scale:   自定义缩放比例
        on:             是否打开视口
    """
    kwargs = {k: v for k, v in {
        "display_locked": display_locked,
        "custom_scale": custom_scale,
        "on": on,
    }.items() if v is not None}
    r = ctrl.set_pviewport_props(handle, **kwargs)
    return f"✓ 已更新视口属性: {r.get('changed', {})}"


# ══════════════════════════════════════════════════════════════════
#  PLOT / PRINT
# ══════════════════════════════════════════════════════════════════

def plot_to_device(plot_config: str = "") -> str:
    """将当前布局发送到打印设备。

    Args:
        plot_config: 打印配置名称（PC3文件或系统打印机名称）
    """
    r = ctrl.plot_to_device(plot_config)
    return r["message"]


def plot_to_file(filepath: str, plot_config: str = "") -> str:
    """将当前布局打印到 PLT 文件。

    Args:
        filepath:   输出文件路径（如 C:/output/drawing.plt）
        plot_config: 打印配置名称
    """
    r = ctrl.plot_to_file(filepath, plot_config)
    return r["message"]


def plot_preview(preview_type: int = 1) -> str:
    """显示打印预览。

    注意：这会打开AutoCAD的打印预览窗口。

    Args:
        preview_type: 0=部分预览, 1=完整预览
    """
    r = ctrl.plot_preview(preview_type)
    return r["message"]


def get_plot_devices() -> str:
    """列出所有可用的打印设备/绘图仪。"""
    devices = ctrl.get_plot_devices()
    if not devices:
        return "无可用打印设备"
    return "可用打印设备:\n" + "\n".join(f"  - {d}" for d in devices)


def get_plot_style_tables() -> str:
    """列出所有可用的打印样式表 (CTB/STB)。"""
    tables = ctrl.get_plot_style_table_names()
    if not tables:
        return "无可用打印样式表"
    return "可用打印样式表:\n" + "\n".join(f"  - {t}" for t in tables)


def get_plot_configurations() -> str:
    """列出所有命名页面设置。"""
    configs = ctrl.get_plot_configurations()
    if not configs:
        return "无命名页面设置"
    return json.dumps(configs, indent=2, ensure_ascii=False)


# ══════════════════════════════════════════════════════════════════
#  MATERIALS
# ══════════════════════════════════════════════════════════════════

def create_material(name: str, description: str = "") -> str:
    """创建新材质。

    Args:
        name:        材质名称
        description: 材质描述
    """
    r = ctrl.create_material(name, description)
    return r["message"]


def get_materials() -> str:
    """列出所有材质。"""
    mats = ctrl.get_materials()
    if not mats:
        return "无材质"
    lines = [f"共 {len(mats)} 个材质:"]
    for m in mats:
        lines.append(f"  - {m['name']}: {m['description']}")
    return "\n".join(lines)


def set_entity_material(handle: str, material_name: str) -> str:
    """为实体分配材质。

    Args:
        handle:        实体句柄
        material_name: 材质名称
    """
    r = ctrl.set_entity_material(handle, material_name)
    return r["message"]


def set_active_material(material_name: str) -> str:
    """设置默认材质（新创建的对象将使用此材质）。

    Args:
        material_name: 材质名称
    """
    r = ctrl.set_active_material(material_name)
    return r["message"]


# ══════════════════════════════════════════════════════════════════
#  LINETYPES
# ══════════════════════════════════════════════════════════════════

def load_linetype(name: str, filename: str = "acad.lin") -> str:
    """从线型库加载线型。

    Args:
        name:     线型名称（如 "HIDDEN", "CENTER", "DASHDOT"）
        filename: 线型库文件名（默认 "acad.lin"）
    """
    r = ctrl.load_linetype(name, filename)
    return r["message"]


def get_linetypes() -> str:
    """列出所有已加载的线型。"""
    lts = ctrl.get_linetypes()
    if not lts:
        return "无线型信息"
    lines = [f"共 {len(lts)} 个线型:"]
    for lt in lts:
        lines.append(f"  - {lt['name']}: {lt['description']}")
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════
#  UTILITY / GEOMETRY
# ══════════════════════════════════════════════════════════════════

def polar_point(x: float, y: float, z: float,
                 angle_deg: float, distance: float) -> str:
    """计算从原点出发，指定角度和距离的目标点坐标。

    Args:
        x, y, z:   起点坐标
        angle_deg: 角度（度，从X轴逆时针计算）
        distance:  距离
    """
    pt = ctrl.polar_point(x, y, z, angle_deg, distance)
    return f"极坐标点: ({pt[0]:.4f}, {pt[1]:.4f}, {pt[2]:.4f})"


def translate_coordinates(x: float, y: float, z: float,
                           from_cs: int = 0, to_cs: int = 1) -> str:
    """在不同坐标系之间转换坐标。

    坐标系统代码:
      0 = WCS (世界坐标系)
      1 = UCS (用户坐标系)
      2 = DCS (显示坐标系)
      3 = PSDCS (图纸空间DCS)
      4 = OCS (对象坐标系)

    Args:
        x, y, z: 源坐标
        from_cs: 源坐标系代码
        to_cs:   目标坐标系代码
    """
    pt = ctrl.translate_coordinates(x, y, z, from_cs, to_cs)
    return f"转换后坐标: ({pt[0]:.4f}, {pt[1]:.4f}, {pt[2]:.4f})"


def angle_from_xaxis(x1: float, y1: float, x2: float, y2: float,
                      z1: float = 0.0, z2: float = 0.0) -> str:
    """计算两点连线与X轴的夹角。

    Args:
        x1,y1,z1: 第一个点
        x2,y2,z2: 第二个点
    """
    angle = ctrl.angle_from_xaxis([x1, y1, z1], [x2, y2, z2])
    return f"与X轴夹角: {angle:.4f}°"


def format_angle(angle_rad: float, unit: int = 0, precision: int = 2) -> str:
    """将角度（弧度）格式化为字符串。

    Args:
        angle_rad: 角度值（弧度）
        unit:      单位: 0=度, 1=度/分/秒, 2=百分度, 3=弧度
        precision: 精度位数
    """
    result = ctrl.angle_to_string(angle_rad, unit, precision)
    return f"格式化角度: {result}"


def format_distance(value: float, unit: int = 0, precision: int = 2) -> str:
    """将数值格式化为距离字符串。

    Args:
        value:     数值
        unit:      单位: 0=十进制, 1=工程制, 2=建筑制, 3=分数
        precision: 精度位数
    """
    result = ctrl.real_to_string(value, unit, precision)
    return f"格式化距离: {result}"


# ══════════════════════════════════════════════════════════════════
#  PREFERENCES
# ══════════════════════════════════════════════════════════════════

def get_preference(pref_path: str) -> str:
    """读取单个 AutoCAD 偏好设置。

    例如:
      Display.CursorSize — 十字光标大小
      Drafting.AutoSnapMarker — 自动捕捉标记
      Files.SupportPath — 支持文件搜索路径
      OpenSave.AutoSaveInterval — 自动保存间隔（分钟）
      Selection.PickBoxSize — 拾取框大小
      System.SingleDocumentMode — 单文档模式

    Args:
        pref_path: 偏好路径，格式为 "Category.Property"
    """
    val = ctrl.get_preference(pref_path)
    return f"{pref_path} = {val}"


def set_preference(pref_path: str, value: str) -> str:
    """设置单个 AutoCAD 偏好设置。

    常用的可设置偏好:
      Display.CursorSize (1-100)
      OpenSave.AutoSaveInterval (分钟)
      OpenSave.CreateBackup (True/False)
      Selection.PickBoxSize (0-50)
      Selection.DisplayGrips (True/False)
      System.BeepOnError (True/False)

    Args:
        pref_path: 偏好路径
        value:     新值（数字或 True/False 字符串）
    """
    # Auto-convert value
    try:
        v = int(value)
    except ValueError:
        try:
            v = float(value)
        except ValueError:
            v_lower = value.lower()
            if v_lower == "true":
                v = True
            elif v_lower == "false":
                v = False
            else:
                v = value
    r = ctrl.set_preference(pref_path, v)
    return r["message"]


def get_preferences_display() -> str:
    """获取显示相关偏好设置。"""
    prefs = ctrl.get_preferences_display()
    return json.dumps(prefs, indent=2, ensure_ascii=False)


def get_preferences_drafting() -> str:
    """获取绘图相关偏好设置。"""
    prefs = ctrl.get_preferences_drafting()
    return json.dumps(prefs, indent=2, ensure_ascii=False)


def get_preferences_files() -> str:
    """获取文件路径相关偏好设置。"""
    prefs = ctrl.get_preferences_files()
    return json.dumps(prefs, indent=2, ensure_ascii=False)


def get_preferences_opensave() -> str:
    """获取打开/保存相关偏好设置。"""
    prefs = ctrl.get_preferences_opensave()
    return json.dumps(prefs, indent=2, ensure_ascii=False)


def get_preferences_selection() -> str:
    """获取选择相关偏好设置。"""
    prefs = ctrl.get_preferences_selection()
    return json.dumps(prefs, indent=2, ensure_ascii=False)


def get_preferences_system() -> str:
    """获取系统相关偏好设置。"""
    prefs = ctrl.get_preferences_system()
    return json.dumps(prefs, indent=2, ensure_ascii=False)


def get_preferences_user() -> str:
    """获取用户相关偏好设置。"""
    prefs = ctrl.get_preferences_user()
    return json.dumps(prefs, indent=2, ensure_ascii=False)


# ══════════════════════════════════════════════════════════════════
#  APPLICATION INFO
# ══════════════════════════════════════════════════════════════════

def get_application_info() -> str:
    """获取 AutoCAD 应用程序信息（版本、路径等）。"""
    info = ctrl.get_app_info()
    return json.dumps(info, indent=2, ensure_ascii=False)


def is_autocad_idle() -> str:
    """检查 AutoCAD 是否处于空闲状态。"""
    state = ctrl.get_acad_state()
    return json.dumps(state, indent=2, ensure_ascii=False)


# ══════════════════════════════════════════════════════════════════
#  SELECTION ENHANCEMENTS
# ══════════════════════════════════════════════════════════════════

def select_by_fence(points: List[float]) -> str:
    """栏选：选择与折线相交的所有实体。

    Args:
        points: 栏选线顶点 [x1,y1, x2,y2, ...]
    """
    r = ctrl.select_by_polygon(2, points)
    if r["success"]:
        return format_success(f"栏选到 {r['count']} 个实体",
                              handles=r.get("handles", [])[:20])
    return f"栏选失败: {r.get('message', '')}"


def select_by_wpolygon(points: List[float]) -> str:
    """窗口多边形选择：选择完全在多边形内的实体。

    Args:
        points: 多边形顶点 [x1,y1, x2,y2, ...]
    """
    r = ctrl.select_by_polygon(6, points)
    if r["success"]:
        return format_success(f"多边形内选择 {r['count']} 个实体",
                              handles=r.get("handles", [])[:20])
    return f"多边形选择失败: {r.get('message', '')}"


def select_by_cpolygon(points: List[float]) -> str:
    """交叉多边形选择：选择与多边形相交或在其内的实体。

    Args:
        points: 多边形顶点 [x1,y1, x2,y2, ...]
    """
    r = ctrl.select_by_polygon(7, points)
    if r["success"]:
        return format_success(f"交叉多边形选择 {r['count']} 个实体",
                              handles=r.get("handles", [])[:20])
    return f"交叉多边形选择失败: {r.get('message', '')}"


def select_at_point(x: float, y: float, z: float = 0.0) -> str:
    """选择经过指定点的实体。

    Args:
        x, y, z: 选择点坐标
    """
    r = ctrl.select_at_point(x, y, z)
    if r["success"]:
        return format_success(f"在点 ({x},{y},{z}) 处选择到 {r['count']} 个实体",
                              handles=r.get("handles", []))
    return f"点选失败: {r.get('message', '')}"


# ══════════════════════════════════════════════════════════════════
#  DOCUMENT UTILITIES
# ══════════════════════════════════════════════════════════════════

def set_document_properties(title: Optional[str] = None,
                             subject: Optional[str] = None,
                             author: Optional[str] = None,
                             keywords: Optional[str] = None,
                             comments: Optional[str] = None) -> str:
    """设置图纸的摘要属性。

    Args:
        title:    图纸标题
        subject:  主题
        author:   作者
        keywords: 关键词
        comments: 注释
    """
    kwargs = {}
    if title is not None: kwargs["Title"] = title
    if subject is not None: kwargs["Subject"] = subject
    if author is not None: kwargs["Author"] = author
    if keywords is not None: kwargs["Keywords"] = keywords
    if comments is not None: kwargs["Comments"] = comments
    if not kwargs:
        return "错误: 至少指定一个要设置的属性"
    r = ctrl.set_summary_info(**kwargs)
    return r["message"]


def set_drawing_password(password: str) -> str:
    """为当前图纸设置密码保护（加密保存）。

    Args:
        password: 密码字符串
    """
    r = ctrl.set_drawing_password(password)
    return r["message"]


def get_file_dependencies() -> str:
    """列出当前图纸的文件依赖（外部参照、图片、字体等）。"""
    deps = ctrl.get_file_dependencies()
    if not deps:
        return "无文件依赖"
    return json.dumps(deps, indent=2, ensure_ascii=False)


def get_active_space_info() -> str:
    """获取当前工作空间信息（模型空间或图纸空间）。"""
    info = ctrl.get_active_space()
    try:
        mspace = ctrl.get_mspace()
        info["mspace"] = mspace
    except Exception:
        pass
    return json.dumps(info, indent=2, ensure_ascii=False)


# ══════════════════════════════════════════════════════════════════
#  DICTIONARIES & REGISTERED APPS
# ══════════════════════════════════════════════════════════════════

def create_registered_application(name: str) -> str:
    """注册新的应用名称（用于XData存储）。

    Args:
        name: 应用名称（如 "MY_AI_DATA"）
    """
    r = ctrl.create_registered_application(name)
    return r["message"]


def get_registered_applications() -> str:
    """列出所有已注册的应用名称。"""
    apps = ctrl.get_registered_applications()
    if not apps:
        return "无注册应用"
    return "已注册应用:\n" + "\n".join(f"  - {a}" for a in apps)


def get_dictionaries() -> str:
    """列出所有命名对象字典。"""
    dicts = ctrl.get_dictionaries()
    if not dicts:
        return "无命名字典"
    return "命名字典:\n" + "\n".join(f"  - {d}" for d in dicts)
