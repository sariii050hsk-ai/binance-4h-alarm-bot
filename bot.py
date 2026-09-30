import os, time, threading
from datetime import datetime
import requests
from flask import Flask

# Binance public SPOT market-data endpoint.
# No Binance account/API key and no trading/order capability.
BINANCE = "https://data-api.binance.vision"
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
TOP_N = int(os.environ.get("TOP_N", "20"))
CHECK_SECONDS = int(os.environ.get("CHECK_SECONDS", "30"))
APPROACH_PCT = float(os.environ.get("APPROACH_PCT", "0.20"))
TOP_REFRESH_SECONDS = int(os.environ.get("TOP_REFRESH_SECONDS", "300"))

app = Flask(__name__)

@app.get("/")
def health():
    return "Binance 4H alarm bot v2 running", 200

def health_server():
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "10000")), use_reloader=False)

def tg(msg):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram ayarlari eksik", flush=True); return
    try:
        r=requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                        data={"chat_id":TELEGRAM_CHAT_ID,"text":msg}, timeout=15)
        if not r.ok: print("Telegram hata:", r.status_code, r.text[:250], flush=True)
    except Exception as e: print("Telegram exception:", repr(e), flush=True)

def get_json(path, params=None):
    r=requests.get(BINANCE+path, params=params, timeout=20)
    if not r.ok:
        raise RuntimeError(f"Binance HTTP {r.status_code}: {r.text[:180]}")
    return r.json()

def top20():
    data=get_json("/api/v3/ticker/24hr")
    rows=[]
    for x in data:
        s=x.get("symbol","")
        if not s.endswith("USDT"): continue
        try:
            last=float(x["lastPrice"]); pct=float(x["priceChangePercent"]); qv=float(x["quoteVolume"])
        except Exception: continue
        if last>0 and qv>0: rows.append((pct,s))
    rows.sort(reverse=True)
    return [s for _,s in rows[:TOP_N]]

def closed_4h(symbol):
    c=get_json("/api/v3/klines", {"symbol":symbol,"interval":"4h","limit":3})
    if len(c)<2: return None
    x=c[-2]
    return float(x[2]), float(x[3])

def price_map(symbols):
    data=get_json("/api/v3/ticker/price")
    wanted=set(symbols)
    return {x["symbol"]:float(x["price"]) for x in data if x.get("symbol") in wanted}

def run():
    top=[]; levels={}; alerted={}; last_refresh=0
    tg("🟡 Binance 4H alarm botu V2 basladi.")
    while True:
        try:
            now=time.time()
            if not top or now-last_refresh>=TOP_REFRESH_SECONDS:
                nt=top20()
                if nt!=top:
                    top=nt
                    print("Kontrol edilen Binance coinleri:", top, flush=True)
                last_refresh=now
                nl={}
                for s in top:
                    try:
                        lv=closed_4h(s)
                        if lv: nl[s]=lv
                    except Exception as e:
                        print(s, "4H veri hatasi:", repr(e), flush=True)
                for s,lv in nl.items():
                    if levels.get(s)!=lv:
                        for k in list(alerted):
                            if k[0]==s: alerted.pop(k,None)
                levels=nl

            pm=price_map(top)
            for s in top:
                if s not in levels or s not in pm: continue
                high,low=levels[s]; p=pm[s]
                # Yalnizca iki seviye arasindayken:
                if low <= p <= high:
                    dr=(high-p)/high*100
                    ds=(p-low)/low*100
                    rk=(s,"R",high); sk=(s,"S",low)
                    if 0<=dr<=APPROACH_PCT and not alerted.get(rk):
                        tg(f"🔴 BINANCE 4H DIRENC YAKLASIYOR\n{s}\nFiyat: {p:g}\nDirenc: {high:g}\nMesafe: %{dr:.3f}")
                        alerted[rk]=True
                    if 0<=ds<=APPROACH_PCT and not alerted.get(sk):
                        tg(f"🟢 BINANCE 4H DESTEK YAKLASIYOR\n{s}\nFiyat: {p:g}\nDestek: {low:g}\nMesafe: %{ds:.3f}")
                        alerted[sk]=True
            time.sleep(CHECK_SECONDS)
        except Exception as e:
            print(datetime.now().isoformat(), "Ana dongu hatasi:", repr(e), flush=True)
            time.sleep(15)

if __name__=="__main__":
    threading.Thread(target=health_server, daemon=True).start()
    run()
