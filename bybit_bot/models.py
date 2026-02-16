"""
Модели данных для DCA Trading Bot.
"""

from dataclasses import dataclass, field
from typing import List, Optional
from datetime import datetime


@dataclass
class DCAEntry:
    """Одна запись DCA-входа."""
    level: int                      # Уровень DCA (1, 2, 3...)
    target_price: float             # Целевая цена входа
    entry_price: float = 0.0        # Фактическая цена входа (после исполнения)
    qty: float = 0.0                # Количество монет
    order_size_usd: float = 0.0     # Сумма в USD
    filled: bool = False            # Исполнен ли ордер
    order_id: Optional[str] = None  # ID ордера на бирже
    filled_at: Optional[datetime] = None  # Время исполнения

    def __repr__(self) -> str:
        status = "✅" if self.filled else "⏳"
        return (
            f"{status} DCA-{self.level}: "
            f"target=${self.target_price:.2f}, "
            f"filled=${self.entry_price:.2f}, "
            f"qty={self.qty:.6f}, "
            f"${self.order_size_usd:.2f}"
        )


@dataclass
class Position:
    """Позиция с несколькими DCA-входами."""
    symbol: str
    entries: List[DCAEntry] = field(default_factory=list)
    is_active: bool = False
    created_at: Optional[datetime] = None
    sell_order_id: Optional[str] = None

    @property
    def filled_entries(self) -> List[DCAEntry]:
        """Только исполненные входы."""
        return [e for e in self.entries if e.filled]

    @property
    def total_qty(self) -> float:
        """Общее количество купленных монет."""
        return sum(e.qty for e in self.filled_entries)

    @property
    def total_invested(self) -> float:
        """Общая сумма вложений в USD."""
        return sum(e.qty * e.entry_price for e in self.filled_entries)

    @property
    def breakeven(self) -> float:
        """
        Средневзвешенная цена (безубыточность).
        breakeven = Σ(qty_i × price_i) / Σ(qty_i)
        """
        if self.total_qty == 0:
            return 0.0
        return self.total_invested / self.total_qty

    def calculate_sell_target(self, sell_profit_pct: float) -> float:
        """
        Цена продажи = безубыточность × (1 + sell_profit_pct / 100).

        Args:
            sell_profit_pct: Процент прибыли от безубыточности.

        Returns:
            Целевая цена продажи.
        """
        be = self.breakeven
        if be == 0:
            return 0.0
        return be * (1 + sell_profit_pct / 100)

    @property
    def current_dca_level(self) -> int:
        """Текущий уровень DCA (количество исполненных входов)."""
        return len(self.filled_entries)

    def summary(self, sell_profit_pct: float) -> str:
        """Текстовое представление позиции."""
        lines = [
            f"═══ Позиция {self.symbol} ═══",
            f"Входов: {self.current_dca_level}/{len(self.entries)}",
            f"Всего монет: {self.total_qty:.6f}",
            f"Вложено: ${self.total_invested:.2f}",
            f"Безубыточность: ${self.breakeven:.2f}",
            f"Цель продажи ({sell_profit_pct}%): ${self.calculate_sell_target(sell_profit_pct):.2f}",
            "─── Входы ───",
        ]
        for entry in self.entries:
            lines.append(f"  {entry}")
        return "\n".join(lines)
