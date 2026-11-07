#!/bin/bash
set -u
set -o pipefail
cd "$(dirname "$0")" || exit 1
export PYTHONUNBUFFERED=1
if ! command -v python3 >/dev/null 2>&1; then
    echo '未找到 Python 3。请先安装 Python 3.9 或更新版本，再重新双击。'
    read -r -p '按回车退出……' _
    exit 1
fi
mkdir -p logs || exit 1
python3 tools/one_click.py --mode file "$@" 2>&1 | tee "logs/console-$(date +%Y%m%d-%H%M%S)-$$.log"
result=$?
if [ "$result" -ne 0 ]; then echo '操作未完成，请查看上方错误与 logs 中的记录。'; fi
if [ -t 0 ]; then read -r -p '按回车退出……' _; fi
exit "$result"
