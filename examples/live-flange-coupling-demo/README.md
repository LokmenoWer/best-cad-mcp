# Live AutoCAD flange-coupling demo

This example records a real best-cad-mcp session against AutoCAD. It uses the
repository's optimized `precise_draw_from_spec` prompt to create a true-size A3
bolted flange-coupling assembly drawing, then rescans and verifies the result.

![Final A3 flange-coupling assembly drawing](../../docs/images/live-flange-coupling-demo.png)

## Artifacts

| Artifact | Purpose |
| --- | --- |
| [flange-coupling-assembly-final.DXF](flange-coupling-assembly-final.DXF) | Interchange snapshot of the final model-space entities |
| [flange-coupling-assembly-readme-demo.pdf](flange-coupling-assembly-readme-demo.pdf) | One-page landscape A3 showcase plot |
| [prompt.md](prompt.md) | Exact natural-language request and workflow constraints |
| [cadplans.json](cadplans.json) | Validated and dry-run CADPlan bundle |
| [verification-report.json](verification-report.json) | Execution, scan, semantic, constraint, geometry, and export evidence |
| [generate_demo.py](generate_demo.py) | MCP client used to generate and refresh the demo |

## Recorded verification

The checked-in verification snapshot records:

- 18 bounded generation phases with 162 steps;
- 2 autonomous layout-repair plans with 4 steps, within the prompt's limit;
- 3 separately recorded, operator-authorized presentation-only release-QA
  plans with 10 steps;
- validation and dry-run before every transactional execution;
- a final rescan of 91 AutoCAD entities;
- 129 detected semantic objects and 7 true dimension annotations processed by the binder;
- 134 checked constraints: 127 satisfied, 7 unknown, and 0 violated;
- post-repair geometry validation with 0 reported issues; and
- clean PNG evidence plus a 1191 x 842 pt A3 PDF. A mapped handle overlay is
  intentionally omitted because the PDF raster and current AutoCAD view do not
  share a verified transform.

The seven unknown constraints are retained in the report instead of being
silently counted as satisfied. This demo makes no formal standards-compliance
or manufacturing-readiness claim.

## Reproduce

Use a disposable clone or workspace, keep AutoCAD open under the same Windows
account, and review [prompt.md](prompt.md) before allowing modification. From
the repository root:

```powershell
.\.venv\Scripts\python.exe examples\live-flange-coupling-demo\generate_demo.py --execute --force
```

The script communicates only through the repository's MCP server. It does not
contain a standalone AutoCAD COM automation path. The final layout is already
part of the fresh-generation plans. The two autonomous repairs and three
separately authorized release-QA plans above document the visual iterations
used while producing this checked-in run.

The native DWG is generated and saved locally but is intentionally not
published because native DWG metadata records the workstation login name. The
sanitized DXF is the downloadable CAD artifact for this public example.

For reproducibility, the client builds the deterministic CADPlans used by this
recorded run. This demonstrates live execution of the optimized prompt's
guarded workflow, not an unscripted one-shot LLM generation.

With the generated DWG active in AutoCAD, evidence can be refreshed without
redrawing geometry:

```powershell
.\.venv\Scripts\python.exe examples\live-flange-coupling-demo\generate_demo.py --execute --refresh-exports
.\.venv\Scripts\python.exe examples\live-flange-coupling-demo\generate_demo.py --execute --refresh-dxf
```

The drawing is marked `DEMO - NOT FOR MANUFACTURE` and follows generic
mechanical assembly practice without claiming ISO, GB, ASME, or other formal
compliance.
