"""Build one pinned KP20D candidate offline. This program never accesses USB."""
import argparse
import binascii
import hashlib
import json
import struct
from pathlib import Path

OFFICIAL_SHA = '6574054de479bb44becf1f7b338d8a05d1190bdae28f8702f463afb1324d11b9'
BASE = 0x08003000
PATCH_ADDRESS = 0x080196DA
OLD_CODE = bytes.fromhex('00f0a2fe40f2dc518842f8d303200be0')
NEW_CODE = bytes.fromhex('012084f8540070bd00bf00bf00bf00bf')
DEVICE_DESCRIPTOR = 0x0801ADB4

def sha(data):
    return hashlib.sha256(data).hexdigest()

def inspect(data):
    if len(data) < 34:
        raise ValueError('Truncated container')
    version, count, size, crc = struct.unpack_from('<IIIH', data)
    if (version, count, size) != (1, 2, len(data)):
        raise ValueError('Unexpected KP20D container')
    if binascii.crc_hqx(data[34:], 0) != crc:
        raise ValueError('Container CRC mismatch')
    end = 34
    segments = []
    for i in range(2):
        offset, length, checksum = struct.unpack_from('<IIH', data, 14 + i * 10)
        if offset != end or length <= 0 or offset + length > len(data):
            raise ValueError('Unexpected segment layout')
        if binascii.crc_hqx(data[offset:offset+length], 0) != checksum:
            raise ValueError('Segment CRC mismatch')
        segments.append((offset, length))
        end = offset + length
    if end != len(data):
        raise ValueError('Trailing bytes')
    return segments

def checksums(data):
    result = bytearray(data)
    for i in range(2):
        offset, length = struct.unpack_from('<II', result, 14 + i * 10)
        struct.pack_into('<H', result, 22 + i * 10,
                         binascii.crc_hqx(result[offset:offset+length], 0))
    struct.pack_into('<H', result, 12, binascii.crc_hqx(result[34:], 0))
    inspect(result)
    return bytes(result)

def build(original):
    if sha(original) != OFFICIAL_SHA:
        raise ValueError('Only the exact official KP20D 1.1.1 image is accepted')
    segments = inspect(original)
    seg0 = segments[0][0]
    code_offset = seg0 + PATCH_ADDRESS - BASE
    descriptor_offset = seg0 + DEVICE_DESCRIPTOR - BASE
    if original[code_offset:code_offset+16] != OLD_CODE:
        raise ValueError('Mode-0 code does not match')
    if original[descriptor_offset:descriptor_offset+18] != bytes.fromhex('1201000200000040bc205851000101020001'):
        raise ValueError('Mode-0 descriptor does not match')
    result = bytearray(original)
    result[code_offset:code_offset+16] = NEW_CODE
    result[descriptor_offset+8:descriptor_offset+12] = bytes.fromhex('5e048e02')
    # Keep class 0 and the composite vendor HID interface for recovery.
    assert result[descriptor_offset+4] == 0
    result = checksums(result)
    s1, n1 = segments[1]
    assert result[s1:s1+n1] == original[s1:s1+n1]
    return result

def restore(candidate):
    segments = inspect(candidate)
    seg0 = segments[0][0]
    code_offset = seg0 + PATCH_ADDRESS - BASE
    descriptor_offset = seg0 + DEVICE_DESCRIPTOR - BASE
    if candidate[code_offset:code_offset+16] != NEW_CODE or candidate[descriptor_offset+8:descriptor_offset+12] != bytes.fromhex('5e048e02'):
        raise ValueError('Not this candidate')
    result = bytearray(candidate)
    result[code_offset:code_offset+16] = OLD_CODE
    result[descriptor_offset+8:descriptor_offset+12] = bytes.fromhex('bc205851')
    result = checksums(result)
    if sha(result) != OFFICIAL_SHA or build(result) != candidate:
        raise ValueError('Does not restore the pinned official image exactly')
    return result

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--restore', action='store_true')
    args = parser.parse_args()
    source = args.input.read_bytes()
    result = restore(source) if args.restore else build(source)
    with args.output.open('xb') as f:
        f.write(result)
    print(json.dumps({'path': str(args.output), 'sha256': sha(result), 'bytes': len(result), 'usb_access': False}))
