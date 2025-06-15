from dotenv import load_dotenv
import os
from pathlib import Path
import time
import shioaji as sj

# 明確指定 .env 檔案路徑，確保能正確載入
dotenv_path = Path(__file__).parent.parent / ".env"
print("Loading .env from:", dotenv_path)
load_dotenv(dotenv_path, override=True)


def main():
    print('version:', sj.__version__)

    # 從環境變數取得帳號密碼，避免硬寫在程式碼
    api_key = os.getenv("SHIOAJI_APIKEY")
    api_secret = os.getenv("SHIOAJI_SECRET")
    ca_path = os.getenv("SHIOAJI_CA_PATH")
    ca_password = os.getenv("SHIOAJI_CA_PASSWORD")
    ca_person_id = os.getenv("SHIOAJI_CA_PERSON_ID")

    if not api_key or not api_secret:
        print("請先設定 SHIOAJI_APIKEY 及 SHIOAJI_SECRET 環境變數！")
        return

    print("=== 初始化 Shioaji API (模擬模式) ===")
    api = sj.Shioaji(simulation=True)

    print("=== 登入帳號 ===")
    accounts = api.login(
        api_key,
        api_secret,
        contracts_cb=lambda security_type: print(f"{security_type} contracts ready")
    )
    print("登入回應：", accounts)

    # 憑證的密碼是PID
    api.activate_ca(
        ca_path=ca_path,
        ca_passwd=ca_person_id
    )

    api.quote.subscribe(api.Contracts.Stocks["2330"], quote_type="tick")

    print("=== 查詢帳戶資訊 ===")
    print("證券帳戶：", api.stock_account)

    print("=== 證券下單測試 (台積電2330) ===")
    stock_order = api.Order(
        price=999,
        quantity=1,
        action=sj.constant.Action.Buy,
        price_type=sj.constant.StockPriceType.LMT,
        order_type=sj.constant.OrderType.ROD,
        account=api.stock_account
    )
    stock_trade = api.place_order(
        contract=api.Contracts.Stocks["2330"],
        order=stock_order
    )
    print("證券下單回應：", stock_trade)
    time.sleep(1)  # 下單需間隔 1 秒

    print("=== 測試結束，登出 ===")
    api.logout()


if __name__ == "__main__":
    main()
