from abc import ABC, abstractmethod

class BrokerInterface(ABC):
    """
    券商下單介面抽象類別，所有券商都要繼承這個介面
    """
    @abstractmethod
    def place_order(self, date, price, quantity, side, symbol):
        """
        下單方法
        :param date: 下單日期
        :param price: 下單價格
        :param quantity: 下單股數
        :param side: 買進 'buy' 或賣出 'sell'
        :param symbol: 股票代號
        """
        pass

    @abstractmethod
    def get_balance(self):
        """
        查詢現金餘額
        """
        pass

    @abstractmethod
    def get_position(self, symbol):
        """
        查詢持股
        :param symbol: 股票代號
        """
        pass
