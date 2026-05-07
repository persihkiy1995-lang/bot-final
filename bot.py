import logging
import os
import threading
from typing import Dict
import numpy as np
import yfinance as yf
from flask import Flask
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

TOKEN = "8092971831:AAH32C8EV_Qhuyu9IglRR59HsIJPYVfTrGw"
PORT = int(os.environ.get("PORT", 8080))

FOREX_PAIRS = {
    "EUR/USD": "EURUSD=X", "GBP/USD": "GBPUSD=X", "USD/JPY": "USDJPY=X",
    "AUD/USD": "AUDUSD=X", "EUR/GBP": "EURGBP=X", "GBP/JPY": "GBPJPY=X",
    "EUR/JPY": "EURJPY=X", "USD/CHF": "USDCHF=X", "USD/CAD": "USDCAD=X",
    "NZD/USD": "NZDUSD=X",
}

OTC_PAIRS = {
    "EUR/USD OTC": "EURUSD=X", "GBP/USD OTC": "GBPUSD=X",
    "USD/JPY OTC": "USDJPY=X", "AUD/USD OTC": "AUDUSD=X",
    "BTC/USD OTC": "BTC-USD", "ETH/USD OTC": "ETH-USD",
    "XAU/USD OTC": "GC=F",
}

ALL_PAIRS = {**FOREX_PAIRS, **OTC_PAIRS}

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")
logger = logging.getLogger(__name__)

flask_app = Flask(__name__)

@flask_app.route('/')
def home():
    return "Bot is running!"


def calculate_rsi(prices, period=14):
    if len(prices) < period + 1:
        return 50.0
    deltas = np.diff(prices[-period-1:])
    gains = deltas[deltas > 0]
    losses = -deltas[deltas < 0]
    avg_gain = np.mean(gains) if len(gains) > 0 else 0
    avg_loss = np.mean(losses) if len(losses) > 0 else 1e-10
    if avg_loss == 0:
        return 100.0
    return 100 - (100 / (1 + avg_gain / avg_loss))


def ema(data, period):
    if len(data) < period:
        return np.array([np.mean(data)])
    alpha = 2 / (period + 1)
    result = np.zeros_like(data)
    result[0] = data[0]
    for i in range(1, len(data)):
        result[i] = alpha * data[i] + (1 - alpha) * result[i - 1]
    return result


def calculate_macd(prices):
    ema_fast = ema(prices, 12)
    ema_slow = ema(prices, 26)
    macd_line = ema_fast - ema_slow
    signal_line = ema(macd_line, 9)
    return macd_line[-1], signal_line[-1], macd_line[-1] - signal_line[-1]


def calculate_sma(prices, period):
    if len(prices) < period:
        return np.mean(prices)
    return np.mean(prices[-period:])


def fetch_data(symbol, is_otc=False):
    try:
        ticker = yf.Ticker(symbol)
        df = ticker.history(period="5d", interval="5m")
        if df.empty:
            return None
        closes = df["Close"].values
        current = closes[-1]
        if is_otc:
            current *= (1 + np.random.normal(0, 0.0005))
        return {"prices": closes, "current": current, "timestamp": df.index[-1]}
    except Exception as e:
        logger.error(f"Error: {e}")
        return None


def analyze_pair(data, is_otc=False):
    prices = data["prices"]
    current = data["current"]
    rsi = calculate_rsi(prices)
    macd_val, signal, histogram = calculate_macd(prices)
    sma50 = calculate_sma(prices, min(50, len(prices)))
    sma200 = calculate_sma(prices, min(200, len(prices)))

    bullish_score = 0
    bearish_score = 0

    if rsi < 30:
        bullish_score += 2
    elif rsi > 70:
        bearish_score += 2

    if histogram > 0:
        bullish_score += 1
    else:
        bearish_score += 1

    if sma50 > sma200:
        bullish_score += 1
    else:
        bearish_score += 1

    if current > sma50:
        bullish_score += 1
    else:
        bearish_score += 1

    total = max(bullish_score + bearish_score, 1)
    confidence = max(bullish_score, bearish_score) / total * 100

    if bullish_score > bearish_score:
        direction = "CALL"
    elif bearish_score > bullish_score:
        direction = "PUT"
    else:
        direction = "NO SIGNAL"

    return {
        "direction": direction,
        "confidence": confidence,
        "rsi": rsi,
        "current_price": current,
        "expiry": 1 if is_otc else 5,
    }


def format_signal(pair, data, analysis, is_otc):
    ts = data["timestamp"]
    time_str = ts.strftime("%H:%M:%S") if hasattr(ts, "strftime") else str(ts)
    pair_type = "OTC" if is_otc else "Forex"

    if "JPY" in pair:
        price_str = f"{analysis['current_price']:.3f}"
    else:
        price_str = f"{analysis['current_price']:.5f}"

    return (
        f"SIGNAL ALGORITHMX\n\n"
        f"Pair: {pair} [{pair_type}]\n"
        f"Price: {price_str}\n"
        f"Signal: {analysis['direction']}\n"
        f"Expiry: {analysis['expiry']} min\n"
        f"Confidence: {analysis['confidence']:.0f}%\n"
        f"RSI: {analysis['rsi']:.1f}\n"
        f"\n{time_str}"
    )


async def start(update, context):
    await update.message.reply_text(
        "AlgorithmX Bot\n\n"
        "/signal EUR/USD\n"
        "/otc EUR/USD\n"
        "/all - Top-5\n"
        "/allotc - Top-5 OTC\n"
        "/pairs"
    )


async def help_cmd(update, context):
    await update.message.reply_text("/signal /otc /all /allotc /pairs")


async def pairs_cmd(update, context):
    text = "FOREX:\n"
    for p in FOREX_PAIRS:
        text += f"- {p}\n"
    text += "\nOTC:\n"
    for p in OTC_PAIRS:
        text += f"- {p}\n"
    await update.message.reply_text(text)


async def signal_cmd(update, context):
    if not context.args:
        await update.message.reply_text("/signal EUR/USD")
        return
    await process(update, context, False)


async def otc_cmd(update, context):
    if not context.args:
        await update.message.reply_text("/otc EUR/USD")
        return
    await process(update, context, True)


async def process(update, context, is_otc):
    pair = " ".join(context.args).upper()
    if is_otc:
        pair += " OTC"
    if pair not in ALL_PAIRS:
        await update.message.reply_text("Not found")
        return
    msg = await update.message.reply_text("Analyzing...")
    data = fetch_data(ALL_PAIRS[pair], is_otc)
    if not data:
        await msg.edit_text("No data")
        return
    analysis = analyze_pair(data, is_otc)
    await msg.edit_text(format_signal(pair, data, analysis, is_otc))


async def all_signals(update, context):
    msg = await update.message.reply_text("Analyzing...")
    signals = []
    for p, s in FOREX_PAIRS.items():
        d = fetch_data(s)
        if d:
            signals.append((p, analyze_pair(d)))
    if not signals:
        await msg.edit_text("No data")
        return
    signals.sort(key=lambda x: x[1]["confidence"], reverse=True)
    text = "TOP-5 FOREX\n\n"
    for p, a in signals[:5]:
        text += f"{p}: {a['direction']} ({a['confidence']:.0f}%)\n"
    await msg.edit_text(text)


async def all_otc(update, context):
    msg = await update.message.reply_text("Analyzing...")
    signals = []
    for p, s in OTC_PAIRS.items():
        d = fetch_data(s, True)
        if d:
            signals.append((p, analyze_pair(d, True)))
    if not signals:
        await msg.edit_text("No data")
        return
    signals.sort(key=lambda x: x[1]["confidence"], reverse=True)
    text = "TOP-5 OTC\n\n"
    for p, a in signals[:5]:
        text += f"{p}: {a['direction']} ({a['confidence']:.0f}%)\n"
    await msg.edit_text(text)


def main():
    threading.Thread(target=lambda: flask_app.run(host='0.0.0.0', port=PORT), daemon=True).start()
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("pairs", pairs_cmd))
    app.add_handler(CommandHandler("signal", signal_cmd))
    app.add_handler(CommandHandler("otc", otc_cmd))
    app.add_handler(CommandHandler("all", all_signals))
    app.add_handler(CommandHandler("allotc", all_otc))
    print("Bot running...")
    app.run_polling()


if __name__ == "__main__":
    main()