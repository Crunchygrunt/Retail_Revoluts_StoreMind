"""
Tests for image_hosting.py.

The Pillow-dependent paths here run against REAL Pillow (it was
available in the assistant's sandbox), so the normalization behavior is
genuinely verified rather than stubbed. Only the GCS backend is mocked —
`google-cloud-storage` needs real credentials and a real bucket.
"""

from __future__ import annotations

import base64
import io

import pytest

from image_hosting import (
    MAX_DIMENSION,
    HostedImage,
    LocalImageStore,
    decode_image_input,
    normalize_image,
    resolve_image_url,
)


def make_jpeg(size=(800, 600), color=(120, 60, 30)) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="JPEG")
    return buf.getvalue()


def make_png_rgba(size=(80, 80)) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGBA", size, (10, 20, 30, 128)).save(buf, format="PNG")
    return buf.getvalue()


class RecordingStore:
    """Stand-in for a backend; records what it was asked to store."""

    backend_name = "recording"

    def __init__(self, fail: bool = False):
        self.fail = fail
        self.stored: list[tuple[str, int, str]] = []

    def store(self, data, name, content_type):
        if self.fail:
            raise RuntimeError("simulated storage outage")
        self.stored.append((name, len(data), content_type))
        return f"https://cdn.example.com/{name}", []


# ---------------------------------------------------------------------
# decode_image_input
# ---------------------------------------------------------------------

def test_raw_bytes_pass_through():
    data = make_jpeg()
    assert decode_image_input(data) == data


def test_data_uri_is_decoded():
    data = make_jpeg()
    uri = "data:image/jpeg;base64," + base64.b64encode(data).decode()
    assert decode_image_input(uri) == data


def test_bare_base64_is_decoded():
    data = make_jpeg()
    assert decode_image_input(base64.b64encode(data).decode()) == data


def test_http_url_is_not_treated_as_inline_bytes():
    assert decode_image_input("https://example.com/photo.jpg") is None


def test_a_product_name_is_not_mistaken_for_base64():
    """Short strings and ordinary text must not accidentally decode."""
    for text in ("Cotton Kurta", "", "   ", "Tata Namak 1kg"):
        assert decode_image_input(text) is None


def test_non_string_non_bytes_input_is_rejected():
    for value in (None, 42, [], {}):
        assert decode_image_input(value) is None


# ---------------------------------------------------------------------
# normalize_image  (real Pillow)
# ---------------------------------------------------------------------

def test_oversized_image_is_downscaled():
    from PIL import Image

    data = make_jpeg(size=(3000, 2000))
    result = normalize_image(data)
    assert result is not None
    normalized, content_type, notes = result
    assert content_type == "image/jpeg"
    assert max(Image.open(io.BytesIO(normalized)).size) <= MAX_DIMENSION
    assert any("resized" in n for n in notes)


def test_normalization_reduces_byte_size():
    data = make_jpeg(size=(3000, 2000))
    normalized, _, _ = normalize_image(data)
    assert len(normalized) < len(data)


def test_rgba_png_is_flattened_not_rejected():
    """Verified against real Pillow: saving RGBA as JPEG raises OSError.
    Transparent product cut-outs are common, so they must be flattened
    (onto white, not black) rather than dropped."""
    from PIL import Image

    result = normalize_image(make_png_rgba())
    assert result is not None
    normalized, _, notes = result
    assert Image.open(io.BytesIO(normalized)).mode == "RGB"
    assert any("transparency" in n for n in notes)


def test_exif_is_stripped():
    """Phone photos carry GPS coordinates. A seller uploading a catalog
    shouldn't be publishing their home location."""
    from PIL import Image

    buf = io.BytesIO()
    img = Image.new("RGB", (100, 100), (5, 5, 5))
    exif = img.getexif()
    exif[271] = "TestMake"  # 271 = Make
    img.save(buf, format="JPEG", exif=exif)

    assert Image.open(io.BytesIO(buf.getvalue())).getexif().get(271) == "TestMake"

    normalized, _, _ = normalize_image(buf.getvalue())
    assert Image.open(io.BytesIO(normalized)).getexif().get(271) is None


def test_garbage_bytes_return_none_rather_than_raising():
    """This runs inside /ingest — it must degrade, not explode."""
    for junk in (b"not an image at all", b"", b"\x00\x01\x02\x03" * 100):
        assert normalize_image(junk) is None


def test_absurdly_large_input_is_refused_before_decoding():
    assert normalize_image(b"x" * (13 * 1024 * 1024)) is None


# ---------------------------------------------------------------------
# resolve_image_url
# ---------------------------------------------------------------------

def test_existing_public_url_is_used_as_is_not_rehosted():
    store = RecordingStore()
    result = resolve_image_url({"image_url": "https://seller.example/p.jpg"}, store)
    assert result.source == "source_url"
    assert result.url == "https://seller.example/p.jpg"
    assert store.stored == [], "a already-addressable image must not be re-uploaded"


def test_inline_image_bytes_get_hosted_and_return_a_real_url():
    store = RecordingStore()
    uri = "data:image/jpeg;base64," + base64.b64encode(make_jpeg()).decode()
    result = resolve_image_url({"image": uri}, store)
    assert result.source == "hosted"
    assert result.url.startswith("https://cdn.example.com/")
    assert result.content_hash and len(store.stored) == 1


def test_no_image_anywhere_is_reported_honestly_and_never_fabricated():
    """The one ACP field StoreMind refuses to close on the seller's
    behalf — inventing a product photo would misrepresent the item at
    the moment of transaction."""
    store = RecordingStore()
    result = resolve_image_url({"name": "Loose sugar 1kg", "price": 45}, store)
    assert result.source == "none"
    assert result.url is None
    assert store.stored == []


def test_identical_bytes_produce_an_identical_blob_name():
    """Content-addressed naming: re-ingesting the same catalog must not
    duplicate uploads."""
    store = RecordingStore()
    data = make_jpeg()
    uri = "data:image/jpeg;base64," + base64.b64encode(data).decode()
    a = resolve_image_url({"image": uri}, store)
    b = resolve_image_url({"image": uri}, store)
    assert a.content_hash == b.content_hash
    assert a.url == b.url


def test_different_images_produce_different_blob_names():
    store = RecordingStore()
    a = resolve_image_url(
        {"image": base64.b64encode(make_jpeg(color=(1, 2, 3))).decode()}, store
    )
    b = resolve_image_url(
        {"image": base64.b64encode(make_jpeg(color=(200, 100, 50))).decode()}, store
    )
    assert a.content_hash != b.content_hash


def test_storage_failure_degrades_instead_of_breaking_the_batch():
    store = RecordingStore(fail=True)
    uri = "data:image/jpeg;base64," + base64.b64encode(make_jpeg()).decode()
    result = resolve_image_url({"image": uri}, store)
    assert result.source == "none"
    assert result.url is None
    assert any("hosting failed" in n for n in result.notes)


def test_undecodable_image_data_is_reported_distinctly_from_no_image():
    store = RecordingStore()
    result = resolve_image_url({"image": base64.b64encode(b"x" * 200).decode()}, store)
    assert result.source == "none"
    assert any("not decodable" in n for n in result.notes)


# ---------------------------------------------------------------------
# LocalImageStore
# ---------------------------------------------------------------------

def test_local_store_writes_the_file_and_returns_a_serving_url(tmp_path=None):
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        store = LocalImageStore(media_dir=Path(tmp), base_url="http://localhost:8080")
        url, notes = store.store(b"fake-bytes", "abc123.jpg", "image/jpeg")
        assert url == "http://localhost:8080/media/abc123.jpg"
        assert (Path(tmp) / "abc123.jpg").read_bytes() == b"fake-bytes"


def test_local_store_does_not_rewrite_an_existing_blob():
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        store = LocalImageStore(media_dir=Path(tmp), base_url="http://x")
        store.store(b"original", "same.jpg", "image/jpeg")
        store.store(b"different", "same.jpg", "image/jpeg")
        # Content-addressed names mean identical name => identical bytes,
        # so skipping the rewrite is safe and saves an upload.
        assert (Path(tmp) / "same.jpg").read_bytes() == b"original"
        