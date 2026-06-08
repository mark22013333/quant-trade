from app.execution.models import ExecutionResult, OrderIntent, PreTradeDecision, normalize_symbol

__all__ = [
    "ExecutionResult",
    "OrderIntent",
    "PreTradeDecision",
    "TradingExecutionService",
    "normalize_symbol",
]


def __getattr__(name: str):
    if name == "TradingExecutionService":
        from app.execution.service import TradingExecutionService

        return TradingExecutionService
    raise AttributeError(name)
