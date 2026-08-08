"""Direct model vision: turning CAD artifacts into model-viewable image content.

These tests are COM-free. They exercise the pure-Python embedding pipeline that
lets a vision-capable model SEE drawings through the MCP instead of only
receiving file paths.
"""

import pytest

from src.cad_database import CADDatabase
from src.cad_understanding import vision
from src.cad_understanding.view_grounding import export_view_image_with_mapping

PILImage = pytest.importorskip("PIL.Image")


def make_db(tmp_path):
    db = CADDatabase(str(tmp_path / "cad.db"))
    db.configure_context(
        workspace_root=str(tmp_path),
        conversation_id="conv",
        thread_id="thread",
        drawing_name="vision.dwg",
        drawing_path=str(tmp_path / "vision.dwg"),
    )
    return db


def write_png(path, size=(320, 240), color=(20, 120, 200)):
    PILImage.new("RGB", size, color).save(path)
    return str(path)


def test_prepare_downscales_oversized_image(tmp_path):
    big = write_png(tmp_path / "big.png", size=(4000, 2000))
    prep = vision.prepare_model_image(big, max_dim=1568)
    assert prep["ok"] and prep["embeddable"]
    assert prep["downscaled"]
    assert max(prep["width"], prep["height"]) == 1568
    assert prep["source_image"] == {"width": 4000, "height": 2000}
    assert prep["observed_image"] == {"width": 1568, "height": 784}
    assert prep["observed_to_source"] == [
        [4000 / 1568, 0.0, 0.0],
        [0.0, 2000 / 784, 0.0],
        [0.0, 0.0, 1.0],
    ]
    assert prep["mime_type"] == "image/png"
    assert prep["image_path"].endswith(".png")


def test_prepare_embeds_small_image_as_is(tmp_path):
    small = write_png(tmp_path / "small.png", size=(300, 200))
    prep = vision.prepare_model_image(small)
    assert prep["embeddable"] and not prep["downscaled"]
    assert prep["image_path"] == small


def test_prepare_transcodes_bmp_to_png(tmp_path):
    bmp = tmp_path / "x.bmp"
    PILImage.new("RGB", (100, 100), (0, 0, 0)).save(bmp)
    prep = vision.prepare_model_image(str(bmp))
    assert prep["embeddable"]
    assert prep["image_path"].endswith(".png")
    assert prep["source_format"] == "bmp"


def test_prepare_missing_file_is_graceful(tmp_path):
    prep = vision.prepare_model_image(str(tmp_path / "nope.png"))
    assert not prep["ok"] and not prep["embeddable"]
    assert "not found" in prep["reason"].lower()


def test_prepare_wmf_without_converter_reports_reason(tmp_path, monkeypatch):
    # Force the "no WMF→PNG converter installed" path deterministically.
    monkeypatch.setattr(vision, "_try_convert_wmf_to_raster", lambda p: None)
    wmf = tmp_path / "v.wmf"
    wmf.write_bytes(b"\xd7\xcd\xc6\x9a" + b"\x00" * 40)
    prep = vision.prepare_model_image(str(wmf))
    assert prep["ok"]  # file exists
    assert not prep["embeddable"]
    assert "wmf" in prep["reason"].lower()


def test_view_image_tool_result_carries_embeddable_payload(tmp_path):
    png = write_png(tmp_path / "ref.png")
    result = vision.view_image(png, label="reference")
    assert result["ok"]
    assert result["data"]["vision"]["embeddable"]
    assert result["data"]["label"] == "reference"


def test_resolve_snapshot_images_after_export(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    db = make_db(tmp_path)
    db.upsert_entity(
        "P1", "Polyline", "AcDbPolyline", layer="OUTLINE",
        geometry={"vertices": [[0, 0, 0], [80, 0, 0], [80, 40, 0]], "closed": True},
        bbox=(0, 0, 80, 40), topology_detail="full",
    )
    # Pre-create the raster at the export path; with no live AutoCAD document the
    # exporter treats an existing .png as the VLM-ready image and records it.
    view_png = write_png(tmp_path / "view.png", size=(800, 400))
    export = export_view_image_with_mapping(filepath=view_png, database=db)
    snapshot_id = export["data"]["snapshot"]["snapshot_id"]

    # Explicit id, clean image.
    resolved = vision.resolve_snapshot_images(snapshot_id, which="clean", database=db)
    assert resolved["ok"]
    images = resolved["data"]["images"]
    assert images and images[0]["embeddable"]

    # Latest snapshot (snapshot_id=None) resolves the same snapshot.
    latest = vision.resolve_snapshot_images(None, which="clean", database=db)
    assert latest["ok"]
    assert latest["data"]["snapshot_id"] == snapshot_id


def test_resolve_snapshot_images_unknown_id(tmp_path):
    db = make_db(tmp_path)
    result = vision.resolve_snapshot_images("does-not-exist", database=db)
    assert not result["ok"]


def test_resolve_snapshot_images_can_embed_specific_dense_tile(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    db = make_db(tmp_path)
    db.upsert_entity(
        "P1", "Polyline", "AcDbPolyline", layer="OUTLINE",
        geometry={"vertices": [[0, 0, 0], [80, 0, 0], [80, 40, 0]], "closed": True},
        bbox=(0, 0, 80, 40), topology_detail="full",
    )
    view_png = write_png(tmp_path / "dense-view.png", size=(1024, 768))
    exported = export_view_image_with_mapping(
        filepath=view_png,
        include_overlay=True,
        include_tiles=True,
        tile_size=384,
        database=db,
    )
    snapshot = exported["data"]["snapshot"]
    tile = next(item for item in snapshot["tiles"] if item.get("clean_tile_path"))

    resolved = vision.resolve_snapshot_images(
        snapshot["snapshot_id"],
        which="clean",
        tile_id=tile["tile_id"],
        database=db,
    )

    assert resolved["ok"], resolved
    assert resolved["data"]["tile_id"] == tile["tile_id"]
    assert resolved["data"]["images"][0]["original_path"] == tile["clean_tile_path"]
    assert resolved["data"]["coordinate_space"] == "tile_local"
    assert resolved["data"]["local_to_global"] == tile["local_to_global"]


def test_resolved_snapshot_images_expose_exact_global_and_tile_transforms(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    db = make_db(tmp_path)
    db.upsert_entity(
        "P1", "Point", "AcDbPoint", layer="MARKERS",
        geometry={"point": [0, 0, 0]}, bbox=(0, 0, 0, 0),
        topology_detail="full",
    )
    view_png = write_png(tmp_path / "mapped-view.png", size=(1024, 768))
    exported = export_view_image_with_mapping(
        filepath=view_png,
        include_overlay=True,
        include_tiles=True,
        tile_size=640,
        tile_overlap=0.2,
        database=db,
    )
    snapshot = exported["data"]["snapshot"]

    # Old positional order remains valid: snapshot_id, which, max_dim, database.
    global_result = vision.resolve_snapshot_images(
        snapshot["snapshot_id"], "clean", 512, db
    )
    assert global_result["ok"], global_result
    global_image = global_result["data"]["images"][0]
    assert global_image["source_image"] == {"width": 1024, "height": 768}
    assert global_image["observed_image"] == {"width": 512, "height": 384}
    assert global_image["observed_to_source"] == [
        [2.0, 0.0, 0.0], [0.0, 2.0, 0.0], [0.0, 0.0, 1.0],
    ]
    global_ref = global_result["data"]["source_ref_template"]
    assert global_ref["coordinate_space"] == "observed_image"
    assert global_ref["observed_to_global"] == global_image["observed_to_source"]

    tile = next(
        item for item in snapshot["tiles"]
        if item["global_pixel_bbox"][0] > 0
        and item["global_pixel_bbox"][1] > 0
        and item["image"] == {"width": 640, "height": 640}
    )
    tile_result = vision.resolve_snapshot_images(
        snapshot["snapshot_id"],
        which="clean",
        max_dim=320,
        database=db,
        tile_id=tile["tile_id"],
    )
    assert tile_result["ok"], tile_result
    tile_image = tile_result["data"]["images"][0]
    tile_ref = tile_result["data"]["source_ref_template"]
    x0, y0 = tile["global_pixel_bbox"][:2]
    assert tile_image["source_image"] == {"width": 640, "height": 640}
    assert tile_image["observed_image"] == {"width": 320, "height": 320}
    assert tile_ref["source_coordinate_space"] == "tile_local"
    assert tile_ref["observed_to_global"] == [
        [2.0, 0.0, x0], [0.0, 2.0, y0], [0.0, 0.0, 1.0],
    ]


def test_resolve_trace_image_after_prepare(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from src.cad_understanding.image_trace import prepare_image_trace

    db = make_db(tmp_path)
    source = write_png(tmp_path / "part.png", size=(640, 480), color=(240, 240, 240))
    prepared = prepare_image_trace(image_path=source, domain="mechanical", database=db)
    assert prepared["ok"]
    image_id = prepared["data"]["image_id"]

    resolved = vision.resolve_trace_image(image_id=image_id, role="normalized", database=db)
    assert resolved["ok"]
    assert resolved["data"]["vision"]["embeddable"]

    # Latest trace (image_id=None) resolves the same trace.
    latest = vision.resolve_trace_image(role="normalized", database=db)
    assert latest["ok"]
    assert latest["data"]["image_id"] == image_id

    # Preserve the historical positional order through the new tile option.
    positional = vision.resolve_trace_image(image_id, "normalized", 320, db)
    assert positional["ok"], positional
    assert positional["data"]["vision"]["source_ref_template"][
        "global_coordinate_space"
    ] == "image_global"


def test_resolve_trace_image_can_embed_a_specific_tile(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from src.cad_understanding.image_trace import prepare_image_trace

    db = make_db(tmp_path)
    source = write_png(tmp_path / "dense.png", size=(1280, 800), color=(240, 240, 240))
    prepared = prepare_image_trace(
        image_path=source,
        domain="mechanical",
        tile_size=512,
        tile_overlap=0.2,
        database=db,
    )
    assert prepared["ok"], prepared
    tile = next(
        item for item in prepared["data"]["tiles"]
        if item["global_pixel_bbox"][0] > 0 and item["global_pixel_bbox"][1] > 0
    )

    resolved = vision.resolve_trace_image(
        image_id=prepared["data"]["image_id"],
        role="normalized",
        tile_id=tile["tile_id"],
        database=db,
    )

    assert resolved["ok"], resolved
    assert resolved["data"]["tile_id"] == tile["tile_id"]
    assert resolved["data"]["vision"]["embeddable"]
    assert resolved["data"]["vision"]["original_path"] == tile["image_path"]
    assert resolved["data"]["vision"]["image_path"] == tile["image_path"]
    x1, y1, x2, y2 = tile["global_pixel_bbox"]
    assert resolved["data"]["vision"]["width"] == x2 - x1
    assert resolved["data"]["vision"]["height"] == y2 - y1

    scaled = vision.resolve_trace_image(
        prepared["data"]["image_id"],
        "normalized",
        256,
        db,
        tile_id=tile["tile_id"],
    )
    assert scaled["ok"], scaled
    scaled_vision = scaled["data"]["vision"]
    source_width = x2 - x1
    source_height = y2 - y1
    assert scaled_vision["source_image"] == {
        "width": source_width,
        "height": source_height,
    }
    assert max(
        scaled_vision["observed_image"]["width"],
        scaled_vision["observed_image"]["height"],
    ) == 256
    observed_width = scaled_vision["observed_image"]["width"]
    observed_height = scaled_vision["observed_image"]["height"]
    assert scaled["data"]["source_ref_template"]["observed_to_global"] == [
        [source_width / observed_width, 0.0, x1],
        [0.0, source_height / observed_height, y1],
        [0.0, 0.0, 1.0],
    ]


def test_prep_payload_builds_real_image_content(tmp_path):
    """The payload must convert to MCP ImageContent — proving the model sees it."""
    from mcp.server.fastmcp.utilities.types import Image as MCPImage

    png = write_png(tmp_path / "shot.png", size=(500, 500))
    prep = vision.prepare_model_image(png)
    assert prep["embeddable"]
    content = MCPImage(path=prep["image_path"]).to_image_content()
    assert content.type == "image"
    assert content.mimeType == "image/png"
    assert content.data  # non-empty base64 payload


def test_vision_capabilities_reports_support():
    caps = vision.vision_capabilities()
    assert caps["direct_vision"] is True
    assert "png" in [fmt.lstrip(".") for fmt in caps["embeddable_formats"]]
    assert "view_image" in caps["vision_tools"]


def test_oversized_file_is_not_embedded(tmp_path, monkeypatch):
    # Force the inline byte ceiling low so even a small PNG trips it.
    monkeypatch.setattr(vision, "MAX_EMBED_BYTES", 10)
    png = write_png(tmp_path / "shot.png", size=(400, 400))
    prep = vision.prepare_model_image(png, max_dim=4096)
    assert prep["ok"] and not prep["embeddable"]
    assert "inline limit" in prep["reason"]


def test_resolve_snapshot_db_error_is_graceful(tmp_path, monkeypatch):
    db = make_db(tmp_path)

    def boom(*args, **kwargs):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(vision, "_latest_snapshot_id", boom)
    result = vision.resolve_snapshot_images(None, database=db)
    assert not result["ok"]
    assert "snapshot" in result["message"].lower()


def test_invalid_path_is_graceful():
    # NUL byte makes the OS path illegal; must not raise.
    prep = vision.prepare_model_image("bad\x00name.png")
    assert not prep["ok"] and not prep["embeddable"]
    assert prep["reason"]
