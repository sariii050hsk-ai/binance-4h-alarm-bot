import os
import time
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import requests

# =========================
# AYARLAR
# =========================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", os.getenv("TELEGRAM_SOHBET_ID", "")).strip()

# Binance Futures ana adresi. 451 olursa bot Binance Spot public market
# verisine otomatik geçer; alarm sistemi çalışmaya devam eder.
FUTURES_BASE = "https://fapi.binance.com"
SPOT_BASE = "https://data-api.binance.vision"

TOP_N = 20
CHECK_SECONDS = 10
TOP_LIST_REFRESH_SECONDS = 60
LEVEL_REFRESH_SECONDS = 300
APPROACH_RATIO = 0.70

session = requests.Session()
session.headers.update({"User-Agent": "Mozilla/5.0 4H-Alarm-Bot/1.0"})

top_symbols = []
levels = {}
last_prices = {}
alert_state = {}
market_mode = "futures"


def telegram_send(text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram ayarlari eksik.")
        return
    try:
        r = session.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            data={"chat_id": TELEGRAM_CHAT_ID, "text": text},
            timeout=15,
        )
        r.raise_for_status()
    except Exception as e:
        print("Telegram hatasi:", e)


def get_json(url, params=None):
    r = session.get(url, params=params, timeout=15)
    r.raise_for_status()
    return r.json()


def detect_market():
    global market_mode
    try:
        get_json(FUTURES_BASE + "/fapi/v1/time")
        market_mode = "futures"
        print("Binance Futures baglantisi aktif.")
    except Exception as e:
        market_mode = "spot"
        print("Binance Futures erisilemiyor, Spot public veriye gecildi:", e)
        # Spot public data endpoint'ini de test et
        get_json(SPOT_BASE + "/api/v3/time")


def fetch_top_symbols():
    if market_mode == "futures":
        data = get_json(FUTURES_BASE + "/fapi/v1/ticker/24hr")
    else:
        data = get_json(SPOT_BASE + "/api/v3/ticker/24hr")

    rows = []
    for x in data:
        symbol = x.get("symbol", "")
        if not symbol.endswith("USDT"):
            continue
        # Sadece normal USDT pariteleri
        if any(tag in symbol for tag in ("UPUSDT", "DOWNUSDT", "BULLUSDT", "BEARUSDT")):
            continue
        try:
            change = float(x.get("priceChangePercent", 0))
            quote_volume = float(x.get("quoteVolume", 0))
        except Exception:
            continue
        rows.append((symbol, change, quote_volume))

    # Kullanıcının istediği: 24 saatte en çok yükselen ilk 20.
    # Eşitlikte hacmi yüksek olan öne gelir.
    rows.sort(key=lambda z: (z[1], z[2]), reverse=True)
    return [z[0] for z in rows[:TOP_N]]


def fetch_price(symbol):
    if market_mode == "futures":
        x = get_json(FUTURES_BASE + "/fapi/v1/ticker/price", {"symbol": symbol})
    else:
        x = get_json(SPOT_BASE + "/api/v3/ticker/price", {"symbol": symbol})
    return float(x["price"])


def fetch_prev_4h_level(symbol):
    if market_mode == "futures":
        data = get_json(
            FUTURES_BASE + "/fapi/v1/klines",
            {"symbol": symbol, "interval": "4h", "limit": 2},
        )
    else:
        data = get_json(
            SPOT_BASE + "/api/v3/klines",
            {"symbol": symbol, "interval": "4h", "limit": 2},
        )
    if len(data) < 2:
        raise RuntimeError("4H mum verisi yetersiz")
    prev = data[-2]  # son kapanmis 4H mum
    resistance = float(prev[2])
    support = float(prev[3])
    return support, resistance


def refresh_levels(symbols):
    for symbol in symbols:
        try:
            support, resistance = fetch_prev_4h_level(symbol)
            if resistance > support:
                levels[symbol] = (support, resistance)
        except Exception as e:
            print(symbol, "seviye hatasi:", e)


def zone_for(price, support, resistance):
    """Sadece iki çizginin ARASINDA alarm üretir.
    Alt çizgiden üst çizgiye mesafe %100 kabul edilir.
    Fiyat destekten dirence doğru %70'i tamamladıysa direnç yaklaşımı,
    dirençten desteğe doğru %70'i tamamladıysa destek yaklaşımı.
    Yön, önceki fiyattan doğrulanır.
    """
    if not (support < price < resistance):
        return None
    width = resistance - support
    pos = (price - support) / width  # destek=0, direnc=1

    # Dirence doğru son %30'luk alan: destekten yolun %70'i tamamlanmış.
    if pos >= APPROACH_RATIO:
        return "RESISTANCE"
    # Desteğe doğru son %30'luk alan: dirençten yolun %70'i tamamlanmış.
    if pos <= (1.0 - APPROACH_RATIO):
        return "SUPPORT"
    return None


def check_symbol(symbol):
    if symbol not in levels:
        return
    support, resistance = levels[symbol]
    price = fetch_price(symbol)
    prev = last_prices.get(symbol)
    last_prices[symbol] = price

    z = zone_for(price, support, resistance)
    old = alert_state.get(symbol)

    if z is None:
        alert_state[symbol] = None
        return

    # İlk kontrolde yön bilinmiyorsa alarm atma.
    if prev is None:
        return

    moving_up = price > prev
    moving_down = price < prev

    # İki çizginin içinden dirence yaklaşırken
    if z == "RESISTANCE" and moving_up and old != "RESISTANCE":
        pct = (price - support) / (resistance - support) * 100
        telegram_send(
            f"BINANCE 4H ALARM\n{symbol}\n"
            f"Dirence yaklasiyor (iki cizginin icinden)\n"
            f"Yol: %{pct:.1f}\nFiyat: {price}\n"
            f"Destek: {support}\nDirenc: {resistance}"
        )
        alert_state[symbol] = "RESISTANCE"

    # İki çizginin içinden desteğe yaklaşırken
    elif z == "SUPPORT" and moving_down and old != "SUPPORT":
        pct = (resistance - price) / (resistance - support) * 100
        telegram_send(
            f"BINANCE 4H ALARM\n{symbol}\n"
            f"Destege yaklasiyor (iki cizginin icinden)\n"
            f"Yol: %{pct:.1f}\nFiyat: {price}\n"
            f"Destek: {support}\nDirenc: {resistance}"
        )
        alert_state[symbol] = "SUPPORT"


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"OK")
    def log_message(self, format, *args):
        return


def health_server():
    port = int(os.getenv("PORT", "10000"))
    print("Health server port", port)
    HTTPServer(("0.0.0.0", port), HealthHandler).serve_forever()


def main():
    threading.Thread(target=health_server, daemon=True).start()

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram ayarlari eksik: TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID")

    try:
        detect_market()
    except Exception as e:
        print("Binance baslangic hatasi:", e)
        telegram_send("Binance veri baglantisi kurulamadi. Bot tekrar deneyecek.")

    last_top_refresh = 0
    last_level_refresh = 0

    while True:
        now = time.time()
        try:
            if now - last_top_refresh >= TOP_LIST_REFRESH_SECONDS or not top_symbols:
                try:
                    new_symbols = fetch_top_symbols()
                except Exception as e:
                    print("Liste hatasi, pazar yeniden kontrol ediliyor:", e)
                    detect_market()
                    new_symbols = fetch_top_symbols()

                top_symbols[:] = new_symbols
                last_top_refresh = now
                print("Ilk 20:", ", ".join(top_symbols))

            if now - last_level_refresh >= LEVEL_REFRESH_SECONDS or not levels:
                refresh_levels(top_symbols)
                last_level_refresh = now

            for symbol in list(top_symbols):
                try:
                    check_symbol(symbol)
                except Exception as e:
                    print(symbol, "kontrol hatasi:", e)

        except Exception as e:
            print("Ana dongu hatasi:", e)

        time.sleep(CHECK_SECONDS)


if __name__ == "__main__":
    main()
