import os
import time
import threading
from datetime import datetime

import requests
from flask import Flask

BINANCE = "https://fapi.binance.com"
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

TOP_N = int(os.environ.get("TOP_N", "20"))
CHECK_SECONDS = int(os.environ.get("CHECK_SECONDS", "30"))
APPROACH_PCT = float(os.environ.get("APPROACH_PCT", "0.20"))  # %0.20 yaklaşınca
TOP_REFRESH_SECONDS = int(os.environ.get("TOP_REFRESH_SECONDS", "300"))

app = Flask(__name__)

@app.get("/")
def health():
    return "Binance 4H alarm bot is running", 200

def health_server():
    port = int(os.environ.get("PORT", "10000"))
    app.run(host="0.0.0.0", port=port, use_reloader=False)

def telegram(text):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram ayarlari eksik:", text, flush=True)
        return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            data={"chat_id": TELEGRAM_CHAT_ID, "text": text},
            timeout=15,
        )
        if not r.ok:
            print("Telegram hata:", r.status_code, r.text[:300], flush=True)
    except Exception as e:
        print("Telegram exception:", repr(e), flush=True)

def get_top20():
    r = requests.get(f"{BINANCE}/fapi/v1/ticker/24hr", timeout=20)
    r.raise_for_status()
    rows = []
    for x in r.json():
        symbol = x.get("symbol", "")
        if not symbol.endswith("USDT"):
            continue
        try:
            last = float(x["lastPrice"])
            pct = float(x["priceChangePercent"])
            quote_volume = float(x["quoteVolume"])
        except (KeyError, ValueError, TypeError):
            continue
        if last <= 0 or quote_volume <= 0:
            continue
        rows.append((pct, symbol))
    rows.sort(reverse=True)
    return [s for _, s in rows[:TOP_N]]

def previous_closed_4h(symbol):
    r = requests.get(
        f"{BINANCE}/fapi/v1/klines",
        params={"symbol": symbol, "interval": "4h", "limit": 3},
        timeout=15,
    )
    r.raise_for_status()
    candles = r.json()
    if len(candles) < 2:
        return None
    c = candles[-2]  # kapanmis son 4H mum
    return float(c[2]), float(c[3])  # high, low

def prices(symbols):
    r = requests.get(f"{BINANCE}/fapi/v1/ticker/price", timeout=20)
    r.raise_for_status()
    wanted = set(symbols)
    return {
        x["symbol"]: float(x["price"])
        for x in r.json()
        if x.get("symbol") in wanted
    }

def run_bot():
    top = []
    levels = {}
    last_top_refresh = 0
    alerted = {}  # (symbol, side, level) -> True

    telegram("🟡 Binance 4H alarm botu basladi.")

    while True:
        try:
            now = time.time()

            if not top or now - last_top_refresh >= TOP_REFRESH_SECONDS:
                new_top = get_top20()
                if new_top != top:
                    top = new_top
                    print("Kontrol edilen coinler:", top, flush=True)
                last_top_refresh = now

                new_levels = {}
                for s in top:
                    try:
                        lv = previous_closed_4h(s)
                        if lv:
                            new_levels[s] = lv
                    except Exception as e:
                        print(s, "4H veri hatasi:", repr(e), flush=True)

                # Seviye degistiyse o seviyenin alarm hakki yeniden acilir.
                for s, lv in new_levels.items():
                    old = levels.get(s)
                    if old != lv:
                        for k in list(alerted):
                            if k[0] == s:
                                alerted.pop(k, None)
                levels = new_levels

            pmap = prices(top)

            for s in top:
                if s not in levels or s not in pmap:
                    continue

                high, low = levels[s]
                p = pmap[s]

                # Fiyat iki seviye arasindayken yaklasma:
                # dirence asagidan, destege yukaridan.
                if low <= p <= high:
                    dist_res = (high - p) / high * 100
                    dist_sup = (p - low) / low * 100

                    rkey = (s, "R", high)
                    skey = (s, "S", low)

                    if 0 <= dist_res <= APPROACH_PCT and not alerted.get(rkey):
                        telegram(
                            f"🔴 BINANCE 4H DIRENC YAKLASIYOR\n"
                            f"{s}\nFiyat: {p:g}\nDirenc: {high:g}\n"
                            f"Mesafe: %{dist_res:.3f}"
                        )
                        alerted[rkey] = True

                    if 0 <= dist_sup <= APPROACH_PCT and not alerted.get(skey):
                        telegram(
                            f"🟢 BINANCE 4H DESTEK YAKLASIYOR\n"
                            f"{s}\nFiyat: {p:g}\nDestek: {low:g}\n"
                            f"Mesafe: %{dist_sup:.3f}"
                        )
                        alerted[skey] = True

            time.sleep(CHECK_SECONDS)

        except Exception as e:
            print(datetime.now().isoformat(), "Ana dongu hatasi:", repr(e), flush=True)
            time.sleep(15)

if __name__ == "__main__":
    threading.Thread(target=health_server, daemon=True).start()
    run_bot()
