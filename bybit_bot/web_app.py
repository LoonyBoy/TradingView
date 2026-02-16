"""
Web Dashboard для Bybit DCA Trading Bot.

Flask-приложение с:
- Интерактивным графиком (TradingView Lightweight Charts)
- Панелью управления ботом (старт/стоп)
- Настройки конфига (пара, баланс, DCA)
- Мониторинг позиции (безубыточность, DCA-входы)
- Просмотр логов в реальном времени

Использование:
    python web_app.py
"""

import sys
import os
import json
import time
import threading
import logging
from datetime import datetime
from collections import deque

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from flask import Flask, render_template, jsonify, request
from pybit.unified_trading import HTTP

import config
from indicator import get_indicator_data, calculate_buy_point, calculate_average_range
from dca import create_position, fill_entry, check_dca_triggers, check_sell_signal
from models import Position, DCAEntry
from utils import (
    save_state, load_state, clear_state,
    serialize_position, deserialize_position, round_to_step,
)

# ─────────────────────────────────────────
# Flask app
# ─────────────────────────────────────────
app = Flask(__name__)
app.secret_key = "bybit_dca_bot_secret"

# ─────────────────────────────────────────
# Глобальное состояние бота
# ─────────────────────────────────────────
bot_state = {
    "running": False,
    "thread": None,
    "position": None,
    "last_tick": None,
    "last_indicator": None,
    "last_price": 0.0,
    "error": None,
    "started_at": None,
    "ticks": 0,
}

# Буфер логов для веб-интерфейса
log_buffer = deque(maxlen=200)


class WebLogHandler(logging.Handler):
    """Логирование в буфер для вывода на веб-страницу."""

    def emit(self, record):
        msg = self.format(record)
        log_buffer.append({
            "time": datetime.now().strftime("%H:%M:%S"),
            "level": record.levelname,
            "message": msg,
        })


# Настройка логирования
def setup_web_logging():
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)

    fmt = logging.Formatter("%(asctime)s | %(levelname)-8s | %(message)s", datefmt="%H:%M:%S")

    # Web handler
    wh = WebLogHandler()
    wh.setFormatter(fmt)
    logger.addHandler(wh)

    # Console handler
    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    logger.addHandler(ch)


setup_web_logging()
web_logger = logging.getLogger("web")


# ─────────────────────────────────────────
# Фоновый поток бота
# ─────────────────────────────────────────
def bot_loop():
    """Фоновый цикл бота (аналог bot.py, но управляемый из веба)."""
    from bybit_client import BybitClient

    web_logger.info("🚀 Бот запущен из веб-интерфейса")
    bot_state["started_at"] = datetime.now().isoformat()
    bot_state["error"] = None

    try:
        client = BybitClient()
        web_logger.info(f"📡 Подключение к Bybit {'[TESTNET]' if config.BYBIT_TESTNET else '[PRODUCTION]'}")

        # Загружаем инструмент
        try:
            instrument = client.get_instrument_info(config.SYMBOL)
        except Exception:
            instrument = {"min_qty": 0.00001, "qty_step": 0.00001, "price_step": 0.01, "min_order_amt": 1.0}

        # Восстанавливаем состояние
        state = load_state()
        if state and state.get("position", {}).get("is_active"):
            bot_state["position"] = deserialize_position(state["position"])
            web_logger.info("♻️ Позиция восстановлена из файла")

        while bot_state["running"]:
            try:
                bot_state["ticks"] += 1

                # Получаем свечи
                klines = client.get_klines(
                    symbol=config.SYMBOL,
                    interval=config.TIMEFRAME,
                    limit=config.LOOKBACK_PERIOD + 10,
                )

                closes = klines["closes"]
                highs = klines["highs"]
                lows = klines["lows"]

                # Индикатор
                indicator = get_indicator_data(closes, highs, lows, config.LOOKBACK_PERIOD)
                bot_state["last_indicator"] = indicator

                # Текущая цена
                price = client.get_current_price(config.SYMBOL)
                bot_state["last_price"] = price
                bot_state["last_tick"] = datetime.now().isoformat()

                web_logger.info(
                    f"📊 {config.SYMBOL}: ${price:,.2f} | "
                    f"Buy Point: ${indicator['buy_point']:,.2f} | "
                    f"Signal: {'✅' if indicator['signal'] else '❌'}"
                )

                position = bot_state["position"]

                # --- Нет позиции: ищем сигнал ---
                if position is None or not position.is_active:
                    if indicator["signal"]:
                        web_logger.info("🟢 СИГНАЛ ПОКУПКИ!")
                        first_price = indicator["signal_price"]

                        position = create_position(
                            symbol=config.SYMBOL,
                            first_entry_price=first_price,
                        )

                        first_entry = position.entries[0]
                        qty = round_to_step(first_entry.qty, instrument.get("qty_step", 0.00001))

                        if qty > 0:
                            order_id = client.place_limit_buy(
                                symbol=config.SYMBOL, qty=qty,
                                price=round_to_step(first_price, instrument.get("price_step", 0.01)),
                                order_link_id=f"dca_{config.SYMBOL}_1",
                            )
                            if order_id:
                                first_entry.order_id = order_id
                                fill_entry(position, 1, first_price)

                                # DCA ордера
                                for entry in position.entries[1:]:
                                    if not entry.filled and entry.order_id is None:
                                        q = round_to_step(entry.qty, instrument.get("qty_step", 0.00001))
                                        p = round_to_step(entry.target_price, instrument.get("price_step", 0.01))
                                        oid = client.place_limit_buy(
                                            symbol=config.SYMBOL, qty=q, price=p,
                                            order_link_id=f"dca_{config.SYMBOL}_{entry.level}",
                                        )
                                        if oid:
                                            entry.order_id = oid

                                # Sell ордер
                                sell_target = position.calculate_sell_target(config.SELL_PROFIT_PCT)
                                total_q = round_to_step(position.total_qty, instrument.get("qty_step", 0.00001))
                                sell_p = round_to_step(sell_target, instrument.get("price_step", 0.01))
                                if total_q > 0:
                                    sid = client.place_limit_sell(
                                        symbol=config.SYMBOL, qty=total_q, price=sell_p,
                                        order_link_id=f"sell_{config.SYMBOL}",
                                    )
                                    if sid:
                                        position.sell_order_id = sid

                                bot_state["position"] = position
                                save_state(serialize_position(position))
                    else:
                        web_logger.info("⏳ Ожидание сигнала...")

                # --- Есть позиция ---
                else:
                    # Проверяем DCA fills
                    for entry in position.entries:
                        if entry.filled or entry.order_id is None:
                            continue
                        status = client.get_order_status(config.SYMBOL, entry.order_id)
                        if status.get("status") == "Filled":
                            avg_p = status.get("avg_price", entry.target_price) or entry.target_price
                            fill_entry(position, entry.level, avg_p)
                            web_logger.info(f"✅ DCA-{entry.level} исполнен: ${avg_p:,.2f}")

                            # Обновляем sell ордер
                            if position.sell_order_id:
                                client.cancel_order(config.SYMBOL, position.sell_order_id)
                            sell_target = position.calculate_sell_target(config.SELL_PROFIT_PCT)
                            total_q = round_to_step(position.total_qty, instrument.get("qty_step", 0.00001))
                            sell_p = round_to_step(sell_target, instrument.get("price_step", 0.01))
                            if total_q > 0:
                                sid = client.place_limit_sell(
                                    symbol=config.SYMBOL, qty=total_q, price=sell_p,
                                    order_link_id=f"sell_{config.SYMBOL}",
                                )
                                if sid:
                                    position.sell_order_id = sid

                    # Проверяем sell
                    if check_sell_signal(position, price, config.SELL_PROFIT_PCT):
                        web_logger.info("💰 ПРОДАЖА!")
                        client.cancel_all_orders(config.SYMBOL)
                        total_q = round_to_step(position.total_qty, instrument.get("qty_step", 0.00001))
                        if total_q > 0:
                            client.place_market_sell(
                                symbol=config.SYMBOL, qty=total_q,
                                order_link_id=f"sell_market_{config.SYMBOL}",
                            )
                        position.is_active = False
                        bot_state["position"] = None
                        clear_state()
                        web_logger.info("✅ Позиция закрыта!")
                    else:
                        save_state(serialize_position(position))
                        bot_state["position"] = position

            except Exception as e:
                web_logger.error(f"Ошибка в цикле: {e}")
                bot_state["error"] = str(e)

            # Ожидание с проверкой флага
            for _ in range(config.CHECK_INTERVAL_SECONDS):
                if not bot_state["running"]:
                    break
                time.sleep(1)

    except Exception as e:
        web_logger.error(f"Критическая ошибка бота: {e}")
        bot_state["error"] = str(e)
    finally:
        bot_state["running"] = False
        web_logger.info("🛑 Бот остановлен")


# ─────────────────────────────────────────
# Маршруты
# ─────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/status")
def api_status():
    """Статус бота."""
    pos = bot_state["position"]
    pos_data = None

    if pos and pos.is_active:
        pos_data = {
            "symbol": pos.symbol,
            "entries": [],
            "total_qty": pos.total_qty,
            "total_invested": pos.total_invested,
            "breakeven": pos.breakeven,
            "sell_target": pos.calculate_sell_target(config.SELL_PROFIT_PCT),
            "current_level": pos.current_dca_level,
            "total_levels": len(pos.entries),
        }
        for e in pos.entries:
            pos_data["entries"].append({
                "level": e.level,
                "target_price": e.target_price,
                "entry_price": e.entry_price,
                "qty": e.qty,
                "order_size_usd": e.order_size_usd,
                "filled": e.filled,
            })

    return jsonify({
        "running": bot_state["running"],
        "last_tick": bot_state["last_tick"],
        "last_price": bot_state["last_price"],
        "last_indicator": bot_state["last_indicator"],
        "error": bot_state["error"],
        "started_at": bot_state["started_at"],
        "ticks": bot_state["ticks"],
        "position": pos_data,
    })


@app.route("/api/config", methods=["GET"])
def api_get_config():
    """Получить текущий конфиг."""
    return jsonify({
        "symbol": config.SYMBOL,
        "timeframe": config.TIMEFRAME,
        "lookback_period": config.LOOKBACK_PERIOD,
        "total_balance": config.TOTAL_BALANCE,
        "sell_profit_pct": config.SELL_PROFIT_PCT,
        "check_interval": config.CHECK_INTERVAL_SECONDS,
        "testnet": config.BYBIT_TESTNET,
        "api_key_set": bool(config.BYBIT_API_KEY),
        "dca_points": config.DCA_POINTS,
    })


@app.route("/api/config", methods=["POST"])
def api_update_config():
    """Обновить конфиг (без перезапуска файла)."""
    data = request.json

    if bot_state["running"]:
        return jsonify({"error": "Остановите бота перед изменением конфигурации"}), 400

    if "symbol" in data:
        config.SYMBOL = data["symbol"].upper()
    if "timeframe" in data:
        config.TIMEFRAME = data["timeframe"]
    if "lookback_period" in data:
        config.LOOKBACK_PERIOD = int(data["lookback_period"])
    if "total_balance" in data:
        config.TOTAL_BALANCE = float(data["total_balance"])
    if "sell_profit_pct" in data:
        config.SELL_PROFIT_PCT = float(data["sell_profit_pct"])
    if "check_interval" in data:
        config.CHECK_INTERVAL_SECONDS = int(data["check_interval"])
    if "testnet" in data:
        config.BYBIT_TESTNET = bool(data["testnet"])
    if "api_key" in data and data["api_key"]:
        config.BYBIT_API_KEY = data["api_key"]
    if "api_secret" in data and data["api_secret"]:
        config.BYBIT_API_SECRET = data["api_secret"]
    if "dca_points" in data:
        config.DCA_POINTS = data["dca_points"]

    web_logger.info(f"⚙️ Конфигурация обновлена: {config.SYMBOL}, Balance=${config.TOTAL_BALANCE}")
    return jsonify({"ok": True})


@app.route("/api/start", methods=["POST"])
def api_start():
    """Запустить бота."""
    if bot_state["running"]:
        return jsonify({"error": "Бот уже запущен"}), 400

    if not config.BYBIT_API_KEY or not config.BYBIT_API_SECRET:
        return jsonify({"error": "API ключи не настроены"}), 400

    bot_state["running"] = True
    bot_state["ticks"] = 0
    t = threading.Thread(target=bot_loop, daemon=True)
    t.start()
    bot_state["thread"] = t

    return jsonify({"ok": True, "message": "Бот запущен"})


@app.route("/api/stop", methods=["POST"])
def api_stop():
    """Остановить бота."""
    if not bot_state["running"]:
        return jsonify({"error": "Бот не запущен"}), 400

    bot_state["running"] = False
    web_logger.info("🛑 Остановка бота...")

    return jsonify({"ok": True, "message": "Бот останавливается..."})


@app.route("/api/klines")
def api_klines():
    """Получить свечи для графика."""
    symbol = request.args.get("symbol", config.SYMBOL)
    interval = request.args.get("interval", config.TIMEFRAME)
    limit = int(request.args.get("limit", 60))

    try:
        session = HTTP(testnet=False)
        response = session.get_kline(
            category="spot", symbol=symbol,
            interval=interval, limit=limit,
        )

        klines_raw = response["result"]["list"]
        klines_raw.reverse()

        candles = []
        buy_points = []
        highs_arr = []
        lows_arr = []
        closes_arr = []

        for k in klines_raw:
            ts = int(k[0]) // 1000
            o, h, l, c = float(k[1]), float(k[2]), float(k[3]), float(k[4])
            candles.append({"time": ts, "open": o, "high": h, "low": l, "close": c})
            highs_arr.append(h)
            lows_arr.append(l)
            closes_arr.append(c)

            if len(closes_arr) >= config.LOOKBACK_PERIOD:
                bp = calculate_buy_point(closes_arr, highs_arr, lows_arr, config.LOOKBACK_PERIOD)
                buy_points.append({"time": ts, "value": round(bp, 2)})
            else:
                buy_points.append({"time": ts, "value": None})

        # DCA levels
        dca_lines = []
        if buy_points and buy_points[-1]["value"]:
            last_bp = buy_points[-1]["value"]
            for pt in config.DCA_POINTS:
                price = last_bp * (1 - pt["drop_pct"] / 100)
                dca_lines.append({
                    "level": pt["level"],
                    "price": round(price, 2),
                    "drop_pct": pt["drop_pct"],
                    "balance_pct": pt["balance_pct"],
                })

        # Position lines
        pos = bot_state["position"]
        pos_lines = {}
        if pos and pos.is_active and pos.breakeven > 0:
            pos_lines = {
                "breakeven": round(pos.breakeven, 2),
                "sell_target": round(pos.calculate_sell_target(config.SELL_PROFIT_PCT), 2),
            }

        return jsonify({
            "candles": candles,
            "buy_points": buy_points,
            "dca_lines": dca_lines,
            "position_lines": pos_lines,
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/logs")
def api_logs():
    """Получить последние логи."""
    since = int(request.args.get("since", 0))
    logs = list(log_buffer)
    if since > 0 and since < len(logs):
        logs = logs[since:]
    return jsonify({"logs": logs, "total": len(log_buffer)})


@app.route("/api/indicator")
def api_indicator():
    """Получить данные индикатора для текущей пары."""
    symbol = request.args.get("symbol", config.SYMBOL)

    try:
        session = HTTP(testnet=False)
        response = session.get_kline(
            category="spot", symbol=symbol,
            interval=config.TIMEFRAME,
            limit=config.LOOKBACK_PERIOD + 10,
        )

        klines_raw = response["result"]["list"]
        klines_raw.reverse()

        closes = [float(k[4]) for k in klines_raw]
        highs = [float(k[2]) for k in klines_raw]
        lows = [float(k[3]) for k in klines_raw]

        indicator = get_indicator_data(closes, highs, lows, config.LOOKBACK_PERIOD)

        # Текущая цена
        price_resp = session.get_tickers(category="spot", symbol=symbol)
        current_price = float(price_resp["result"]["list"][0]["lastPrice"])

        return jsonify({
            **indicator,
            "current_price": current_price,
            "symbol": symbol,
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ─────────────────────────────────────────
if __name__ == "__main__":
    print("\n" + "=" * 50)
    print("  BYBIT DCA BOT — Web Dashboard")
    print("  http://localhost:5000")
    print("=" * 50 + "\n")

    app.run(host="0.0.0.0", port=5000, debug=False)
