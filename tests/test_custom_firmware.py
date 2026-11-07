import binascii
import contextlib
import importlib.util
import io
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import native_updater as u
import one_click as o


def container(first=b'custom code!', second=b'custom data!'):
    data = bytearray(34) + first + second
    struct.pack_into('<IIIH', data, 0, 1, 2, len(data), binascii.crc_hqx(data[34:], 0))
    for i, (offset, length) in enumerate(((34, len(first)), (34 + len(first), len(second)))):
        struct.pack_into('<IIH', data, 14 + 10*i, offset, length, binascii.crc_hqx(data[offset:offset+length], 0))
    return bytes(data)


class CustomFirmwareTests(unittest.TestCase):
    def test_custom_without_official_backup(self):
        with patch.object(u, 'OFFICIAL', Path('/does-not-exist/official.bin')):
            digest, segments = u.inspect_image(container(), allow_custom=True)
            self.assertEqual(len(digest), 64)
            self.assertEqual([n for _, n in segments], [12, 12])

    def test_default_still_rejects_unpinned_image(self):
        with self.assertRaises(ValueError):
            u.inspect_image(container())

    def test_custom_crc_damage_rejected(self):
        data = bytearray(container()); data[-1] ^= 1
        with self.assertRaises(ValueError):
            u.inspect_image(data, allow_custom=True)

    def test_oversized_segment_rejected(self):
        for data in (container(first=b'x'*100453), container(second=b'x'*239817)):
            with self.assertRaises(ValueError):
                u.inspect_image(data, allow_custom=True)

    def test_raw_binary_rejected(self):
        with self.assertRaises(ValueError):
            u.inspect_image(b'raw flash image' * 100, allow_custom=True)

    def test_changed_device_version_is_reported_in_custom_mode(self):
        class HID:
            def write(self, data): pass
            def read(self, timeout): return b'\x02\x55\x01\x02\x03\x01'.ljust(64, b'\0')
        self.assertEqual(u.query_version(HID(), allow_custom=True)['version'], '1.2.3')
        with self.assertRaises(RuntimeError):
            u.query_version(HID())

    def test_digest_mismatch_before_device_open(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(u, 'HID') as hid:
            p = Path(folder)/'custom.bin'; p.write_bytes(container())
            with patch.object(sys, 'argv', ['updater', '--flash', str(p), '--allow-custom', '--expected-sha256', '0'*64, '--journal', str(Path(folder)/'log.jsonl')]):
                with self.assertRaises(ValueError): u.main()
            hid.assert_not_called()

    def exercise(self, confirm='YES', mutate=False, prepare=False):
        with tempfile.TemporaryDirectory(prefix='custom image ') as folder:
            root = Path(folder); image = root/'my modified.bin'; image.write_bytes(container())
            def answer(prompt):
                if mutate: image.write_bytes(container(first=b'different!!!'))
                return confirm
            with patch.object(o, 'ROOT', root), patch.object(o.platform, 'system', return_value='Darwin'), \
                    patch.object(o, 'acquire_lock', return_value=io.StringIO()), \
                    patch.object(o, 'prepare_images', side_effect=AssertionError('Custom mode must not require pinned images')), \
                    patch.object(o, 'prepare_library'), patch.object(o, 'check_conflicting_apps'), \
                    patch.object(o.sys.stdin, 'isatty', return_value=True), patch('builtins.input', side_effect=answer), \
                    patch.object(o.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0)) as run, \
                    contextlib.redirect_stdout(io.StringIO()):
                args = ['--image', str(image)] + (['--prepare-only'] if prepare else [])
                if mutate:
                    with self.assertRaises(ValueError): o.main(args)
                else:
                    self.assertEqual(o.main(args), 0)
                return run.call_args_list

    def test_custom_selection_is_passed_as_one_argument(self):
        calls = self.exercise()
        self.assertEqual(len(calls), 1)
        cmd = calls[0].args[0]
        self.assertIn('--allow-custom', cmd)
        self.assertIn('my modified.bin', cmd[cmd.index('--flash')+1])
        self.assertIn('--expected-sha256', cmd)

    def test_custom_cancel_never_starts_updater(self):
        self.assertEqual(self.exercise(confirm='no'), [])

    def test_custom_prepare_never_starts_updater(self):
        self.assertEqual(self.exercise(prepare=True), [])

    def test_changed_selected_file_never_starts_updater(self):
        self.assertEqual(self.exercise(mutate=True), [])

    def test_picker_cancel_does_not_prepare_or_open_device(self):
        with patch.object(o.platform, 'system', return_value='Darwin'), \
                patch.object(o, 'acquire_lock', return_value=io.StringIO()), \
                patch.object(o, 'select_image', return_value=None), \
                patch.object(o, 'prepare_library') as library, patch.object(o.subprocess, 'run') as run:
            self.assertEqual(o.main([]), 0)
            library.assert_not_called(); run.assert_not_called()


if __name__ == '__main__': unittest.main()
