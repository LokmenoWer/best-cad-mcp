from src.cad_understanding.view_grounding import (
    apply_matrix_2d,
    bbox_world_to_pixel,
    compute_view_transform,
    compute_plan_view_transform,
)


def test_plan_view_transform_round_trip():
    transform = compute_plan_view_transform(
        {"center": [0, 0, 0], "height": 50, "width": 100, "direction": [0, 0, 1]},
        1000,
        500,
    )

    pixel = apply_matrix_2d(transform["world_to_pixel"], 0, 0)
    world = apply_matrix_2d(transform["pixel_to_world"], pixel[0], pixel[1])

    assert pixel == [500, 250]
    assert abs(world[0]) < 1e-9
    assert abs(world[1]) < 1e-9


def test_bbox_world_to_pixel():
    transform = compute_plan_view_transform(
        {"center": [0, 0, 0], "height": 100, "width": 100, "direction": [0, 0, 1]},
        100,
        100,
    )

    bbox = bbox_world_to_pixel((-10, -10, 10, 10), transform["world_to_pixel"])

    assert bbox == [40, 40, 60, 60]


def test_transform_preserves_aspect_with_letterboxing():
    transform = compute_plan_view_transform(
        {"center": [0, 0, 0], "height": 100, "width": 200, "direction": [0, 0, 1]},
        1000,
        1000,
    )

    bbox = bbox_world_to_pixel((-10, -10, 10, 10), transform["world_to_pixel"])

    assert bbox == [450, 450, 550, 550]
    assert transform["content_bbox"] == [0.0, 250.0, 1000.0, 750.0]


def test_twist_view_transform_round_trip():
    transform = compute_view_transform(
        {"center": [0, 0, 0], "height": 100, "width": 100, "direction": [0, 0, 1], "twist": 1.5707963267948966},
        1000,
        1000,
    )

    pixel = apply_matrix_2d(transform["world_to_pixel"], 10, 0)
    world = apply_matrix_2d(transform["pixel_to_world"], pixel[0], pixel[1])

    assert abs(world[0] - 10) < 1e-9
    assert abs(world[1]) < 1e-9
    assert transform["confidence"] > 0.9
    assert any("twist" in warning.lower() for warning in transform["warnings"])


def test_changed_view_center_and_height():
    transform = compute_view_transform(
        {"center": [100, 50, 0], "height": 50, "width": 100, "direction": [0, 0, 1]},
        1000,
        500,
    )

    assert apply_matrix_2d(transform["world_to_pixel"], 100, 50) == [500, 250]
    assert apply_matrix_2d(transform["world_to_pixel"], 50, 75) == [0, 0]


def test_pixel_world_pixel_roundtrip():
    transform = compute_view_transform(
        {"center": [10, -5, 0], "height": 80, "width": 120, "direction": [0, 0, 1], "twist": 0.25},
        1200,
        800,
    )

    world = apply_matrix_2d(transform["pixel_to_world"], 333, 222)
    pixel = apply_matrix_2d(transform["world_to_pixel"], world[0], world[1])

    assert abs(pixel[0] - 333) < 1e-9
    assert abs(pixel[1] - 222) < 1e-9


def test_non_plan_view_warns_and_lowers_confidence():
    transform = compute_view_transform(
        {"center": [0, 0, 0], "height": 100, "width": 100, "direction": [1, 0, 1]},
        100,
        100,
    )

    assert transform["confidence"] < 0.8
    assert "non_plan_view" in transform["limitations"]
