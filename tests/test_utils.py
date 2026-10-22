"""工具函数与资源管理测试。"""

from __future__ import annotations

import base64
from pathlib import Path

from doc_converter.assets import AssetStore, MODE_DATAURI, MODE_FILE
from doc_converter.model import Document, Heading, Paragraph, Text
from doc_converter.utils import (guess_ext, human_size, make_data_uri,
                                 needs_title_header, parse_data_uri,
                                 resolve_local_path, sanitize_filename,
                                 short_hash)


def test_sanitize_filename_keeps_chinese_and_blocks_reserved():
    assert sanitize_filename("报告 2024.pdf") == "报告_2024.pdf"
    assert sanitize_filename("CON.txt").startswith("_")
    assert sanitize_filename("a/b\\c") == "c"
    assert sanitize_filename("") == "file"
    assert len(sanitize_filename("x" * 300)) <= 120


def test_data_uri_roundtrip():
    payload = b"\x00\x01hello"
    uri = make_data_uri(payload, "application/octet-stream")
    data, mime = parse_data_uri(uri)
    assert data == payload
    assert mime == "application/octet-stream"


def test_guess_ext_and_human_size():
    assert guess_ext("image/jpeg") == "jpg"
    assert guess_ext("image/svg+xml") == "svg"
    assert guess_ext("application/unknown", "bin") == "bin"
    assert human_size(512) == "512B"
    assert human_size(2048) == "2.0KB"


def test_short_hash_stable():
    assert short_hash(b"abc", 6) == short_hash(b"abc", 6)
    assert len(short_hash(b"abc", 6)) == 6


def test_resolve_local_path(tmp_path):
    target = tmp_path / "img.png"
    target.write_bytes(b"png")
    assert resolve_local_path("img.png", tmp_path) == target
    assert resolve_local_path("missing.png", tmp_path) is None
    assert resolve_local_path("https://example.com/a.png", tmp_path) is None
    assert resolve_local_path(target.as_uri(), None) == target


def test_needs_title_header():
    doc = Document(title="标题")
    assert needs_title_header(doc) is True
    doc.blocks = [Heading(level=1, children=[Text("标题")])]
    assert needs_title_header(doc) is False
    doc.blocks = [Heading(level=1, children=[Text("别的标题")])]
    assert needs_title_header(doc) is True
    assert needs_title_header(Document()) is False


def test_asset_store_file_mode(tmp_path, png_bytes):
    store = AssetStore(tmp_path, "report", mode=MODE_FILE)
    asset = store.add_image(png_bytes, "image/png", hint="示意图")
    assert asset.path is not None and asset.path.exists()
    assert asset.ref.startswith("report_assets/")
    assert store.files


def test_asset_store_datauri_mode(tmp_path, png_bytes):
    store = AssetStore(tmp_path, "report", mode=MODE_DATAURI)
    asset = store.add_image(png_bytes, "image/png")
    assert asset.ref.startswith("data:image/png;base64,")
    assert asset.path is None


def test_asset_store_attachment_always_written(tmp_path):
    store = AssetStore(tmp_path, "report", mode=MODE_DATAURI)
    asset = store.add_attachment("数据.csv", b"a,b\n1,2\n")
    assert asset.path is not None and asset.path.exists()
    assert "report_assets/" in asset.ref


def test_asset_store_reads_local_image(tmp_path, png_bytes):
    source = tmp_path / "src"
    source.mkdir()
    image = source / "pic.png"
    image.write_bytes(png_bytes)
    store = AssetStore(tmp_path / "out", "doc", mode=MODE_FILE,
                       source_dir=source)
    asset = store.resolve_image("pic.png", None, "image/png")
    assert asset.data == png_bytes


def test_asset_store_missing_image_reports_warning(tmp_path):
    warnings = []
    store = AssetStore(tmp_path, "doc", warn=warnings.append)
    asset = store.resolve_image("missing.png", None, "image/png")
    assert asset.error
    assert warnings
