#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

HOST="${DASHBOARD_HOST:-127.0.0.1}"
PORT="${DASHBOARD_PORT:-8766}"
SKIP_INSTALL="${DASHBOARD_SKIP_INSTALL:-0}"
SKIP_GENERATE="${DASHBOARD_SKIP_GENERATE:-0}"
RELOAD="${WEB_RELOAD:-0}"
START_SCHEDULER="${DASHBOARD_START_SCHEDULER:-1}"

usage() {
  cat <<'EOF'
用法：./run_dashboard.sh [選項]

自動準備 Python 虛擬環境、產生短期 Dashboard，並啟動 Web 控制台。

選項：
  --host HOST       綁定主機，預設 127.0.0.1
  --port PORT       綁定連接埠，預設 8766
  --reload          啟用 Web 程式碼自動重新載入
  --skip-install    略過 requirements.txt 套件確認
  --skip-generate   啟動前不重新產生短期 Dashboard
  --no-scheduler    不啟動短期 Top 10 排程器
  -h, --help        顯示說明

也可使用環境變數：
  DASHBOARD_HOST、DASHBOARD_PORT、DASHBOARD_SKIP_INSTALL、
  DASHBOARD_SKIP_GENERATE、DASHBOARD_START_SCHEDULER、WEB_RELOAD、CONTROL_PANEL_TOKEN
EOF
}

is_true() {
  case "$1" in
    1|true|TRUE|True|yes|YES|Yes|on|ON|On) return 0 ;;
    *) return 1 ;;
  esac
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --host)
      [[ $# -ge 2 ]] || { echo "[ERROR] --host 需要指定值" >&2; exit 2; }
      HOST="$2"
      shift 2
      ;;
    --port)
      [[ $# -ge 2 ]] || { echo "[ERROR] --port 需要指定值" >&2; exit 2; }
      PORT="$2"
      shift 2
      ;;
    --reload)
      RELOAD=1
      shift
      ;;
    --skip-install)
      SKIP_INSTALL=1
      shift
      ;;
    --skip-generate)
      SKIP_GENERATE=1
      shift
      ;;
    --no-scheduler)
      START_SCHEDULER=0
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "[ERROR] 未知選項：$1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ ! "$PORT" =~ ^[0-9]+$ ]] || (( PORT < 1 || PORT > 65535 )); then
  echo "[ERROR] 連接埠必須是 1 到 65535 之間的整數：$PORT" >&2
  exit 2
fi

if [[ "$HOST" != "127.0.0.1" && "$HOST" != "localhost" && "$HOST" != "::1" ]] \
  && [[ -z "${CONTROL_PANEL_TOKEN:-}" ]]; then
  echo "[ERROR] 綁定非 localhost 時必須設定 CONTROL_PANEL_TOKEN" >&2
  exit 2
fi

VENV_DIR=""
if [[ -x "$SCRIPT_DIR/venv/bin/python" ]]; then
  VENV_DIR="$SCRIPT_DIR/venv"
elif [[ -x "$SCRIPT_DIR/.venv/bin/python" ]]; then
  VENV_DIR="$SCRIPT_DIR/.venv"
else
  if ! command -v python3 >/dev/null 2>&1; then
    echo "[ERROR] 找不到 python3，請先安裝 Python 3.10 以上版本" >&2
    exit 1
  fi
  VENV_DIR="$SCRIPT_DIR/.venv"
  echo "[INFO] 建立虛擬環境：$VENV_DIR"
  python3 -m venv "$VENV_DIR"
fi

PYTHON_BIN="$VENV_DIR/bin/python"
if ! "$PYTHON_BIN" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)'; then
  echo "[ERROR] Python 版本過舊，需要 Python 3.10 以上版本" >&2
  exit 1
fi

echo "[INFO] 專案目錄：$SCRIPT_DIR"
echo "[INFO] Python：$("$PYTHON_BIN" -c 'import sys; print(sys.executable)')"
echo "[INFO] 版本：$("$PYTHON_BIN" --version 2>&1)"

if ! is_true "$SKIP_INSTALL"; then
  echo "[INFO] 確認 requirements.txt 套件…"
  "$PYTHON_BIN" -m pip install -r requirements.txt
else
  echo "[INFO] 已略過套件安裝檢查"
fi

mkdir -p reports

echo "[INFO] 初始化資料庫結構…"
"$PYTHON_BIN" -m app.cli init-db

if command -v codex >/dev/null 2>&1; then
  if CODEX_LOGIN_STATUS="$(codex login status 2>&1)"; then
    if [[ "$CODEX_LOGIN_STATUS" == *"ChatGPT"* || "$CODEX_LOGIN_STATUS" == *"logged in"* || "$CODEX_LOGIN_STATUS" == *"Logged in"* ]]; then
      echo "[READY] Codex CLI 已使用 ChatGPT 訂閱登入"
    else
      echo "[WARN] Codex CLI 可使用，但無法確認 ChatGPT 訂閱登入；AI 失敗時會改採純量化模式"
    fi
  else
    echo "[WARN] Codex 尚未登入。可另開終端機執行 codex login；目前會使用純量化模式"
  fi
else
  echo "[WARN] 找不到 Codex CLI；服務仍會啟動，AI 分析將使用純量化降級模式"
fi

if ! is_true "$SKIP_GENERATE"; then
  echo "[INFO] 產生短期投資 Dashboard…"
  if ! "$PYTHON_BIN" main.py --mode dashboard; then
    echo "[WARN] 舊版靜態 Dashboard 產生失敗；新版 Top 10 服務仍會繼續啟動" >&2
  fi
else
  echo "[INFO] 已略過啟動前的 Dashboard 產生步驟"
fi

SERVER_ARGS=(--host "$HOST" --port "$PORT")
if is_true "$RELOAD"; then
  SERVER_ARGS+=(--reload)
fi

DISPLAY_HOST="$HOST"
if [[ "$DISPLAY_HOST" == "0.0.0.0" || "$DISPLAY_HOST" == "::" ]]; then
  DISPLAY_HOST="127.0.0.1"
fi

echo
echo "[READY] Web 控制台：http://$DISPLAY_HOST:$PORT"
echo "[READY] 首頁會顯示短期投資 Top 10；進階工具位於 /advanced"
echo "[INFO] 按 Ctrl+C 停止服務"

SCHEDULER_PID=""
cleanup() {
  if [[ -n "$SCHEDULER_PID" ]] && kill -0 "$SCHEDULER_PID" 2>/dev/null; then
    kill "$SCHEDULER_PID" 2>/dev/null || true
    wait "$SCHEDULER_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

if is_true "$START_SCHEDULER"; then
  echo "[INFO] 啟動 Top 10 排程器（07:30 盤前、14:10 量化、14:40 AI、15:20 固化、週日成果驗證）"
  "$PYTHON_BIN" -m app.cli run-scheduler \
    --disable-signal-job \
    --enable-shortlist \
    --notify none \
    >> reports/shortlist_scheduler.log 2>&1 &
  SCHEDULER_PID=$!
fi

"$PYTHON_BIN" run_web.py "${SERVER_ARGS[@]}"
