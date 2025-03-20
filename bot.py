import requests
import pandas as pd
import time
from binance.client import Client
from binance.enums import SIDE_BUY, SIDE_SELL

# Binance Futures public endpoints
funding_url = "https://fapi.binance.com/fapi/v1/premiumIndex"
price_url = "https://fapi.binance.com/fapi/v1/ticker/price"

API_KEY = "8e6784e2504b1b730c119731788c395ca01ddd86fad078fb09d2f76fde95ca67"
API_SECRET = "32218a9b0d32f3d8b7c832037a7b6f1a9d31926f6a25267c9278b1892145a741"

# Funding rate verilerini çekme
def get_funding_rates():
    try:
        response = requests.get(funding_url, timeout=30)
        response.raise_for_status()
        data = response.json()
        funding_data = {item['symbol']: float(item['lastFundingRate']) * 100 for item in data}
        return funding_data
    except (requests.exceptions.RequestException, requests.exceptions.Timeout) as e:
        print(f"Funding rate çekme hatası: {e}")
        return None

# Fiyat verilerini çekme
def get_prices():
    try:
        response = requests.get(price_url, timeout=30)
        response.raise_for_status()
        data = response.json()
        price_data = {item['symbol']: float(item['price']) for item in data}
        return price_data
    except (requests.exceptions.RequestException, requests.exceptions.Timeout) as e:
        print(f"Fiyat çekme hatası: {e}")
        return None

# Trader Bot
class FundingRateBot:
    def __init__(self):
        # Binance Testnet istemcisi
        self.client = Client(API_KEY, API_SECRET, testnet=True)

        # Hedge Mode kontrolü ve ayarı
        try:
            position_mode = self.client.futures_get_position_mode()
            if not position_mode['dualSidePosition']:
                self.client.futures_change_position_mode(dualSidePosition=True)
                print("Pozisyon modu Hedge Mode'a çevrildi")
            else:
                print("Pozisyon modu zaten Hedge Mode")
        except Exception as e:
            print(f"Pozisyon modu ayarında hata: {e}")

        # Exchange bilgisi (hassasiyet ve maksimum miktar için)
        self.exchange_info = self.client.futures_exchange_info()
        self.symbol_precision = {s['symbol']: s['quantityPrecision'] for s in self.exchange_info['symbols']}
        self.price_precision = {s['symbol']: s['pricePrecision'] for s in self.exchange_info['symbols']}
        self.max_quantity = {s['symbol']: float(s['filters'][2]['maxQty']) for s in self.exchange_info['symbols']}

        # Veri depolama
        self.old_funding_rates = {}
        self.old_prices = {}
        self.open_positions = set()

        # İlk verileri çekme
        initial_funding_rates = get_funding_rates()
        initial_prices = get_prices()
        if initial_funding_rates and initial_prices:
            self.old_funding_rates = initial_funding_rates.copy()
            self.old_prices = initial_prices.copy()

        # Botu başlat
        self.run()

    def decide_trade(self, funding_change, roc):
        if funding_change < -0.005 and roc > 0.1:  # Long koşulu
            return "Long"
        elif funding_change > 0.005 and roc < -0.1:  # Short koşulu
            return "Short"
        return "Wait"

    def open_long_position(self, symbol, current_price):
        try:
            self.client.futures_change_leverage(symbol=symbol, leverage=10)
            account = self.client.futures_account()
            usdt_balance = float([asset for asset in account['assets'] if asset['asset'] == 'USDT'][0]['availableBalance'])
            trade_amount = usdt_balance * 0.01  # %1 bakiye
            quantity = (trade_amount * 10) / current_price  # 10x kaldıraç
            max_qty = self.max_quantity.get(symbol, 1000)
            quantity = min(int(round(quantity)), max_qty)
            price_prec = self.price_precision.get(symbol, 2)

            # Long pozisyon açma
            order = self.client.futures_create_order(
                symbol=symbol,
                side=SIDE_BUY,
                type="MARKET",
                positionSide="LONG",
                quantity=quantity
            )
            print(f"Long pozisyon açıldı: {symbol}, Miktar: {quantity}")

            # Stop-Loss ayarı (%1 aşağı)
            stop_price = round(current_price * 0.99, price_prec)
            self.client.futures_create_order(
                symbol=symbol,
                side=SIDE_SELL,
                type="STOP_MARKET",
                positionSide="LONG",
                stopPrice=stop_price,
                quantity=quantity
            )
            print(f"Stop-Loss ayarlandı: {stop_price}")

            # Take-Profit ayarı (%2 yukarı)
            profit_price = round(current_price * 1.02, price_prec)
            self.client.futures_create_order(
                symbol=symbol,
                side=SIDE_SELL,
                type="TAKE_PROFIT_MARKET",
                positionSide="LONG",
                stopPrice=profit_price,
                quantity=quantity
            )
            print(f"Take-Profit ayarlandı: {profit_price}")

            self.open_positions.add(symbol)
        except Exception as e:
            print(f"{symbol} için Long pozisyon açma hatası: {e}")

    def open_short_position(self, symbol, current_price):
        try:
            self.client.futures_change_leverage(symbol=symbol, leverage=10)
            account = self.client.futures_account()
            usdt_balance = float([asset for asset in account['assets'] if asset['asset'] == 'USDT'][0]['availableBalance'])
            trade_amount = usdt_balance * 0.01  # %1 bakiye
            quantity = (trade_amount * 10) / current_price  # 10x kaldıraç
            max_qty = self.max_quantity.get(symbol, 1000)
            quantity = min(int(round(quantity)), max_qty)
            price_prec = self.price_precision.get(symbol, 2)

            # Short pozisyon açma
            order = self.client.futures_create_order(
                symbol=symbol,
                side=SIDE_SELL,
                type="MARKET",
                positionSide="SHORT",
                quantity=quantity
            )
            print(f"Short pozisyon açıldı: {symbol}, Miktar: {quantity}")

            # Stop-Loss ayarı (%2 yukarı)
            stop_price = round(current_price * 1.02, price_prec)
            self.client.futures_create_order(
                symbol=symbol,
                side=SIDE_BUY,
                type="STOP_MARKET",
                positionSide="SHORT",
                stopPrice=stop_price,
                quantity=quantity
            )
            print(f"Stop-Loss ayarlandı: {stop_price}")

            # Take-Profit ayarı (%5 aşağı)
            profit_price = round(current_price * 0.95, price_prec)
            self.client.futures_create_order(
                symbol=symbol,
                side=SIDE_BUY,
                type="TAKE_PROFIT_MARKET",
                positionSide="SHORT",
                stopPrice=profit_price,
                quantity=quantity
            )
            print(f"Take-Profit ayarlandı: {profit_price}")

            self.open_positions.add(symbol)
        except Exception as e:
            print(f"{symbol} için Short pozisyon açma hatası: {e}")

    def run(self):
        print("Trader bot çalışıyor...")
        while True:
            new_funding_rates = get_funding_rates()
            new_prices = get_prices()

            if new_funding_rates and new_prices:
                for symbol in new_funding_rates.keys():
                    new_rate = new_funding_rates.get(symbol)
                    old_rate = self.old_funding_rates.get(symbol, None)
                    new_price = new_prices.get(symbol)
                    old_price = self.old_prices.get(symbol, None)

                    funding_change = (new_rate - old_rate) if old_rate is not None else 0.0
                    roc = ((new_price - old_price) / old_price * 100) if old_price is not None else 0.0
                    advice = self.decide_trade(funding_change, roc)

                    print(f"{symbol}: Funding Change = {funding_change:.4f}%, ROC = {roc:.4f}%, Tavsiye = {advice}")

                    if advice == "Long" and symbol not in self.open_positions:
                        self.open_long_position(symbol, new_price)
                    elif advice == "Short" and symbol not in self.open_positions:
                        self.open_short_position(symbol, new_price)

                self.old_funding_rates = new_funding_rates.copy()
                self.old_prices = new_prices.copy()

            time.sleep(60)  # Her 60 saniyede bir kontrol et

# Botu başlatma
if __name__ == "__main__":
    bot = FundingRateBot()