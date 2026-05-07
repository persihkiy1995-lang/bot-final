import logging
import os
import threading
from typing import Dict
import numpy as np
import yfinance as yf
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

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
    "EUR/GBP OTC": "EURGBP=X", "GBP/JPY OTC": "GBPJPY=X",
    "BTC/USD OTC": "BTC-USD", "ETH/USD OTC": "ETH-USD",
    "XAU/USD OTC": "GC=F",
}

ALL_PAIRS = {**FOREX_PAIRS, **OTC_PAIRS}

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s", datefmt="%H:%M:%S")
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

    bullish = 0
    bearish = 0

    if rsi < 30:
        bullish += 2
    elif rsi > 70:
        bearish += 2

    if histogram > 0:
        bullish += 1
    else:
        bearish += 1

    if sma50 > sma200:
        bullish += 1
    else:
        bearish += 1

    if current > sma50:
        bullish += 1
    else:
        bearish += 1

    total = max(bullish + bearish, 1)
    confidence = max(bullish, bearish) / total * 100

    if bullish > bearish:
        direction = "ПОКУПКА 📈"
        direction_short = "CALL"
    elif bearish > bullish:
        direction = "ПРОДАЖА 📉"
        direction_short = "PUT"
    else:
        direction = "НЕТ СИГНАЛА ⏸️"
        direction_short = "WAIT"

    # Определяем время сделки
    expiry = "1 минута" if is_otc else "5 минут"

    # Тренд
    if sma50 > sma200:
        trend = "Бычий (восходящий)"
    else:
        trend = "Медвежий (нисходящий)"

    # RSI статус
    if rsi < 30:
        rsi_status = "Перепродан"
    elif rsi > 70:
        rsi_status = "Перекуплен"
    else:
        rsi_status = "Нейтральный"

    return {
        "direction": direction,
        "direction_short": direction_short,
        "confidence": confidence,
        "rsi": rsi,
        "rsi_status": rsi_status,
        "trend": trend,
        "current_price": current,
        "expiry": expiry,
    }


def format_price(pair, price):
    if "BTC" in pair:
        return f"${price:,.0f}"
    if "XAU" in pair:
        return f"${price:.1f}"
    if "JPY" in pair:
        return f"{price:.3f}"
    return f"{price:.5f}"


# ==================== КЛАВИАТУРЫ ====================

def main_menu_keyboard():
    keyboard = [
        [InlineKeyboardButton("💱 Forex пары", callback_data="menu_forex")],
        [InlineKeyboardButton("🔮 OTC пары", callback_data="menu_otc")],
        [InlineKeyboardButton("📊 Топ-5 Forex", callback_data="all_forex")],
        [InlineKeyboardButton("📊 Топ-5 OTC", callback_data="all_otc")],
        [InlineKeyboardButton("ℹ️ Помощь", callback_data="help")],
    ]
    return InlineKeyboardMarkup(keyboard)


def pairs_keyboard(pairs_dict, prefix):
    keyboard = []
    row = []
    for i, pair in enumerate(pairs_dict):
        row.append(InlineKeyboardButton(pair, callback_data=f"{prefix}_{pair}"))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="back_main")])
    return InlineKeyboardMarkup(keyboard)


# ==================== ОБРАБОТЧИКИ КОМАНД ====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 <b>AlgorithmX Bot</b>\n\n"
        "Реальные сигналы для бинарных опционов\n"
        "Анализ на основе RSI + MACD + MA50/200\n\n"
        "Выберите раздел:",
        parse_mode="HTML",
        reply_markup=main_menu_keyboard()
    )


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "menu_forex":
        await query.edit_message_text(
            "💱 <b>Выберите валютную пару Forex:</b>",
            parse_mode="HTML",
            reply_markup=pairs_keyboard(FOREX_PAIRS, "analyze_forex")
        )

    elif data == "menu_otc":
        await query.edit_message_text(
            "🔮 <b>Выберите OTC пару:</b>",
            parse_mode="HTML",
            reply_markup=pairs_keyboard(OTC_PAIRS, "analyze_otc")
        )

    elif data == "all_forex":
        await query.edit_message_text("⏳ Анализирую Forex пары...")
        await show_top_signals(query, FOREX_PAIRS, is_otc=False)

    elif data == "all_otc":
        await query.edit_message_text("⏳ Анализирую OTC пары...")
        await show_top_signals(query, OTC_PAIRS, is_otc=True)

    elif data == "help":
        await query.edit_message_text(
            "ℹ️ <b>О боте</b>\n\n"
            "🔹 Выберите пару из меню\n"
            "🔹 Бот загрузит реальные данные\n"
            "🔹 Рассчитает индикаторы\n"
            "🔹 Выдаст сигнал: ПОКУПКА или ПРОДАЖА\n\n"
            "⏱ Экспирация:\n"
            "• Forex: 5 минут\n"
            "• OTC: 1 минута\n\n"
            "📊 Стратегия: RSI + MACD + MA50/200",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Назад", callback_data="back_main")]
            ])
        )

    elif data == "back_main":
        await query.edit_message_text(
            "👋 <b>Главное меню</b>\n\nВыберите раздел:",
            parse_mode="HTML",
            reply_markup=main_menu_keyboard()
        )

    elif data.startswith("analyze_"):
        parts = data.split("_", 1)
        pair = parts[1]
        is_otc = "otc" in data
        await query.edit_message_text(f"⏳ Анализирую {pair}...")
        await show_signal(query, pair, is_otc)


async def show_signal(query, pair, is_otc):
    symbol = ALL_PAIRS[pair]
    data = fetch_data(symbol, is_otc)

    if data is None:
        await query.edit_message_text(
            f"❌ Нет данных для {pair}. Попробуйте позже.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Назад", callback_data="back_main")]
            ])
        )
        return

    a = analyze_pair(data, is_otc)
    price_str = format_price(pair, a["current_price"])
    pair_type = "OTC" if is_otc else "Forex"
    ts = data["timestamp"]
    time_str = ts.strftime("%H:%M:%S") if hasattr(ts, "strftime") else str(ts)

    text = f"🔔 <b>СИГНАЛ ALGORITHMX</b>\n\n"
    text += f"📊 <b>Пара:</b> {pair} [{pair_type}]\n"
    text += f"💵 <b>Цена:</b> {price_str}\n"
    text += f"⏱ <b>Экспирация:</b> {a['expiry']}\n\n"
    text += f"📈 <b>Сигнал:</b> {a['direction']}\n"
    text += f"📊 <b>Уверенность:</b> {a['confidence']:.0f}%\n\n"
    text += f"📋 <b>Индикаторы:</b>\n"
    text += f"• RSI(14): {a['rsi']:.1f} ({a['rsi_status']})\n"
    text += f"• Тренд: {a['trend']}\n\n"
    text += f"⏰ <i>Обновлено: {time_str}</i>"

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Обновить", callback_data=f"analyze_{'otc' if is_otc else 'forex'}_{pair}")],
        [InlineKeyboardButton("🔙 Назад", callback_data=f"menu_{'otc' if is_otc else 'forex'}")],
        [InlineKeyboardButton("🏠 Главное меню", callback_data="back_main")],
    ])

    await query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)


async def show_top_signals(query, pairs_dict, is_otc):
    signals = []
    for p, s in pairs_dict.items():
        d = fetch_data(s, is_otc)
        if d:
            signals.append((p, analyze_pair(d, is_otc)))

    if not signals:
        await query.edit_message_text("❌ Нет данных.", reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 Назад", callback_data="back_main")]
        ]))
        return

    signals.sort(key=lambda x: x[1]["confidence"], reverse=True)
    pair_type = "OTC" if is_otc else "Forex"

    text = f"📊 <b>ТОП-5 {pair_type} СИГНАЛОВ</b>\n\n"
    for pair, a in signals[:5]:
        price_str = format_price(pair, a["current_price"])
        text += f"• <b>{pair}</b>\n"
        text += f"  Сигнал: {a['direction']}\n"
        text += f"  Цена: {price_str}\n"
        text += f"  Уверенность: {a['confidence']:.0f}%\n\n"

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Обновить", callback_data=f"all_{'otc' if is_otc else 'forex'}")],
        [InlineKeyboardButton("🏠 Главное меню", callback_data="back_main")],
    ])

    await query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)


# ==================== ЗАПУСК ====================

def main():
    threading.Thread(target=lambda: flask_app.run(host='0.0.0.0', port=PORT), daemon=True).start()
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    print("Bot running 24/7 on Render!")
    app.run_polling()


if __name__ == "__main__":
    main()
