"""
永豐金證券 Shioaji API 券商介面實作
"""
import pandas as pd
from datetime import datetime
import time
import shioaji as sj
from pathlib import Path
import os
from dotenv import load_dotenv
from .broker_interface import BrokerInterface

class ShioajiBroker(BrokerInterface):
    """
    永豐金證券 Shioaji 券商介面
    實作與永豐金證券 API 的整合
    """
    def __init__(self, simulation=True, api=None):
        """
        初始化永豐金券商介面
        :param simulation: 是否為模擬環境
        :param api: 已存在的 Shioaji API 實例
        """
        self.simulation = simulation
        self.api = api
        self._connected = False
        self.positions = {}
        self.orders = {}
        self.trades = {}
    
    def connect(self):
        """
        連接永豐金 API
        使用環境變數中的帳密登入
        """
        # 載入環境變數
        env_path = Path().absolute() / ".env"
        load_dotenv(env_path)
        
        api_key = os.getenv("SHIOAJI_APIKEY")
        api_secret = os.getenv("SHIOAJI_SECRET")
        ca_path = os.getenv("SHIOAJI_CA_PATH")
        ca_person_id = os.getenv("SHIOAJI_CA_PERSON_ID")
        
        if not api_key or not api_secret:
            raise ValueError("請設定 SHIOAJI_APIKEY 及 SHIOAJI_SECRET 環境變數")
        
        try:
            # 如果還沒有 API 實例，建立一個
            if self.api is None:
                self.api = sj.Shioaji(simulation=self.simulation)
            
            # 登入
            accounts = self.api.login(
                api_key,
                api_secret,
                contracts_cb=lambda security_type: print(f"{security_type} 合約載入完成")
            )
            
            # 如果有設定憑證，啟用憑證
            if ca_path and ca_person_id:
                self.api.activate_ca(ca_path=ca_path, ca_passwd=ca_person_id)
                print("憑證啟用成功")
            
            self._connected = True
            print("永豐金 API 連接成功")
            
            # 設定預設帳戶
            self.stock_account = self.api.stock_account
            
            return True
            
        except Exception as e:
            print(f"連接永豐金 API 失敗: {str(e)}")
            self._connected = False
            return False
    
    def disconnect(self):
        """
        登出永豐金 API
        """
        if self.api and self._connected:
            try:
                self.api.logout()
                self._connected = False
                print("永豐金 API 已登出")
                return True
            except Exception as e:
                print(f"登出永豐金 API 失敗: {str(e)}")
                return False
        return True
    
    def _ensure_connected(self):
        """
        確保已連接 API
        """
        if not self._connected or self.api is None:
            raise ConnectionError("尚未連接永豐金 API，請先呼叫 connect() 方法")
    
    def place_order(self, date, price, quantity, side, symbol):
        """
        下單方法
        :param date: 下單日期時間
        :param price: 下單價格
        :param quantity: 下單股數
        :param side: 買進 'buy' 或賣出 'sell'
        :param symbol: 股票代號，例如 '2330' 或 '2330.TW'
        """
        self._ensure_connected()
        
        # 移除 symbol 尾端的 .TW
        if '.TW' in symbol:
            symbol = symbol.replace('.TW', '')
        
        try:
            # 取得合約資訊
            contract = self.api.Contracts.Stocks[symbol]
            
            # 建立委託單
            action = sj.constant.Action.Buy if side.lower() == 'buy' else sj.constant.Action.Sell
            price_type = sj.constant.StockPriceType.LMT  # 限價單
            order_type = sj.constant.OrderType.ROD  # 當日有效單
            
            order = self.api.Order(
                price=price,
                quantity=quantity,
                action=action,
                price_type=price_type,
                order_type=order_type,
                account=self.stock_account
            )
            
            # 下單
            trade = self.api.place_order(contract, order)
            
            # 儲存訂單資訊
            order_id = trade.order.id
            self.orders[order_id] = {
                'contract': contract,
                'order': order,
                'trade': trade,
                'status': 'placed',
                'time': datetime.now()
            }
            
            # 若為非模擬環境，等待 1 秒避免 API 限流
            if not self.simulation:
                time.sleep(1)
            
            return True
            
        except Exception as e:
            print(f"下單失敗 ({symbol} {side} {quantity} @ {price}): {str(e)}")
            return False
    
    def get_balance(self):
        """
        查詢現金餘額
        """
        self._ensure_connected()
        
        try:
            # 取得庫存資訊
            portfolio = self.api.list_positions(self.stock_account)
            cash = portfolio.get('available_balance', 0)
            return float(cash)
        except Exception as e:
            print(f"查詢現金餘額失敗: {str(e)}")
            return 0
    
    def get_position(self, symbol):
        """
        查詢持股
        :param symbol: 股票代號
        """
        self._ensure_connected()
        
        # 移除 symbol 尾端的 .TW
        if '.TW' in symbol:
            symbol = symbol.replace('.TW', '')
            
        try:
            # 取得庫存資訊
            positions = self.api.list_positions(self.stock_account)
            
            # 查找指定股票
            for position in positions.get('positions', []):
                if position.get('code') == symbol:
                    return position.get('quantity', 0)
                    
            return 0  # 若沒有持有該股票
            
        except Exception as e:
            print(f"查詢持股 {symbol} 失敗: {str(e)}")
            return 0
    
    def get_all_positions(self):
        """
        查詢所有持股
        """
        self._ensure_connected()
        
        try:
            # 取得庫存資訊
            positions = self.api.list_positions(self.stock_account)
            
            # 整理持股資訊
            result = {}
            for position in positions.get('positions', []):
                symbol = position.get('code', '')
                if symbol:
                    result[symbol] = {
                        'quantity': position.get('quantity', 0),
                        'price': position.get('price', 0),
                        'last_price': position.get('last_price', 0),
                        'pnl': position.get('pnl', 0),
                        'pnl_ratio': position.get('pnl_ratio', 0)
                    }
                    
            return result
            
        except Exception as e:
            print(f"查詢所有持股失敗: {str(e)}")
            return {}
    
    def get_order_status(self, order_id):
        """
        查詢訂單狀態
        :param order_id: 訂單編號
        """
        self._ensure_connected()
        
        if order_id in self.orders:
            order_info = self.orders[order_id]
            
            try:
                # 更新訂單狀態
                updated_status = self.api.get_orders(self.stock_account)[order_id].status
                order_info['status'] = updated_status.status
                return order_info
                
            except Exception as e:
                print(f"查詢訂單狀態失敗 (ID: {order_id}): {str(e)}")
                return order_info
        else:
            return None
    
    def get_transactions(self, start_date=None, end_date=None):
        """
        查詢成交紀錄
        :param start_date: 開始日期，預設為今日
        :param end_date: 結束日期，預設為今日
        """
        self._ensure_connected()
        
        if start_date is None:
            start_date = datetime.now().strftime('%Y-%m-%d')
        if end_date is None:
            end_date = datetime.now().strftime('%Y-%m-%d')
            
        try:
            # 查詢成交紀錄
            transactions = self.api.get_transactions(
                self.stock_account,
                start_date=start_date,
                end_date=end_date
            )
            
            # 整理成交資料
            result = []
            for trans in transactions:
                result.append({
                    'id': trans.id,
                    'symbol': trans.contract.code,
                    'action': 'buy' if trans.action == sj.constant.Action.Buy else 'sell',
                    'quantity': trans.quantity,
                    'price': trans.price,
                    'time': trans.time
                })
                
            return result
            
        except Exception as e:
            print(f"查詢成交紀錄失敗: {str(e)}")
            return []
    
    def cancel_order(self, order_id):
        """
        取消訂單
        :param order_id: 訂單編號
        """
        self._ensure_connected()
        
        if order_id in self.orders:
            try:
                # 取消訂單
                self.api.cancel_order(self.orders[order_id]['trade'])
                self.orders[order_id]['status'] = 'cancelled'
                return True
                
            except Exception as e:
                print(f"取消訂單失敗 (ID: {order_id}): {str(e)}")
                return False
        else:
            print(f"找不到訂單 (ID: {order_id})")
            return False
