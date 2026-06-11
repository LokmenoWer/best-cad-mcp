# AutoCAD COM Interface — 完整调研报告

## 调研范围

基于对整个 AutoCAD ActiveX/COM API 参考文档的综合研究，结合对 `best-cad-mcp` 项目现有实现的完整审计，本报告识别了所有缺失的 COM 接口。

### 三线并行调研
1. **整体 COM API** — 完整对象模型和所有接口
2. **3D 建模** — 实体、曲面、网格、区域、布尔运算
3. **工具类和文档级** — Preferences、Plot、Utility、UCS、Views、Viewports、XData 等

---

## 当前已实现的功能 (基线)

| 类别 | 已实现 |
|-------|------------|
| **基元** | Line、Circle、Arc、Ellipse、Spline、LWPolyline、3DPoly、Point、Ray、XLine、MLine、Solid |
| **文字** | Text、MText、Leader、MLeader、Table |
| **标注** | DimAligned、DimRotated、DimAngular、DimRadial、DimDiametric、DimOrdinate |
| **填充** | Hatch（部分 — 添加边界需完善） |
| **图块/Xref** | CreateBlock、InsertBlock、AttachXRef、Unload、Reload |
| **编辑** | Move、Rotate、Copy、Delete、Mirror、Scale、Offset、ArrayRect、ArrayPolar、Explode |
| **图层** | 完整的 CRUD、Freeze/Thaw、Lock/Unlock、On/Off、Isolate |
| **视图** | Zoom 全部变体、Pan、Layouts、ActiveLayout |
| **查询** | 实体扫描、区域扫描、窗选/叉选、高亮、SQL 查询 |
| **文件** | Open、Save、Export（PDF/DXF/DWF/BMP）、Purge、Audit、Undo/Redo |
| **系统** | GetVariable、SetVariable、SendCommand、Regen |

---

## 缺失的 API：完整清单

### 🟢 第 1 层 — 高优先级（核心 CAD 工作流）

#### 1. 3D 实体基元（在 ModelSpace 上有直接 COM 方法）
- `AddBox(Origin, Length, Width, Height)` → Acad3DSolid
- `AddCone(Center, BaseRadius, Height)` → Acad3DSolid
- `AddCylinder(Center, Radius, Height)` → Acad3DSolid
- `AddEllipticalCone(Center, MajorRadius, MinorRadius, Height)` → Acad3DSolid
- `AddEllipticalCylinder(Center, MajorRadius, MinorRadius, Height)` → Acad3DSolid
- `AddSphere(Center, Radius)` → Acad3DSolid
- `AddTorus(Center, TorusRadius, TubeRadius)` → Acad3DSolid
- `AddWedge(Center, Length, Width, Height)` → Acad3DSolid

#### 2. Region 和从 Region 生成的实体
- `AddRegion(ObjectList)` → Variant (Region 对象数组)
- `AddExtrudedSolid(Profile, Height, TaperAngle)` → Acad3DSolid
- `AddExtrudedSolidAlongPath(Profile, Path)` → Acad3DSolid
- `AddRevolvedSolid(Profile, AxisPoint, AxisDir, Angle)` → Acad3DSolid
- Region 属性：Area、Centroid、Perimeter、MomentOfInertia 等

#### 3. 实体编辑（3DSolid 和 Region 上的方法）
- `Solid.Boolean(Operation, Solid)` — acUnion(0)、acIntersection(1)、acSubtraction(2)
- `Solid.CheckInterference(Solid, CreateSolid, OutBool)` → Acad3DSolid
- `Solid.SliceSolid(P1, P2, P3, PositiveSideOnly)` → Acad3DSolid
- `Solid.SectionSolid(P1, P2, P3)` → AcadRegion

#### 4. 通用实体方法（任何 AcadEntity 上都有）
- `Entity.GetBoundingBox(MinPoint, MaxPoint)` → 两个 Variant 输出参数
- `Entity.IntersectWith(Entity, Option)` → Variant 点数组
- `Entity.TransformBy(Matrix)` — 4x4 变换矩阵
- `Entity.Mirror3D(P1, P2, P3)` — 关于 3D 平面镜像
- `Entity.Rotate3D(P1, P2, Angle)` — 关于 3D 轴旋转
- `Entity.SetXData(XDataType, XDataValue)` — 附着扩展实体数据
- `Entity.GetXData(AppName, OutType, OutValue)` — 检索扩展数据
- `Entity.GetExtensionDictionary()` — 获取/创建扩展字典

#### 5. 实体属性（目前缺失）
- `Entity.TrueColor` — AcCmColor 对象（RGB 真彩色）
- `Entity.Material` — 材质名称字符串（读写）
- `Entity.EntityTransparency` — 透明度 0-100%
- `Entity.Hyperlinks` — Hyperlinks 集合
- `Entity.PlotStyleName` — 打印样式名称
- `Entity.HasExtensionDictionary` — 布尔值

#### 6. 打印/绘图
- `Document.Plot.PlotToDevice(plotConfigName)` → 布尔值
- `Document.Plot.PlotToFile(plotConfigName, fileName)` → 布尔值
- `Document.Plot.DisplayPlotPreview(previewType)` → 布尔值
- `Document.Plot.SetLayoutsToPlot(Layouts)` — 布局名称数组
- `Document.Plot.NumberOfCopies`、`QuietErrorMode`、`BatchPlotProgress`
- `Document.PlotConfigurations` — 命名页面设置集合

#### 7. 工具类/几何运算
- `Utility.PolarPoint(Point, Angle, Distance)` → Variant 点
- `Utility.TranslateCoordinates(Point, FromCS, ToCS, Displacement)` → Variant
- `Utility.AngleFromXAxis(Point1, Point2)` → Double（弧度）
- `Utility.AngleToReal(AngleString, Unit)` → Double
- `Utility.AngleToString(Angle, Unit, Precision)` → String
- `Utility.DistanceToReal(DistString, Unit)` → Double
- `Utility.RealToString(Value, Unit, Precision)` → String
- `Utility.CreateTypedArray(ArrayType, Values)` → Variant

### 🟡 第 2 层 — 中等优先级（高级用户功能）

#### 8. Preferences（9 个子对象）
- `Application.Preferences.Display` — 光标大小、颜色、字体、滚动条
- `Application.Preferences.Drafting` — AutoSnap、AutoTrack、孔径
- `Application.Preferences.Files` — SupportPath、TemplatePath、LogFile
- `Application.Preferences.OpenSave` — AutoSaveInterval、Backup、MRU
- `Application.Preferences.Output` — DefaultPlotDevice、PlotStyleTable
- `Application.Preferences.Profiles` — 配置文件管理
- `Application.Preferences.Selection` — 夹点、拾取框、选择模式
- `Application.Preferences.System` — 单文档模式、提示音
- `Application.Preferences.User` — 键盘优先级、插入单位

#### 9. UCS 管理
- `Document.UserCoordinateSystems.Add(Origin, XAxis, YAxis, Name)`
- `Document.ActiveUCS`（获取/设置）— 通过 `SendCommand` 或创建的应用设置当前 UCS
- UCS 属性：`Name`、`Origin`、`XVector`、`YVector`、`GetUCSMatrix()`

#### 10. 命名视图
- `Document.Views.Add(Name)` — 将当前视图保存为命名视图
- `Document.SetView(ViewObject)` — 恢复命名视图
- View 属性：`Name`、`Center`、`Width`、`Height`、`Target`、`Direction`

#### 11. 视口
- `Document.PViewports` / `PaperSpaceViewports` — 浮动图纸空间视口
- `AddPViewport(Center, Width, Height)` — 在布局上创建视口
- `PViewport.On`、`DisplayLocked`、`StandardScale`、`CustomScale`、`ViewDirection`

#### 12. Dictionaries / XRecords
- `Document.Dictionaries` — 命名对象字典集合
- `Dictionary.AddXRecord(Keyword)` — 在字典中创建 XRecord
- `XRecord.SetXRecordData(XDataType, XDataValue)` — 存储数据
- `XRecord.GetXRecordData(OutType, OutValue)` — 检索数据

#### 13. 选择增强功能
- `SelectionSet.SelectOnScreen(FilterType, FilterData)` — 交互式选择
- `SelectionSet.SelectByPolygon(Mode, PointsList, FilterType, FilterData)` — 多边形/栏选
- `SelectionSet.SelectAtPoint(Point, FilterType, FilterData)` — 点选
- `SelectionSet.Highlight(State)`、`Clear()`、`Erase()`、`Update()`

#### 14. 布局属性
- `Layout.GetPaperSize(Width, Height)`、`GetPaperMargins(LowerLeft, UpperRight)`
- `Layout.CanonicalMediaName`、`ConfigName`、`PlotType`、`StyleSheet`
- `Layout.PlotOrigin`、`PlotRotation`、`CenterPlot`、`StandardScale`

#### 15. 材质
- `Document.Materials.Add(MaterialName)` — 创建材质
- `Document.ActiveMaterial` — 新对象的默认材质
- 在实体上：`Entity.Material = "MaterialName"`
- 材质属性：`Name`、`Description`、`DiffuseColor`

#### 16. 网格
- `Add3DMesh(M, N, PointsMatrix)` → AcadPolygonMesh
- `AddPolyfaceMesh(VerticesList, FaceList)` → AcadPolyfaceMesh
- 网格属性：`MClose`、`NClose`、`Coordinates`


### 🔵 第 3 层 — 低优先级（完善性 / 利基功能）

#### 17. 超链接
- 在任何实体上：`Entity.Hyperlinks.Add(URL, Description, NamedLocation)`
- `Hyperlinks.Count`、`Item(Index)`
- Hyperlink 属性：`URL`、`Description`、`NamedLocation`、`Delete()`

#### 18. 其他实体类型
- `Add3DFace(Point1, Point2, Point3, Point4)` → Acad3DFace
- `AddRaster(ImageFile, InsertionPoint, Scale, Rotation)` → AcadRasterImage
- `AddMInsertBlock(InsertPt, Name, X/Y/ZScale, Rotation, Rows, Cols, RowSpc, ColSpc)` → AcadMInsertBlock
- `AddTolerance(Text, InsertionPoint, Direction)` → AcadTolerance
- `AddShape(ShapeName, InsertionPoint, Scale, Rotation)` → AcadShape
- `AddTrace(PointsArray)` → AcadTrace

#### 19. 应用程序级
- `Application.GetAcadState()` → AcadState（`IsQuiescent`）
- `Application.ListArx()`、`LoadArx()`、`UnloadArx()`
- `Application.LoadDVB()`、`UnloadDVB()`、`RunMacro()`
- `Application.Caption`、`FullName`、`Path`、`Version`、`LocaleId`

#### 20. 安全
- `Document.SecurityOptions.Password` — 设置/移除密码
- `SecurityParams.Algorithm`

#### 21. 其他
- `Document.FileDependencies` — 文件依赖集合
- `Document.SectionManager` — 截面管理器
- `Application.MenuGroups` / `MenuBar` — 菜单/工具栏操作
- `Document.RegisteredApplications` — RegApp ID 集合
- `Document.DatabasePreferences` — 数据库级偏好
- `Document.PickfirstSelectionSet` — 先选择后执行

---

## 实现优先级汇总

| 层级 | 数量 | 内容 |
|------|-------|---------|
| **第 1 层** | ~40 个新工具 | 3D 实体、区域、布尔运算、实体方法、打印、工具类/几何运算 |
| **第 2 层** | ~50 个新工具 | Preferences、UCS、视图、视口、字典、选择、布局、材质、网格 |
| **第 3 层** | ~35 个新工具 | 超链接、附加实体类型、应用程序、安全、文件依赖等 |

**总计：~125 个可添加的新 MCP 工具**

---

## 架构说明

所有实现模式遵循现有的三层架构，项目中已建立：
1. **`cad_controller.py`** — 原始 COM 调用，变体编组，错误处理
2. **`cad_tools/*.py`** — 业务逻辑，数据库同步，中文格式化
3. **`server.py`** — MCP 工具注册，`@mcp.tool()`，含中文文档字符串
