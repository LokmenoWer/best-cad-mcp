# Mainland China Construction CAD Guidance

Use this reference whenever the user asks for standard, constructible, mainland China CAD
drawings, Chinese layer names, local fonts, architectural/structural/MEP/fire drawings, shop
drawings, or construction details.

This guidance is a drafting baseline. It does not replace official codes, project standards,
local review requirements, or licensed professional judgment. If the user asks for latest legal
or code compliance, verify the current standards and local requirements before claiming compliance.

## Standards Posture

Common drawing-standard families to check against project requirements include:

- `GB/T 50001` 房屋建筑制图统一标准。
- `GB/T 50103` 总图制图标准。
- `GB/T 50104` 建筑制图标准。
- `GB/T 50105` 建筑结构制图标准。
- `GB/T 50106` 建筑给水排水制图标准。
- `GB/T 50114` 暖通空调制图标准。
- `GB/T 50786` 建筑电气制图标准。
- Project, institute, owner, discipline, local审图, fire, civil, interior, shop, and national atlas requirements.

If a dimension, fire rating, structural size, pipe diameter, elevation, or capacity is missing,
ask or mark it as `待确认`; do not invent safety-critical values.

## Units, Scale, and Sheets

- Draw model space 1:1 in millimeters for building and MEP drawings.
- Express most dimensions in millimeters; express elevations in meters unless project standards say otherwise.
- Use layout/paper space and viewport scale for plotting. Do not scale model geometry to fit the sheet.
- Common sheet sizes: A0 `841x1189`, A1 `594x841`, A2 `420x594`, A3 `297x420`, A4 `210x297` mm.
- Common scales: site `1:500` or `1:1000`; plans/elevations/sections `1:50`, `1:100`, `1:200`; details `1:5`, `1:10`, `1:20`, `1:25`, `1:50`.

## Chinese Layer Names

Use project layer standards first. If none exist, default to `专业-对象-状态`; omit status when not needed.

| Layer | Purpose | Color | Linetype | Lineweight |
|---|---|---:|---|---:|
| `建筑-轴网` | Grids, grid bubbles | 8 | CENTER | 0.13 |
| `建筑-墙体` | Walls and partitions | 7 | Continuous | 0.35 |
| `建筑-门窗` | Doors, windows, openings | 3 | Continuous | 0.25 |
| `建筑-楼梯` | Stairs, ramps, rails | 4 | Continuous | 0.25 |
| `建筑-家具设备` | Furniture, fixtures, fixed equipment | 8 | Continuous | 0.18 |
| `结构-柱` | Columns | 1 | Continuous | 0.50 |
| `结构-梁` | Beams | 1 | Continuous | 0.35 |
| `结构-板` | Slabs, openings, reinforcement areas | 2 | Continuous | 0.25 |
| `结构-钢筋` | Reinforcement symbols/details | 1 | Continuous | 0.25 |
| `结构-基础` | Foundations, caps, grade beams | 1 | Continuous | 0.50 |
| `给排水-给水` | Water supply | 5 | Continuous | 0.25 |
| `给排水-排水` | Drainage, rainwater, vent | 6 | DASHED | 0.25 |
| `暖通-风管` | Ducts, air outlets, dampers | 4 | Continuous | 0.25 |
| `暖通-水管` | HVAC water/condensate/heating pipe | 5 | Continuous | 0.25 |
| `电气-照明` | Lighting fixtures and circuits | 2 | Continuous | 0.18 |
| `电气-动力` | Power, outlets, cable trays | 1 | Continuous | 0.25 |
| `消防-喷淋` | Sprinkler pipe, heads, valves | 1 | Continuous | 0.25 |
| `消防-报警` | Alarm, detector, linkage points | 1 | DASHED | 0.18 |
| `总图-红线` | Property/control lines | 1 | Continuous | 0.35 |
| `总图-道路` | Roads and hardscape | 7 | Continuous | 0.25 |
| `总图-管线` | Outdoor utilities | 5 | DASHED | 0.25 |
| `填充-材料` | Section/material hatches | 8 | Continuous | 0.13 |
| `标注-尺寸` | Dimensions | 6 | Continuous | 0.13 |
| `标注-文字` | General text | 7 | Continuous | 0.13 |
| `标注-引线` | Leaders/callouts | 6 | Continuous | 0.13 |
| `标注-索引` | Section/detail/node tags | 6 | Continuous | 0.18 |
| `图框-标题栏` | Border, title block, revision area | 7 | Continuous | 0.25 |
| `辅助-中心线` | Center/symmetry lines | 8 | CENTER | 0.13 |
| `辅助-不打印` | Temporary construction/check lines | 9 | Continuous | 0.13 |

Formal model objects should not remain on `0`, `Defpoints`, or test layers.

## Fonts and Text Styles

1. Call `get_text_styles()` first; reuse an existing style that displays Chinese correctly.
2. Prefer local fonts. On Windows, inspect `C:\Windows\Fonts` or AutoCAD support paths when needed.
3. Common choices: `SimSun`/宋体 for body notes, `SimHei`/黑体 for titles, `FangSong`/仿宋 for formal notes/title blocks, `Microsoft YaHei` only when the project permits screen-friendly output.
4. If using SHX fonts, confirm the `.shx` file exists. Do not reference missing `HZTXT.SHX`, `TSSDENG.SHX`, or `ROMANS.SHX`.
5. Use `create_text_style`, then `set_current_text_style`. Check PDF export for Chinese garbling.

Typical plotted text heights: dimensions and notes `2.5` or `3.5` mm; room/equipment labels `3.5` or `5.0`; drawing/detail titles `5.0` or `7.0`; sheet title `7.0` or `10.0`.

## Constructible Content Minimums

- Architectural: axes, walls, columns, doors/windows/openings, stairs, rooms/areas, elevations, section/detail references, materials or finish references.
- Structural: grids, member IDs, sizes, elevations, reinforcement or detail references, foundation/member notes.
- Plumbing: systems, pipe diameters, slope, elevation, valves, fixtures/equipment tags, sleeves/reserved openings.
- HVAC: duct/pipe sizes, elevation, airflow/direction, equipment IDs, dampers/fire dampers, insulation/system notes.
- Electrical: panel IDs, circuit numbers, lighting/outlet/weak-current points, cable trays/conduits, system diagram references.
- Fire protection: sprinklers, alarm points, hydrants, valves, pipe sizes, fire zoning/system relationship.
- Site: redline, coordinates, building positioning, roads, grading elevations, outdoor utilities, existing references.

## Mandatory Construction Check

Before handoff:

- Dimensions are true dimension objects and match measured geometry.
- Hatches are hatch objects with boundaries.
- Repeated symbols are blocks or arrays.
- Tables are table entities, not loose lines and text.
- Layers are Chinese/business layers, with properties by layer.
- Font and PDF output show Chinese correctly.
- Layout, viewport scale, title block, drawing number, scale, date, revision, discipline, and project name are present when producing sheets.
- All missing design inputs are listed as `待确认`.
