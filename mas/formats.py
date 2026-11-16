# -*- coding: utf-8 -*-
"""v0.7.71: low-level format parsers - what is *inside* this file.

``mas/content.py`` answers "what kind of file is this". This module holds
the parsers that answer the harder half of that question for the
formats whose payload is neither text nor a zip:

* fonts (TTF / OTF / TTC / WOFF / WOFF2) - the family name, the
  number of glyphs;
* images (JPEG / WEBP / TIFF) - the pixel size, when the header says
  it plainly;
* media (WAV / MP4 / MOV / MKV / MP3 / FLAC / OGG) - the container
  brand, the track count, the duration;
* 3D models (glTF / GLB / OBJ / STL / FBX / PLY / 3MF) - how many
  meshes, vertices or triangles the model holds.

Design rules
------------
* Read-only, standard library only. Nothing is executed, nothing is
  unpacked outside this process.
* Every parser bails out with an empty result on a truncated or lying
  file. A wrong number is worse than no number, because the reader
  cannot tell the difference.
* A fact is only reported when the header *proves* it. A guess is not
  a fact.
"""

from __future__ import annotations

import contextlib
import json
import os
import struct
import zipfile
from typing import Any, Dict, List, Optional, Tuple

#: The signatures of the font containers we recognise.
FONT_SIGNATURES = (
    (b"\x00\x01\x00\x00", "truetype"),
    (b"OTTO", "opentype"),
    (b"ttcf", "fontcollection"),
    (b"wOFF", "woff"),
    (b"wOF2", "woff2"),
)

#: The signature of a binary glTF file.
GLB_MAGIC = b"glTF"

#: The signature of a binary FBX file.
FBX_MAGIC = b"Kaydara FBX Binary  \x00"


def _u16(data: bytes, offset: int) -> int:
    if offset + 2 > len(data):
        raise ValueError("out of range")
    return struct.unpack_from(">H", data, offset)[0]


def _u32(data: bytes, offset: int) -> int:
    if offset + 4 > len(data):
        raise ValueError("out of range")
    return struct.unpack_from(">I", data, offset)[0]


def _u16le(data: bytes, offset: int) -> int:
    if offset + 2 > len(data):
        raise ValueError("out of range")
    return struct.unpack_from("<H", data, offset)[0]


def _u32le(data: bytes, offset: int) -> int:
    if offset + 4 > len(data):
        raise ValueError("out of range")
    return struct.unpack_from("<I", data, offset)[0]


def _u24le(data: bytes, offset: int) -> int:
    if offset + 3 > len(data):
        raise ValueError("out of range")
    return int.from_bytes(data[offset:offset + 3], "little")


# ------------------------------------------------------------------ fonts


def _sfnt_tables(data: bytes) -> Dict[str, Tuple[int, int]]:
    """The table directory of an sfnt font: tag -> (offset, length)."""
    tables: Dict[str, Tuple[int, int]] = {}
    try:
        count = _u16(data, 4)
    except ValueError:
        return tables
    for index in range(min(count, 512)):
        base = 12 + index * 16
        if base + 16 > len(data):
            break
        tag = data[base:base + 4]
        try:
            offset = _u32(data, base + 8)
            length = _u32(data, base + 12)
        except ValueError:
            break
        tables[tag.decode("latin-1", "replace")] = (offset, length)
    return tables


def _sfnt_names(data: bytes) -> Dict[str, str]:
    """The family and subfamily names of an sfnt font."""
    out: Dict[str, str] = {}
    tables = _sfnt_tables(data)
    entry = tables.get("name")
    if not entry:
        return out
    offset, length = entry
    chunk = data[offset:offset + length]
    try:
        count = _u16(chunk, 2)
        string_offset = _u16(chunk, 4)
    except ValueError:
        return out
    wanted = {1: "family", 2: "subfamily", 4: "full_name", 6: "postscript"}
    for index in range(min(count, 2048)):
        base = 6 + index * 12
        if base + 12 > len(chunk):
            break
        platform = _u16(chunk, base)
        encoding = _u16(chunk, base + 2)
        name_id = _u16(chunk, base + 6)
        str_length = _u16(chunk, base + 8)
        str_offset = _u16(chunk, base + 10)
        key = wanted.get(name_id)
        if key is None or key in out:
            continue
        start = string_offset + str_offset
        raw = chunk[start:start + str_length]
        if not raw:
            continue
        if platform == 0 or (platform == 3 and encoding in (0, 1)):
            text = raw.decode("utf-16-be", "replace")
        else:
            text = raw.decode("latin-1", "replace")
        text = text.replace("\x00", "").strip()
        if text:
            out[key] = text
    return out


def font_info(path: Any) -> Dict[str, Any]:
    """Facts about a font file: its family, its glyph count, its flavour."""
    try:
        with open(path, "rb") as handle:
            data = handle.read()
    except OSError:
        return {}
    flavour = ""
    for signature, name in FONT_SIGNATURES:
        if data.startswith(signature):
            flavour = name
            break
    if not flavour:
        return {}
    facts: Dict[str, Any] = {"font": flavour}
    if flavour in ("truetype", "opentype", "fontcollection"):
        tables = _sfnt_tables(data)
        if not tables:
            # The signature is there but the directory is not: the file
            # was cut short. Say nothing rather than half a fact.
            return {}
        names = _sfnt_names(data)
        if names.get("family"):
            facts["family"] = names["family"]
        if names.get("subfamily"):
            facts["subfamily"] = names["subfamily"]
        maxp = tables.get("maxp")
        if maxp:
            with contextlib.suppress(ValueError):
                facts["glyphs"] = _u16(data, maxp[0] + 4)
        if tables:
            facts["tables"] = len(tables)
    return facts


# ----------------------------------------------------------------- images


def _jpeg_size(data: bytes) -> Dict[str, int]:
    """The pixel size of a JPEG, read from its start-of-frame marker."""
    offset = 2
    size = len(data)
    while offset + 4 <= size:
        if data[offset] != 0xFF:
            offset += 1
            continue
        marker = data[offset + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            offset += 2
            continue
        try:
            length = _u16(data, offset + 2)
        except ValueError:
            break
        # Every start-of-frame marker except DHT (C4), JPG (C8) and DAC
        # (CC) carries the frame size.
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            try:
                height = _u16(data, offset + 5)
                width = _u16(data, offset + 7)
            except ValueError:
                break
            if width and height:
                return {"width": width, "height": height}
        offset += 2 + length
    return {}


def _webp_size(data: bytes) -> Dict[str, int]:
    """The pixel size of a WEBP, from the VP8 / VP8L / VP8X chunk."""
    if len(data) < 30:
        return {}
    chunk = data[12:16]
    if chunk == b"VP8 ":
        try:
            width = _u16le(data, 26) & 0x3FFF
            height = _u16le(data, 28) & 0x3FFF
        except ValueError:
            return {}
        return {"width": width, "height": height}
    if chunk == b"VP8L":
        try:
            bits = int.from_bytes(data[21:25], "little")
        except ValueError:
            return {}
        width = (bits & 0x3FFF) + 1
        height = ((bits >> 14) & 0x3FFF) + 1
        return {"width": width, "height": height}
    if chunk == b"VP8X":
        width = _u24le(data, 24) + 1
        height = _u24le(data, 27) + 1
        return {"width": width, "height": height}
    return {}


def _tiff_size(data: bytes) -> Dict[str, int]:
    """The pixel size of a TIFF, from the first image directory."""
    if len(data) < 8:
        return {}
    if data[:2] == b"II":
        order = "little"
    elif data[:2] == b"MM":
        order = "big"
    else:
        return {}
    try:
        ifd = int.from_bytes(data[4:8], order)
        count = int.from_bytes(data[ifd:ifd + 2], order)
    except (ValueError, IndexError):
        return {}
    out: Dict[str, int] = {}
    for index in range(min(count, 512)):
        base = ifd + 2 + index * 12
        if base + 12 > len(data):
            break
        tag = int.from_bytes(data[base:base + 2], order)
        if tag not in (0x0100, 0x0101):
            continue
        kind = int.from_bytes(data[base + 2:base + 4], order)
        value = int.from_bytes(data[base + 8:base + 12], order)
        if kind == 3:
            value = value & 0xFFFF
        out["width" if tag == 0x0100 else "height"] = int(value)
    if "width" in out and "height" in out:
        return out
    return {}


def image_info(path: Any) -> Dict[str, Any]:
    """Facts about an image file: its pixel size, when the header says it."""
    try:
        with open(path, "rb") as handle:
            data = handle.read(65536)
    except OSError:
        return {}
    out: Dict[str, int] = {}
    if data.startswith(b"\xff\xd8\xff"):
        out = _jpeg_size(data)
    elif data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
        try:
            width = _u32(data, 16)
            height = _u32(data, 20)
        except ValueError:
            return {}
        out = {"width": width, "height": height}
    elif data.startswith(b"GIF") and len(data) >= 10:
        try:
            width = _u16le(data, 6)
            height = _u16le(data, 8)
        except ValueError:
            return {}
        out = {"width": width, "height": height}
    elif data.startswith(b"BM") and len(data) >= 26:
        try:
            width = int.from_bytes(data[18:22], "little", signed=True)
            height = int.from_bytes(data[22:26], "little", signed=True)
        except ValueError:
            return {}
        out = {"width": abs(width), "height": abs(height)}
    elif data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        out = _webp_size(data)
    elif data[:4] in (b"II*\x00", b"MM\x00*"):
        out = _tiff_size(data)
    return out


# ------------------------------------------------------------------ media


def _wav_info(data: bytes) -> Dict[str, Any]:
    """Facts about a WAV file: its channels, its rate, its duration."""
    if len(data) < 44 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        return {}
    out: Dict[str, Any] = {}
    offset = 12
    byte_rate = 0
    data_bytes = 0
    while offset + 8 <= len(data):
        chunk_id = data[offset:offset + 4]
        try:
            size = _u32le(data, offset + 4)
        except ValueError:
            break
        body = offset + 8
        if chunk_id == b"fmt " and body + 16 <= len(data):
            try:
                out["channels"] = _u16le(data, body + 2)
                out["sample_rate"] = _u32le(data, body + 4)
                byte_rate = _u32le(data, body + 8)
                out["bits_per_sample"] = _u16le(data, body + 14)
            except ValueError:
                pass
        elif chunk_id == b"data":
            data_bytes = size
        offset = body + size + (size & 1)
    if byte_rate and data_bytes:
        out["duration_sec"] = round(data_bytes / float(byte_rate), 3)
    return out


def _isobmff_boxes(data: bytes, start: int, end: int) -> List[Tuple[str, int, int]]:
    """The boxes of an ISO base media file (MP4 / MOV)."""
    boxes: List[Tuple[str, int, int]] = []
    offset = start
    while offset + 8 <= end:
        try:
            size = _u32(data, offset)
        except ValueError:
            break
        kind = data[offset + 4:offset + 8].decode("latin-1", "replace")
        body = offset + 8
        if size == 1:
            try:
                size = struct.unpack_from(">Q", data, offset + 8)[0]
                body = offset + 16
            except struct.error:
                break
        elif size == 0:
            size = end - offset
        if size < 8:
            break
        boxes.append((kind, body, offset + size))
        offset += size
    return boxes


def _mp4_info(data: bytes) -> Dict[str, Any]:
    """Facts about an MP4 / MOV: its brands, its tracks, its duration."""
    out: Dict[str, Any] = {}
    tracks = 0
    duration = 0.0
    timescale = 0

    def walk(start: int, end: int, depth: int) -> None:
        nonlocal tracks, duration, timescale
        if depth > 6:
            return
        for kind, body, box_end in _isobmff_boxes(data, start, end):
            if kind == "ftyp":
                brand = data[body:body + 4].decode("latin-1", "replace")
                if brand:
                    out["brand"] = brand
                compatible = []
                cursor = body + 8
                while cursor + 4 <= box_end:
                    name = data[cursor:cursor + 4].decode("latin-1", "replace")
                    if name.strip():
                        compatible.append(name)
                    cursor += 4
                if compatible:
                    out["compatible"] = compatible
            elif kind == "mvhd":
                try:
                    version = data[body]
                    if version == 1:
                        timescale = _u32(data, body + 20)
                        ticks = struct.unpack_from(">Q", data, body + 24)[0]
                    else:
                        timescale = _u32(data, body + 12)
                        ticks = _u32(data, body + 16)
                    if timescale:
                        duration = ticks / float(timescale)
                except (ValueError, struct.error):
                    pass
            elif kind == "trak":
                tracks += 1
            elif kind in ("moov", "trak", "mdia", "minf", "stbl", "udta", "edts"):
                walk(body, box_end, depth + 1)

    walk(0, len(data), 0)
    if tracks:
        out["tracks"] = tracks
    if duration > 0:
        out["duration_sec"] = round(duration, 3)
    return out


def _flac_info(data: bytes) -> Dict[str, Any]:
    """Facts about a FLAC file: its rate, its channels, its duration."""
    if len(data) < 42 or data[:4] != b"fLaC":
        return {}
    try:
        offset = 4
        while offset + 4 <= len(data):
            head = data[offset]
            kind = head & 0x7F
            size = int.from_bytes(data[offset + 1:offset + 4], "big")
            body = offset + 4
            if kind == 0 and body + 18 <= len(data):
                sample_rate = int.from_bytes(data[body + 10:body + 13], "big") >> 4
                channels = ((data[body + 12] >> 1) & 0x07) + 1
                total = int.from_bytes(data[body + 13:body + 18], "big")
                out = {
                    "sample_rate": sample_rate,
                    "channels": channels,
                }
                if sample_rate:
                    out["duration_sec"] = round(total / float(sample_rate), 3)
                return out
            offset = body + size
    except (ValueError, IndexError):
        return {}
    return {}


def media_info(path: Any) -> Dict[str, Any]:
    """Facts about a media file, when the container states them plainly."""
    try:
        with open(path, "rb") as handle:
            data = handle.read(4 * 1024 * 1024)
    except OSError:
        return {}
    out: Dict[str, Any] = {}
    if data[:4] == b"RIFF":
        if data[8:12] == b"WAVE":
            out.update(_wav_info(data))
        elif data[8:12] == b"AVI ":
            out["container"] = "avi"
    elif data[4:8] == b"ftyp":
        out.update(_mp4_info(data))
    elif data[:4] == b"fLaC":
        out.update(_flac_info(data))
    elif data[:4] == b"OggS":
        out["container"] = "ogg"
    elif data[:3] == b"ID3":
        out["container"] = "mp3"
        out["has_id3"] = True
    elif len(data) >= 2 and data[0] == 0xFF and (data[1] & 0xE0) == 0xE0:
        out["container"] = "mp3"
    elif data[:4] == b"\x1a\x45\xdf\xa3":
        out["container"] = "matroska"
    return out


# --------------------------------------------------------------- 3D models


def _gltf_json(path: Any) -> Optional[Dict[str, Any]]:
    """The JSON document of a .gltf file, when it parses."""
    try:
        with open(path, "rb") as handle:
            data = handle.read(16 * 1024 * 1024)
    except OSError:
        return None
    try:
        parsed = json.loads(data.decode("utf-8", "replace"))
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _gltf_facts(parsed: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    asset = parsed.get("asset")
    if isinstance(asset, dict):
        version = str(asset.get("version") or "")
        if version:
            out["gltf_version"] = version
        generator = str(asset.get("generator") or "")
        if generator:
            out["generator"] = generator
    for key, name in (
        ("meshes", "meshes"),
        ("nodes", "nodes"),
        ("materials", "materials"),
        ("animations", "animations"),
    ):
        value = parsed.get(key)
        if isinstance(value, list) and value:
            out[name] = len(value)
    return out


def _gltf_version_text(version: int) -> str:
    """The glTF version of a container, as text.

    A GLB stores the version as a plain integer (2). A .gltf stores it
    as the string "2.0". Both are stated as "2.0" here, so the two
    containers compare equal in the index.
    """
    if version <= 0xFFFF:
        return "%d.0" % version
    return "%d.%d" % (version >> 16, (version >> 8) & 0xFF)


def _glb_info(path: Any) -> Dict[str, Any]:
    """Facts about a binary glTF file."""
    try:
        with open(path, "rb") as handle:
            head = handle.read(20)
            if len(head) < 20 or head[:4] != GLB_MAGIC:
                return {}
            # The GLB container is little-endian, unlike the sfnt
            # font tables next door.
            version = _u32le(head, 4)
            total = _u32le(head, 8)
            # The GLB header is 12 bytes; the chunks start right after
            # it, each with its own 8 byte header.
            handle.seek(12)
            body = handle.read(max(0, total - 12))
    except OSError:
        return {}
    out: Dict[str, Any] = {"gltf_version": _gltf_version_text(version)}
    offset = 0
    while offset + 8 <= len(body):
        try:
            length = _u32le(body, offset)
        except ValueError:
            break
        kind = _u32le(body, offset + 4)
        chunk = body[offset + 8:offset + 8 + length]
        if kind == 0x4E4F534A:
            try:
                parsed = json.loads(chunk.decode("utf-8", "replace"))
            except ValueError:
                parsed = None
            if isinstance(parsed, dict):
                out.update(_gltf_facts(parsed))
        elif kind == 0x004E4942:
            out["binary_bytes"] = length
        offset += 8 + length + ((4 - length % 4) % 4 if length % 4 else 0)
    return out


def _obj_info(path: Any) -> Dict[str, Any]:
    """The vertex and face counts of a Wavefront .obj file."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            vertices = 0
            faces = 0
            for line in handle:
                if line.startswith("v "):
                    vertices += 1
                elif line.startswith("f "):
                    faces += 1
    except OSError:
        return {}
    out: Dict[str, Any] = {}
    if vertices:
        out["vertices"] = vertices
    if faces:
        out["faces"] = faces
    return out


def _stl_info(path: Any) -> Dict[str, Any]:
    """The triangle count of an STL file, binary or ASCII."""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as handle:
            head = handle.read(84)
            # A binary STL starts with an 80 byte header and states its
            # triangle count at offset 80; an ASCII STL starts with the
            # word "solid" and may be shorter than the binary header.
            if len(head) >= 84:
                count = _u32le(head, 80)
                if 84 + count * 50 == size:
                    return {"triangles": count}
            text = head.decode("latin-1", "replace")
            if "facet" in text or text.strip().startswith("solid"):
                with open(path, "r", encoding="latin-1", errors="replace") as handle:
                    facets = sum(
                        1 for line in handle if line.strip().startswith("facet")
                    )
                if facets:
                    return {"triangles": facets}
    except (OSError, ValueError):
        return {}
    return {}


def _fbx_info(path: Any) -> Dict[str, Any]:
    """The version of a binary FBX file."""
    try:
        with open(path, "rb") as handle:
            head = handle.read(27)
    except OSError:
        return {}
    if not head.startswith(FBX_MAGIC):
        return {}
    if len(head) >= 27:
        try:
            version = _u32le(head, 23)
            return {
                "fbx_version": "%d.%d.%d"
                % (version >> 24, (version >> 16) & 0xFF, (version >> 8) & 0xFF)
            }
        except ValueError:
            return {}
    return {}


def _ply_info(path: Any) -> Dict[str, Any]:
    """The element counts of a PLY file."""
    try:
        with open(path, "rb") as handle:
            head = handle.read(8192)
    except OSError:
        return {}
    if not head.startswith(b"ply"):
        return {}
    out: Dict[str, Any] = {}
    text = head.decode("ascii", "replace")
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 3 and parts[0] == "element":
            try:
                count = int(parts[2])
            except ValueError:
                continue
            element = str(parts[1])
            out[element] = count
            if element == "vertex":
                out["vertices"] = count
            elif element == "face":
                out["faces"] = count
        if parts and parts[0] == "format":
            out["ply_format"] = parts[1] if len(parts) > 1 else ""
    return out


def _threemf_info(path: Any) -> Dict[str, Any]:
    """The number of model parts inside a 3MF package."""
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
    except (zipfile.BadZipFile, OSError):
        return {}
    models = [name for name in names if name.lower().endswith(".model")]
    if not models:
        return {}
    return {"parts": len(models)}


def model_info(path: Any) -> Dict[str, Any]:
    """Facts about a 3D model file."""
    try:
        with open(path, "rb") as handle:
            head = handle.read(32)
    except OSError:
        return {}
    suffix = str(path).lower()
    if head[:4] == GLB_MAGIC:
        return _glb_info(path)
    if head[:4] == b"glTF":
        parsed = _gltf_json(path)
        return _gltf_facts(parsed) if parsed else {}
    if suffix.endswith(".gltf"):
        # A .gltf file is plain JSON: its first bytes are "{", so the
        # header cannot name it - only the extension can.
        parsed = _gltf_json(path)
        return _gltf_facts(parsed) if parsed else {}
    if head.startswith(FBX_MAGIC):
        return _fbx_info(path)
    if head.startswith(b"ply"):
        return _ply_info(path)
    if head[:2] == b"PK":
        return _threemf_info(path)
    if suffix.endswith(".obj") or head.lstrip()[:1] == b"v":
        return _obj_info(path)
    if suffix.endswith(".stl"):
        return _stl_info(path)
    return {}
