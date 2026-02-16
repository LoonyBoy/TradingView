"""
Тесты для indicator.py — логика Average Range.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from indicator import (
    calculate_average_range,
    calculate_buy_point,
    check_buy_signal,
    get_indicator_data,
)


def test_average_range():
    """Тест расчёта среднего диапазона."""
    highs = [110, 120, 115, 125, 130]
    lows = [100, 105, 100, 110, 115]
    # Ranges: 10, 15, 15, 15, 15
    # Average (5 bars): (10+15+15+15+15)/5 = 14.0

    result = calculate_average_range(highs, lows, lookback=5)
    assert abs(result - 14.0) < 0.01, f"Expected 14.0, got {result}"
    print("✅ test_average_range PASSED")


def test_average_range_last_3():
    """Тест среднего диапазона за 3 бара."""
    highs = [110, 120, 115, 125, 130]
    lows = [100, 105, 100, 110, 115]
    # Last 3 bars: ranges = 15, 15, 15
    # Average: 15.0

    result = calculate_average_range(highs, lows, lookback=3)
    assert abs(result - 15.0) < 0.01, f"Expected 15.0, got {result}"
    print("✅ test_average_range_last_3 PASSED")


def test_buy_point():
    """Тест расчёта точки покупки."""
    closes = [100, 105, 110, 115, 120]
    highs = [105, 110, 115, 120, 125]
    lows = [95, 100, 105, 110, 115]
    # All ranges = 10, avg = 10
    # Buy point = close[-1] - avg_range = 120 - 10 = 110

    result = calculate_buy_point(closes, highs, lows, lookback=5)
    assert abs(result - 110.0) < 0.01, f"Expected 110.0, got {result}"
    print("✅ test_buy_point PASSED")


def test_buy_signal_triggered():
    """Тест: сигнал покупки есть (low <= prev buy_point)."""
    # 7 баров (lookback=5, +2 для сигнала)
    closes = [100, 102, 104, 106, 108, 110, 105]
    highs = [105, 107, 109, 111, 113, 115, 110]
    lows = [95, 97, 99, 101, 103, 105, 90]  # Last low = 90, very low
    # Prev bar (index 5): avg_range from bars 1-5 = avg of (10,10,10,10,10)=10
    # Prev buy_point = 110 - 10 = 100
    # Current low (index 6) = 90 <= 100 ✅

    is_signal, price = check_buy_signal(closes, highs, lows, lookback=5)
    assert is_signal, "Expected signal to be True"
    assert abs(price - 100.0) < 0.01, f"Expected price 100.0, got {price}"
    print("✅ test_buy_signal_triggered PASSED")


def test_buy_signal_not_triggered():
    """Тест: сигнала нет (low > prev buy_point)."""
    closes = [100, 102, 104, 106, 108, 110, 112]
    highs = [105, 107, 109, 111, 113, 115, 117]
    lows = [95, 97, 99, 101, 103, 105, 107]
    # Prev buy_point = 110 - 10 = 100
    # Current low = 107 > 100 ❌

    is_signal, price = check_buy_signal(closes, highs, lows, lookback=5)
    assert not is_signal, "Expected signal to be False"
    print("✅ test_buy_signal_not_triggered PASSED")


def test_get_indicator_data():
    """Тест комплексного вызова get_indicator_data."""
    closes = [100, 102, 104, 106, 108, 110, 105]
    highs = [105, 107, 109, 111, 113, 115, 110]
    lows = [95, 97, 99, 101, 103, 105, 90]

    data = get_indicator_data(closes, highs, lows, lookback=5)

    assert data["current_close"] == 105
    # Last 5 bars ranges: 10, 10, 10, 10, 20 → avg = 12.0
    assert abs(data["average_range"] - 12.0) < 0.01
    # buy_point = 105 - 12 = 93.0
    assert abs(data["buy_point"] - 93.0) < 0.01
    assert data["signal"] == True
    print("✅ test_get_indicator_data PASSED")


if __name__ == "__main__":
    test_average_range()
    test_average_range_last_3()
    test_buy_point()
    test_buy_signal_triggered()
    test_buy_signal_not_triggered()
    test_get_indicator_data()
    print("\n🎉 Все тесты indicator.py пройдены!")
