"""Owned lab image format, deliberately unrelated to Bosch firmware formats."""
import hashlib
import struct
import zlib

HEADER = struct.Struct(">4sII32s")  # magic, version, payload length, SHA-256
MAX_IMAGE = 65536


def build_image(payload, version):
    if not 0 < version <= 0xffffffff or not payload:
        raise ValueError("positive version and nonempty payload required")
    body = HEADER.pack(b"LAB1",version,len(payload),hashlib.sha256(payload).digest())+payload
    result = body+struct.pack(">I",zlib.crc32(body))
    if len(result) > MAX_IMAGE:
        raise ValueError("image exceeds virtual bank")
    return result


def validate_image(data):
    if not HEADER.size+4 < len(data) <= MAX_IMAGE:
        raise ValueError("invalid image length")
    magic,version,size,digest = HEADER.unpack_from(data)
    if magic != b"LAB1" or version == 0 or size != len(data)-HEADER.size-4:
        raise ValueError("invalid image header")
    if zlib.crc32(data[:-4]) != int.from_bytes(data[-4:],"big"):
        raise ValueError("image CRC32 mismatch")
    if hashlib.sha256(data[HEADER.size:-4]).digest() != digest:
        raise ValueError("payload SHA-256 mismatch")
    return version
