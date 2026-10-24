"""Phase 5: PDF export must embed real image bytes, not blank placeholders.

Two failure modes today:

* ``[Image: source: <abs-path>]`` text markers are escaped as plain text,
  never converted to ``<img>``.
* Even when an ``<img>`` exists, ``WeasyPrint.HTML(string=..)`` cannot
  fetch ``/api/cc-image?path=<abs>`` — there's no HTTP server context.

Fix: rewrite markers to ``<img src=...>`` and pass a ``url_fetcher``
callback that reads bytes from disk.

These tests verify the bytes that land in the PDF actually match the
fixture image, not just that *some* image stream exists (WeasyPrint
emits valid streams for broken-image icons, which would falsely pass).
"""

from __future__ import annotations

import io
import zlib
from datetime import datetime
from pathlib import Path

import pytest

from backend.export import create_pdf
from backend.models import ConversationDetail, Message

# A deterministic 2x1 PNG with two unique RGB pixels:
#   pixel 0 = (0xAB, 0xCD, 0xEF)
#   pixel 1 = (0x12, 0x34, 0x56)
#
# We pick distinctive colors so that after WeasyPrint decodes the PNG
# and re-encodes it as a FlateDecode RGB stream inside the PDF, the
# concatenated pixel bytes still appear verbatim in the decompressed
# image data — giving us a content-level check that the *real* fixture
# was embedded, not a 1x1 transparent placeholder for a missing file.
_FIXTURE_PNG = bytes.fromhex(
    "89504e470d0a1a0a"
    "0000000d49484452000000020000000108020000007b40e8dd"
    "0000000f49444154789c63587df6bd904918000cba03049f4768ab"
    "0000000049454e44ae426082"
)
_FIXTURE_PIXELS = bytes([0xAB, 0xCD, 0xEF, 0x12, 0x34, 0x56])

# G4 audit — second fixture with byte-distinct pixels. Used by the
# multi-image test to prove that the PDF embeds BOTH images (not one
# image twice and not one image overwriting the other). Pixels chosen
# to be visually distinct from _FIXTURE_PIXELS AND have a different
# sha256 so a `for img in images: assert pixels in img` collision is
# impossible.
#   pixel 0 = (0x77, 0x88, 0x99)
#   pixel 1 = (0xAA, 0xBB, 0xCC)
_FIXTURE_PNG_2 = bytes.fromhex(
    "89504e470d0a1a0a"
    "0000000d49484452000000020000000108020000007b40e8dd"
    "0000000f49444154789c6328ef98b96af719000c1d03cadde28f8d"
    "0000000049454e44ae426082"
)
_FIXTURE_PIXELS_2 = bytes([0x77, 0x88, 0x99, 0xAA, 0xBB, 0xCC])


def _has_pypdf() -> bool:
    try:
        import pypdf  # noqa: F401

        return True
    except ImportError:
        return False


def _png_unfilter_row(filtered: bytes, bpp: int) -> bytes:
    """Reverse PNG row filtering for a single row.

    PNG IDAT data prefixes each row with a 1-byte filter type:
       0 = None (raw), 1 = Sub, 2 = Up, 3 = Average, 4 = Paeth.

    For a single-row image (height=1), Up/Average/Paeth all degenerate
    to using zero for the "previous row" terms, so the recovery is:
       None    → raw[i]
       Sub     → raw[i] + recon[i-bpp]
       Up      → raw[i]                              (prev row is zero)
       Average → raw[i] + recon[i-bpp] // 2          (prev row is zero)
       Paeth   → raw[i] + paeth(recon[i-bpp], 0, 0)  (prev row is zero)
                 which collapses to raw[i] + recon[i-bpp]

    WeasyPrint can pick any filter when it re-encodes the PNG into the
    PDF FlateDecode stream, so the test MUST be filter-agnostic.
    """
    if not filtered:
        return b""
    filt = filtered[0]
    data = filtered[1:]
    recon = bytearray(len(data))
    if filt == 0:
        return bytes(data)
    if filt == 1 or filt == 4:
        for i in range(len(data)):
            left = recon[i - bpp] if i >= bpp else 0
            recon[i] = (data[i] + left) & 0xFF
        return bytes(recon)
    if filt == 2:
        return bytes(data)  # prev-row is zero in a 1-row image
    if filt == 3:
        for i in range(len(data)):
            left = recon[i - bpp] if i >= bpp else 0
            recon[i] = (data[i] + (left // 2)) & 0xFF
        return bytes(recon)
    raise ValueError(f"unknown PNG filter byte {filt}")


def _extract_image_xobjects(pdf_bytes: bytes) -> list[dict]:
    """Walk a PDF and return per-image dicts with width/height + the
    flate-decompressed raw pixel bytes.

    For FlateDecode streams that include a leading PNG filter byte
    (which WeasyPrint emits per row), we also return ``pixels`` — the
    reconstructed un-filtered RGB byte sequence. Tests that want to
    check pixel content should use ``pixels``, not ``decompressed``,
    since WeasyPrint's choice of PNG filter is implementation-defined
    and can vary between adjacent images.
    """
    import pypdf

    reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
    out: list[dict] = []
    for page in reader.pages:
        resources = page.get("/Resources")
        if resources is None:
            continue
        xobjects = resources.get("/XObject") if hasattr(resources, "get") else None
        if not xobjects:
            continue
        try:
            xobjects = xobjects.get_object()
        except AttributeError:
            pass
        for name in xobjects:
            obj = xobjects[name]
            try:
                obj = obj.get_object()
            except AttributeError:
                pass
            if obj.get("/Subtype") != "/Image":
                continue
            raw = getattr(obj, "_data", b"") or b""
            decompressed = b""
            pixels = b""
            if obj.get("/Filter") == "/FlateDecode":
                try:
                    decompressed = zlib.decompress(raw)
                except zlib.error:
                    decompressed = b""
                # Best-effort PNG-unfilter when the dimensions + sample
                # depth are known.
                width = obj.get("/Width") or 0
                bps = obj.get("/BitsPerComponent") or 8
                # Map ColorSpace to bytes-per-pixel.
                cs = obj.get("/ColorSpace")
                bpp_map = {"/DeviceRGB": 3, "/DeviceGray": 1, "/DeviceCMYK": 4}
                bpp = bpp_map.get(str(cs), 0)
                if bpp and bps == 8 and width:
                    stride = 1 + width * bpp  # +1 for the filter byte
                    rows = []
                    for r_start in range(0, len(decompressed), stride):
                        row = decompressed[r_start : r_start + stride]
                        if len(row) == stride:
                            rows.append(_png_unfilter_row(row, bpp))
                    pixels = b"".join(rows)
            out.append(
                {
                    "width": obj.get("/Width"),
                    "height": obj.get("/Height"),
                    "filter": obj.get("/Filter"),
                    "raw": raw,
                    "decompressed": decompressed,
                    "pixels": pixels,
                }
            )
    return out


def _make_conversation(text: str, message_uuid: str = "m1") -> ConversationDetail:
    msg = Message(
        uuid=message_uuid,
        sender="human",
        text=text,
        content=[],
        created_at=datetime(2026, 5, 1, 12, 0),
        updated_at=datetime(2026, 5, 1, 12, 0),
        files=[],
        files_v2=[],
    )
    return ConversationDetail(
        uuid="c-fixture",
        name="PDF image fixture",
        summary="",
        model="claude",
        created_at=datetime(2026, 5, 1, 12, 0),
        updated_at=datetime(2026, 5, 1, 12, 0),
        message_count=1,
        human_message_count=1,
        current_leaf_message_uuid=message_uuid,
        messages=[msg],
    )


@pytest.mark.skipif(not _has_pypdf(), reason="pypdf required to inspect PDF streams")
def test_pdf_embeds_marker_image_bytes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Marker `[Image: source: <abs>]` must produce a PDF whose image
    stream contains the fixture PNG bytes — i.e. WeasyPrint successfully
    fetched and embedded the on-disk file rather than rendering a
    broken-image placeholder."""
    # Place the fixture under a dir we point CLAUDE_DIR at, so the
    # url_fetcher's safety check (path must live under image-cache root)
    # accepts it.
    claude_dir = tmp_path / "claude"
    image_cache = claude_dir / "image-cache" / "sess-abc"
    image_cache.mkdir(parents=True)
    fixture = image_cache / "1.png"
    fixture.write_bytes(_FIXTURE_PNG)

    monkeypatch.setenv("CLAUDE_DIR", str(claude_dir))
    # Reset cached settings so the new env var takes effect.
    from backend.config import get_settings

    get_settings.cache_clear()  # type: ignore[attr-defined]

    conv = _make_conversation(f"Look at this: [Image: source: {fixture}]")
    pdf_bytes = create_pdf(conv)
    assert isinstance(pdf_bytes, bytes)
    assert pdf_bytes.startswith(b"%PDF")

    images = _extract_image_xobjects(pdf_bytes)
    assert images, "PDF contained no image XObject streams at all"

    # WeasyPrint decodes the source PNG to raw RGB and re-encodes it as
    # a FlateDecode XObject in the PDF. To prove the *real* fixture was
    # embedded (not a 1x1 transparent placeholder for a missing file),
    # we look for an XObject with our 2x1 dimensions whose decompressed
    # stream contains the fixture's pixel bytes verbatim.
    matched = [
        img
        for img in images
        if img["width"] == 2
        and img["height"] == 1
        and _FIXTURE_PIXELS in img["decompressed"]
    ]
    assert matched, (
        "No image stream in the PDF matched the fixture pixel bytes. "
        "WeasyPrint likely rendered a broken-image placeholder. "
        f"Saw images: {[(i['width'], i['height'], i['filter']) for i in images]}"
    )


@pytest.mark.skipif(not _has_pypdf(), reason="pypdf required to inspect PDF streams")
def test_pdf_embeds_multiple_distinct_image_markers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """G4 audit — when a conversation references two distinct images via
    ``[Image: source: ...]`` markers, the PDF must embed BOTH bytes
    streams. Catches a regression where one image overwrites another
    (single-image cache key, shared filename, etc.) or where only the
    first marker is processed.

    Council's call (G4 disagreement resolution): assert count + content
    only. Visual layout ordering is heavily WeasyPrint-implementation
    dependent (xobject definition order != content-stream draw order),
    so a byte-position ordering check would be brittle. Two byte-
    distinct fixtures with two distinct sha256s + assertion that BOTH
    pixel signatures appear in the PDF's image streams catches the
    "one image overwrote the other" regression that the contract
    actually cares about.
    """
    import hashlib

    claude_dir = tmp_path / "claude"
    image_cache = claude_dir / "image-cache" / "sess-multi"
    image_cache.mkdir(parents=True)
    fixture_a = image_cache / "1.png"
    fixture_b = image_cache / "2.png"
    fixture_a.write_bytes(_FIXTURE_PNG)
    fixture_b.write_bytes(_FIXTURE_PNG_2)

    monkeypatch.setenv("CLAUDE_DIR", str(claude_dir))
    from backend.config import get_settings

    get_settings.cache_clear()  # type: ignore[attr-defined]

    # Two markers in one message body, both pointing at different
    # files. The marker regex finds them independently; each should
    # produce an <img> tag and a distinct embed in the PDF.
    conv = _make_conversation(
        f"Look:\n[Image: source: {fixture_a}]\n[Image: source: {fixture_b}]"
    )
    pdf_bytes = create_pdf(conv)
    assert isinstance(pdf_bytes, bytes)
    assert pdf_bytes.startswith(b"%PDF")

    images = _extract_image_xobjects(pdf_bytes)
    # Two distinct image XObject streams in the PDF — regression would
    # collapse to 1 if a shared-filename cache key overwrote one with
    # the other, or if marker parsing only handled the first match.
    assert len(images) >= 2, (
        f"Expected ≥2 image streams in PDF, got {len(images)}. "
        f"Saw images: {[(i['width'], i['height'], i['filter']) for i in images]}"
    )

    # Both pixel signatures must appear, post-PNG-unfilter, somewhere
    # among the PDF's image streams. We use `pixels` (the unfiltered
    # RGB byte sequence) rather than `decompressed` because WeasyPrint
    # picks PNG row filters at re-encoding time and a Sub-filtered row
    # masks the raw pixel bytes from a naïve substring search.
    # (See _png_unfilter_row docstring for the filter-agnostic decode.)
    saw_a = any(
        img["width"] == 2 and img["height"] == 1 and _FIXTURE_PIXELS == img["pixels"]
        for img in images
    )
    saw_b = any(
        img["width"] == 2 and img["height"] == 1 and _FIXTURE_PIXELS_2 == img["pixels"]
        for img in images
    )
    assert saw_a, f"First image's pixel bytes missing from PDF. Saw pixels: {[i['pixels'].hex() for i in images]}"
    assert saw_b, f"Second image's pixel bytes missing from PDF. Saw pixels: {[i['pixels'].hex() for i in images]}"

    # Bidirectional check: prove the two fixture digests are actually
    # different (so the "both bytes appear" claim isn't vacuous). If
    # someone edits a fixture and accidentally makes them identical
    # this assertion fails first.
    digest_a = hashlib.sha256(_FIXTURE_PIXELS).hexdigest()
    digest_b = hashlib.sha256(_FIXTURE_PIXELS_2).hexdigest()
    assert digest_a != digest_b


def test_pdf_missing_marker_renders_cleanly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A marker pointing at a non-existent path must NOT raise. The PDF
    can render a placeholder; we only require a non-empty bytes return."""
    claude_dir = tmp_path / "claude"
    (claude_dir / "image-cache").mkdir(parents=True)
    monkeypatch.setenv("CLAUDE_DIR", str(claude_dir))
    from backend.config import get_settings

    get_settings.cache_clear()  # type: ignore[attr-defined]

    ghost = claude_dir / "image-cache" / "sess-zzz" / "9.png"
    conv = _make_conversation(f"Where? [Image: source: {ghost}]")

    pdf_bytes = create_pdf(conv)
    assert isinstance(pdf_bytes, bytes)
    assert len(pdf_bytes) > 0
    assert pdf_bytes.startswith(b"%PDF")
