"""Native macOS KP20D type-21 updater. Pinned images only; no VM or OpenJoy."""
import argparse
import ctypes
import datetime
import hashlib
import importlib.util
import json
import math
import os
import struct
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'tools'))
from protocol import reports, parse_ack, crc16
spec = importlib.util.spec_from_file_location('image_builder', ROOT / 'tools/build_firmware.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)
OFFICIAL = ROOT / 'firmware/KP20D-official-1.1.1.bin'
CANDIDATE_SHA = 'b8f9a7e864b0750eda4ebb86af19b19d9a0c09f588ae5178cabc877b3f38857b'

class Journal:
    def __init__(self, path):
        self.file = path.open('x', buffering=1)
        self.last_progress = -1
    def log(self, event, **values):
        row = dict(event=event, utc=datetime.datetime.now(datetime.timezone.utc).isoformat(), **values)
        self.file.write(json.dumps(row, ensure_ascii=False)+'\n')
        self.file.flush()
        os.fsync(self.file.fileno())
        if event in ('connected', 'version', 'phase', 'segment_success', 'success', 'failure'):
            print(json.dumps(row, ensure_ascii=False), flush=True)

class HID:
    def __init__(self):
        self.lib = ctypes.CDLL(str(ROOT / 'build/libkp20d-native-hid.dylib'))
        self.lib.kp_open.argtypes = [ctypes.c_int]
        self.lib.kp_open.restype = ctypes.c_int
        self.lib.kp_error.restype = ctypes.c_char_p
        self.lib.kp_identity.argtypes = [ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int)]
        self.lib.kp_write.argtypes = [ctypes.POINTER(ctypes.c_uint8), ctypes.c_int]
        self.lib.kp_read.argtypes = [ctypes.POINTER(ctypes.c_uint8), ctypes.c_int]
        self.lib.kp_pump.argtypes = [ctypes.c_int]
        self.closed = False
    def error(self):
        return self.lib.kp_error().decode(errors='replace')
    def connect(self, seconds):
        if self.lib.kp_open(seconds) != 1:
            raise RuntimeError(self.error())
        vid, pid = ctypes.c_int(), ctypes.c_int()
        if self.lib.kp_identity(ctypes.byref(vid), ctypes.byref(pid)):
            raise RuntimeError('Cannot identify vendor HID')
        if (vid.value, pid.value) not in ((0x20bc, 0x5158), (0x045e, 0x028e)):
            raise RuntimeError('Unexpected wired identity')
        return {'vid': f'{vid.value:04x}', 'pid': f'{pid.value:04x}'}
    def write(self, data):
        assert len(data) == 64
        array = (ctypes.c_uint8*64).from_buffer_copy(data)
        if self.lib.kp_write(array, 64) != 64:
            raise RuntimeError(self.error())
    def read(self, milliseconds):
        array = (ctypes.c_uint8*64)()
        result = self.lib.kp_read(array, milliseconds)
        if result < 0:
            raise RuntimeError(self.error())
        return bytes(array) if result == 64 else None
    def pump(self, ms):
        self.lib.kp_pump(ms)
    def disconnected(self):
        return bool(self.lib.kp_disconnected())
    def close(self):
        if not self.closed:
            self.lib.kp_close()
            self.closed = True

def query_version(hid):
    hid.write(bytes([2, 0x55]).ljust(64, b'\0'))
    deadline = time.monotonic()+4
    while time.monotonic() < deadline:
        value = hid.read(100)
        if value and value[:2] == b'\2\x55':
            if tuple(value[2:5]) != (1, 1, 1):
                raise RuntimeError('Unverified firmware revision '+str(tuple(value[2:5])))
            return {'version': '1.1.1', 'raw': value.hex()}
    raise RuntimeError('No valid official version-query reply; no upgrade commands sent')

def send(hid, channel, operation, payload):
    for frame in reports(channel, operation, payload):
        hid.write(frame)
        hid.pump(20)  # Preserve headroom for the official 16 ms write delay + 3 ms spacing.

def wait_ack(hid, channel, operation, seconds, journal, predicate=None):
    deadline = time.monotonic()+seconds
    while time.monotonic() < deadline:
        raw = hid.read(min(100, max(1, int((deadline-time.monotonic())*1000))))
        if raw is None or raw[:2] != b'\2\x0f':
            continue
        try:
            value = parse_ack(raw, channel)
        except ValueError as exc:
            journal.log('invalid_ack', channel=channel, reason=str(exc), raw=raw.hex())
            continue
        n = struct.unpack_from('<H', raw, 4)[0]
        if (value['operation'] == 0 and (n != 6 or raw[9] != 1)) or (value['operation'] == 2 and (n != 8 or raw[11] != 1)):
            journal.log('invalid_ack', channel=channel, reason='Unexpected acceptance flag/block count', raw=raw.hex())
            continue
        if value['operation'] == operation and (predicate is None or predicate(value)):
            return value
    return None

def transfer(hid, channel, data, journal):
    pages = math.ceil(len(data)/256)
    padded = data.ljust(pages*256, b'\0')
    final_crc = crc16(padded)
    deadline = time.monotonic()+400
    journal.log('phase', channel=channel, operation='prepare', bytes=len(data), pages=pages,
                payload_sha256=hashlib.sha256(data).hexdigest())
    for attempt in range(10):
        send(hid, channel, 0, struct.pack('<H', pages))
        ack = wait_ack(hid, channel, 0, .5, journal)
        if ack:
            if ack['page_count'] != pages:
                raise RuntimeError('Wrong preparation page count')
            break
    else:
        raise RuntimeError('Preparation not acknowledged; no erase command sent for this segment')
    journal.log('phase', channel=channel, operation='erase_staging')
    while time.monotonic() < deadline:
        send(hid, channel, 1, struct.pack('<H', pages))
        # Intermediate progress ACKs must not accelerate the 500 ms polling interval.
        ack = wait_ack(hid, channel, 1, .5, journal, lambda x: x['erase_progress'] == 100)
        if ack:
            break
    else:
        raise RuntimeError('Staging erase deadline reached; no blocks or commit sent')
    journal.log('phase', channel=channel, operation='write_blocks')
    for index in range(pages):
        if time.monotonic() >= deadline:
            raise RuntimeError('Segment deadline reached before all blocks were confirmed')
        block = padded[index*256:(index+1)*256]
        expected = sum(block) & 65535
        for attempt in range(3):
            send(hid, channel, 2, struct.pack('<H', index)+b'\1'+block)
            ack = wait_ack(hid, channel, 2, .75, journal,
                           lambda x: x['block_index'] == index and x['block_sum'] == expected)
            if ack:
                break
            journal.log('block_retry', channel=channel, block=index, attempt=attempt+1)
        else:
            raise RuntimeError(f'Block {index} lacked a matching readback checksum; no commit sent')
        if index % 16 == 0 or index+1 == pages:
            pct = int((index+1)*100/pages)
            journal.log('progress', channel=channel, confirmed_blocks=index+1, pages=pages, percent=pct)
            print(f'CHANNEL {channel} {pct}% ({index+1}/{pages} confirmed)', flush=True)
    # This opcode starts CRC scanning. It is never sent before all block ACKs match.
    journal.log('phase', channel=channel, operation='verify_and_commit', padded_crc=final_crc)
    while time.monotonic() < deadline:
        send(hid, channel, 3, struct.pack('<H', final_crc))
        ack = wait_ack(hid, channel, 3, .5, journal)
        if ack:
            if ack['payload_crc'] != final_crc:
                raise RuntimeError('Device staging CRC does not match; stopped')
            journal.log('segment_success', channel=channel, padded_crc=final_crc,
                        confirmed_blocks=pages, device_crc_matches=True)
            return
    raise RuntimeError('Final CRC response deadline reached; no automatic full retry')

def inspect_image(data):
    digest = hashlib.sha256(data).hexdigest()
    if digest not in (builder.OFFICIAL_SHA, CANDIDATE_SHA):
        raise ValueError('Only the pinned official image or validated v2 candidate is permitted')
    segments = builder.inspect(data)
    if digest == CANDIDATE_SHA:
        if hashlib.sha256(builder.restore(data)).hexdigest() != builder.OFFICIAL_SHA:
            raise ValueError('Candidate does not restore the official image')
    if hashlib.sha256(OFFICIAL.read_bytes()).hexdigest() != builder.OFFICIAL_SHA:
        raise ValueError('Pinned official recovery image is unavailable or changed')
    return digest, segments

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--inspect', action='store_true')
    group.add_argument('--flash', type=Path)
    parser.add_argument('--journal', type=Path)
    parser.add_argument('--connect-timeout', type=int, default=120)
    args = parser.parse_args()
    data, segments, digest = None, None, None
    if args.flash:
        data = args.flash.read_bytes()
        digest, segments = inspect_image(data)
    if not args.journal:
        parser.error('--journal is required for device access')
    journal = Journal(args.journal)
    journal.log('start', mode='flash' if args.flash else 'read_only', image_sha256=digest,
                transport='native_macos_IOKit', virtual_machine_used=False, OpenJoy_used=False)
    hid = HID()
    try:
        print('WAITING for wired KP20D on this Mac. Replug USB to Mac if it is in NS mode.', flush=True)
        journal.log('connected', **hid.connect(args.connect_timeout))
        journal.log('version', **query_version(hid))
        hid.pump(3500)
        if hid.disconnected():
            raise RuntimeError('Connection did not remain stable past the mode-detection window; no upgrade commands sent')
        journal.log('version', **query_version(hid))
        journal.log('preflight_stable', seconds=3.5, firmware_query_repeated=True)
        if args.inspect:
            journal.log('success', read_only=True, firmware_write_performed=False)
            return
        for channel, (offset, length) in ((2, segments[1]), (1, segments[0])):
            transfer(hid, channel, data[offset:offset+length], journal)
            if channel == 2:
                hid.pump(500)
        journal.log('phase', operation='await_device_restart')
        for _ in range(10):
            hid.pump(500)
            if hid.disconnected():
                break
        journal.log('restart_observation', usb_disconnect_seen=hid.disconnected())
        journal.log('success', image_sha256=digest, both_segments_crc_acknowledged=True,
                    full_flash_readback_obtained=False, next_step='Reconnect to Mac and verify native Xbox enumeration')
    except BaseException as exc:
        journal.log('failure', error=str(exc), automatic_full_retry=False)
        raise
    finally:
        hid.close()

if __name__ == '__main__':
    main()
