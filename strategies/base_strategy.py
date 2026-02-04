"""
策略基礎類別：所有交易策略都需繼承此類別
"""
from abc import ABC, abstractmethod
import pandas as pd

class BaseStrategy(ABC):
    """
    交易策略基礎類別，所有策略都需繼承此類別
    """
    def __init__(self, parameters=None):
        """
        初始化策略
        :param parameters: 策略參數字典
        """
        self.parameters = parameters or {}
        self.name = self.__class__.__name__
        
    @abstractmethod
    def generate_signals(self, data):
        """
        產生交易訊號
        :param data: 市場資料 DataFrame
        :return: 加入交易訊號的 DataFrame (position欄位: 1=做多, -1=做空, 0=空手)
        """
        pass
    
    def set_parameters(self, parameters):
        """
        設定策略參數
        :param parameters: 策略參數字典
        """
        self.parameters.update(parameters)
        return self
        
    def get_parameters(self):
        """
        取得目前的策略參數
        :return: 參數字典
        """
        return self.parameters
        
    def optimize_parameters(self, data, param_grid, metric='total_return', test_size=0.3):
        """
        最佳化策略參數
        :param data: 市場資料 DataFrame
        :param param_grid: 參數網格，例如 {'sma_short': [5, 10, 15], 'sma_long': [20, 30, 40]}
        :param metric: 優化指標，例如 'total_return', 'sharpe_ratio'
        :param test_size: 測試集比例
        :return: 最佳參數組合
        """
        import itertools
        from backtest.backtest_engine import BacktestEngine
        from broker.paper_broker import PaperBroker
        
        # 切分訓練集和測試集
        split_idx = int(len(data) * (1 - test_size))
        train_data = data.iloc[:split_idx]
        
        # 產生所有參數組合
        param_names = param_grid.keys()
        param_values = param_grid.values()
        param_combinations = list(itertools.product(*param_values))
        
        best_score = -float('inf')
        best_params = None
        
        test_data = data.iloc[split_idx:]

        # 測試每個參數組合
        for params in param_combinations:
            # 設定當前參數
            current_params = dict(zip(param_names, params))
            self.set_parameters(current_params)
            
            # 產生訊號
            signals = self.generate_signals(train_data)
            
            # 執行回測
            broker = PaperBroker()
            engine = BacktestEngine(train_data, self, broker)
            engine.run()
            
            # 取得績效
            if metric == 'total_return':
                score = engine.result['total_return']
            elif metric == 'sharpe_ratio':
                score = engine.result.get('sharpe', 0)
            elif metric == 'max_drawdown':
                score = -engine.result['max_drawdown']  # 負號因為更小的回撤更好
                
            # 更新最佳參數
            if score > best_score:
                best_score = score
                best_params = current_params
                
        # 設定為最佳參數並回測測試集
        self.set_parameters(best_params)
        if len(test_data) > 0:
            broker = PaperBroker()
            engine = BacktestEngine(test_data, self, broker)
            engine.run()
            self.last_optimization = {
                'best_params': best_params,
                'train_score': best_score,
                'test_result': engine.result
            }
        else:
            self.last_optimization = {
                'best_params': best_params,
                'train_score': best_score,
                'test_result': None
            }
        
        return best_params
        
    def __str__(self):
        """
        字串表示法
        """
        return f"{self.name} - 參數: {self.parameters}"
