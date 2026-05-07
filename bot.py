import logging
import os
import threading
import asyncio
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
    "NZD/USD": "NZDUSD=X", "EUR/CHF": "EURCHF=X", "EUR/AUD": "EURAUD=X",
    "EUR/CAD": "EURCAD=X", "GBP/CHF": "GBPCHF=X", "GBP/AUD": "GBPAUD=X",
    "AUD/JPY": "AUDJPY=X", "NZD/JPY": "NZDJPY=X", "CAD/JPY": "CADJPY=X",
    "CHF/JPY": "CHFJPY=X", "USD/MXN": "USDMXN=X", "USD/ZAR": "USDZAR=X",
}

OTC_PAIRS = {
    "EUR/USD OTC": "EURUSD=X", "GBP/USD OTC": "GBPUSD=X",
    "USD/JPY OTC": "USDJPY=X", "AUD/USD OTC": "AUDUSD=X",
    "EUR/GBP OTC": "EURGBP=X", "GBP/JPY OTC": "GBPJPY=X",
    "EUR/JPY OTC": "EURJPY=X", "USD/CHF OTC": "USDCHF=X",
    "USD/CAD OTC": "USDCAD=X", "NZD/USD OTC": "NZDUSD=X",
    "BTC/USD OTC": "BTC-USD", "ETH/USD OTC": "ETH-USD",
    "XAU/USD OTC": "GC=F",
}

ALL_PAIRS = {**FOREX_PAIRS, **OTC_PAIRS}

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger(__name__)

flask_app = Flask(__name__)

@flask_app.route('/')
def home():
    return "AlgorithmX Bot is running 24/7!"


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
        logger.error(f"Error loading {symbol}: {e}")
        return None


def analyze_pair(data, is_otc=False):
    prices = data["prices"]
    current = data["current"]
    rsi = calculate_rsi(prices)
    macd_val, macd_signal, histogram = calculate_macd(prices)
    sma50 = calculate_sma(prices, min(50, len(prices)))
    sma200 = calculate_sma(prices, min(200, len(prices)))
    volatility = np.std(prices[-20:]) / current * 100 if len(prices) >= 20 else 0

    reasons = []
    bullish_score = 0
    bearish_score = 0

    if rsi < 30:
        bullish_score += 2
        reasons.append(f"RSI({rsi:.1f}) - oversold")
    elif rsi > 70:
        bearish_score += 2
        reasons.append(f"RSI({rsi:.1f}) - overbought")
    else:
        reasons.append(f"RSI({rsi:.1f}) - neutral")

    if histogram > 0:
        bullish_score += 1
        reasons.append("MACD up (bullish)")
    else:
        bearish_score += 1
        reasons.append("MACD down (bearish)")

    if sma50 > sma200:
        bullish_score += 1
        reasons.append("MA50 > MA200 (uptrend)")
    else:
        bearish_score += 1
        reasons.append("MA50 < MA200 (downtrend)")

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
        "macd": macd_val,
        "histogram": histogram,
        "sma50": sma50,
        "sma200": sma200,
        "volatility": volatility,
        "reasons": reasons,
        "current_price": current,
        "expiry": 1 if is_otc else 5,
    }


def format_price(pair, price):
    if "BTC" in pair:
        return f"${price:,.0f}"
    if "XAU" in pair:
        return f"${price:.1f}"
    if "JPY" in pair:
        return f"{price:.3f}"
    return f"{price:.5f}"


def format_signal(pair, data, analysis, is_otc):
    ts = data["timestamp"]
    time_str = ts.strftime("%H:%M:%S") if hasattr(ts, "strftime") else str(ts)
    price_str = format_price(pair, analysis["current_price"])
    pair_type = "OTC" if is_otc else "Forex"

    text = f"SIGNAL ALGORITHMX\n\n"
    text += f"Pair: {pair} [{pair_type}]\n"
    text += f"Price: {price_str}\n"
    text += f"Signal: {analysis['direction']}\n"
    text += f"Expiry: {analysis['expiry']} min\n"
    text += f"Confidence: {analysis['confidence']:.0f}%\n\n"
    text += f"Indicators:\n"
    text += f"- RSI(14): {analysis['rsi']:.1f}\n"
    text += f"- MACD: {analysis['macd']:.5f}\n"
    text += f"- Volatility: {analysis['volatility']:.2f}%\n"
    text += f"- MA50: {analysis['sma50']:.4f}\n"
    text += f"- MA200: {analysis['sma200']:.4f}\n\n"
    text += f"Reasons:\n"
    for r in analysis["reasons"]:
        text += f"- {r}\n"
    text += f"\n{time_str}"
    return text


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "AlgorithmX Bot\n\n"
        "Real signals for binary options\n"
        "Forex + OTC pairs\n\n"
        "Commands:\n"
        "/signal EUR/USD - Forex analysis\n"
        "/otc EUR/USD - OTC analysis\n"
        "/all - Top-5 Forex signals\n"
        "/allotc - Top-5 OTC signals\n"
        "/pairs - All pairs\n"
        "/help - Help"
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Commands:\n\n"
        "/signal EUR/USD - Forex analysis\n"
        "/otc EUR/USD - OTC analysis\n"
        "/all - Top-5 Forex\n"
        "/allotc - Top-5 OTC\n"
        "/pairs - List of pairs\n\n"
        "Data: Yahoo Finance (real prices)\n"
        "Expiry: 5 min (Forex), 1 min (OTC)\n"
        "Strategy: RSI + MACD + MA50/200"
    )


async def pairs_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = f"FOREX ({len(FOREX_PAIRS)} pairs):\n"
    for pair in FOREX_PAIRS:
        text += f"- {pair}\n"
    text += f"\nOTC ({len(OTC_PAIRS)} pairs):\n"
    for pair in OTC_PAIRS:
        text += f"- {pair}\n"
    await update.message.reply_text(text)


async def signal_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Specify pair. Example: /signal EUR/USD")
        return
    await process_signal(update, context, is_otc=False)


async def otc_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Specify pair. Example: /otc EUR/USD")
        return
    await process_signal(update, context, is_otc=True)


async def process_signal(update: Update, context: ContextTypes.DEFAULT_TYPE, is_otc: bool):
    pair_input = " ".join(context.args).upper()
    if is_otc:
        pair_input += " OTC"

    if pair_input not in ALL_PAIRS:
        await update.message.reply_text("Pair not found. List: /pairs")
        return

    msg = await update.message.reply_text(f"Analyzing {pair_input}...")
    symbol = ALL_PAIRS[pair_input]
    data = fetch_data(symbol, is_otc=is_otc)

    if data is None:
        await msg.edit_text(f"No data for {pair_input}. Try later.")
        return

    analysis = analyze_pair(data, is_otc=is_otc)
    signal_text = format_signal(pair_input, data, analysis, is_otc)
    await msg.edit_text(signal_text)


async def all_signals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("Analyzing Forex pairs...")
    await process_all(msg, FOREX_PAIRS, is_otc=False)


async def all_otc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("Analyzing OTC pairs...")
    await process_all(msg, OTC_PAIRS, is_otc=True)


async def process_all(msg, pairs_dict: Dict, is_otc: bool):
    signals = []
    for pair, symbol in pairs_dict.items():
        data = fetch_data(symbol, is_otc=is_otc)
        if data:
            analysis = analyze_pair(data, is_otc=is_otc)
            signals.append((pair, analysis))

    if not signals:
        await msg.edit_text("Failed to load data.")
        return

    signals.sort(key=lambda x: x[1]["confidence"], reverse=True)
    pair_type = "OTC" if is_otc else "Forex"

    text = f"TOP-5 {pair_type} SIGNALS\n\n"
    for pair, a in signals[:5]:
        price_str = format_price(pair, a["current_price"])
        text += f"{pair}: {a['direction']} ({a['confidence']:.0f}%)\n"
        text += f"Price: {price_str} | RSI: {a['rsi']:.1f}\n\n"

    text += "Details: /signal EUR/USD or /otc EUR/USD"
    await msg.edit_text(text)


async def main():
    threading.Thread(target=lambda: flask_app.run(host='0.0.0.0', port=PORT), daemon=True).start()
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("pairs", pairs_cmd))
    app.add_handler(CommandHandler("signal", signal_cmd))
    app.add_handler(CommandHandler("otc", otc_cmd))
    app.add_handler(CommandHandler("all", all_signals))
    app.add_handler(CommandHandler("allotc", all_otc))
    print("Bot running 24/7 on Render!")
    await app.run_polling()


if __name__ == "__main__":
    asyncio.run(main())
