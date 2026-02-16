"""
Тесты для dca.py — DCA логика и расчёт безубыточности.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dca import build_dca_entries, create_position, fill_entry, check_sell_signal
from models import Position, DCAEntry


def test_build_dca_entries():
    """Тест построения DCA-входов."""
    first_price = 100.0
    total_balance = 1000.0
    dca_points = [
        {"level": 1, "drop_pct": 0.0, "balance_pct": 20.0},
        {"level": 2, "drop_pct": 5.0, "balance_pct": 30.0},
        {"level": 3, "drop_pct": 10.0, "balance_pct": 50.0},
    ]

    entries = build_dca_entries(first_price, dca_points, total_balance)

    assert len(entries) == 3

    # DCA-1: target = 100 * (1 - 0/100) = 100, size = 1000 * 20/100 = 200
    assert abs(entries[0].target_price - 100.0) < 0.01
    assert abs(entries[0].order_size_usd - 200.0) < 0.01
    assert abs(entries[0].qty - 2.0) < 0.01  # 200 / 100

    # DCA-2: target = 100 * (1 - 5/100) = 95, size = 300
    assert abs(entries[1].target_price - 95.0) < 0.01
    assert abs(entries[1].order_size_usd - 300.0) < 0.01
    assert abs(entries[1].qty - 300.0 / 95.0) < 0.01

    # DCA-3: target = 100 * (1 - 10/100) = 90, size = 500
    assert abs(entries[2].target_price - 90.0) < 0.01
    assert abs(entries[2].order_size_usd - 500.0) < 0.01
    assert abs(entries[2].qty - 500.0 / 90.0) < 0.01

    print("✅ test_build_dca_entries PASSED")


def test_breakeven_single_entry():
    """Тест безубыточности с одним входом."""
    position = Position(symbol="BTCUSDT")
    position.entries = [
        DCAEntry(level=1, target_price=100, entry_price=100, qty=2.0,
                 order_size_usd=200, filled=True),
    ]

    # Breakeven = (2 * 100) / 2 = 100
    assert abs(position.breakeven - 100.0) < 0.01
    print("✅ test_breakeven_single_entry PASSED")


def test_breakeven_multiple_entries():
    """Тест безубыточности с несколькими входами."""
    position = Position(symbol="BTCUSDT")
    position.entries = [
        DCAEntry(level=1, target_price=100, entry_price=100, qty=2.0,
                 order_size_usd=200, filled=True),
        DCAEntry(level=2, target_price=95, entry_price=95, qty=3.0,
                 order_size_usd=285, filled=True),
    ]

    # Breakeven = (2*100 + 3*95) / (2+3) = (200+285) / 5 = 97.0
    assert abs(position.breakeven - 97.0) < 0.01
    print("✅ test_breakeven_multiple_entries PASSED")


def test_breakeven_three_entries():
    """Тест безубыточности с тремя входами (более сложный)."""
    position = Position(symbol="BTCUSDT")
    position.entries = [
        DCAEntry(level=1, target_price=100, entry_price=100, qty=1.0,
                 order_size_usd=100, filled=True),
        DCAEntry(level=2, target_price=90, entry_price=90, qty=2.0,
                 order_size_usd=180, filled=True),
        DCAEntry(level=3, target_price=80, entry_price=80, qty=3.0,
                 order_size_usd=240, filled=True),
    ]

    # Breakeven = (1*100 + 2*90 + 3*80) / (1+2+3) = (100+180+240) / 6 = 86.67
    expected = (100 + 180 + 240) / 6
    assert abs(position.breakeven - expected) < 0.01, \
        f"Expected {expected}, got {position.breakeven}"
    print("✅ test_breakeven_three_entries PASSED")


def test_sell_target():
    """Тест расчёта цены продажи."""
    position = Position(symbol="BTCUSDT")
    position.entries = [
        DCAEntry(level=1, target_price=100, entry_price=100, qty=2.0,
                 order_size_usd=200, filled=True),
        DCAEntry(level=2, target_price=90, entry_price=90, qty=3.0,
                 order_size_usd=270, filled=True),
    ]

    # Breakeven = (200 + 270) / (2+3) = 94.0
    # Sell target (1%) = 94 * 1.01 = 94.94
    sell_target = position.calculate_sell_target(1.0)
    expected_be = (200 + 270) / 5
    expected_sell = expected_be * 1.01

    assert abs(sell_target - expected_sell) < 0.01, \
        f"Expected {expected_sell}, got {sell_target}"
    print("✅ test_sell_target PASSED")


def test_fill_entry_updates_breakeven():
    """Тест: fill_entry обновляет безубыточность."""
    dca_points = [
        {"level": 1, "drop_pct": 0.0, "balance_pct": 50.0},
        {"level": 2, "drop_pct": 10.0, "balance_pct": 50.0},
    ]

    position = create_position("BTCUSDT", 100.0, dca_points, 1000.0)

    # Заполняем первый вход
    fill_entry(position, 1, 100.0)
    assert position.current_dca_level == 1
    # qty = 500 / 100 = 5.0
    assert abs(position.breakeven - 100.0) < 0.01

    # Заполняем второй вход
    fill_entry(position, 2, 90.0)
    assert position.current_dca_level == 2
    # entry2: qty = 500 / 90 = 5.555...
    # breakeven = (5*100 + 5.555*90) / (5+5.555) = (500 + 500) / 10.555 ≈ 94.74
    expected_be = (5.0 * 100 + (500 / 90) * 90) / (5.0 + 500 / 90)
    assert abs(position.breakeven - expected_be) < 0.1, \
        f"Expected ~{expected_be:.2f}, got {position.breakeven:.2f}"
    print("✅ test_fill_entry_updates_breakeven PASSED")


def test_sell_signal():
    """Тест проверки сигнала продажи."""
    position = Position(symbol="BTCUSDT")
    position.entries = [
        DCAEntry(level=1, target_price=100, entry_price=100, qty=5.0,
                 order_size_usd=500, filled=True),
    ]
    # Breakeven = 100, sell target (1%) = 101

    assert not check_sell_signal(position, 100.0, 1.0)  # Ниже
    assert not check_sell_signal(position, 100.5, 1.0)  # Ниже
    assert check_sell_signal(position, 101.0, 1.0)       # Равно
    assert check_sell_signal(position, 105.0, 1.0)       # Выше
    print("✅ test_sell_signal PASSED")


if __name__ == "__main__":
    test_build_dca_entries()
    test_breakeven_single_entry()
    test_breakeven_multiple_entries()
    test_breakeven_three_entries()
    test_sell_target()
    test_fill_entry_updates_breakeven()
    test_sell_signal()
    print("\n🎉 Все тесты dca.py пройдены!")
