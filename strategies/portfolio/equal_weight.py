"""
等權重投資組合策略
"""
import pandas as pd
import numpy as np
from utils.signals import add_signal_from_position
from ..base_strategy import BaseStrategy

class EqualWeightStrategy(BaseStrategy):
    """
    等權重投資組合策略
    
    參數說明：
    - rebalance_frequency: 再平衡頻率，預設為 'M' (每月)，可選 'W'(每週), 'Q'(每季)
    - stock_universe: 股票池，預設為 None，需要在使用時設定
    """
    def __init__(self, parameters=None):
        default_params = {
            'rebalance_frequency': 'M',  # 每月再平衡
            'stock_universe': None       # 股票池
        }
        # 更新預設參數
        if parameters:
            default_params.update(parameters)
            
        super().__init__(default_params)
        
    def generate_signals(self, data):
        """
        產生交易訊號
        
        策略邏輯：
        - 按指定頻率將資金平均分配到所有標的
        - 每次再平衡時調整持股比例
        
        注意：data 參數應是包含多個股票的 Panel 資料，
        格式為 MultiIndex DataFrame，其中第一層索引是日期，第二層是股票代號
        """
        if isinstance(data, pd.DataFrame) and len(data.columns) > 3:  # 判斷是否單一股票資料
            # 單一股票資料轉換為 Panel
            return self._generate_signals_for_single_stock(data)
        else:
            # 多股票 Panel 資料
            return self._generate_signals_for_portfolio(data)
    
    def _generate_signals_for_single_stock(self, data):
        """處理單一股票資料"""
        df = data.copy()
        df['position'] = 1  # 等權重策略下對單一股票永遠是持有
        df = add_signal_from_position(df)
        return df
    
    def _generate_signals_for_portfolio(self, data):
        """處理投資組合資料"""
        # 假設 data 是 MultiIndex DataFrame
        if not isinstance(data.index, pd.MultiIndex):
            raise ValueError("投資組合資料應為 MultiIndex DataFrame")
            
        df = data.copy()
        
        # 確保有股票池
        stock_universe = self.parameters['stock_universe']
        if not stock_universe:
            # 若未指定股票池，使用資料中的所有股票
            dates = df.index.get_level_values(0).unique()
            stocks = df.index.get_level_values(1).unique()
            stock_universe = list(stocks)
        
        # 將資料重新組織為更容易處理的形式
        # 股票 -> 日期 -> 價格
        stock_data = {}
        for stock in stock_universe:
            stock_data[stock] = df.xs(stock, level=1)
        
        # 取得所有交易日
        all_dates = sorted(df.index.get_level_values(0).unique())
        
        # 根據再平衡頻率確定再平衡日期
        freq = self.parameters['rebalance_frequency']
        # 將日期轉換為 Period 以按照頻率分組
        period_dates = pd.Series(all_dates).dt.to_period(freq)
        rebalance_dates = []
        
        # 找出每個週期的第一個交易日作為再平衡日
        for period in period_dates.unique():
            period_start = pd.Series(all_dates)[period_dates == period].min()
            rebalance_dates.append(period_start)
        
        # 初始化結果 DataFrame
        result = pd.DataFrame(index=pd.MultiIndex.from_product([all_dates, stock_universe], names=['date', 'symbol']))
        result['position'] = 0
        
        # 設定等權重部位
        n_stocks = len(stock_universe)
        weight = 1.0 / n_stocks if n_stocks > 0 else 0
        
        # 根據再平衡日期設定部位
        current_positions = {stock: 0 for stock in stock_universe}
        
        for i, date in enumerate(all_dates):
            # 如果是再平衡日，更新所有部位
            if date in rebalance_dates:
                for stock in stock_universe:
                    current_positions[stock] = weight
            
            # 將當前部位寫入結果
            for stock in stock_universe:
                idx = (date, stock)
                if idx in result.index:
                    result.loc[idx, 'position'] = current_positions[stock]
        
        # 為每個股票計算 signal
        result['signal'] = (
            result.groupby(level=1)['position']
            .diff()
            .fillna(0)
        )
        return result
