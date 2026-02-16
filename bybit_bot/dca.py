"""
DCA (Dollar Cost Averaging) логика.

Управляет точками усреднения, расчётом безубыточности
и определением цены продажи.
"""

import logging
from typing import List, Optional
from datetime import datetime

from models import DCAEntry, Position
import config

logger = logging.getLogger(__name__)


def build_dca_entries(
    first_entry_price: float,
    dca_points: List[dict],
    total_balance: float
) -> List[DCAEntry]:
    """
    Построить список DCA-входов на основе цены первого входа.

    Каждая точка усреднения:
      target_price = first_entry_price × (1 - drop_pct / 100)
      order_size = total_balance × balance_pct / 100
      qty = order_size / target_price

    Args:
        first_entry_price: Цена первого входа (от индикатора).
        dca_points: Список точек DCA из конфига.
        total_balance: Общий баланс стратегии в USD.

    Returns:
        Список DCAEntry с рассчитанными целевыми ценами и количествами.
    """
    entries: List[DCAEntry] = []

    for point in dca_points:
        level = point["level"]
        drop_pct = point["drop_pct"]
        balance_pct = point["balance_pct"]

        target_price = first_entry_price * (1 - drop_pct / 100)
        order_size = total_balance * balance_pct / 100
        qty = order_size / target_price

        entry = DCAEntry(
            level=level,
            target_price=round(target_price, 8),
            order_size_usd=round(order_size, 2),
            qty=round(qty, 8),
        )
        entries.append(entry)

        logger.info(
            f"DCA-{level}: drop={drop_pct}%, target=${target_price:.2f}, "
            f"size=${order_size:.2f} ({balance_pct}%), qty={qty:.8f}"
        )

    return entries


def create_position(
    symbol: str,
    first_entry_price: float,
    dca_points: List[dict] = None,
    total_balance: float = None
) -> Position:
    """
    Создать новую позицию с DCA-входами.

    Args:
        symbol: Торговая пара (напр. BTCUSDT).
        first_entry_price: Цена первого входа.
        dca_points: Точки DCA (по умолчанию из конфига).
        total_balance: Общий баланс (по умолчанию из конфига).

    Returns:
        Объект Position с подготовленными DCA-входами.
    """
    if dca_points is None:
        dca_points = config.DCA_POINTS
    if total_balance is None:
        total_balance = config.TOTAL_BALANCE

    # Валидация: сумма balance_pct не должна превышать 100%
    total_pct = sum(p["balance_pct"] for p in dca_points)
    if total_pct > 100:
        logger.warning(
            f"⚠️ Сумма balance_pct = {total_pct}% > 100%! "
            f"Уменьшите значения."
        )

    entries = build_dca_entries(first_entry_price, dca_points, total_balance)

    position = Position(
        symbol=symbol,
        entries=entries,
        is_active=True,
        created_at=datetime.now(),
    )

    logger.info(f"Создана позиция {symbol} с {len(entries)} DCA-уровнями")
    return position


def fill_entry(position: Position, level: int, actual_price: float) -> None:
    """
    Отметить DCA-вход как исполненный.

    После исполнения пересчитывается количество монет на основе
    фактической цены (а не целевой).

    Args:
        position: Позиция.
        level: Уровень DCA.
        actual_price: Фактическая цена покупки.
    """
    for entry in position.entries:
        if entry.level == level and not entry.filled:
            entry.entry_price = actual_price
            entry.qty = entry.order_size_usd / actual_price
            entry.filled = True
            entry.filled_at = datetime.now()

            logger.info(
                f"✅ DCA-{level} исполнен: "
                f"price=${actual_price:.2f}, qty={entry.qty:.8f}"
            )
            logger.info(
                f"📊 Безубыточность: ${position.breakeven:.2f}, "
                f"Цель продажи: "
                f"${position.calculate_sell_target(config.SELL_PROFIT_PCT):.2f}"
            )
            return

    logger.warning(f"DCA-{level} не найден или уже исполнен")


def check_dca_triggers(
    position: Position,
    current_price: float
) -> List[DCAEntry]:
    """
    Проверить, какие DCA-уровни должны сработать по текущей цене.

    Args:
        position: Текущая позиция.
        current_price: Текущая цена актива.

    Returns:
        Список DCA-входов, которые нужно исполнить.
    """
    triggered: List[DCAEntry] = []

    for entry in position.entries:
        if not entry.filled and current_price <= entry.target_price:
            triggered.append(entry)
            logger.info(
                f"🎯 DCA-{entry.level} triggered: "
                f"price=${current_price:.2f} <= "
                f"target=${entry.target_price:.2f}"
            )

    return triggered


def check_sell_signal(
    position: Position,
    current_price: float,
    sell_profit_pct: float = None
) -> bool:
    """
    Проверить, достигнута ли цель продажи.

    Продажа происходит когда текущая цена >= sell_target
    (безубыточность + sell_profit_pct%).

    Args:
        position: Текущая позиция.
        current_price: Текущая цена актива.
        sell_profit_pct: % прибыли от безубыточности.

    Returns:
        True если нужно продавать.
    """
    if sell_profit_pct is None:
        sell_profit_pct = config.SELL_PROFIT_PCT

    if position.current_dca_level == 0:
        return False

    sell_target = position.calculate_sell_target(sell_profit_pct)

    if current_price >= sell_target:
        logger.info(
            f"💰 SELL SIGNAL: price=${current_price:.2f} >= "
            f"target=${sell_target:.2f} "
            f"(breakeven ${position.breakeven:.2f} + {sell_profit_pct}%)"
        )
        return True

    return False


def get_position_report(position: Position) -> str:
    """
    Получить отчёт по текущей позиции.

    Returns:
        Форматированная строка с информацией о позиции.
    """
    sell_target = position.calculate_sell_target(config.SELL_PROFIT_PCT)
    lines = [
        "",
        "╔══════════════════════════════════════════╗",
        f"║  Позиция: {position.symbol:<30}║",
        "╠══════════════════════════════════════════╣",
        f"║  Входов: {position.current_dca_level}/{len(position.entries):<31}║",
        f"║  Монет: {position.total_qty:<32.8f}║",
        f"║  Вложено: ${position.total_invested:<29.2f}║",
        f"║  Безубыточность: ${position.breakeven:<22.2f}║",
        f"║  Цель продажи: ${sell_target:<24.2f}║",
        "╠══════════════════════════════════════════╣",
    ]

    for entry in position.entries:
        status = "✅" if entry.filled else "⏳"
        price = entry.entry_price if entry.filled else entry.target_price
        lines.append(
            f"║  {status} DCA-{entry.level}: "
            f"${price:<8.2f} | "
            f"${entry.order_size_usd:<8.2f} | "
            f"{entry.qty:<12.8f}║"
        )

    lines.append("╚══════════════════════════════════════════╝")
    lines.append("")

    return "\n".join(lines)
