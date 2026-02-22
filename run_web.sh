#!/bin/bash

set -euo pipefail

ROOT_DIR="$(dirname "$0")"
VENV_DIR="$ROOT_DIR/venv"
REQ_FILE="$ROOT_DIR/requirements.txt"
STAMP_FILE="$VENV_DIR/.requirements_installed"

if [ ! -f "$VENV_DIR/bin/activate" ]; then
  echo "找不到虛擬環境，正在建立 venv..."
  python -m venv "$VENV_DIR"
fi

source "$VENV_DIR/bin/activate"

if [ ! -f "$STAMP_FILE" ] || [ "$REQ_FILE" -nt "$STAMP_FILE" ]; then
  echo "安裝/更新依賴套件..."
  python -m pip install -r "$REQ_FILE"
  touch "$STAMP_FILE"
fi

echo "啟動 Web 控制台 (http://127.0.0.1:8000)"
if [ "${WEB_RELOAD:-0}" = "1" ]; then
  echo "開啟開發模式（reload）。"
  python run_web.py --reload
else
  python run_web.py
fi
