"""
實時交易主控模組
負責協調策略、資料提供者和券商，執行實時交易
"""
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import time
import threading
import logging
import os
import sys
import queue

# 將父目錄加入路徑以便引用其他模組
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

class LiveTrader:
    """
    實時交易主控
    負責協調策略、資料提供者和券商，執行實時交易
    """
    def __init__(self, strategy, broker, data_provider, symbols, risk_manager=None):
        """
        初始化實時交易主控
        :param strategy: 交易策略
        :param broker: 券商介面
        :param data_provider: 資料提供者
        :param symbols: 交易標的列表
        :param risk_manager: 風險管理器
        """
        self.strategy = strategy
        self.broker = broker
        self.data_provider = data_provider
        self.symbols = symbols if isinstance(symbols, list) else [symbols]
        self.risk_manager = risk_manager
        
        self.is_running = False
        self.thread = None
        self.event_queue = queue.Queue()
        
        # 設定日誌
        self.logger = self._setup_logger()
    
    def _setup_logger(self):
        """
        設定日誌
        """
        logger = logging.getLogger('LiveTrader')
        logger.setLevel(logging.INFO)
        
        # 如果已經有處理器，不重複添加
        if not logger.handlers:
            # 檔案處理器
            file_handler = logging.FileHandler('live_trading.log')
            file_handler.setLevel(logging.INFO)
            
            # 控制台處理器
            console_handler = logging.StreamHandler()
            console_handler.setLevel(logging.INFO)
            
            # 設定格式
            formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
            file_handler.setFormatter(formatter)
            console_handler.setFormatter(formatter)
            
            # 加入處理器
            logger.addHandler(file_handler)
            logger.addHandler(console_handler)
            
        return logger
        
    def start(self):
        """
        啟動交易
        """
        if self.is_running:
            self.logger.warning("交易系統已在運行中")
            return False
            
        # 建立連接
        self._connect()
        
        self.is_running = True
        self.thread = threading.Thread(target=self._trading_loop)
        self.thread.daemon = True
        self.thread.start()
        
        self.logger.info("交易系統已啟動")
        return True
        
    def stop(self):
        """
        停止交易
        """
        if not self.is_running:
            self.logger.warning("交易系統未運行")
            return False
            
        self.is_running = False
        if self.thread:
            self.thread.join(timeout=5.0)
            
        # 斷開連接
        self._disconnect()
        
        self.logger.info("交易系統已停止")
        return True
        
    def _connect(self):
        """
        建立所有必要連接
        """
        # 連接券商 API
        if hasattr(self.broker, 'connect'):
            self.logger.info("連接券商...")
            if self.broker.connect():
                self.logger.info("券商連接成功")
            else:
                self.logger.error("券商連接失敗")
                raise ConnectionError("無法連接券商")
                
        # 連接資料提供者
        if hasattr(self.data_provider, 'connect'):
            self.logger.info("連接資料提供者...")
            if hasattr(self.data_provider, 'connect') and callable(self.data_provider.connect):
                if self.data_provider.connect():
                    self.logger.info("資料提供者連接成功")
                else:
                    self.logger.error("資料提供者連接失敗")
                    raise ConnectionError("無法連接資料提供者")
    
    def _disconnect(self):
        """
        斷開所有連接
        """
        # 斷開券商 API
        if hasattr(self.broker, 'disconnect'):
            self.logger.info("斷開券商連接...")
            self.broker.disconnect()
            
        # 斷開資料提供者
        if hasattr(self.data_provider, 'disconnect'):
            self.logger.info("斷開資料提供者連接...")
            if hasattr(self.data_provider, 'disconnect') and callable(self.data_provider.disconnect):
                self.data_provider.disconnect()
    
    def _trading_loop(self):
        """
        交易主循環
        """
        while self.is_running:
            try:
                self._process_events()
                self._check_market_status()
                self._update_market_data()
                self._generate_signals()
                self._execute_trades()
                
                # 每隔一段時間檢查一次
                time.sleep(60)  # 1分鐘更新一次
                
            except Exception as e:
                self.logger.error(f"交易循環發生錯誤: {str(e)}")
                time.sleep(60)  # 發生錯誤時等待一分鐘後重試
    
    def _process_events(self):
        """
        處理事件佇列中的事件
        """
        try:
            while not self.event_queue.empty():
                event = self.event_queue.get(block=False)
                self._handle_event(event)
                self.event_queue.task_done()
        except queue.Empty:
            pass
    
    def _handle_event(self, event):
        """
        處理單一事件
        """
        event_type = event.get('type')
        data = event.get('data')
        
        if event_type == 'order_update':
            self.logger.info(f"訂單更新: {data}")
        elif event_type == 'market_update':
            self.logger.debug(f"市場更新: {data}")
        elif event_type == 'error':
            self.logger.error(f"錯誤: {data}")
    
    def _check_market_status(self):
        """
        檢查市場狀態 (是否開市)
        """
        now = datetime.now()
        # 台灣股市交易時間 9:00 - 13:30
        market_open = datetime.now().replace(hour=9, minute=0, second=0, microsecond=0)
        market_close = datetime.now().replace(hour=13, minute=30, second=0, microsecond=0)
        
        # 檢查是否是交易日 (週一至週五)
        is_weekday = now.weekday() < 5
        
        # 檢查是否在交易時間內
        is_trading_hours = market_open <= now <= market_close
        
        # 更新狀態
        self.is_market_open = is_weekday and is_trading_hours
        
        # 日誌記錄
        if is_weekday and market_open <= now <= market_open + timedelta(minutes=5):
            self.logger.info("市場開盤")
        elif is_weekday and market_close <= now <= market_close + timedelta(minutes=5):
            self.logger.info("市場收盤")
    
    def _update_market_data(self):
        """
        更新市場資料
        """
        # 如果市場已收盤，不更新資料
        if not self.is_running:
            return
            
        try:
            # 針對每個交易標的更新資料
            for symbol in self.symbols:
                # 取得起始日期 (回溯 30 天，確保有足夠資料計算指標)
                end_date = datetime.now()
                start_date = end_date - timedelta(days=30)
                
                # 取得歷史資料
                data = self.data_provider.get_historical_data(
                    symbol, 
                    start_date.strftime('%Y-%m-%d'),
                    end_date.strftime('%Y-%m-%d')
                )
                
                # 儲存到實例變數供後續使用
                if symbol not in getattr(self, 'market_data', {}):
                    self.market_data = {}
                self.market_data[symbol] = data
                
            self.logger.debug(f"已更新市場資料，共 {len(self.symbols)} 個交易標的")
            
        except Exception as e:
            self.logger.error(f"更新市場資料時發生錯誤: {str(e)}")
    
    def _generate_signals(self):
        """
        產生交易訊號
        """
        if not hasattr(self, 'market_data') or not self.market_data:
            self.logger.warning("無市場資料可產生訊號")
            return
            
        self.signals = {}
        
        try:
            # 針對每個交易標的產生訊號
            for symbol, data in self.market_data.items():
                if data is not None and not data.empty:
                    # 使用策略產生訊號
                    signals_df = self.strategy.generate_signals(data)
                    
                    # 取得最新訊號
                    latest_signal = signals_df.iloc[-1]
                    
                    # 儲存訊號
                    self.signals[symbol] = {
                        'position': latest_signal.get('position', 0),
                        'price': latest_signal.get('Close', 0),
                        'timestamp': datetime.now()
                    }
            
            self.logger.debug(f"已產生交易訊號，共 {len(self.signals)} 個標的")
            
        except Exception as e:
            self.logger.error(f"產生訊號時發生錯誤: {str(e)}")
    
    def _execute_trades(self):
        """
        執行交易
        """
        if not hasattr(self, 'signals') or not self.signals:
            return
            
        try:
            for symbol, signal in self.signals.items():
                # 取得現有部位
                current_position = self.broker.get_position(symbol)
                target_position = signal['position']
                
                # 如果訊號沒有變化，跳過
                if target_position == 0 and current_position == 0:
                    continue
                    
                # 計算交易量 (假設 position=1 表示使用全部資金，可依風險管理調整)
                price = signal['price']
                cash = self.broker.get_balance()
                
                # 套用風險管理規則
                if self.risk_manager:
                    max_position_size = self.risk_manager.get_position_size(symbol, price)
                else:
                    max_position_size = int(cash * 0.2 / price)  # 預設使用 20% 資金
                
                # 執行買入
                if target_position > 0 and current_position == 0:
                    quantity = max_position_size
                    if quantity > 0:
                        self.logger.info(f"買入訊號: {symbol} 數量: {quantity} 價格: {price}")
                        if self.broker.place_order(datetime.now(), price, quantity, 'buy', symbol):
                            self.logger.info(f"買入委託成功: {symbol}")
                        else:
                            self.logger.error(f"買入委託失敗: {symbol}")
                
                # 執行賣出
                elif target_position <= 0 and current_position > 0:
                    quantity = current_position
                    self.logger.info(f"賣出訊號: {symbol} 數量: {quantity} 價格: {price}")
                    if self.broker.place_order(datetime.now(), price, quantity, 'sell', symbol):
                        self.logger.info(f"賣出委託成功: {symbol}")
                    else:
                        self.logger.error(f"賣出委託失敗: {symbol}")
                        
        except Exception as e:
            self.logger.error(f"執行交易時發生錯誤: {str(e)}")
            
    def get_status(self):
        """
        取得交易系統狀態
        """
        status = {
            'is_running': self.is_running,
            'is_market_open': getattr(self, 'is_market_open', False),
            'symbols': self.symbols,
            'positions': {},
            'signals': getattr(self, 'signals', {})
        }
        
        # 取得持倉
        try:
            for symbol in self.symbols:
                position = self.broker.get_position(symbol)
                status['positions'][symbol] = position
        except:
            pass
            
        return status
