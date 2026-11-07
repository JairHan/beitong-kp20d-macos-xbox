import hashlib
import importlib.util
import io
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('one_click', Path(__file__).resolve().parents[1] / 'tools/one_click.py')
o = importlib.util.module_from_spec(spec)
spec.loader.exec_module(o)


class OneClickTests(unittest.TestCase):
    def test_verifier_rejects_wrong_size_and_hash(self):
        with self.assertRaises(ValueError):
            o.verify(b'bad', o.OFFICIAL_SHA)
        with self.assertRaises(ValueError):
            o.verify(b'x' * o.IMAGE_SIZE, o.OFFICIAL_SHA)

    def test_atomic_save_does_not_overwrite(self):
        with tempfile.TemporaryDirectory() as folder:
            p = Path(folder) / 'image.bin'
            o.save_new(p, b'original')
            with self.assertRaises(FileExistsError):
                o.save_new(p, b'replacement')
            self.assertEqual(p.read_bytes(), b'original')
            self.assertEqual([x.name for x in p.parent.iterdir()], ['image.bin'])

    def test_existing_corrupt_image_stops_before_download(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(o.urllib.request, 'urlopen') as request:
            root = Path(folder)
            (root / 'firmware').mkdir()
            path = root / 'firmware/KP20D-official-1.1.1.bin'
            path.write_bytes(b'corrupt')
            with self.assertRaises(ValueError):
                o.prepare_images(root)
            request.assert_not_called()
            self.assertEqual(path.read_bytes(), b'corrupt')

    def test_download_failure_leaves_no_image(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(o.urllib.request, 'urlopen', side_effect=OSError('offline')):
            root = Path(folder)
            with self.assertRaises(OSError):
                o.prepare_images(root)
            self.assertFalse((root / 'firmware/KP20D-official-1.1.1.bin').exists())

    def test_corrupt_download_is_not_saved(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(o.urllib.request, 'urlopen') as request:
            response = request.return_value.__enter__.return_value
            response.geturl.return_value = 'https://example.test/firmware'
            response.read.return_value = b'corrupt'
            with self.assertRaises(ValueError):
                o.prepare_images(Path(folder))
            self.assertFalse((Path(folder) / 'firmware/KP20D-official-1.1.1.bin').exists())

    def test_offline_images_are_reused_and_generated(self):
        original, patched = b'official', b'patched!'
        with tempfile.TemporaryDirectory() as folder, patch.object(o, 'IMAGE_SIZE', 8), \
                patch.object(o, 'OFFICIAL_SHA', hashlib.sha256(original).hexdigest()), \
                patch.object(o, 'PATCH_SHA', hashlib.sha256(patched).hexdigest()), \
                patch.object(o.builder, 'inspect'), patch.object(o.builder, 'build', return_value=patched) as build, \
                patch.object(o.builder, 'restore', return_value=original), \
                patch.object(o.urllib.request, 'urlopen') as request:
            root = Path(folder)
            o.save_new(root / 'firmware/KP20D-official-1.1.1.bin', original)
            _, output = o.prepare_images(root)
            self.assertEqual(output.read_bytes(), patched)
            o.prepare_images(root)
            build.assert_called_once_with(original)
            request.assert_not_called()

    def test_restore_does_not_depend_on_patch_file(self):
        original = b'official'
        with tempfile.TemporaryDirectory() as folder, patch.object(o, 'IMAGE_SIZE', 8), \
                patch.object(o, 'OFFICIAL_SHA', hashlib.sha256(original).hexdigest()), \
                patch.object(o.builder, 'inspect'), patch.object(o.builder, 'build') as build:
            root = Path(folder)
            o.save_new(root / 'firmware/KP20D-official-1.1.1.bin', original)
            o.save_new(root / 'firmware/KP20D-nativeXboxUSB-v2.bin', b'locally modified')
            official, patched = o.prepare_images(root, include_patch=False)
            self.assertEqual(official.read_bytes(), original)
            self.assertIsNone(patched)
            build.assert_not_called()

    def run_main(self, args, answer='YES', result=0, tty=True, failure=None):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            official, patched = root / 'official.bin', root / 'patched.bin'
            official.write_bytes(b'official'); patched.write_bytes(b'patched')
            with patch.object(o, 'ROOT', root), patch.object(o.platform, 'system', return_value='Darwin'), \
                    patch.object(o, 'acquire_lock', return_value=io.StringIO()), \
                    patch.object(o, 'prepare_images', return_value=(official, patched)), \
                    patch.object(o, 'prepare_library'), patch.object(o, 'verify'), \
                    patch.object(o, 'check_conflicting_apps'), patch.object(o.sys.stdin, 'isatty', return_value=tty), \
                    patch('builtins.input', return_value=answer), \
                    patch.object(o.subprocess, 'run', return_value=subprocess.CompletedProcess([], result)) as run:
                if failure:
                    with self.assertRaises(failure):
                        o.main(['--mode', 'patch'] + args)
                else:
                    self.assertEqual(o.main(['--mode', 'patch'] + args), 0)
                return run.call_args_list, official, patched

    def test_prepare_only_never_launches_updater(self):
        calls, _, _ = self.run_main(['--prepare-only'])
        self.assertEqual(calls, [])

    def test_cancel_never_launches_updater(self):
        calls, _, _ = self.run_main([], answer='no')
        self.assertEqual(calls, [])

    def test_noninteractive_refused(self):
        calls, _, _ = self.run_main([], tty=False, failure=RuntimeError)
        self.assertEqual(calls, [])

    def test_patch_and_restore_select_correct_image(self):
        for mode in ('patch', 'restore'):
            calls, official, patched = self.run_main(['--mode', mode])
            self.assertEqual(len(calls), 1)
            cmd = calls[0].args[0]
            self.assertEqual(cmd[0], '/usr/bin/caffeinate')
            self.assertEqual(cmd[cmd.index('--flash') + 1], str(official if mode == 'restore' else patched))

    def test_updater_failure_stops_without_retry(self):
        calls, _, _ = self.run_main([], result=1, failure=RuntimeError)
        self.assertEqual(len(calls), 1)

    def test_conflicting_app_refused(self):
        with patch.object(o.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, '/Applications/OpenJoy.app/Contents/MacOS/OpenJoy\n')):
            with self.assertRaises(RuntimeError):
                o.check_conflicting_apps()

    def test_lock_refuses_second_process(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(o.Path, 'home', return_value=Path(folder)):
            with o.acquire_lock():
                with self.assertRaises(RuntimeError):
                    o.acquire_lock()


if __name__ == '__main__':
    unittest.main()
