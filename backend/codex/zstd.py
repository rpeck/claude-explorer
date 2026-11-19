"""Zstandard decoding for Codex ``.jsonl.zst`` rollouts.

Python 3.14+ has ``compression.zstd`` in the standard library; older
versions use the ``zstandard`` package (a dependency only below 3.14).
The imports stay inside the functions, so importing this module costs
nothing, and the MCPB build lists ``zstandard`` by hand because the
closure analyzer skips function bodies.
"""

from __future__ import annotations

import io
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO


def decompress(data: bytes) -> bytes:
    """Decode a whole zstd stream. A frame without a content size works too."""
    if sys.version_info >= (3, 14):
        from compression import zstd

        return zstd.decompress(data)
    import zstandard

    # Like compression.zstd: read every frame, also frames that do not
    # record their content size (ZstdDecompressor.decompress() refuses those).
    with zstandard.ZstdDecompressor().stream_reader(
        io.BytesIO(data), read_across_frames=True
    ) as stream:
        return stream.read()


@contextmanager
def open_decompressed(path: Path) -> Iterator[BinaryIO]:
    """Open ``path`` read-only and yield a binary stream that decodes as it reads."""
    if sys.version_info >= (3, 14):
        from compression import zstd

        with zstd.open(path, "rb") as stream:
            yield stream
        return
    import zstandard

    with open(path, "rb") as raw, zstandard.ZstdDecompressor().stream_reader(
        raw, read_across_frames=True
    ) as reader:
        # The raw reader cannot readline() or iterate; ZstdFile on 3.14+ can.
        yield io.BufferedReader(reader)
