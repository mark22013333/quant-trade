"""
全域設定檔
"""

# 預設交易設定
DEFAULT_SETTINGS = {
    # 回測設定
    'BACKTEST': {
        'INITIAL_CAPITAL': 1_000_000,  # 起始資金
        'COMMISSION_RATE': 0.001425,   # 手續費率 (買賣各收 0.1425%)
        'MIN_COMMISSION_FEE': 20.0,    # 最低手續費（元）
        'TAX_RATE': 0.003,           # 證券交易稅 (賣出時收取 0.3%)
        'SLIPPAGE': 0.001,           # 滑價 (假設每次交易有 0.1% 的滑價)
        'MAX_GAP_FOR_ENTRY': 0.07,   # 進場跳空超過門檻則延後成交
        'MIN_DAILY_VOLUME_FOR_FILL': 0,  # 低於門檻視為流動性不足（0 代表不限制）
        'LIMIT_DOWN_FACTOR': 0.90,   # 跌停價近似比例（壓力情境模擬）
        'LIMIT_UP_FACTOR': 1.10,     # 漲停價近似比例（壓力情境模擬）
        'MAX_ENTRY_DELAY_DAYS': 3,   # 訊號待成交最大延遲日數
    },
    
    # 風險管理設定
    'RISK': {
        'MAX_POSITION_SIZE': 0.2,    # 單一部位最大比例 (資金的 20%)
        'STOP_LOSS_PCT': 0.05,       # 單筆停損比例 (5%)
        'MAX_DRAWDOWN_PCT': 0.15,    # 最大回撤停損 (15%)
        'MAX_TRADES_PER_DAY': 5,     # 每日最大交易次數
    },
    
    # 資料設定
    'DATA': {
        'DEFAULT_PROVIDER': 'yfinance',
        'HISTORY_DAYS': 365,         # 載入歷史資料的天數
    },
    
    # 通知設定
    'NOTIFICATION': {
        'ENABLE_LINE': False,
        'ENABLE_EMAIL': False,
    }
}

# 交易時間設定 (台灣市場)
MARKET_HOURS = {
    'OPEN': '09:00',
    'CLOSE': '13:30',
    'TIMEZONE': 'Asia/Taipei',
}

# 日誌設定
LOG_SETTINGS = {
    'LEVEL': 'INFO',  # 可設為: DEBUG, INFO, WARNING, ERROR, CRITICAL
    'FORMAT': '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    'FILE': 'quant_trade.log',
}
