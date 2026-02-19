"""
主程式：整合資料下載、策略、回測、模擬下單
"""
import argparse
import os
from datetime import datetime, timedelta

import pandas as pd

from backtest.backtest_engine import BacktestEngine
from broker.paper_broker import PaperBroker
from config.settings import DEFAULT_SETTINGS
from data.market_data import MarketData
from data.providers.yfinance_provider import YFinanceProvider
from strategies.ma_cross import MACrossStrategy


def _load_data_from_csv(symbol: str):
    candidates = [
        os.path.join("data", f"{symbol}.csv"),
        os.path.join("data", "stock_data", f"{symbol}_1d.csv"),
        os.path.join("data", "stock_data", f"{symbol}.csv")
    ]
    data_path = next((p for p in candidates if os.path.exists(p)), None)
    if data_path is None:
        return None
    df = pd.read_csv(data_path, index_col=0, parse_dates=True)
    df.index = pd.to_datetime(df.index, errors="coerce")
    df = df[~df.index.isna()]
    df = df[pd.to_numeric(df['Close'], errors='coerce').notnull()]
    df['Close'] = df['Close'].astype(float)
    return df


def _load_data_from_provider(symbol: str, start_date: str, end_date: str):
    provider = YFinanceProvider()
    market_data = MarketData(provider)
    df = market_data.load_data(symbol, start_date, end_date)
    return df


def run_backtest(args):
    settings = DEFAULT_SETTINGS.get('BACKTEST', {})
    data_settings = DEFAULT_SETTINGS.get('DATA', {})

    if args.start_date:
        start_date = args.start_date
    else:
        days = int(data_settings.get('HISTORY_DAYS', 365))
        start_date = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')

    end_date = args.end_date or datetime.now().strftime('%Y-%m-%d')

    # 優先使用本地 CSV
    df = _load_data_from_csv(args.symbol)
    if df is None:
        df = _load_data_from_provider(args.symbol, start_date, end_date)

    if df is None or df.empty:
        raise RuntimeError("無法取得歷史資料，請確認資料來源或 CSV 是否存在")

    strategy = MACrossStrategy(short_window=args.short_window, long_window=args.long_window)
    broker = PaperBroker(init_cash=settings.get('INITIAL_CAPITAL', 1_000_000))

    engine = BacktestEngine(df, strategy, broker)
    engine.run()
    engine.report()


def run_report(_args):
    # 直接呼叫波段分析報表
    import swing_analysis
    swing_analysis.main()


def run_dashboard(_args):
    from analysis.short_term_ranker import run_short_term_ranking
    from dashboard_generator import generate_dashboard

    output = run_short_term_ranking()
    html_path = generate_dashboard(output.full_df, output.top20_df)
    print(f"Dashboard 已產生: {html_path}")


def build_parser():
    parser = argparse.ArgumentParser(description="台股量化交易系統")
    parser.add_argument(
        "--mode",
        choices=["backtest", "report", "dashboard"],
        default="backtest",
        help="執行模式: backtest / report / dashboard"
    )
    parser.add_argument("--symbol", default="2330.TW", help="回測標的 (預設 2330.TW)")
    parser.add_argument("--start-date", dest="start_date", help="回測起始日 YYYY-MM-DD")
    parser.add_argument("--end-date", dest="end_date", help="回測結束日 YYYY-MM-DD")
    parser.add_argument("--short-window", type=int, default=5, help="短期均線週期")
    parser.add_argument("--long-window", type=int, default=20, help="長期均線週期")
    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    if args.mode == "backtest":
        run_backtest(args)
    elif args.mode == "report":
        run_report(args)
    elif args.mode == "dashboard":
        run_dashboard(args)


if __name__ == "__main__":
    main()
