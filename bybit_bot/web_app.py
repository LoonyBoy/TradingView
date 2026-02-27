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


def get_current_tp_pct(position) -> float:
    """Получить TP% по максимальному заполненному DCA-уровню."""
    if not position or not position.filled_entries:
        return 1.0
    max_level = max(e.level for e in position.filled_entries)
    for dp in config.DCA_POINTS:
        if dp["level"] == max_level:
            return float(dp.get("tp_pct", 1.0))
    return 1.0


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
        except Exception as e:
            err_msg = str(e)
            if "Not supported" in err_msg or "ErrCode: 10001" in err_msg or "list index" in err_msg:
                mode = "тестнете" if config.BYBIT_TESTNET else "production"
                web_logger.error(
                    f"⛔ Пара {config.SYMBOL} не найдена на {mode}! "
                    f"Смените символ или переключите режим (Testnet/Production)."
                )
                bot_state["error"] = f"Пара {config.SYMBOL} не поддерживается на {mode}"
                bot_state["running"] = False
                return
            web_logger.warning(f"Не удалось получить инструмент: {e}, используем приблизительные параметры")
            instrument = {"min_qty": 0.00001, "qty_step": 0.00001, "price_step": 0.01, "min_order_amt": 1.0}

        # Восстанавливаем состояние
        state = load_state()
        if state and state.get("position", {}).get("is_active"):
            bot_state["position"] = deserialize_position(state["position"])
            web_logger.info("♻️ Позиция восстановлена из файла")

        while bot_state["running"]:
            try:
                bot_state["ticks"] += 1

                # Текущая цена — нужна всегда (каждые 60 сек)
                price = client.get_current_price(config.SYMBOL)
                bot_state["last_price"] = price
                bot_state["last_tick"] = datetime.now().isoformat()

                position = bot_state["position"]

                # --- Нет позиции: проверяем сигнал по ДНЕВНЫМ свечам ---
                if position is None or not position.is_active:
                    # Дневные свечи запрашиваем ТОЛЬКО для поиска сигнала покупки
                    klines = client.get_klines(
                        symbol=config.SYMBOL,
                        interval=config.SIGNAL_TIMEFRAME,  # Таймфрейм из настроек
                        limit=config.LOOKBACK_PERIOD + 10,
                    )

                    closes = klines["closes"]
                    highs = klines["highs"]
                    lows = klines["lows"]

                    indicator = get_indicator_data(closes, highs, lows, config.LOOKBACK_PERIOD)
                    bot_state["last_indicator"] = indicator

                    web_logger.info(
                        f"📊 {config.SYMBOL}: ${price:,.2f} | "
                        f"Buy Point (D): ${indicator['buy_point']:,.2f} | "
                        f"Signal: {'✅' if indicator['signal'] else '❌'}"
                    )
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
                                sell_target = position.calculate_sell_target(get_current_tp_pct(position))
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

                # --- Есть позиция: DCA и продажа по текущей цене (каждые 60 сек) ---
                else:
                    web_logger.info(
                        f"📈 {config.SYMBOL}: ${price:,.2f} | "
                        f"Breakeven: ${position.breakeven:,.2f} | "
                        f"DCA {position.current_dca_level}/{len(position.entries)}"
                    )
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
                            sell_target = position.calculate_sell_target(get_current_tp_pct(position))
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
                    if check_sell_signal(position, price, get_current_tp_pct(position)):
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
                err_msg = str(e)
                web_logger.error(f"Ошибка в цикле: {err_msg}")
                bot_state["error"] = err_msg

                # Критическая ошибка символа — останавливаем бота
                if "Not supported" in err_msg or "ErrCode: 10001" in err_msg:
                    mode = "тестнете" if config.BYBIT_TESTNET else "production"
                    web_logger.error(
                        f"⛔ Пара {config.SYMBOL} не поддерживается на {mode}! "
                        f"Бот остановлен. Смените символ или режим."
                    )
                    bot_state["running"] = False
                    break

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
            "sell_target": pos.calculate_sell_target(get_current_tp_pct(pos)),
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
        "check_interval": config.CHECK_INTERVAL_SECONDS,
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
        config.SIGNAL_TIMEFRAME = data["timeframe"]
    if "lookback_period" in data:
        config.LOOKBACK_PERIOD = int(data["lookback_period"])
    if "total_balance" in data:
        config.TOTAL_BALANCE = float(data["total_balance"])
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

        # Рассчитываем buy_point для каждого бара
        raw_buy_points = []
        for i in range(len(candles)):
            if i + 1 >= config.LOOKBACK_PERIOD:
                bp = calculate_buy_point(
                    closes_arr[:i + 1], highs_arr[:i + 1], lows_arr[:i + 1],
                    config.LOOKBACK_PERIOD
                )
                raw_buy_points.append(bp)
            else:
                raw_buy_points.append(None)

        # Сдвигаем на 1 бар вперёд: на баре i показываем buy_point[i-1],
        # т.к. сигнал покупки на баре i проверяет low[i] <= buyPoint[i-1]
        buy_points = []
        for i in range(1, len(candles)):
            if raw_buy_points[i - 1] is not None:
                buy_points.append({"time": candles[i]["time"], "value": round(raw_buy_points[i - 1], 10)})
            else:
                buy_points.append({"time": candles[i]["time"], "value": None})

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
                "sell_target": round(pos.calculate_sell_target(get_current_tp_pct(pos)), 2),
            }

        # Strategy calculation: always show the 5-bar calculation
        # Use the DAILY timeframe regardless of chart TF
        strategy_info = None
        try:
            daily_resp = session.get_kline(
                category="spot", symbol=symbol,
                interval=config.SIGNAL_TIMEFRAME,  # Таймфрейм из настроек
                limit=config.LOOKBACK_PERIOD + 5,
            )
            daily_raw = daily_resp["result"]["list"]
            daily_raw.reverse()

            d_closes = [float(k[4]) for k in daily_raw]
            d_highs = [float(k[2]) for k in daily_raw]
            d_lows = [float(k[3]) for k in daily_raw]
            d_timestamps = [int(k[0]) // 1000 for k in daily_raw]

            if len(d_closes) >= config.LOOKBACK_PERIOD:
                indicator = get_indicator_data(d_closes, d_highs, d_lows, config.LOOKBACK_PERIOD)

                # The 5 bars used for calculation (last LOOKBACK bars before the current one)
                lb = config.LOOKBACK_PERIOD
                calc_bars = []
                for i in range(len(daily_raw) - 1 - lb, len(daily_raw) - 1):
                    if i >= 0:
                        calc_bars.append({
                            "time": d_timestamps[i],
                            "high": d_highs[i],
                            "low": d_lows[i],
                            "close": d_closes[i],
                            "range": round(d_highs[i] - d_lows[i], 2),
                        })

                # Current bar (the one being checked for signal)
                current_bar = {
                    "time": d_timestamps[-1],
                    "low": d_lows[-1],
                    "close": d_closes[-1],
                }

                strategy_info = {
                    "lookback": lb,
                    "average_range": round(indicator["average_range"], 2),
                    "buy_point": round(indicator["buy_point"], 2),
                    "signal": indicator["signal"],
                    "signal_price": round(indicator.get("signal_price", 0), 2),
                    "calc_bars": calc_bars,
                    "current_bar": current_bar,
                    "timeframe": config.SIGNAL_TIMEFRAME,
                }
        except Exception:
            pass  # Non-critical, chart still works

        return jsonify({
            "candles": candles,
            "buy_points": buy_points,
            "dca_lines": dca_lines,
            "position_lines": pos_lines,
            "strategy": strategy_info,
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


@app.route("/api/backtest", methods=["GET", "POST"])
def api_backtest():
    """
    Бэктест DCA-стратегии на исторических данных.
    Логика:
      1. Сигнал покупки: low <= buyPoint предыдущего бара → покупка DCA-1 (0%)
      2. Если цена падает ниже DCA-уровней → покупки DCA-2, DCA-3 и т.д.
      3. Каждый DCA-уровень покупается ОДИН раз за цикл
      4. Продажа: ВСЕ позиции продаются, когда цена >= безубыточность * (1 + sell_pct%)
      5. После продажи — новый цикл (все DCA-уровни сбрасываются)
    """
    # Поддержка GET (query params) и POST (JSON body с DCA-таблицей)
    if request.method == "POST":
        body = request.get_json(force=True) or {}
        symbol = body.get("symbol", config.SYMBOL)
        interval = body.get("interval", config.TIMEFRAME)
        limit = int(body.get("limit", 1000))
        lookback = int(body.get("lookback", config.LOOKBACK_PERIOD))
        balance = float(body.get("balance", config.TOTAL_BALANCE))
        dca_points = body.get("dca_points", config.DCA_POINTS)
        start_date = body.get("start_date", "")  # "YYYY-MM-DD"
        end_date = body.get("end_date", "")        # "YYYY-MM-DD"
    else:
        symbol = request.args.get("symbol", config.SYMBOL)
        interval = request.args.get("interval", config.TIMEFRAME)
        limit = int(request.args.get("limit", 1000))
        lookback = int(request.args.get("lookback", config.LOOKBACK_PERIOD))
        balance = float(request.args.get("balance", config.TOTAL_BALANCE))
        dca_points = config.DCA_POINTS
        start_date = request.args.get("start_date", "")
        end_date = request.args.get("end_date", "")

    # Собрать мапу tp_pct по уровням: {level: tp_pct}
    tp_by_level = {}
    for dp in dca_points:
        lvl = dp.get("level", dp.get("level", 0))
        tp_by_level[lvl] = float(dp.get("tp_pct", 1.0))

    try:
        session = HTTP(testnet=False)
        response = session.get_kline(
            category="spot", symbol=symbol,
            interval=interval, limit=limit,
        )

        klines_raw = response["result"]["list"]
        klines_raw.reverse()

        # Подготовка данных
        candles = []
        for k in klines_raw:
            candles.append({
                "time": int(k[0]) // 1000,
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
            })

        # Фильтрация по диапазону дат
        from datetime import datetime
        if start_date:
            try:
                start_ts = int(datetime.strptime(start_date, "%Y-%m-%d").timestamp())
                candles = [c for c in candles if c["time"] >= start_ts]
            except ValueError:
                pass
        if end_date:
            try:
                end_ts = int(datetime.strptime(end_date, "%Y-%m-%d").timestamp()) + 86400  # end of day
                candles = [c for c in candles if c["time"] < end_ts]
            except ValueError:
                pass

        # Массивы для расчётов
        highs = [c["high"] for c in candles]
        lows = [c["low"] for c in candles]
        closes = [c["close"] for c in candles]

        # Рассчитываем buy_point для каждого бара
        buy_points = []
        for i in range(len(candles)):
            if i + 1 >= lookback:
                h_slice = highs[:i + 1]
                l_slice = lows[:i + 1]
                c_slice = closes[:i + 1]
                bp = calculate_buy_point(c_slice, h_slice, l_slice, lookback)
                buy_points.append(bp)
            else:
                buy_points.append(None)

        # ══════════════════════════════════════════════════════
        # DCA-симуляция торговли
        # ══════════════════════════════════════════════════════
        cycles = []         # Завершённые циклы [{entries: [...], exit_time, exit_price, ...}]
        open_positions = []  # Текущие открытые DCA-позиции
        filled_levels = set()  # Какие DCA-уровни уже заполнены в текущем цикле
        first_entry_price = None  # Цена первого входа (DCA-1) — для расчёта DCA-уровней
        cycle_active = False  # Идёт ли торговый цикл

        for i in range(1, len(candles)):
            ts = candles[i]["time"]
            low_price = candles[i]["low"]
            high_price = candles[i]["high"]

            # ─── ПРОДАЖА: проверяем TP по безубыточности ───
            if open_positions:
                total_cost = sum(p["entry_price"] * p["qty"] for p in open_positions)
                total_qty = sum(p["qty"] for p in open_positions)
                breakeven = total_cost / total_qty if total_qty > 0 else 0
                # TP% определяется по максимальному заполненному уровню
                max_filled = max(filled_levels)
                current_tp_pct = tp_by_level.get(max_filled, 1.0)
                sell_target = breakeven * (1 + current_tp_pct / 100)

                if high_price >= sell_target:
                    # Продаём ВСЕ позиции по sell_target
                    total_invested = sum(p["order_size"] for p in open_positions)
                    total_pnl = (sell_target - breakeven) * total_qty
                    pnl_pct = (sell_target / breakeven - 1) * 100

                    cycle_data = {
                        "entries": [],
                        "exit_time": ts,
                        "exit_price": round(sell_target, 6),
                        "breakeven": round(breakeven, 6),
                        "total_qty": total_qty,
                        "total_invested": round(total_invested, 2),
                        "total_pnl": round(total_pnl, 4),
                        "pnl_pct": round(pnl_pct, 4),
                        "dca_count": len(open_positions),
                        "result": "win",
                    }
                    for p in open_positions:
                        entry_data = {
                            "entry_time": p["entry_time"],
                            "entry_price": round(p["entry_price"], 6),
                            "qty": p["qty"],
                            "order_size": p["order_size"],
                            "level": p["level"],
                        }
                        if "calc" in p:
                            entry_data["calc"] = p["calc"]
                        cycle_data["entries"].append(entry_data)

                    cycles.append(cycle_data)

                    # Сброс цикла
                    open_positions = []
                    filled_levels = set()
                    first_entry_price = None
                    cycle_active = False
                    continue  # Переходим к следующему бару

            # ─── ПОКУПКА: проверяем сигнал и DCA-уровни ───
            prev_bp = buy_points[i - 1]

            if not cycle_active:
                # Нет активного цикла — ждём сигнал покупки (DCA-1)
                if prev_bp is not None and low_price <= prev_bp:
                    # Сигнал! Открываем DCA-1
                    dca1 = dca_points[0]
                    order_size = balance * dca1["balance_pct"] / 100
                    entry_price = prev_bp
                    qty = order_size / entry_price

                    # Собираем данные расчёта (5 баров и формулу)
                    calc_bars = []
                    prev_bar_idx = i - 1  # Бар, чей buy_point мы используем
                    for bi in range(lookback):
                        idx = prev_bar_idx - bi
                        if idx >= 0:
                            calc_bars.append({
                                "time": candles[idx]["time"],
                                "high": candles[idx]["high"],
                                "low": candles[idx]["low"],
                                "range": round(candles[idx]["high"] - candles[idx]["low"], 10),
                            })
                    calc_bars.reverse()  # От старого к новому
                    avg_range = sum(b["range"] for b in calc_bars) / len(calc_bars) if calc_bars else 0
                    prev_close = candles[prev_bar_idx]["close"] if prev_bar_idx >= 0 else 0

                    open_positions.append({
                        "entry_price": entry_price,
                        "entry_time": ts,
                        "qty": qty,
                        "order_size": order_size,
                        "level": 1,
                        "calc": {
                            "bars": calc_bars,
                            "avg_range": round(avg_range, 10),
                            "prev_close": prev_close,
                            "buy_point": round(entry_price, 10),
                            "signal_bar_time": ts,
                            "signal_bar_low": low_price,
                        },
                    })
                    filled_levels.add(1)
                    first_entry_price = entry_price
                    cycle_active = True
            else:
                # Цикл активен — проверяем DCA-уровни 2, 3, 4...
                for dca in dca_points[1:]:  # Пропускаем level 1 (уже куплен)
                    lvl = dca["level"]
                    if lvl in filled_levels:
                        continue  # Уже куплен в этом цикле

                    dca_price = first_entry_price * (1 - dca["drop_pct"] / 100)
                    if low_price <= dca_price:
                        order_size = balance * dca["balance_pct"] / 100
                        entry_price = dca_price
                        qty = order_size / entry_price

                        open_positions.append({
                            "entry_price": entry_price,
                            "entry_time": ts,
                            "qty": qty,
                            "order_size": order_size,
                            "level": lvl,
                        })
                        filled_levels.add(lvl)

        # ─── Незакрытый цикл (позиции открыты) ───
        last_price = closes[-1] if closes else 0
        if open_positions:
            total_cost = sum(p["entry_price"] * p["qty"] for p in open_positions)
            total_qty = sum(p["qty"] for p in open_positions)
            breakeven = total_cost / total_qty if total_qty > 0 else 0
            total_invested = sum(p["order_size"] for p in open_positions)
            total_pnl = (last_price - breakeven) * total_qty
            pnl_pct = (last_price / breakeven - 1) * 100 if breakeven > 0 else 0

            cycle_data = {
                "entries": [],
                "exit_time": None,
                "exit_price": round(last_price, 6),
                "breakeven": round(breakeven, 6),
                "total_qty": total_qty,
                "total_invested": round(total_invested, 2),
                "total_pnl": round(total_pnl, 4),
                "pnl_pct": round(pnl_pct, 4),
                "dca_count": len(open_positions),
                "result": "open",
            }
            for p in open_positions:
                entry_data = {
                    "entry_time": p["entry_time"],
                    "entry_price": round(p["entry_price"], 6),
                    "qty": p["qty"],
                    "order_size": p["order_size"],
                    "level": p["level"],
                }
                if "calc" in p:
                    entry_data["calc"] = p["calc"]
                cycle_data["entries"].append(entry_data)
            cycles.append(cycle_data)

        # ══════════════════════════════════════════════════════
        # Формируем плоский список trades для совместимости с UI
        # ══════════════════════════════════════════════════════
        trades = []
        for cycle in cycles:
            for entry in cycle["entries"]:
                pnl_per_entry = (cycle["exit_price"] - entry["entry_price"]) * entry["qty"] if cycle["exit_time"] else (last_price - entry["entry_price"]) * entry["qty"]
                pnl_pct_entry = ((cycle["exit_price"] / entry["entry_price"]) - 1) * 100 if cycle["exit_time"] else ((last_price / entry["entry_price"]) - 1) * 100 if entry["entry_price"] > 0 else 0
                trade_data = {
                    "entry_time": entry["entry_time"],
                    "exit_time": cycle["exit_time"],
                    "entry_price": entry["entry_price"],
                    "exit_price": cycle["exit_price"],
                    "qty": entry["qty"],
                    "order_size": entry["order_size"],
                    "pnl": round(pnl_per_entry, 4),
                    "pnl_pct": round(pnl_pct_entry, 4),
                    "result": cycle["result"],
                    "level": entry["level"],
                    "dca_count": cycle["dca_count"],
                    "breakeven": cycle["breakeven"],
                }
                if "calc" in entry:
                    trade_data["calc"] = entry["calc"]
                trades.append(trade_data)

        # Статистика
        closed_cycles = [c for c in cycles if c["result"] != "open"]
        open_cycles = [c for c in cycles if c["result"] == "open"]
        closed_trades = [t for t in trades if t["result"] != "open"]
        open_trades_list = [t for t in trades if t["result"] == "open"]
        total_pnl = sum(c["total_pnl"] for c in cycles)
        closed_pnl = sum(c["total_pnl"] for c in closed_cycles)
        total_invested = sum(c["total_invested"] for c in cycles)
        roi = (total_pnl / total_invested * 100) if total_invested > 0 else 0
        avg_dca = sum(c["dca_count"] for c in cycles) / len(cycles) if cycles else 0

        # Маркеры для графика
        def fmt_price(p):
            """Умное форматирование цены: больше знаков для дешёвых монет."""
            if p >= 1000:
                return f"{p:,.2f}"
            elif p >= 1:
                return f"{p:.4f}"
            elif p >= 0.01:
                return f"{p:.6f}"
            elif p >= 0.0001:
                return f"{p:.8f}"
            elif p >= 0.000001:
                return f"{p:.10f}"
            elif p >= 0.00000001:
                return f"{p:.12f}"
            elif p >= 0.0000000001:
                return f"{p:.14f}"
            elif p >= 0.000000000001:
                return f"{p:.16f}"
            elif p >= 0.00000000000001:
                return f"{p:.18f}"
            else:
                return f"{p:.20f}"

        buy_markers = []
        sell_markers = []
        for t in trades:
            level_text = f"DCA-{t['level']}" if t["level"] > 1 else "BUY"
            buy_markers.append({
                "time": t["entry_time"],
                "position": "belowBar",
                "color": "#10b981",
                "shape": "arrowUp",
                "text": f"{level_text} ${fmt_price(t['entry_price'])}",
            })
        for cycle in cycles:
            if cycle["exit_time"] is not None:
                sell_markers.append({
                    "time": cycle["exit_time"],
                    "position": "aboveBar",
                    "color": "#10b981",
                    "shape": "arrowDown",
                    "text": f"SELL ${fmt_price(cycle['exit_price'])} (+${cycle['total_pnl']:.2f})",
                })

        stats = {
            "total_trades": len(trades),
            "total_cycles": len(cycles),
            "closed_cycles": len(closed_cycles),
            "open_cycles": len(open_cycles),
            "closed_trades": len(closed_trades),
            "open_trades": len(open_trades_list),
            "wins": len(closed_cycles),
            "losses": 0,
            "win_rate": 100.0 if closed_cycles else 0,
            "total_pnl": round(total_pnl, 4),
            "closed_pnl": round(closed_pnl, 4),
            "total_invested": round(total_invested, 2),
            "roi": round(roi, 2),
            "avg_pnl": round(closed_pnl / len(closed_cycles), 4) if closed_cycles else 0,
            "avg_dca_count": round(avg_dca, 1),
            "best_trade": round(max((c["total_pnl"] for c in closed_cycles), default=0), 4),
            "worst_trade": round(min((c["total_pnl"] for c in closed_cycles), default=0), 4),
        }

        # Buy point линия для графика
        # Сдвигаем на 1 бар вперёд: на баре i показываем buy_points[i-1],
        # т.к. сигнал на баре i проверяет low[i] <= buyPoint[i-1]
        bp_line = []
        for i in range(1, len(candles)):
            if buy_points[i - 1] is not None:
                bp_line.append({
                    "time": candles[i]["time"],
                    "value": round(buy_points[i - 1], 10),
                })

        return jsonify({
            "trades": trades,
            "cycles": cycles,
            "stats": stats,
            "buy_markers": buy_markers,
            "sell_markers": sell_markers,
            "buy_points": bp_line,
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/api/symbols")
def api_symbols():
    """Получить список торговых пар с Bybit."""
    search = request.args.get("search", "").upper()
    category = request.args.get("category", "spot")
    limit = int(request.args.get("limit", 50))

    try:
        session = HTTP(testnet=False)
        response = session.get_tickers(category=category)
        symbols_raw = response["result"]["list"]

        symbols = []
        for s in symbols_raw:
            sym = s["symbol"]
            price = s.get("lastPrice", "0")
            volume = float(s.get("turnover24h", 0))
            pct = s.get("price24hPcnt", "0")

            if search and search not in sym:
                continue

            symbols.append({
                "symbol": sym,
                "price": price,
                "volume24h": volume,
                "change24h": float(pct) * 100 if pct else 0,
            })

        # Сортируем по объёму (самые торгуемые сверху)
        symbols.sort(key=lambda x: x["volume24h"], reverse=True)

        return jsonify({"symbols": symbols[:limit]})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/indicator")
def api_indicator():
    """Получить данные индикатора для текущей пары."""
    symbol = request.args.get("symbol", config.SYMBOL)

    try:
        session = HTTP(testnet=False)
        response = session.get_kline(
            category="spot", symbol=symbol,
            interval=config.SIGNAL_TIMEFRAME,  # Таймфрейм из настроек
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
    port = int(os.environ.get("PORT", 5000))
    print("\n" + "=" * 50)
    print("  BYBIT DCA BOT — Web Dashboard")
    print(f"  http://localhost:{port}")
    print("=" * 50 + "\n")

    app.run(host="0.0.0.0", port=port, debug=False)
