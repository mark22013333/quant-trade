"""
台股產業分類股票清單
"""

# 科技股：主要包含半導體、IC設計、通訊電子等科技相關公司
TECH_STOCKS = [
    '2330.TW',  # 台積電
    '2454.TW',  # 聯發科
    '2317.TW',  # 鴻海
    '2308.TW',  # 台達電
    '2382.TW',  # 廣達
    '2379.TW',  # 瑞昱
    '3034.TW',  # 聯詠
    '2353.TW',  # 宏碁
    '2345.TW',  # 智邦
    '3037.TW',  # 欣興
    '7547.TW',  # 碩網
]

# 電子股：包含面板、印刷電路板、電子零組件等
ELECTRONIC_STOCKS = [
    '2409.TW',  # 友達
    '3481.TW',  # 群創
    '8046.TW',  # 南電
    '2385.TW',  # 群光
    '2301.TW',  # 光寶科
    '2313.TW',  # 華通
    '2327.TW',  # 國巨
    '2459.TW',  # 敦吉
    '2458.TW',  # 義隆
    '2449.TW',  # 京元電子
]

# 傳產股：包含金融、鋼鐵、塑化、紡織、水泥等傳統產業
TRADITIONAL_STOCKS = [
    '2801.TW',  # 彰銀
    '2881.TW',  # 富邦金
    '2882.TW',  # 國泰金
    '2002.TW',  # 中鋼
    '1301.TW',  # 台塑
    '1303.TW',  # 南亞
    '1326.TW',  # 台化
    '1101.TW',  # 台泥
    '2105.TW',  # 正新
    '9945.TW',  # 潤泰新
]

# 取得所有分類的股票清單
ALL_CATEGORY_STOCKS = {
    '科技股': TECH_STOCKS,
    '電子股': ELECTRONIC_STOCKS,
    '傳產股': TRADITIONAL_STOCKS
}

def get_all_stocks():
    """取得所有分類的股票"""
    all_stocks = []
    for category, stocks in ALL_CATEGORY_STOCKS.items():
        all_stocks.extend(stocks)
    return list(set(all_stocks))  # 移除可能的重複項目
