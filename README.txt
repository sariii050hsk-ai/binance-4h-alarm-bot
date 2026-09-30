BINANCE 4H ALARM BOT

Render ayarlari:
Build Command:
pip install -r requirements.txt

Start Command:
python bot.py

Environment Variables:
TELEGRAM_BOT_TOKEN = Telegram bot tokeniniz
TELEGRAM_CHAT_ID = Telegram chat ID'niz

Opsiyonel:
TOP_N = 20
CHECK_SECONDS = 30
APPROACH_PCT = 0.20
TOP_REFRESH_SECONDS = 300

Mantik:
- Binance USD-M Futures USDT pariteleri
- 24 saatlik priceChangePercent'e gore ilk 20
- Son kapanmis 4 saatlik mumun high = direnc, low = destek
- Fiyat mum araligindayken dirence asagidan veya destege yukaridan %0.20 yaklasinca bir kez Telegram alarmi
- Ilk 20 liste 5 dakikada bir yenilenir
- Yeni 4H seviye geldiginde alarm hakki sifirlanir
