BINANCE 4H ALARM BOT V2
Bu surum Binance'in public Spot market-data endpointini kullanir.
Al-sat yapmaz, API key istemez.

Render:
Build Command: pip install -r requirements.txt
Start Command: python bot.py

Environment:
TELEGRAM_BOT_TOKEN
TELEGRAM_CHAT_ID

Mantik:
- Binance Spot USDT pariteleri
- 24 saatlik yuzde degisime gore ilk 20
- Son kapanmis 4H mum high/low
- Yalnizca high-low arasindan yaklasirken alarm
- Varsayilan yaklasma: %0.20
