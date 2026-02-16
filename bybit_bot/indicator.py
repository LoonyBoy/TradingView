"""
Average Range Indicator — порт с Pine Script.

Исходный индикатор: average_range_indicator.pine
Логика:
  1. Средний диапазон = среднее(high - low) за N последних баров
  2. Точка покупки = close - средний_диапазон
  3. Сигнал покупки: low текущего бара <= buy_point предыдущего бара
"""

from typing import List, Tuple, Optional
import logging

logger = logging.getLogger(__name__)


def calculate_average_range(
    highs: List[float],
    lows: List[float],
    lookback: int
) -> float:
    """
    Рассчитать средний диапазон (high - low) за последние N баров.

    Args:
        highs: Список цен high (от старых к новым).
        lows: Список цен low (от старых к новым).
        lookback: Количество баров для расчёта.

    Returns:
        Средний диапазон.

    Raises:
        ValueError: Если недостаточно данных.
    """
    if len(highs) < lookback or len(lows) < lookback:
        raise ValueError(
            f"Недостаточно данных: нужно {lookback} баров, "
            f"получено highs={len(highs)}, lows={len(lows)}"
        )

    total = 0.0
    for i in range(lookback):
        idx = len(highs) - 1 - i  # Берём последние N баров
        total += highs[idx] - lows[idx]

    avg_range = total / lookback
    logger.debug(f"Average range ({lookback} bars): {avg_range:.6f}")
    return avg_range


def calculate_buy_point(
    closes: List[float],
    highs: List[float],
    lows: List[float],
    lookback: int
) -> float:
    """
    Рассчитать точку покупки: close - average_range.

    Args:
        closes: Список цен close (от старых к новым).
        highs: Список цен high.
        lows: Список цен low.
        lookback: Количество баров для расчёта.

    Returns:
        Цена точки покупки.
    """
    avg_range = calculate_average_range(highs, lows, lookback)
    current_close = closes[-1]
    buy_point = current_close - avg_range

    logger.debug(
        f"Buy point: close={current_close:.6f} - avg_range={avg_range:.6f} "
        f"= {buy_point:.6f}"
    )
    return buy_point


def check_buy_signal(
    closes: List[float],
    highs: List[float],
    lows: List[float],
    lookback: int
) -> Tuple[bool, float]:
    """
    Проверить наличие сигнала покупки.

    Логика (из Pine Script):
      Покупаем когда low текущего бара касается или пробивает вниз
      buy_point предыдущего бара.

    Для этого нужно минимум lookback + 2 баров:
      - lookback баров для расчёта buy_point предыдущего бара
      - 1 предыдущий бар (чей buy_point мы используем)
      - 1 текущий бар (чей low мы проверяем)

    Args:
        closes: Цены close (от старых к новым), минимум lookback + 1.
        highs: Цены high.
        lows: Цены low.
        lookback: Количество баров для расчёта.

    Returns:
        Кортеж (is_signal, buy_point_price):
          is_signal — True если есть сигнал покупки
          buy_point_price — цена buy_point предыдущего бара
    """
    if len(closes) < lookback + 2:
        logger.warning(
            f"Недостаточно данных для сигнала: нужно {lookback + 2}, "
            f"есть {len(closes)}"
        )
        return False, 0.0

    # Рассчитываем buy_point на ПРЕДЫДУЩЕМ баре
    # Для этого берём данные без последнего бара
    prev_closes = closes[:-1]
    prev_highs = highs[:-1]
    prev_lows = lows[:-1]

    prev_buy_point = calculate_buy_point(prev_closes, prev_highs, prev_lows, lookback)

    # Текущий low (последний бар)
    current_low = lows[-1]

    # Сигнал: low текущего бара <= buy_point предыдущего бара
    is_signal = current_low <= prev_buy_point

    logger.info(
        f"Signal check: current_low={current_low:.6f}, "
        f"prev_buy_point={prev_buy_point:.6f}, "
        f"signal={'YES ✅' if is_signal else 'NO ❌'}"
    )

    return is_signal, prev_buy_point


def get_indicator_data(
    closes: List[float],
    highs: List[float],
    lows: List[float],
    lookback: int
) -> dict:
    """
    Получить все данные индикатора одним вызовом.

    Returns:
        Словарь с данными:
          - average_range: средний диапазон
          - buy_point: текущая точка покупки
          - signal: есть ли сигнал покупки
          - signal_price: цена сигнала (buy_point предыдущего бара)
          - current_close: текущая цена закрытия
    """
    avg_range = calculate_average_range(highs, lows, lookback)
    buy_point = closes[-1] - avg_range
    is_signal, signal_price = check_buy_signal(closes, highs, lows, lookback)

    return {
        "average_range": avg_range,
        "buy_point": buy_point,
        "signal": is_signal,
        "signal_price": signal_price,
        "current_close": closes[-1],
    }
