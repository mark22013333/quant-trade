"""
交易標的設定
"""

# 台股主要指數
INDICES = {
    'TAIEX': '^TWII',       # 台灣加權指數
    'TAIEX_ELECTRONIC': '^TWIE',  # 台灣電子類指數
    'TAIEX_FINANCE': '^TWIF',  # 台灣金融類指數
}

# 熱門 ETF
ETFS = {
    'TW50': '0050.TW',     # 台灣50
    'TWE': '0051.TW',      # 中型100
    'TWN': '006204.TW',    # 台灣高息
}

# 主要股票清單（示範）
STOCKS = {
    # 半導體族群
    'TSMC': '2330.TW',      # 台積電
    'UMC': '2303.TW',       # 聯電
    'MEDIATEK': '2454.TW',  # 聯發科
    
    # 電子代工
    'FOXCONN': '2317.TW',   # 鴻海
    'PEGATRON': '4938.TW',  # 和碩
    
    # 金融股
    'CATHAY': '2882.TW',    # 國泰金
    'FUBON': '2881.TW',     # 富邦金
}

# 策略用股票群組
STOCK_GROUPS = {
    'SEMICONDUCTOR': ['2330.TW', '2303.TW', '2454.TW', '2379.TW', '3105.TW'],
    'ELECTRONICS': ['2317.TW', '2354.TW', '2382.TW', '4938.TW'],
    'FINANCIAL': ['2882.TW', '2881.TW', '2886.TW', '2884.TW'],
}
