from src.cad_database import CADDatabase
from src.cad_understanding.constraints import extract_drawing_constraints, get_constraints
from src.cad_understanding.dimension_binding import bind_dimension_to_geometry


def make_db(tmp_path):
    db = CADDatabase(str(tmp_path / "cad.db"))
    db.configure_context(workspace_root=str(tmp_path), conversation_id="conv", thread_id="thread")
    return db


def test_extract_constraints_for_basic_geometry(tmp_path):
    db = make_db(tmp_path)
    db.upsert_entity(
        "C1",
        "Circle",
        "AcDbCircle",
        geometry={"center": [0, 0, 0], "radius": 5},
        bbox=(-5, -5, 5, 5),
    )
    db.upsert_entity(
        "L1",
        "Line",
        "AcDbLine",
        geometry={"start": [0, 0, 0], "end": [10, 0, 0]},
        bbox=(0, 0, 10, 0),
    )
    db.upsert_entity(
        "L2",
        "Line",
        "AcDbLine",
        geometry={"start": [0, 5, 0], "end": [10, 5, 0]},
        bbox=(0, 5, 10, 5),
    )
    db.upsert_entity(
        "P1",
        "Polyline",
        "AcDbPolyline",
        geometry={"vertices": [[0, 0, 0], [1, 0, 0], [1, 1, 0]], "closed": True},
        bbox=(0, 0, 1, 1),
    )

    result = extract_drawing_constraints(database=db)
    constraints = get_constraints(database=db)["data"]["constraints"]
    types = {item["constraint_type"] for item in constraints}

    assert result["ok"] is True
    assert {"radius", "diameter", "distance", "parallel", "closed_profile"}.issubset(types)
    assert all(item["status"] in {"satisfied", "unknown"} for item in constraints)


def test_radial_dimension_binds_to_circle(tmp_path):
    db = make_db(tmp_path)
    db.upsert_entity(
        "C1",
        "Circle",
        "AcDbCircle",
        geometry={"center": [0, 0, 0], "radius": 5},
        bbox=(-5, -5, 5, 5),
    )
    db.upsert_entity(
        "D1",
        "RadialDimension",
        "AcDbRadialDimension",
        geometry={"measurement": 5, "center": [0, 0, 0]},
        bbox=(-2, -2, 8, 8),
    )

    result = bind_dimension_to_geometry("D1", database=db)
    binding = result["data"]["binding"]

    assert result["ok"] is True
    assert binding["status"] == "bound"
    assert binding["best_target"]["handle"] == "C1"
    assert binding["best_target"]["primitive_key"]


def test_diameter_dimension_binds_to_circle(tmp_path):
    db = make_db(tmp_path)
    db.upsert_entity(
        "C1",
        "Circle",
        "AcDbCircle",
        geometry={"center": [0, 0, 0], "radius": 5},
        bbox=(-5, -5, 5, 5),
    )
    db.upsert_entity(
        "D1",
        "DiametricDimension",
        "AcDbDiametricDimension",
        geometry={"measurement": 10},
        bbox=(-6, -6, 6, 6),
    )

    binding = bind_dimension_to_geometry("D1", database=db)["data"]["binding"]

    assert binding["status"] == "bound"
    assert binding["best_target"]["actual"] == 10


def test_linear_dimension_binds_to_line(tmp_path):
    db = make_db(tmp_path)
    db.upsert_entity(
        "L1",
        "Line",
        "AcDbLine",
        geometry={"start": [0, 0, 0], "end": [10, 0, 0]},
        bbox=(0, 0, 10, 0),
    )
    db.upsert_entity(
        "D1",
        "LinearDimension",
        "AcDbRotatedDimension",
        geometry={
            "measurement": 10,
            "extension_line_1_point": [0, 0, 0],
            "extension_line_2_point": [10, 0, 0],
        },
        bbox=(0, -2, 10, 2),
    )

    binding = bind_dimension_to_geometry("D1", database=db)["data"]["binding"]

    assert binding["status"] == "bound"
    assert binding["best_target"]["handle"] == "L1"


def test_ambiguous_dimension_remains_unknown(tmp_path):
    db = make_db(tmp_path)
    for handle, x in [("C1", -10), ("C2", 10)]:
        db.upsert_entity(
            handle,
            "Circle",
            "AcDbCircle",
            geometry={"center": [x, 0, 0], "radius": 5},
            bbox=(x - 5, -5, x + 5, 5),
        )
    db.upsert_entity(
        "D1",
        "RadialDimension",
        "AcDbRadialDimension",
        geometry={"measurement": 5},
        bbox=(-2, -2, 2, 2),
    )

    binding = bind_dimension_to_geometry("D1", database=db)["data"]["binding"]

    assert binding["status"] == "ambiguous"


def test_text_override_mismatch_creates_violated_constraint(tmp_path):
    db = make_db(tmp_path)
    db.upsert_entity(
        "C1",
        "Circle",
        "AcDbCircle",
        geometry={"center": [0, 0, 0], "radius": 5},
        bbox=(-5, -5, 5, 5),
    )
    db.upsert_entity(
        "D1",
        "RadialDimension",
        "AcDbRadialDimension",
        geometry={"measurement": 5, "text_override": "R6"},
        bbox=(-2, -2, 8, 8),
    )

    extract_drawing_constraints(database=db)
    constraints = get_constraints(database=db)["data"]["constraints"]
    dim_constraints = [item for item in constraints if item["source"].startswith("dimension:")]

    assert dim_constraints[0]["status"] == "violated"
    assert dim_constraints[0]["value"] == 6
    assert dim_constraints[0]["actual"] == 5
