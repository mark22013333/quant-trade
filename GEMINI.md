# Gemini Project Context: quant-trade

## Project Overview

This project is a comprehensive and modular quantitative trading system designed primarily for the Taiwan stock market. It covers the entire workflow from data acquisition, feature engineering, strategy development, backtesting, to live trading. The architecture emphasizes modularity and extensibility.

## Key Directories & Files

- **`config/`**: Contains global configuration files.
  - `settings.py`: System-wide settings for backtesting, risk management, etc.
  - `symbols.py`: Defines the trading universe (stocks, ETFs).
- **`data/`**: Handles all data-related tasks.
  - `market_data.py`: A manager for accessing market data.
  - `providers/`: Connectors to different data sources (e.g., `yfinance_provider.py`, `shioaji_provider.py`).
  - `processors/`: For data processing like feature engineering.
- **`strategies/`**: Houses the trading strategy logic.
  - `base_strategy.py`: Defines the base class for all strategies.
  - Subdirectories for different strategy types: `momentum/`, `mean_reversion/`, `portfolio/`.
- **`backtest/`**: Contains the backtesting engine (`backtest_engine.py`).
- **`broker/`**: Manages communication with brokers.
  - `broker_interface.py`: The base interface for brokers.
  - `paper_broker.py`: A simulated broker for backtesting and paper trading.
  - `shioaji_broker.py`: An interface for the Shioaji API (a Taiwanese broker).
- **`live_trading/`**: Modules for real-time trading execution.
  - `trader.py`: The main trading controller.
  - `risk_manager.py`: Manages risk parameters during live trading.
- **`reports/`**: The default output directory for generated analysis reports (HTML, CSV).
- **`tests/`**: Contains tests for different parts of the application.
- **`main.py`**: The main entry point for running backtests or live trading.
- **`swing_analysis.py`**: A key script to analyze the suitability of stocks for swing trading and generate reports.
- **`html_report_generator.py`**: A utility to create HTML reports, likely using Plotly.
- **`requirements.txt`**: A list of Python package dependencies.
- **`start.sh`**: A shell script likely used to set up the environment and run the main application.

## Primary Workflows & Commands

- **Installation**:
  ```bash
  pip install -r requirements.txt
  ```
- **Running Swing Trading Analysis**: This is a core feature that analyzes stocks and generates an interactive HTML report.
  ```bash
  python swing_analysis.py
  ```
- **Running Backtest or Live Trading**: The main entry point for executing strategies.
  ```bash
  python main.py
  ```
- **Running Tests**: (Assumed command, framework needs confirmation)
  ```bash
  # Possibly using pytest or unittest
  pytest
  ```

## Dependencies

- **Core**: `pandas`, `numpy`
- **Data**: `yfinance`, `shioaji`, `TA-Lib`
- **Visualization**: `matplotlib`, `plotly`, `seaborn`
- **Utilities**: `tqdm`, `python-dotenv`
