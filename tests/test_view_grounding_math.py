from src.cad_understanding.view_grounding import (
    apply_matrix_2d,
    bbox_world_to_pixel,
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
