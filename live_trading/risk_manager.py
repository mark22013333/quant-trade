"""
風險管理模組
負責控制交易規模、部位大小及風險限制
"""
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import os
import sys

class RiskManager:
    """
    風險管理模組
    實作各種風險控制方法，包含部位大小、交易量限制、虧損停損等
    """
    def __init__(self, config=None):
        """
        初始化風險管理器
        
        :param config: 風險管理設定，如果未提供則使用預設值
        """
        # 預設風險管理設定
        default_config = {
            'max_position_percent': 0.2,        # 單一部位最大資金比例
            'max_portfolio_percent': 0.8,       # 投資組合最大資金比例
            'max_drawdown_percent': 0.1,        # 最大可接受回撤
            'stop_loss_percent': 0.05,          # 單一部位停損比例
            'trailing_stop_percent': 0.03,      # 移動停損追蹤比例
            'min_trading_volume': 1000,         # 最小交易量
            'max_trading_volume_percent': 0.1,  # 最大交易量佔標的成交量比例
            'min_cash_reserve': 100000          # 最小現金保留
        }
        
        # 如果有提供設定，則覆寫預設值
        self.config = default_config
        if config:
            self.config.update(config)
            
        # 初始化風險監控資料
        self.positions = {}
        self.portfolio_value = 0
        self.cash = 0
        self.drawdowns = {}
        self.peak_values = {}
    
    def update_portfolio_status(self, positions, cash, market_data):
        """
        更新投資組合狀態
        
        :param positions: 目前持倉 {symbol: quantity, ...}
        :param cash: 目前現金
        :param market_data: 市場資料，用於計算持倉價值
        """
        self.cash = cash
        self.positions = positions
        
        # 計算投資組合總價值
        portfolio_value = cash
        for symbol, quantity in positions.items():
            if symbol in market_data and 'Close' in market_data[symbol]:
                current_price = market_data[symbol]['Close'].iloc[-1]
                portfolio_value += quantity * current_price
        
        self.portfolio_value = portfolio_value
        
        # 更新歷史高點，用於計算回撤
        if not hasattr(self, 'peak_portfolio_value') or portfolio_value > self.peak_portfolio_value:
            self.peak_portfolio_value = portfolio_value
            
        # 計算當前回撤
        self.current_drawdown = 1 - portfolio_value / self.peak_portfolio_value if self.peak_portfolio_value > 0 else 0
    
    def check_position_size(self, symbol, price, quantity):
        """
        檢查單一部位大小是否符合風險限制
        
        :param symbol: 交易標的
        :param price: 交易價格
        :param quantity: 交易數量
        :return: 是否符合限制，及調整後的數量
        """
        # 計算此交易會佔用的資金比例
        position_value = price * quantity
        max_position_value = self.portfolio_value * self.config['max_position_percent']
        
        # 檢查是否超過單一部位限制
        if position_value > max_position_value:
            # 調整交易數量
            adjusted_quantity = int(max_position_value / price)
            return False, adjusted_quantity
        
        return True, quantity
    
    def check_portfolio_exposure(self, new_position_value):
        """
        檢查整體投資組合曝險是否符合限制
        
        :param new_position_value: 新增部位價值
        :return: 是否符合限制
        """
        # 計算目前所有部位價值 (不含新部位)
        current_positions_value = self.portfolio_value - self.cash
        
        # 加入新部位後的總曝險比例
        total_exposure_percent = (current_positions_value + new_position_value) / self.portfolio_value
        
        # 檢查是否超過總曝險限制
        return total_exposure_percent <= self.config['max_portfolio_percent']
    
    def check_drawdown_limit(self):
        """
        檢查當前回撤是否超過限制
        
        :return: 是否符合限制
        """
        return self.current_drawdown <= self.config['max_drawdown_percent']
    
    def check_stop_loss(self, symbol, current_price, entry_price):
        """
        檢查是否觸發停損
        
        :param symbol: 交易標的
        :param current_price: 當前價格
        :param entry_price: 進場價格
        :return: 是否應該停損
        """
        # 長倉停損邏輯
        if entry_price > 0 and current_price < entry_price * (1 - self.config['stop_loss_percent']):
            return True
            
        return False
    
    def check_trailing_stop(self, symbol, current_price, highest_price):
        """
        檢查是否觸發移動停損
        
        :param symbol: 交易標的
        :param current_price: 當前價格
        :param highest_price: 持有期間最高價
        :return: 是否應該停損
        """
        # 移動停損邏輯
        if highest_price > 0 and current_price < highest_price * (1 - self.config['trailing_stop_percent']):
            return True
            
        return False
    
    def get_position_size(self, symbol, price, market_data=None):
        """
        計算適當的部位大小
        
        :param symbol: 交易標的
        :param price: 交易價格
        :param market_data: 市場資料，用於計算成交量限制等
        :return: 建議交易數量
        """
        # 確保至少有最小現金保留
        available_cash = max(0, self.cash - self.config['min_cash_reserve'])
        
        # 計算依照資金比例可買入的最大數量
        max_position_value = self.portfolio_value * self.config['max_position_percent']
        max_quantity_by_fund = int(min(available_cash, max_position_value) / price)
        
        # 如果有市場資料，計算依成交量限制
        max_quantity_by_volume = float('inf')
        if market_data and symbol in market_data and 'Volume' in market_data[symbol]:
            avg_volume = market_data[symbol]['Volume'].mean()
            max_quantity_by_volume = int(avg_volume * self.config['max_trading_volume_percent'])
        
        # 綜合各項限制，取最小值
        max_quantity = min(max_quantity_by_fund, max_quantity_by_volume)
        
        # 確保至少達到最小交易量
        if max_quantity < self.config['min_trading_volume']:
            return 0  # 若無法達到最小交易量，則不交易
            
        return max_quantity
    
    def should_reduce_exposure(self, market_stress_indicators=None):
        """
        根據市場壓力指標決定是否應減少曝險
        
        :param market_stress_indicators: 市場壓力指標，如 VIX、波動率等
        :return: 是否應減少曝險，及建議的曝險比例
        """
        # 預設值
        should_reduce = False
        exposure_factor = 1.0
        
        # 檢查回撤情況
        if self.current_drawdown > self.config['max_drawdown_percent'] * 0.8:
            should_reduce = True
            exposure_factor = 0.5  # 回撤接近限制，減半曝險
            
        # 如果提供市場壓力指標，進一步判斷
        if market_stress_indicators:
            # 例如檢查 VIX 指標
            if 'vix' in market_stress_indicators and market_stress_indicators['vix'] > 30:
                should_reduce = True
                exposure_factor = min(exposure_factor, 0.3)  # VIX 高，大幅降低曝險
                
            # 檢查波動率
            if 'volatility' in market_stress_indicators:
                vol = market_stress_indicators['volatility']
                if vol > 0.03:  # 3% 日波動率
                    should_reduce = True
                    exposure_factor = min(exposure_factor, 0.7)  # 波動率高，適度降低曝險
        
        return should_reduce, exposure_factor
    
    def generate_risk_report(self):
        """
        產生風險報告
        
        :return: 風險報告字典
        """
        report = {
            'portfolio_value': self.portfolio_value,
            'cash': self.cash,
            'cash_percent': self.cash / self.portfolio_value if self.portfolio_value > 0 else 0,
            'positions_count': len(self.positions),
            'current_drawdown': self.current_drawdown,
            'max_drawdown_limit': self.config['max_drawdown_percent'],
            'risk_status': 'Normal' if self.check_drawdown_limit() else 'Warning'
        }
        
        return report
