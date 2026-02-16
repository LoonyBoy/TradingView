"""
Демо-скрипт для проверки индикатора Average Range.

Загружает реальные дневные свечи с Bybit и показывает:
- Средний диапазон
- Точку покупки
- Сигнал покупки

Сравни значения с TradingView!
API ключи НЕ нужны (публичные данные).
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pybit.unified_trading import HTTP
from indicator import get_indicator_data, calculate_average_range, calculate_buy_point
from datetime import datetime


def main():
    # === НАСТРОЙКИ ===
    SYMBOL = "BTCUSDT"       # Меняй на любую пару
    TIMEFRAME = "D"          # Дневной таймфрейм
    LOOKBACK = 5             # Как в Pine Script
    LIMIT = 20               # Сколько свечей загрузить

    print(f"\n{'═' * 60}")
    print(f"  AVERAGE RANGE INDICATOR — DEMO")
    print(f"  Пара: {SYMBOL} | Таймфрейм: {TIMEFRAME} | Lookback: {LOOKBACK}")
    print(f"{'═' * 60}\n")

    # Загружаем свечи (API ключи не нужны)
    session = HTTP(testnet=False)
    response = session.get_kline(
        category="spot",
        symbol=SYMBOL,
        interval=TIMEFRAME,
        limit=LIMIT,
    )

    klines = response["result"]["list"]
    klines.reverse()  # От старых к новым

    # Парсим данные
    timestamps = []
    opens = []
    highs = []
    lows = []
    closes = []

    for k in klines:
        ts = int(k[0]) / 1000
        timestamps.append(datetime.fromtimestamp(ts).strftime("%Y-%m-%d"))
        opens.append(float(k[1]))
        highs.append(float(k[2]))
        lows.append(float(k[3]))
        closes.append(float(k[4]))

    # Показываем последние свечи
    print(f"{'Дата':<12} {'Open':>10} {'High':>10} {'Low':>10} {'Close':>10} {'Range':>10}")
    print(f"{'─' * 62}")

    for i in range(len(timestamps)):
        rng = highs[i] - lows[i]
        print(f"{timestamps[i]:<12} {opens[i]:>10.2f} {highs[i]:>10.2f} "
              f"{lows[i]:>10.2f} {closes[i]:>10.2f} {rng:>10.2f}")

    # Рассчитываем индикатор
    print(f"\n{'═' * 60}")
    print(f"  РЕЗУЛЬТАТЫ ИНДИКАТОРА")
    print(f"{'═' * 60}\n")

    indicator = get_indicator_data(closes, highs, lows, LOOKBACK)

    print(f"  Текущая цена закрытия:  ${indicator['current_close']:,.2f}")
    print(f"  Средний диапазон ({LOOKBACK}):   ${indicator['average_range']:,.2f}")
    print(f"  Точка покупки:          ${indicator['buy_point']:,.2f}")
    print(f"  Сигнал покупки:         {'✅ ДА' if indicator['signal'] else '❌ НЕТ'}")

    if indicator['signal']:
        print(f"  Цена сигнала:           ${indicator['signal_price']:,.2f}")

    # Показываем расчёт по последним N барам
    print(f"\n{'─' * 60}")
    print(f"  Расчёт среднего диапазона (последние {LOOKBACK} баров):")
    total = 0
    for i in range(LOOKBACK):
        idx = len(highs) - 1 - i
        rng = highs[idx] - lows[idx]
        total += rng
        print(f"    {timestamps[idx]}: high={highs[idx]:.2f} - low={lows[idx]:.2f} = {rng:.2f}")
    print(f"    Среднее: {total}/{LOOKBACK} = {total/LOOKBACK:.2f}")

    print(f"\n  📊 Сравни эти значения с TradingView!")
    print(f"     Открой {SYMBOL} на дневном графике и добавь индикатор")
    print(f"     'Average Range Strategy' — значения должны совпадать.\n")


if __name__ == "__main__":
    main()
