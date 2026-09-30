import os
import time
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import requests

# =========================
# AYARLAR
# =========================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

BASE = "https://fapi.binance.com"

TOP_N = 20
CHECK_SECONDS = 10
TOP_LIST_REFRESH_SECONDS = 60
LEVEL_REFRESH_SECONDS = 300

# %70 yaklaşma:
# İki çizgi arası %100 kabul edilir.
# Destekten dirence yolun %70'i tamamlanınca SHORT bölgesi,
# dirençten desteğe yolun %70'i tamamlanınca LONG bölgesi.
APPROACH_RATIO = 0.70

session = requests.Session()

top_symbols = []
levels = {}
last_prices = {}
alert_state = {}

# =========================
# RENDER HEALTH SERVER
# =========================
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"Binance 4H alarm bot is running")

    def log_message(self, format, *args):
        return


def run_health_server():
    port = int(os.getenv("PORT", "10000"))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    print(f"Health server port {port}", flush=True)
    server.serve_forever()


# =========================
# TELEGRAM
# =========================
def telegram(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram ayarlari eksik.", flush=True)
        return

    try:
        r = session.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            data={"chat_id": TELEGRAM_CHAT_ID, "text": message},
            timeout=15,
        )
        r.raise_for_status()
    except Exception as e:
        print("Telegram hata:", e, flush=True)


# =========================
# BINANCE VERILERI
# =========================
def tradable_usdt_perpetuals():
    r = session.get(f"{BASE}/fapi/v1/exchangeInfo", timeout=30)
    r.raise_for_status()
    data = r.json()

    return {
        x["symbol"]
        for x in data.get("symbols", [])
        if x.get("status") == "TRADING"
        and x.get("contractType") == "PERPETUAL"
        and x.get("quoteAsset") == "USDT"
    }


def get_top_20():
    valid = tradable_usdt_perpetuals()

    r = session.get(f"{BASE}/fapi/v1/ticker/24hr", timeout=30)
    r.raise_for_status()

    coins = []
    for x in r.json():
        try:
            symbol = x["symbol"]
            if symbol not in valid:
                continue

            change = float(x.get("priceChangePercent", 0) or 0)
            quote_volume = float(x.get("quoteVolume", 0) or 0)

            coins.append((symbol, change, quote_volume))
        except (KeyError, TypeError, ValueError):
            continue

    coins.sort(key=lambda item: item[1], reverse=True)
    return [x[0] for x in coins[:TOP_N]]


def get_previous_closed_4h(symbol):
    r = session.get(
        f"{BASE}/fapi/v1/klines",
        params={"symbol": symbol, "interval": "4h", "limit": 3},
        timeout=20,
    )
    r.raise_for_status()

    candles = r.json()
    now_ms = int(time.time() * 1000)

    closed = [c for c in candles if int(c[6]) < now_ms]
    if not closed:
        return None

    c = closed[-1]
    return {
        "high": float(c[2]),
        "low": float(c[3]),
        "open_time": int(c[0]),
    }


def get_all_prices():
    r = session.get(f"{BASE}/fapi/v1/ticker/price", timeout=30)
    r.raise_for_status()

    prices = {}
    for x in r.json():
        try:
            prices[x["symbol"]] = float(x["price"])
        except (KeyError, TypeError, ValueError):
            continue
    return prices


# =========================
# SEVIYELER
# =========================
def update_level(symbol):
    new = get_previous_closed_4h(symbol)
    if not new:
        return

    old = levels.get(symbol)
    levels[symbol] = new

    if not old or old["open_time"] != new["open_time"]:
        alert_state[symbol] = {"LONG": False, "SHORT": False}
        last_prices.pop(symbol, None)


def refresh_all_levels():
    for symbol in list(top_symbols):
        try:
            update_level(symbol)
        except Exception as e:
            print(symbol, "4H seviye hatasi:", e, flush=True)


def refresh_top_list():
    global top_symbols

    new_list = get_top_20()
    old_set = set(top_symbols)
    top_symbols = new_list

    for symbol in new_list:
        if symbol not in old_set or symbol not in levels:
            try:
                update_level(symbol)
            except Exception as e:
                print(symbol, "yeni coin seviye hatasi:", e, flush=True)

    print("Kontrol edilen Binance coinleri:", top_symbols, flush=True)


# =========================
# ALARM MANTIGI
# =========================
def check_prices():
    prices = get_all_prices()

    for symbol in list(top_symbols):
        info = levels.get(symbol)
        price = prices.get(symbol)

        if not info or price is None:
            continue

        high = info["high"]      # ÜST YEŞİL ÇİZGİ = direnç
        low = info["low"]        # ALT YEŞİL ÇİZGİ = destek

        if high <= low:
            continue

        previous_price = last_prices.get(symbol)
        last_prices[symbol] = price

        state = alert_state.setdefault(symbol, {"LONG": False, "SHORT": False})

        # SADECE İKİ ÇİZGİNİN ARASINDA ÇALIŞIR.
        if not (low < price < high):
            state["LONG"] = False
            state["SHORT"] = False
            continue

        full_range = high - low

        # Alt çizgiye yaklaşım:
        # dirençten desteğe doğru yolun %70'i tamamlanmışsa
        # fiyat aralığın alt %30'undadır.
        long_trigger = low + full_range * (1.0 - APPROACH_RATIO)

        # Üst çizgiye yaklaşım:
        # destekten dirence doğru yolun %70'i tamamlanmışsa
        # fiyat aralığın üst %30'undadır.
        short_trigger = low + full_range * APPROACH_RATIO

        # Yön kontrolü:
        moving_down = previous_price is not None and price < previous_price
        moving_up = previous_price is not None and price > previous_price

        # LONG: iki çizgi arasında, yukarıdan aşağı destek çizgisine yaklaşırken
        in_long_zone = low < price <= long_trigger
        if in_long_zone and moving_down:
            if not state["LONG"]:
                progress = (high - price) / full_range * 100
                telegram(
                    f"🟢 LONG YAKLAŞIM\n"
                    f"{symbol}\n"
                    f"Fiyat: {price}\n"
                    f"4H Destek: {low}\n"
                    f"4H Direnç: {high}\n"
                    f"Desteğe yaklaşım: %{progress:.1f}\n"
                    f"İki çizginin ARASINDAN desteğe yaklaşıyor."
                )
                state["LONG"] = True
        elif not in_long_zone:
            state["LONG"] = False

        # SHORT: iki çizgi arasında, aşağıdan yukarı direnç çizgisine yaklaşırken
        in_short_zone = short_trigger <= price < high
        if in_short_zone and moving_up:
            if not state["SHORT"]:
                progress = (price - low) / full_range * 100
                telegram(
                    f"🔴 SHORT YAKLAŞIM\n"
                    f"{symbol}\n"
                    f"Fiyat: {price}\n"
                    f"4H Destek: {low}\n"
                    f"4H Direnç: {high}\n"
                    f"Dirence yaklaşım: %{progress:.1f}\n"
                    f"İki çizginin ARASINDAN dirence yaklaşıyor."
                )
                state["SHORT"] = True
        elif not in_short_zone:
            state["SHORT"] = False


# =========================
# ANA DONGU
# =========================
def main():
    threading.Thread(target=run_health_server, daemon=True).start()

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram ayarlari eksik: TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID", flush=True)

    # İlk bağlantı
    while True:
        try:
            refresh_top_list()
            refresh_all_levels()
            break
        except Exception as e:
            print("Binance baslangic hatasi:", e, flush=True)
            time.sleep(30)

    telegram(
        "🟡 Binance 4H alarm botu V3 başladı.\n"
        "İki çizginin ARASINDA çalışır.\n"
        "%70 yaklaşımda LONG/SHORT bildirimi verir."
    )

    last_top_refresh = time.time()
    last_level_refresh = time.time()

    while True:
        try:
            check_prices()

            now = time.time()

            if now - last_top_refresh >= TOP_LIST_REFRESH_SECONDS:
                refresh_top_list()
                last_top_refresh = now

            if now - last_level_refresh >= LEVEL_REFRESH_SECONDS:
                refresh_all_levels()
                last_level_refresh = now

            time.sleep(CHECK_SECONDS)

        except Exception as e:
            print("Ana dongu hatasi:", e, flush=True)
            time.sleep(20)


if __name__ == "__main__":
    main()
