# 施工图工具选择规则

先读 `../cad-operations/references/TOOL-MAP.md` 获取完整工具名。本文件只列施工图高频意图。

## 几何与构件

| 意图 | 使用工具 | 禁止做法 |
|---|---|---|
| 矩形柱、洞口、图框、设备基础 | `draw_rectangle` | 用 4 条 `draw_line` 拼矩形 |
| 房间、地坪边界、不规则闭合范围 | `draw_polyline(closed=True)` | 松散线段不闭合 |
| 双线墙体、平行管沟 | `draw_mline` | 手动画两条平行线 |
| 轴线、中心线、对称线 | `draw_xline` 或中心线图层上的专用线 | 普通实线混在构件层 |
| 圆形柱、孔洞、设备基础 | `draw_circle` | 用多段线近似圆 |
| 弧形墙、弧形洞口 | `draw_arc` 或 `polyline_set_bulge` | 多段短线近似弧 |
| 剖面材料、墙体填充 | `add_hatch` 加 `hatch_add_boundary` | 用很多斜线手绘剖面线 |
| 门、窗、洁具、阀门、灯具、喷头 | `create_block` 后 `insert_block` | 每个实例重复画一遍 |
| 成排灯具、喷头、柱网、座椅 | `array_rectangular` 或 `insert_minsert_block` | 循环调用复制或重画 |
| 环形喷头、螺栓、圆周构件 | `array_polar` | 手算角度逐个放置 |

## 建筑图

- 墙体优先用 `draw_mline` 或项目墙体块；开洞后用修剪/打断工具处理，不要覆盖白色线条伪装洞口。
- 门窗创建参数化命名块，如 `门-M0921`、`窗-C1215`，插入时旋转定位。
- 楼梯可把标准踏步线创建为块或用阵列生成，平台边界用闭合多段线。
- 房间名称和面积使用 `draw_mtext`，面积来自几何或用户提供数据，不要凭视觉估计。

## 结构图

- 梁、柱、基础等构件用对应中文图层并保持尺寸闭合可量取。
- 钢筋、箍筋、锚固、构造详图要用可编辑几何和真实标注；重复钢筋符号用块。
- 构件编号、截面尺寸、标高、节点索引必须与表格/说明一致。

## 机电与消防图

- 管线、电缆桥架、风管路线使用多段线、多线或专用块，保持连续和可查询。
- 阀门、喷头、探测器、灯具、插座、风口等点位使用块；需要编号时用块属性或邻近文字。
- 管径、风管尺寸、回路号、系统编号、标高和坡度用文字/多重引线，不要藏在图层名里。
- 不同系统必须分层，如 `给排水-给水`、`消防-喷淋`、`电气-照明`。

## 标注、索引与表格

- 尺寸：`add_linear_dimension`、`add_rotated_dimension`、`add_baseline_dimension`、`add_continue_dimension`、`add_qdim`。
- 半径/直径：`add_radial_dimension`、`add_diametric_dimension`。
- 角度/弧长：`add_angular_dimension`、`add_arc_dimension`。
- 引出说明：优先 `add_mleader`。
- 门窗表、设备表、材料表、图纸目录：`add_table` 和 `edit_table_cell`。

不要把表格做成一堆线加文字，除非没有表格工具且用户明确接受。

## 修改既有图纸

1. `open_drawing`。
2. `scan_all_entities`。
3. 用 `execute_query` 按图层、类型、文字、范围定位对象。
4. 用 `move_entity`、`rotate_entity`、`offset_entity`、`trim_entity`、`extend_entity`、`set_entity_properties` 修改。
5. 修改后重新扫描并校核统计。

不要删除后重画来模拟移动、镜像、偏移、修剪或缩放。
