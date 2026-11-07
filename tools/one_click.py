"""Prepare and launch the pinned KP20D updater; --prepare-only never opens USB."""
import argparse
import ctypes
import fcntl
import hashlib
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import urllib.request
import uuid
from datetime import datetime

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import build_firmware as builder

OFFICIAL_SHA = builder.OFFICIAL_SHA
PATCH_SHA = 'b8f9a7e864b0750eda4ebb86af19b19d9a0c09f588ae5178cabc877b3f38857b'
IMAGE_SIZE = 340302
OFFICIAL_URL = ('https://github.com/JairHan/beitong-kp20d-macos-xbox/'
                'releases/download/v2.0.0/KP20D-official-1.1.1.bin')


def verify(data, expected):
    if len(data) != IMAGE_SIZE or hashlib.sha256(data).hexdigest() != expected:
        raise ValueError('固件大小或 SHA256 不匹配，已停止；不会刷写或覆盖现有文件。')
    builder.inspect(data)


def save_new(path, data):
    """Publish a complete file without replacing an existing local image."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.kp20d-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(name, path)
    finally:
        os.unlink(name)


def prepare_images(root=ROOT):
    official = root / 'firmware/KP20D-official-1.1.1.bin'
    patched = root / 'firmware/KP20D-nativeXboxUSB-v2.bin'
    if official.exists():
        original = official.read_bytes()
        verify(original, OFFICIAL_SHA)
    else:
        print('正在下载指定官方原件（约 332 KiB），随后校验 SHA256……', flush=True)
        with urllib.request.urlopen(OFFICIAL_URL, timeout=30) as response:
            if not response.geturl().startswith('https://'):
                raise ValueError('拒绝非 HTTPS 下载。')
            original = response.read(IMAGE_SIZE + 1)
        verify(original, OFFICIAL_SHA)
        save_new(official, original)
    if patched.exists():
        result = patched.read_bytes()
        verify(result, PATCH_SHA)
        if builder.restore(result) != original:
            raise ValueError('补丁无法精确还原官方原件，已停止。')
    else:
        result = builder.build(original)
        verify(result, PATCH_SHA)
        if builder.restore(result) != original:
            raise ValueError('补丁逆变换检查失败。')
        save_new(patched, result)
    print('官方原件与 v2 补丁均已通过大小、SHA256、分段 CRC 和逆变换检查。', flush=True)
    return official, patched


def prepare_library(root=ROOT):
    library = root / 'build/libkp20d-native-hid.dylib'
    if library.exists():
        try:
            ctypes.CDLL(str(library))  # Loading the library does not call kp_open().
            return
        except OSError:
            print('现有原生库无法载入，尝试在本机重新编译……', flush=True)
    probe = subprocess.run(['/usr/bin/xcrun', '--find', 'clang'], capture_output=True, text=True)
    if probe.returncode:
        raise RuntimeError('缺少 Xcode Command Line Tools。请先运行 xcode-select --install，安装完成后重新双击脚本。')
    library.parent.mkdir(parents=True, exist_ok=True)
    temporary = library.with_name('libkp20d-' + uuid.uuid4().hex + '.dylib')
    try:
        subprocess.run([probe.stdout.strip(), '-O2', '-mmacosx-version-min=12.0',
                        '-dynamiclib', '-framework', 'Foundation', '-framework', 'IOKit',
                        str(root / 'tools/kp20d-native-hid.m'), '-o', str(temporary)], check=True)
        ctypes.CDLL(str(temporary))
        os.replace(temporary, library)
    finally:
        temporary.unlink(missing_ok=True)


def check_conflicting_apps():
    result = subprocess.run(['/bin/ps', '-axo', 'comm='], capture_output=True, text=True, check=True)
    conflicts = {'openjoy', 'openjoystickdriver', 'ojd'}
    if any(Path(line.strip()).name.lower() in conflicts for line in result.stdout.splitlines()):
        raise RuntimeError('检测到 OpenJoy / OpenJoystickDriver 正在运行，请退出后重新双击脚本。')


def acquire_lock():
    folder = Path.home() / 'Library/Caches/beitong-kp20d-macos-xbox'
    folder.mkdir(parents=True, exist_ok=True)
    stream = (folder / 'flash.lock').open('a')
    try:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        stream.close()
        raise RuntimeError('已有另一份一键工具运行，请勿同时刷写。')
    return stream


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=('patch', 'restore'), default='patch')
    parser.add_argument('--prepare-only', action='store_true', help='下载/校验固件并准备库，不访问 USB、不刷写')
    args = parser.parse_args(argv)
    if platform.system() != 'Darwin':
        raise RuntimeError('此工具仅支持 macOS。')
    if sys.version_info < (3, 9):
        raise RuntimeError('需要 Python 3.9 或更新版本。')
    with acquire_lock():
        official, patched = prepare_images()
        prepare_library()
        if args.prepare_only:
            print('准备完成；没有打开 USB 或执行刷写。')
            return 0
        check_conflicting_apps()
        title = '恢复官方固件（撤销 macOS 原生有线修复）' if args.mode == 'restore' else '刷入 macOS 原生 Xbox 有线补丁 v2'
        print('\n即将' + title)
        print('仅用于 BTP-KP20D 与指定 1.1.1 镜像。一次只连接一只目标手柄。')
        print('请关闭其他升级工具，用 USB 数据线连接 Mac，并保持供电。')
        print('刷写开始后不要拔线、退出或休眠；中断可能导致无法启动。')
        print('当前已正常工作的手柄无需重复刷写。')
        if not sys.stdin.isatty():
            raise RuntimeError('需要在终端中手动确认；未访问 USB。')
        try:
            answer = input('输入 YES 并回车开始，其他输入取消：').strip()
        except EOFError:
            answer = ''
        if answer != 'YES':
            print('已取消，没有访问 USB 或刷写。')
            return 0
        # Revalidate after confirmation; users may have replaced a file while waiting.
        target = official if args.mode == 'restore' else patched
        verify(official.read_bytes(), OFFICIAL_SHA)
        verify(target.read_bytes(), OFFICIAL_SHA if args.mode == 'restore' else PATCH_SHA)
        check_conflicting_apps()
        logs = ROOT / 'logs'
        logs.mkdir(exist_ok=True)
        journal = logs / (datetime.now().strftime('%Y%m%d-%H%M%S-') + args.mode + '-' + uuid.uuid4().hex[:8] + '.jsonl')
        print('刷写日志：' + str(journal), flush=True)
        print('工具会等待手柄；若未识别，请在刷写尚未开始时拔插一次，并连接到 Mac。', flush=True)
        command = ['/usr/bin/caffeinate', '-i', '-m', '-s', sys.executable,
                   str(ROOT / 'tools/native_updater.py'), '--flash', str(target),
                   '--journal', str(journal), '--connect-timeout', '180']
        result = subprocess.run(command)
        if result.returncode:
            raise RuntimeError('刷写未成功完成，已停止，不会自动重刷。请保留日志排查。')
        print('\n设备已确认两段固件 CRC。请退出工具并重新插入 USB，再测试输入。')
        if args.mode == 'restore':
            print('已写入官方镜像；macOS 原生有线修复将被撤销。')
        else:
            print('请保持 OpenJoy 关闭，确认 macOS 原生 Xbox 输入；本脚本未自动验收按键。')
        return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print('\n工具已中断；如果已经开始刷写，请保留日志排查，不要反复尝试。', file=sys.stderr)
        sys.exit(130)
    except Exception as exc:
        print('\n错误：' + str(exc), file=sys.stderr)
        sys.exit(1)
