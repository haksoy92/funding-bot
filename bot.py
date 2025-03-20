
import requests
import pandas as pd
import tkinter as tk
from tkinter import ttk
import time
import threading
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

# GUI ve bot mantığı
class FundingRateBot:
    def __init__(self, root):
        self.root = root
        self.root.title("Binance Futures Funding Rate & ROC Bot")
        self.root.geometry("900x450")

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

        # Arama çubuğu frame'i
        search_frame = tk.Frame(self.root)
        search_frame.pack(pady=5)

        self.search_var = tk.StringVar()
        search_entry = tk.Entry(search_frame, textvariable=self.search_var, width=20)
        search_entry.pack(side=tk.LEFT, padx=5)

        search_button = tk.Button(search_frame, text="Ara", command=self.search_symbol)
        search_button.pack(side=tk.LEFT, padx=5)

        reset_button = tk.Button(search_frame, text="Sıfırla", command=self.reset_table)
        reset_button.pack(side=tk.LEFT, padx=5)

        # Tablo oluşturma
        self.tree = ttk.Treeview(self.root, columns=("Symbol", "Previous Funding (%)", "Current Funding (%)", "Funding Change (%)", "ROC (%)", "Advice"), show="headings")
        self.tree.pack(fill="both", expand=True)

        # Sütun başlıkları ve sıralama
        self.sort_directions = {col: True for col in ["Symbol", "Previous Funding (%)", "Current Funding (%)", "Funding Change (%)", "ROC (%)", "Advice"]}
        for col in self.tree["columns"]:
            self.tree.heading(col, text=col, command=lambda c=col: self.sort_column(c))
            self.tree.column(col, width=150, anchor="center")

        # Long ve Short renkleri
        self.tree.tag_configure("long", foreground="green")
        self.tree.tag_configure("short", foreground="red")

        # Veri depolama
        self.old_funding_rates = {}
        self.old_prices = {}
        self.open_positions = set()
        self.all_data = []

        # İlk verileri çekme
        initial_funding_rates = get_funding_rates()
        initial_prices = get_prices()
        if initial_funding_rates and initial_prices:
            self.old_funding_rates = initial_funding_rates.copy()
            self.old_prices = initial_prices.copy()

        # Arka planda güncelleme başlatma
        self.update_thread = threading.Thread(target=self.update_table)
        self.update_thread.daemon = True
        self.update_thread.start()

    def sort_column(self, col):
        data = [(self.tree.set(item, col), item) for item in self.tree.get_children('')]
        if col not in ["Symbol", "Advice"]:
            data.sort(key=lambda x: float(x[0]) if x[0] != "N/A" else float('-inf'), reverse=not self.sort_directions[col])
        else:
            data.sort(reverse=not self.sort_directions[col])
        for index, (val, item) in enumerate(data):
            self.tree.move(item, '', index)
        self.sort_directions[col] = not self.sort_directions[col]

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

    def search_symbol(self):
        search_term = self.search_var.get().upper()
        for item in self.tree.get_children():
            self.tree.delete(item)
        filtered_data = [row for row in self.all_data if search_term in row["Symbol"]]
        for row in filtered_data:
            tags = ("long",) if row["Advice"] == "Long" else ("short",) if row["Advice"] == "Short" else ()
            self.tree.insert("", "end", values=(
                row["Symbol"],
                f"{row['Previous Funding (%)']:.4f}" if row['Previous Funding (%)'] is not None else "N/A",
                f"{row['Current Funding (%)']:.4f}",
                f"{row['Funding Change (%)']:.4f}",
                f"{row['ROC (%)']:.4f}",
                row["Advice"]
            ), tags=tags)

    def reset_table(self):
        for item in self.tree.get_children():
            self.tree.delete(item)
        for row in self.all_data:
            tags = ("long",) if row["Advice"] == "Long" else ("short",) if row["Advice"] == "Short" else ()
            self.tree.insert("", "end", values=(
                row["Symbol"],
                f"{row['Previous Funding (%)']:.4f}" if row['Previous Funding (%)'] is not None else "N/A",
                f"{row['Current Funding (%)']:.4f}",
                f"{row['Funding Change (%)']:.4f}",
                f"{row['ROC (%)']:.4f}",
                row["Advice"]
            ), tags=tags)

    def update_table(self):
        while True:
            for item in self.tree.get_children():
                self.tree.delete(item)

            new_funding_rates = get_funding_rates()
            new_prices = get_prices()

            if new_funding_rates and new_prices:
                self.all_data = []
                for symbol in new_funding_rates.keys():
                    new_rate = new_funding_rates.get(symbol)
                    old_rate = self.old_funding_rates.get(symbol, None)
                    new_price = new_prices.get(symbol)
                    old_price = self.old_prices.get(symbol, None)

                    funding_change = (new_rate - old_rate) if old_rate is not None else 0.0
                    roc = ((new_price - old_price) / old_price * 100) if old_price is not None else 0.0
                    advice = self.decide_trade(funding_change, roc)

                    if advice == "Long" and symbol not in self.open_positions:
                        self.open_long_position(symbol, new_price)
                    elif advice == "Short" and symbol not in self.open_positions:
                        self.open_short_position(symbol, new_price)

                    row = {
                        "Symbol": symbol,
                        "Previous Funding (%)": old_rate,
                        "Current Funding (%)": new_rate,
                        "Funding Change (%)": funding_change,
                        "ROC (%)": roc,
                        "Advice": advice
                    }
                    self.all_data.append(row)

                for row in self.all_data:
                    tags = ("long",) if row["Advice"] == "Long" else ("short",) if row["Advice"] == "Short" else ()
                    self.tree.insert("", "end", values=(
                        row["Symbol"],
                        f"{row['Previous Funding (%)']:.4f}" if row['Previous Funding (%)'] is not None else "N/A",
                        f"{row['Current Funding (%)']:.4f}",
                        f"{row['Funding Change (%)']:.4f}",
                        f"{row['ROC (%)']:.4f}",
                        row["Advice"]
                    ), tags=tags)

                self.old_funding_rates = new_funding_rates.copy()
                self.old_prices = new_prices.copy()

            time.sleep(60)  # Her 60 saniyede bir güncelle

# Uygulamayı başlatma
if __name__ == "__main__":
    root = tk.Tk()
    app = FundingRateBot(root)
    root.mainloop()