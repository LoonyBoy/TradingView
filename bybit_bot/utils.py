"""
Утилиты: логирование, форматирование, работа с состоянием.
"""

import json
import logging
import os
from datetime import datetime
from typing import Optional

import config


def setup_logging(
    log_file: str = None,
    log_level: str = None
) -> logging.Logger:
    """
    Настроить логирование для бота.

    Args:
        log_file: Путь к файлу логов.
        log_level: Уровень логирования.

    Returns:
        Корневой логгер.
    """
    if log_file is None:
        log_file = config.LOG_FILE
    if log_level is None:
        log_level = config.LOG_LEVEL

    logger = logging.getLogger()
    logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))

    # Формат логов
    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)-15s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    # Консоль
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(fmt)
    logger.addHandler(console_handler)

    # Файл
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)

    return logger


# ==================== СОХРАНЕНИЕ / ЗАГРУЗКА СОСТОЯНИЯ ====================

STATE_FILE = "bot_state.json"


def save_state(position_data: dict, extra: dict = None) -> None:
    """
    Сохранить состояние бота в JSON файл.

    Args:
        position_data: Данные текущей позиции.
        extra: Дополнительные данные.
    """
    state = {
        "timestamp": datetime.now().isoformat(),
        "position": position_data,
    }
    if extra:
        state.update(extra)

    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False, default=str)

    logging.getLogger(__name__).debug(f"Состояние сохранено в {STATE_FILE}")


def load_state() -> Optional[dict]:
    """
    Загрузить состояние бота из JSON файла.

    Returns:
        Словарь с состоянием или None.
    """
    if not os.path.exists(STATE_FILE):
        return None

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            state = json.load(f)
        logging.getLogger(__name__).debug(f"Состояние загружено из {STATE_FILE}")
        return state
    except Exception as e:
        logging.getLogger(__name__).error(f"Ошибка загрузки состояния: {e}")
        return None


def clear_state() -> None:
    """Удалить файл состояния."""
    if os.path.exists(STATE_FILE):
        os.remove(STATE_FILE)
        logging.getLogger(__name__).info("Состояние очищено")


# ==================== ФОРМАТИРОВАНИЕ ====================

def format_price(price: float, decimals: int = 2) -> str:
    """Форматировать цену."""
    return f"${price:,.{decimals}f}"


def format_qty(qty: float, decimals: int = 8) -> str:
    """Форматировать количество монет."""
    return f"{qty:.{decimals}f}"


def format_pct(pct: float) -> str:
    """Форматировать проценты."""
    return f"{pct:+.2f}%"


def round_to_step(value: float, step: float) -> float:
    """
    Округлить значение до шага (для Bybit min qty/price step).

    Args:
        value: Исходное значение.
        step: Шаг округления.

    Returns:
        Округлённое значение.
    """
    if step == 0:
        return value
    return round(round(value / step) * step, 10)


def serialize_position(position) -> dict:
    """
    Сериализовать позицию в словарь для сохранения.

    Args:
        position: Объект Position.

    Returns:
        Словарь с данными позиции.
    """
    return {
        "symbol": position.symbol,
        "is_active": position.is_active,
        "created_at": str(position.created_at) if position.created_at else None,
        "sell_order_id": position.sell_order_id,
        "entries": [
            {
                "level": e.level,
                "target_price": e.target_price,
                "entry_price": e.entry_price,
                "qty": e.qty,
                "order_size_usd": e.order_size_usd,
                "filled": e.filled,
                "order_id": e.order_id,
                "filled_at": str(e.filled_at) if e.filled_at else None,
            }
            for e in position.entries
        ],
    }


def deserialize_position(data: dict):
    """
    Десериализовать позицию из словаря.

    Args:
        data: Словарь с данными позиции.

    Returns:
        Объект Position.
    """
    from models import Position, DCAEntry

    entries = []
    for e_data in data.get("entries", []):
        entry = DCAEntry(
            level=e_data["level"],
            target_price=e_data["target_price"],
            entry_price=e_data.get("entry_price", 0.0),
            qty=e_data.get("qty", 0.0),
            order_size_usd=e_data.get("order_size_usd", 0.0),
            filled=e_data.get("filled", False),
            order_id=e_data.get("order_id"),
            filled_at=None,  # Simplified
        )
        entries.append(entry)

    position = Position(
        symbol=data["symbol"],
        entries=entries,
        is_active=data.get("is_active", False),
        sell_order_id=data.get("sell_order_id"),
    )

    return position
