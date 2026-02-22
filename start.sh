#!/bin/bash

# 啟動正確的虛擬環境
source "$(dirname "$0")/venv/bin/activate"

# 顯示目前使用的 Python 環境路徑
echo "使用的 Python 環境: $(which python)"
echo "Python 版本: $(python --version)"

# 顯示功能選單
echo "===================="
echo "台股量化交易系統選單"
echo "===================="
echo "1) 執行波段交易分析"
echo "2) 執行完整回測"
echo "3) 產出波段分析報表"
echo "4) 產生短期投資 Dashboard"
echo "5) 產生 Shioaji AI 協作中心"
echo "6) 啟動 Web 控制台"
echo "q) 離開"
echo ""

# shellcheck disable=SC2162
read -p "請選擇功能 (1-6, q): " choice

case "$choice" in
  1)
    echo "執行波段交易分析..."
    python swing_analysis.py
    ;;
  2)
    echo "執行完整回測..."
    python main.py --mode backtest
    ;;
  3)
    echo "產出波段分析報表..."
    python main.py --mode report
    ;;
  4)
    echo "產生短期投資 Dashboard..."
    python main.py --mode dashboard
    ;;
  5)
    echo "產生 Shioaji AI 協作中心..."
    python main.py --mode shioaji-ai
    ;;
  6)
    echo "啟動 Web 控制台..."
    python run_web.py --reload
    ;;
  q)
    echo "離開"
    exit 0
    ;;
  *)
    echo "無效的選擇，請重新執行腳本"
    ;;
esac
