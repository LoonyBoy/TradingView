"""
Bybit DCA Trading Bot — Average Range Strategy.

Главный модуль бота. Запускает цикл мониторинга,
получает свечи, рассчитывает сигналы, управляет DCA-позицией.

Использование:
    python bot.py
"""

import sys
import os
import time
import signal
import logging
from datetime import datetime
from typing import Optional

# Добавляем текущую директорию в путь
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config
from bybit_client import BybitClient
from indicator import get_indicator_data
from dca import (
    create_position,
    fill_entry,
    check_dca_triggers,
    check_sell_signal,
    get_position_report,
)
from models import Position
from utils import (
    setup_logging,
    save_state,
    load_state,
    clear_state,
    serialize_position,
    deserialize_position,
    round_to_step,
)

logger = logging.getLogger(__name__)

# Глобальный флаг для graceful shutdown
running = True


def signal_handler(signum, frame):
    """Обработчик сигнала для корректной остановки."""
    global running
    logger.info("🛑 Получен сигнал остановки...")
    running = False


class TradingBot:
    """Основной класс торгового бота."""

    def __init__(self):
        """Инициализация бота."""
        self.client: Optional[BybitClient] = None
        self.position: Optional[Position] = None
        self.instrument_info: dict = {}

        # Параметры из конфига
        self.symbol = config.SYMBOL
        self.timeframe = config.TIMEFRAME
        self.lookback = config.LOOKBACK_PERIOD
        self.check_interval = config.CHECK_INTERVAL_SECONDS

    def start(self):
        """Запуск бота."""
        self._print_banner()

        # Инициализация клиента
        self.client = BybitClient()

        # Получение информации об инструменте
        self._load_instrument_info()

        # Загрузка сохранённого состояния
        self._restore_state()

        # Главный цикл
        logger.info("🚀 Бот запущен. Нажмите Ctrl+C для остановки.")
        self._main_loop()

    def _print_banner(self):
        """Вывести баннер при запуске."""
        banner = f"""
╔══════════════════════════════════════════════════╗
║      BYBIT DCA BOT — Average Range Strategy      ║
╠══════════════════════════════════════════════════╣
║  Пара:         {self.symbol:<33}║
║  Таймфрейм:    {self.timeframe:<33}║
║  Lookback:     {self.lookback:<33}║
║  Баланс:       ${config.TOTAL_BALANCE:<32}║
║  DCA точек:    {len(config.DCA_POINTS):<33}║
║  Тестнет:      {str(config.BYBIT_TESTNET):<33}║
╚══════════════════════════════════════════════════╝
"""
        print(banner)
        logger.info(f"Конфигурация: {self.symbol}, TF={self.timeframe}, "
                     f"Balance=${config.TOTAL_BALANCE}, "
                     f"DCA={len(config.DCA_POINTS)} levels")

    def _load_instrument_info(self):
        """Загрузить информацию об инструменте."""
        try:
            self.instrument_info = self.client.get_instrument_info(self.symbol)
            logger.info(
                f"Инструмент {self.symbol}: "
                f"min_qty={self.instrument_info['min_qty']}, "
                f"qty_step={self.instrument_info['qty_step']}, "
                f"price_step={self.instrument_info['price_step']}"
            )
        except Exception as e:
            logger.error(f"Не удалось получить инструмент: {e}")
            logger.warning("Используем приблизительные параметры")
            self.instrument_info = {
                "min_qty": 0.00001,
                "qty_step": 0.00001,
                "price_step": 0.01,
                "min_order_amt": 1.0,
            }

    def _restore_state(self):
        """Восстановить состояние из файла."""
        state = load_state()
        if state and state.get("position"):
            position_data = state["position"]
            if position_data.get("is_active") and position_data.get("symbol") == self.symbol:
                self.position = deserialize_position(position_data)
                logger.info(
                    f"♻️ Восстановлена позиция: "
                    f"{self.position.current_dca_level} входов, "
                    f"breakeven=${self.position.breakeven:.2f}"
                )
                print(get_position_report(self.position))
            else:
                logger.info("Сохранённая позиция неактивна или для другой пары")
        else:
            logger.info("Нет сохранённого состояния, начинаем с чистого листа")

    def _save_current_state(self):
        """Сохранить текущее состояние."""
        if self.position:
            save_state(serialize_position(self.position))
        else:
            save_state({})

    def _adjust_qty(self, qty: float) -> float:
        """Округлить количество до шага инструмента."""
        step = self.instrument_info.get("qty_step", 0.00001)
        return round_to_step(qty, step)

    def _adjust_price(self, price: float) -> float:
        """Округлить цену до шага инструмента."""
        step = self.instrument_info.get("price_step", 0.01)
        return round_to_step(price, step)

    def _main_loop(self):
        """Главный цикл бота."""
        global running

        while running:
            try:
                self._tick()
            except KeyboardInterrupt:
                running = False
                break
            except Exception as e:
                logger.error(f"Ошибка в главном цикле: {e}", exc_info=True)

            # Ждём следующую проверку
            if running:
                logger.debug(
                    f"Следующая проверка через {self.check_interval}с..."
                )
                for _ in range(self.check_interval):
                    if not running:
                        break
                    time.sleep(1)

        # Сохраняем состояние при выходе
        self._save_current_state()
        logger.info("👋 Бот остановлен. Состояние сохранено.")

    def _tick(self):
        """Один цикл проверки рынка."""
        logger.info(f"{'═' * 50}")
        logger.info(f"⏰ Проверка: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

        # Текущая цена — нужна всегда
        current_price = self.client.get_current_price(self.symbol)
        logger.info(f"💲 Текущая цена: ${current_price:.2f}")

        if self.position is None or not self.position.is_active:
            # ── НЕТ ПОЗИЦИИ: проверяем сигнал по ДНЕВНЫМ свечам ──
            # Сигнал покупки определяется ТОЛЬКО по дневному ТФ (5 баров)
            klines = self.client.get_klines(
                symbol=self.symbol,
                interval=config.SIGNAL_TIMEFRAME,  # Таймфрейм из настроек
                limit=self.lookback + 10,
            )

            closes = klines["closes"]
            highs = klines["highs"]
            lows = klines["lows"]

            indicator = get_indicator_data(closes, highs, lows, self.lookback)

            logger.info(
                f"📊 Индикатор (дневной): close=${indicator['current_close']:.2f}, "
                f"avg_range=${indicator['average_range']:.2f}, "
                f"buy_point=${indicator['buy_point']:.2f}, "
                f"signal={'YES' if indicator['signal'] else 'NO'}"
            )

            self._handle_no_position(indicator, current_price)
        else:
            # ── ЕСТЬ ПОЗИЦИЯ: DCA и продажа по текущей цене (каждые 60 сек) ──
            # Дневные свечи НЕ запрашиваются — DCA/sell работают в реальном времени
            self._handle_active_position(current_price)

    def _handle_no_position(self, indicator: dict, current_price: float):
        """Обработка: нет открытой позиции."""
        if indicator["signal"]:
            logger.info("🟢 СИГНАЛ ПОКУПКИ! Создаём позицию с DCA...")

            # Используем buy_point как первую цену входа
            first_price = indicator["signal_price"]

            # Создаём позицию с DCA-уровнями
            self.position = create_position(
                symbol=self.symbol,
                first_entry_price=first_price,
            )

            # Первый вход — покупаем по рынку (сигнал уже есть)
            first_entry = self.position.entries[0]
            qty = self._adjust_qty(first_entry.qty)

            if qty > 0:
                order_id = self.client.place_limit_buy(
                    symbol=self.symbol,
                    qty=qty,
                    price=self._adjust_price(first_price),
                    order_link_id=f"dca_{self.symbol}_1",
                )

                if order_id:
                    first_entry.order_id = order_id
                    # Для простоты сразу считаем исполненным по target_price
                    fill_entry(self.position, 1, first_price)

                    # Выставляем DCA-ордера
                    self._place_dca_orders()

                    # Выставляем ордер на продажу
                    self._place_sell_order()

                    # Сохраняем состояние
                    self._save_current_state()
                    print(get_position_report(self.position))
                else:
                    logger.error("Не удалось создать первый ордер")
                    self.position = None
        else:
            logger.info("⏳ Ожидание сигнала покупки...")

    def _handle_active_position(self, current_price: float):
        """Обработка: есть активная позиция."""
        logger.info(
            f"📈 Позиция активна: "
            f"{self.position.current_dca_level}/{len(self.position.entries)} входов, "
            f"breakeven=${self.position.breakeven:.2f}"
        )

        # Проверяем исполнение DCA-ордеров
        self._check_dca_fills()

        # Проверяем триггеры DCA по текущей цене
        triggered = check_dca_triggers(self.position, current_price)
        for entry in triggered:
            if entry.order_id is None:
                # Ордер ещё не выставлен — выставляем
                qty = self._adjust_qty(entry.qty)
                price = self._adjust_price(entry.target_price)

                order_id = self.client.place_limit_buy(
                    symbol=self.symbol,
                    qty=qty,
                    price=price,
                    order_link_id=f"dca_{self.symbol}_{entry.level}",
                )

                if order_id:
                    entry.order_id = order_id

        # Проверяем продажу (используем TP% текущего уровня)
        current_tp = self._get_current_tp_pct()
        if check_sell_signal(self.position, current_price, current_tp):
            self._execute_sell(current_price)
        else:
            sell_target = self.position.calculate_sell_target(current_tp)
            diff_pct = ((current_price - self.position.breakeven) /
                        self.position.breakeven * 100) if self.position.breakeven > 0 else 0
            logger.info(
                f"📊 До безубыточности: {diff_pct:+.2f}%, "
                f"до продажи: ${sell_target:.2f} (TP {current_tp}%)"
            )

        self._save_current_state()

    def _get_current_tp_pct(self) -> float:
        """Получить TP% текущего максимального заполненного уровня."""
        if not self.position or not self.position.filled_entries:
            return 1.0
        max_level = max(e.level for e in self.position.filled_entries)
        for dp in config.DCA_POINTS:
            if dp["level"] == max_level:
                return float(dp.get("tp_pct", 1.0))
        return 1.0

    def _place_dca_orders(self):
        """Выставить лимитные ордера для всех DCA-уровней."""
        for entry in self.position.entries:
            if entry.level == 1:  # Первый уже исполнен
                continue
            if entry.filled or entry.order_id is not None:
                continue

            qty = self._adjust_qty(entry.qty)
            price = self._adjust_price(entry.target_price)

            order_id = self.client.place_limit_buy(
                symbol=self.symbol,
                qty=qty,
                price=price,
                order_link_id=f"dca_{self.symbol}_{entry.level}",
            )

            if order_id:
                entry.order_id = order_id
                logger.info(
                    f"📥 DCA-{entry.level} ордер: "
                    f"${price:.2f}, qty={qty}"
                )

    def _check_dca_fills(self):
        """Проверить исполнение DCA-ордеров на бирже."""
        updated = False

        for entry in self.position.entries:
            if entry.filled or entry.order_id is None:
                continue

            status = self.client.get_order_status(self.symbol, entry.order_id)

            if status.get("status") == "Filled":
                avg_price = status.get("avg_price", entry.target_price)
                if avg_price == 0:
                    avg_price = entry.target_price

                fill_entry(self.position, entry.level, avg_price)
                updated = True

                logger.info(
                    f"✅ DCA-{entry.level} исполнен на бирже: "
                    f"${avg_price:.2f}"
                )

        if updated:
            # Пересчитываем и обновляем ордер на продажу
            self._update_sell_order()
            print(get_position_report(self.position))

    def _place_sell_order(self):
        """Выставить ордер на продажу по цели."""
        if self.position.total_qty == 0:
            return

        sell_target = self.position.calculate_sell_target(self._get_current_tp_pct())
        total_qty = self._adjust_qty(self.position.total_qty)
        sell_price = self._adjust_price(sell_target)

        if total_qty <= 0:
            return

        order_id = self.client.place_limit_sell(
            symbol=self.symbol,
            qty=total_qty,
            price=sell_price,
            order_link_id=f"sell_{self.symbol}",
        )

        if order_id:
            self.position.sell_order_id = order_id
            current_tp = self._get_current_tp_pct()
            logger.info(
                f"📤 SELL ордер: ${sell_price:.2f}, "
                f"qty={total_qty} "
                f"(breakeven ${self.position.breakeven:.2f} + "
                f"{current_tp}%)"
            )

    def _update_sell_order(self):
        """Обновить ордер на продажу (отменить старый, выставить новый)."""
        # Отменяем старый ордер на продажу
        if self.position.sell_order_id:
            self.client.cancel_order(self.symbol, self.position.sell_order_id)
            self.position.sell_order_id = None

        # Выставляем новый
        self._place_sell_order()

    def _execute_sell(self, current_price: float):
        """Продать всю позицию."""
        total_qty = self._adjust_qty(self.position.total_qty)

        if total_qty <= 0:
            logger.warning("Нечего продавать")
            return

        logger.info(
            f"💰 ПРОДАЖА! Цена ${current_price:.2f}, "
            f"qty={total_qty}, "
            f"breakeven=${self.position.breakeven:.2f}"
        )

        # Отменяем все ордера
        self.client.cancel_all_orders(self.symbol)

        # Продаём по рынку
        order_id = self.client.place_market_sell(
            symbol=self.symbol,
            qty=total_qty,
            order_link_id=f"sell_market_{self.symbol}",
        )

        if order_id:
            profit = (current_price - self.position.breakeven) * total_qty
            logger.info(
                f"✅ ПОЗИЦИЯ ЗАКРЫТА! "
                f"Прибыль: ~${profit:.2f}"
            )

            # Сбрасываем позицию
            self.position.is_active = False
            self.position = None
            clear_state()
        else:
            logger.error("Не удалось продать позицию!")


def main():
    """Точка входа."""
    # Настраиваем логирование
    setup_logging()

    # Обработчик сигналов для корректной остановки
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Валидация конфига
    if not config.BYBIT_API_KEY or not config.BYBIT_API_SECRET:
        logger.warning(
            "⚠️ API ключи не указаны в config.py! "
            "Бот не сможет торговать."
        )
        if not config.BYBIT_TESTNET:
            logger.error("ОСТАНОВКА: нельзя запустить production без API ключей")
            sys.exit(1)

    total_pct = sum(p["balance_pct"] for p in config.DCA_POINTS)
    if total_pct > 100:
        logger.error(
            f"Сумма balance_pct = {total_pct}% > 100%! "
            f"Проверьте DCA_POINTS в config.py"
        )
        sys.exit(1)

    logger.info(f"DCA: {len(config.DCA_POINTS)} точек, "
                f"суммарно {total_pct}% от баланса")

    # Запускаем бота
    bot = TradingBot()
    bot.start()


if __name__ == "__main__":
    main()
