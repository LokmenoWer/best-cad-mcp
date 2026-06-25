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
