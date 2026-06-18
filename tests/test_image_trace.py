import struct
from pathlib import Path

from src.cad_database import CADDatabase
from src.cad_understanding.image_trace import (
    compile_image_spec_to_cad_plan,
    prepare_image_trace,
    submit_image_drawing_spec,
    validate_image_drawing_spec,
    validate_image_fidelity_contract,
)
from src.cad_understanding.plan import dry_run_cad_plan, validate_cad_plan


def make_db(tmp_path):
    db = CADDatabase(str(tmp_path / "cad.db"))
    db.configure_context(
        workspace_root=str(tmp_path),
        conversation_id="conv",
        thread_id="thread",
        drawing_name="image-trace.dwg",
        drawing_path=str(tmp_path / "image-trace.dwg"),
    )
    return db


def write_bmp(path: Path, width: int = 64, height: int = 48):
    row_size = ((width * 3 + 3) // 4) * 4
    pixel_data = bytearray()
    for _ in range(height):
        pixel_data.extend(bytes([255, 255, 255]) * width)
        pixel_data.extend(b"\x00" * (row_size - width * 3))
    file_size = 14 + 40 + len(pixel_data)
    header = b"BM" + struct.pack("<IHHI", file_size, 0, 0, 54)
    dib = struct.pack(
        "<IiiHHIIiiII",
        40,
        width,
        height,
        1,
        24,
        0,
        len(pixel_data),
        2835,
        2835,
        0,
        0,
    )
    path.write_bytes(header + dib + pixel_data)


def sample_spec():
    return {
        "schema_version": "ImageDrawingSpec/v1",
        "domain": "mechanical",
        "units": "mm",
        "calibration_candidates": [
            {
                "id": "cal_1",
                "value": 40,
                "pixel_distance": 40,
                "confidence": 0.92,
                "evidence": {"text": "40 mm overall width"},
            }
        ],
        "features": [
            {
                "id": "plate_outline",
                "kind": "chamfered_rectangle",
                "confidence": 0.93,
                "pixel_bbox": [10, 10, 50, 40],
                "pixel_geometry": {
                    "vertices": [[15, 10], [45, 10], [50, 15], [50, 35], [45, 40], [15, 40], [10, 35], [10, 15]],
                    "closed": True,
                },
                "chamfers": [{"corner": "all", "pixel_length": 5}],
                "evidence": {"text": "four visible chamfered corners"},
            },
            {
                "id": "hole_1",
                "kind": "hole",
                "confidence": 0.9,
                "pixel_bbox": [25, 20, 35, 30],
                "pixel_geometry": {"center": [30, 25], "radius": 5},
                "evidence": {"text": "central circular hole"},
            },
        ],
        "geometry": [],
        "annotations": [
            {
                "id": "dim_width",
                "kind": "dimension",
                "confidence": 0.88,
                "pixel_bbox": [10, 42, 50, 47],
                "pixel_geometry": {"p1": [10, 40], "p2": [50, 40], "text_point": [30, 46]},
                "text": "40",
                "evidence": {"text": "readable width dimension"},
            }
        ],
        "relations": [],
        "tables": [
            {
                "id": "bom",
                "kind": "table",
                "confidence": 0.82,
                "pixel_bbox": [2, 2, 18, 9],
                "rows": [["ITEM", "QTY"], ["PLATE", "1"]],
                "evidence": {"text": "small readable table"},
            }
        ],
        "uncertainties": [],
    }


def tube_bundle_hatch_spec():
    holes = []
    for row, y in enumerate((30, 40)):
        for col, x in enumerate((30, 40, 50)):
            holes.append({
                "id": f"tube_{row}_{col}",
                "kind": "hole",
                "confidence": 0.91,
                "pixel_bbox": [x - 2, y - 2, x + 2, y + 2],
                "pixel_geometry": {"center": [x, y], "radius": 2},
                "evidence": {"text": "repeated tube hole"},
            })
    member_ids = [hole["id"] for hole in holes]
    return {
        "schema_version": "ImageDrawingSpec/v1",
        "domain": "mechanical",
        "units": "mm",
        "image_height": 100,
        "calibration_candidates": [
            {
                "id": "cal_1",
                "value": 100,
                "pixel_distance": 100,
                "confidence": 0.95,
                "evidence": {"text": "100 mm scale"},
            }
        ],
        "features": [
            *holes,
            {
                "id": "tube_bundle",
                "kind": "pattern",
                "confidence": 0.92,
                "pixel_bbox": [28, 28, 52, 42],
                "member_ids": member_ids,
                "pattern_type": "rectangular",
                "rows": 2,
                "columns": 3,
                "row_spacing": 10,
                "column_spacing": 10,
                "evidence": {"text": "2 by 3 tube bundle pitch pattern"},
            },
            {
                "id": "tube_sheet_hatch",
                "kind": "hatch",
                "confidence": 0.87,
                "pixel_bbox": [20, 20, 60, 50],
                "pattern_name": "ANSI31",
                "evidence": {"text": "section hatch bounded by sheet outline"},
            },
        ],
        "geometry": [
            {
                "id": "sheet_outline",
                "kind": "rectangle",
                "confidence": 0.94,
                "pixel_bbox": [20, 20, 60, 50],
                "evidence": {"text": "closed sheet outline"},
            }
        ],
        "annotations": [],
        "relations": [
            {"type": "hatch_boundary", "source": "tube_sheet_hatch", "target": "sheet_outline"}
        ],
        "tables": [],
        "uncertainties": [],
    }


def test_prepare_image_trace_with_bmp(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    db = make_db(tmp_path)
    image = tmp_path / "source.bmp"
    write_bmp(image)

    result = prepare_image_trace(str(image), database=db)

    assert result["ok"]
    assert result["data"]["image"]["width"] == 64
    assert result["data"]["image"]["height"] == 48
    assert Path(result["data"]["normalized_image_path"]).exists()
    assert Path(result["data"]["tile_index_path"]).exists()
    assert result["data"]["tiles"]


def test_validate_image_drawing_spec_rejects_bad_items(tmp_path):
    db = make_db(tmp_path)
    bad_spec = {
        "schema_version": "ImageDrawingSpec/v1",
        "domain": "mechanical",
        "units": "mm",
        "calibration_candidates": [],
        "features": [{"id": "bad", "kind": "plain_square", "confidence": 1.2, "pixel_bbox": [0, 0, 1, 1]}],
        "geometry": [],
        "annotations": [],
        "relations": [],
        "tables": [],
        "uncertainties": [],
    }

    result = validate_image_drawing_spec(bad_spec, database=db)

    assert not result["ok"]
    messages = " ".join(" ".join(err["errors"]) for err in result["data"]["errors"])
    assert "kind must be one" in messages
    assert "confidence" in messages
    assert "evidence" in messages


def test_compile_complex_spec_to_valid_dry_run_plan(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    db = make_db(tmp_path)
    image = tmp_path / "source.bmp"
    write_bmp(image)
    prepared = prepare_image_trace(str(image), database=db)
    image_id = prepared["data"]["image_id"]
    submitted = submit_image_drawing_spec(image_id, sample_spec(), source_model="unit-test", database=db)
    compiled = compile_image_spec_to_cad_plan(image_id=image_id, database=db)

    assert submitted["ok"]
    assert compiled["ok"], compiled
    plan = compiled["data"]["plan"]
    ops = [step["op"] for step in plan["steps"]]
    assert "draw_polyline" in ops
    assert "draw_rectangle" not in ops
    assert "draw_circle" in ops
    assert "add_linear_dimension" in ops
    assert "add_table" in ops
    assert "edit_table_cell" in ops
    assert validate_cad_plan(plan)["ok"]
    assert dry_run_cad_plan(plan)["ok"]


def test_compile_pattern_and_hatch_bind_to_plan_handles(tmp_path):
    db = make_db(tmp_path)
    compiled = compile_image_spec_to_cad_plan(spec=tube_bundle_hatch_spec(), database=db)

    assert compiled["ok"], compiled
    assert not [warning for warning in compiled["warnings"] if "remains in the spec" in warning]
    assert not [warning for warning in compiled["warnings"] if "could not bind" in warning]
    plan = compiled["data"]["plan"]
    ops = [step["op"] for step in plan["steps"]]
    assert ops.count("draw_circle") == 1
    assert "array_rectangular" in ops
    assert "add_hatch" in ops
    assert "hatch_add_boundary" in ops

    array_step = next(step for step in plan["steps"] if step["op"] == "array_rectangular")
    assert array_step["args"]["handle"] == "$tube_0_0"
    assert array_step["args"]["rows"] == 2
    assert array_step["args"]["columns"] == 3

    boundary_step = next(step for step in plan["steps"] if step["op"] == "hatch_add_boundary")
    assert boundary_step["args"]["handle"] == "$tube_sheet_hatch"
    assert boundary_step["args"]["boundary_handles"] == ["$sheet_outline"]
    assert validate_cad_plan(plan)["ok"]
    dry = dry_run_cad_plan(plan)
    assert dry["ok"], dry


def test_fidelity_rejects_chamfered_rectangle_downgrade():
    spec = sample_spec()
    plan = {
        "steps": [
            {
                "step_id": "plate_outline",
                "op": "draw_rectangle",
                "args": {"x1": 0, "y1": 0, "x2": 1, "y2": 1},
            }
        ]
    }

    result = validate_image_fidelity_contract(spec, plan)

    assert not result["ok"]
    assert "chamfered_rectangle" in result["data"]["errors"][0]["kind"]


def test_fidelity_rejects_filleted_rectangle_without_radius_preservation():
    spec = {
        **sample_spec(),
        "features": [
            {
                "id": "rounded",
                "kind": "filleted_rectangle",
                "confidence": 0.9,
                "pixel_bbox": [0, 0, 20, 10],
                "pixel_geometry": {"vertices": [[2, 0], [18, 0], [20, 2], [20, 8], [18, 10], [2, 10], [0, 8], [0, 2]]},
                "radius": 2,
                "evidence": {"text": "rounded corners"},
            }
        ],
    }
    plan = {
        "steps": [
            {
                "step_id": "rounded",
                "op": "draw_polyline",
                "args": {"points": [0, 0, 1, 0, 1, 1], "closed": True},
            }
        ]
    }

    result = validate_image_fidelity_contract(spec, plan)

    assert not result["ok"]
    assert "filleted_rectangle" in result["data"]["errors"][0]["kind"]
