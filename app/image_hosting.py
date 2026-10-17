"""
Image hosting — turns whatever image a seller actually has into a
stable, public `image_url` that an ACP/UCP feed can reference.

The gap this closes: ACP requires `image_url`, but SME/D2C sellers in
the target segment overwhelmingly have image FILES (a WhatsApp photo, a
phone snap, a marketplace export attachment) rather than hosted URLs.
That's a format problem, not an absence problem — the picture exists, it
just isn't addressable. Rejecting those products would exclude exactly
the sellers this product is for, so StoreMind hosts the bytes and hands
back a real URL.

What it does NOT do: invent an image. A product with no photo anywhere
stays without one and is reported as a genuine gap (see
acp_schema.NOT_YET_SUPPLIED handling in main.py). Generating or
stock-substituting a product image would misrepresent the item at the
exact moment an agent is about to transact on it — the same
fabrication-blindness failure readiness_score.py's source_richness
component exists to catch, just moved to a new field.

Libraries used, and why:
  - `Pillow` (https://python-pillow.org/) for decode/validate/normalize.
    Four behaviors verified against the real package rather than
    assumed, each of which shapes the code below:
      1. `Image.verify()` leaves the object UNUSABLE — any later
         operation raises. The image must be reopened afterwards.
         (Confirmed: AssertionError on a post-verify thumbnail call.)
      2. Non-image bytes raise `UnidentifiedImageError` on open.
      3. An RGBA image cannot be saved as JPEG without an explicit
         `.convert("RGB")` — raises OSError otherwise.
      4. Normalization is worth doing on cost grounds alone: a 3000x2000
         94 KB JPEG came out at 10 KB after thumbnail+optimize.
  - `google-cloud-storage` for the GCS backend — the GCP-native object
    store this project already targets.
  - `hashlib` (stdlib) for content-addressed blob names: identical bytes
    produce an identical name, so re-ingesting the same catalog doesn't
    duplicate uploads, and the name can't collide across sellers by
    accident the way a sku-derived name could.

Backend-swappable, matching this project's established convention
(STOREMIND_LLM_BACKEND / STOREMIND_STORE_BACKEND):

    STOREMIND_IMAGE_BACKEND = local (default, offline) | gcs
    STOREMIND_IMAGE_BUCKET  = <bucket name>          (gcs only)
    STOREMIND_PUBLIC_BASE_URL = https://...          (local backend URL prefix)

so the whole pipeline still runs offline with zero GCP access, exactly
like local_store.py vs firestore_client.py.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import io
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Pillow is imported lazily inside _normalize so this module still
# imports cleanly without it installed, matching the lazy-import pattern
# used for google-genai in catalog_agent.py.

MAX_DIMENSION = 1600  # px on the longest side
JPEG_QUALITY = 85
MAX_SOURCE_BYTES = 12 * 1024 * 1024  # refuse absurd inputs before decoding

LOCAL_MEDIA_DIR = Path(os.environ.get("STOREMIND_LOCAL_MEDIA_DIR", "media"))

_DATA_URI_PATTERN = re.compile(r"^data:image/[a-zA-Z0-9.+-]+;base64,", re.IGNORECASE)


@dataclass
class HostedImage:
    url: str | None
    source: str  # "source_url" | "hosted" | "none"
    notes: list[str] = field(default_factory=list)
    content_hash: str | None = None
    bytes_stored: int | None = None


# ---------------------------------------------------------------------
# Decoding — get raw bytes out of whatever the row actually carried
# ---------------------------------------------------------------------

def decode_image_input(value: Any) -> bytes | None:
    """Extracts image bytes from a catalog row's image field.

    Handles the three shapes an SME catalog row realistically carries:
    raw bytes, a `data:image/...;base64,...` URI, or a bare base64
    string. Returns None for anything else (including http URLs, which
    are handled separately — see resolve_image_url) rather than
    guessing. Never raises.
    """
    if isinstance(value, (bytes, bytearray)):
        return bytes(value) or None
    if not isinstance(value, str):
        return None

    text = value.strip()
    if not text:
        return None

    if _DATA_URI_PATTERN.match(text):
        text = text.split(",", 1)[1]
    elif text.lower().startswith(("http://", "https://")):
        return None  # a real URL, not inline bytes

    # Bare base64. Validate strictly — a product NAME would otherwise
    # sometimes decode as valid base64 and produce garbage bytes.
    if len(text) < 32:
        return None
    try:
        return base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError):
        return None


# ---------------------------------------------------------------------
# Normalization — Pillow
# ---------------------------------------------------------------------

def normalize_image(raw: bytes) -> tuple[bytes, str, list[str]] | None:
    """Validates and normalizes image bytes: confirms it's a real image,
    downscales to MAX_DIMENSION, strips EXIF (both a privacy matter —
    phone photos carry GPS coordinates — and a size saving), and
    re-encodes as JPEG.

    Returns (normalized_bytes, content_type, notes), or None if the
    input isn't a usable image. Pure and offline — no network, fully
    unit-testable.
    """
    notes: list[str] = []
    if not raw:
        return None
    if len(raw) > MAX_SOURCE_BYTES:
        return None

    try:
        from PIL import Image, UnidentifiedImageError
    except ImportError:
        return None

    # Pass 1: validate. Image.verify() leaves the object unusable
    # afterwards (verified against real Pillow — a subsequent operation
    # raises), which is exactly why this is a separate open from the one
    # below rather than a single reused handle.
    try:
        Image.open(io.BytesIO(raw)).verify()
    except (UnidentifiedImageError, Exception):  # noqa: B014 - breadth is deliberate; see below
        # Pillow raises a wide variety of types on malformed input
        # (UnidentifiedImageError, OSError, SyntaxError, ValueError,
        # struct.error...). This runs inside /ingest and must degrade to
        # "no image" rather than break the batch, so all of them are
        # treated identically.
        return None

    # Pass 2: reopen and transform.
    try:
        img = Image.open(io.BytesIO(raw))
        original_size = img.size

        if max(img.size) > MAX_DIMENSION:
            img.thumbnail((MAX_DIMENSION, MAX_DIMENSION), Image.Resampling.LANCZOS)
            notes.append(f"resized {original_size[0]}x{original_size[1]} -> {img.size[0]}x{img.size[1]}")

        # RGBA/P/LA cannot be written as JPEG (verified: OSError
        # "cannot write mode RGBA as JPEG"). Flatten onto white rather
        # than dropping the alpha channel to black, which is what a bare
        # convert("RGB") does to transparent product cut-outs.
        if img.mode in ("RGBA", "LA", "P"):
            img = img.convert("RGBA")
            background = Image.new("RGB", img.size, (255, 255, 255))
            background.paste(img, mask=img.split()[-1])
            img = background
            notes.append("flattened transparency onto white")
        elif img.mode != "RGB":
            img = img.convert("RGB")

        out = io.BytesIO()
        # No `exif=` argument passed, so EXIF is dropped on save.
        img.save(out, format="JPEG", quality=JPEG_QUALITY, optimize=True)
        data = out.getvalue()
    except Exception:  # noqa: BLE001 — see note in pass 1
        return None

    if len(data) < len(raw):
        notes.append(f"normalized {len(raw)} -> {len(data)} bytes")
    return data, "image/jpeg", notes


# ---------------------------------------------------------------------
# Backends
# ---------------------------------------------------------------------

class LocalImageStore:
    """Offline backend — writes into a local directory, served by
    main.py's /media/{filename} route. Lets the full image-hosting path
    be demoed and tested with no GCP access at all, exactly like
    local_store.py does for Firestore."""

    backend_name = "local"

    def __init__(self, media_dir: Path | None = None, base_url: str | None = None) -> None:
        self.media_dir = Path(media_dir or LOCAL_MEDIA_DIR)
        self.base_url = (base_url or os.environ.get("STOREMIND_PUBLIC_BASE_URL", "http://localhost:8080")).rstrip("/")

    def store(self, data: bytes, name: str, content_type: str) -> tuple[str, list[str]]:
        self.media_dir.mkdir(parents=True, exist_ok=True)
        path = self.media_dir / name
        if not path.exists():  # content-addressed: identical bytes, identical name
            path.write_bytes(data)
        return f"{self.base_url}/media/{name}", []


class GCSImageStore:
    """Google Cloud Storage backend."""

    backend_name = "gcs"

    def __init__(self, bucket_name: str | None = None) -> None:
        self.bucket_name = bucket_name or os.environ.get("STOREMIND_IMAGE_BUCKET")
        if not self.bucket_name:
            raise RuntimeError(
                "STOREMIND_IMAGE_BUCKET not set. Required when "
                "STOREMIND_IMAGE_BACKEND=gcs — this is the bucket name, not a URL."
            )
        self._client = None

    def _get_client(self):
        if self._client is None:
            from google.cloud import storage  # lazy, same pattern as catalog_agent.py

            self._client = storage.Client()
        return self._client

    def store(self, data: bytes, name: str, content_type: str) -> tuple[str, list[str]]:
        notes: list[str] = []
        bucket = self._get_client().bucket(self.bucket_name)
        blob = bucket.blob(f"products/{name}")

        if not blob.exists():  # content-addressed dedupe: skip a redundant upload
            blob.upload_from_string(data, content_type=content_type)

        # make_public() raises on buckets with Uniform Bucket-Level
        # Access enabled — which is the modern GCS default, and what a
        # bucket created today most likely has. In that case per-object
        # ACLs simply don't exist and public read must be granted once
        # at the BUCKET level (roles/storage.objectViewer for allUsers)
        # at deploy time. The object is still reachable at the same
        # public URL, so this is a note, not a failure.
        try:
            blob.make_public()
        except Exception as exc:  # noqa: BLE001
            notes.append(
                f"could not set per-object public ACL ({type(exc).__name__}) — "
                "expected if the bucket uses Uniform Bucket-Level Access; "
                "grant allUsers roles/storage.objectViewer on the bucket instead"
            )

        return f"https://storage.googleapis.com/{self.bucket_name}/products/{name}", notes


def build_image_store():
    """Mirrors main.py's _build_agent/_build_store factories."""
    if os.environ.get("STOREMIND_IMAGE_BACKEND", "local") == "gcs":
        return GCSImageStore()
    return LocalImageStore()


# ---------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------

def resolve_image_url(raw_row: dict[str, Any], image_store) -> HostedImage:
    """Resolves ACP's `image_url` for one row.

    Cascade, mirroring brand_resolution.py's shape:
      1. source_url — the row already has a public http(s) image URL.
         Used as-is; StoreMind doesn't re-host what's already
         addressable.
      2. hosted     — the row carries actual image bytes (data URI, bare
         base64, or raw bytes). Normalized and stored; a real URL comes
         back.
      3. none       — no image anywhere. Reported honestly as a gap; NOT
         fabricated. This is the one ACP field StoreMind deliberately
         cannot close on the seller's behalf.

    Never raises — a hosting failure degrades to source "none" with a
    note, so one bad image can't kill an ingest batch.
    """
    existing = raw_row.get("image_url") or raw_row.get("image")
    if isinstance(existing, str) and existing.strip().lower().startswith(("http://", "https://")):
        return HostedImage(url=existing.strip(), source="source_url")

    raw = decode_image_input(raw_row.get("image_bytes") or raw_row.get("image") or raw_row.get("image_url"))
    if raw is None:
        return HostedImage(url=None, source="none", notes=["no usable image data in this row"])

    normalized = normalize_image(raw)
    if normalized is None:
        return HostedImage(url=None, source="none", notes=["image data present but not decodable as an image"])

    data, content_type, notes = normalized
    content_hash = hashlib.sha256(data).hexdigest()[:32]
    name = f"{content_hash}.jpg"

    try:
        url, store_notes = image_store.store(data, name, content_type)
    except Exception as exc:  # noqa: BLE001 — a storage failure must not break ingest
        return HostedImage(
            url=None, source="none",
            notes=notes + [f"image hosting failed: {type(exc).__name__}: {exc}"],
            content_hash=content_hash,
        )

    return HostedImage(
        url=url, source="hosted", notes=notes + store_notes,
        content_hash=content_hash, bytes_stored=len(data),
    )
    