"""
Bybit API клиент (Spot торговля).

Обёртка над pybit SDK для работы с Bybit Spot API.
"""

import logging
from typing import List, Dict, Optional, Any

from pybit.unified_trading import HTTP

import config

logger = logging.getLogger(__name__)


class BybitClient:
    """Клиент для работы с Bybit Spot API."""

    def __init__(
        self,
        api_key: str = None,
        api_secret: str = None,
        testnet: bool = None
    ):
        """
        Инициализация клиента Bybit.

        Args:
            api_key: API ключ (по умолчанию из конфига).
            api_secret: API секрет (по умолчанию из конфига).
            testnet: Использовать тестнет (по умолчанию из конфига).
        """
        self.api_key = api_key or config.BYBIT_API_KEY
        self.api_secret = api_secret or config.BYBIT_API_SECRET
        self.testnet = testnet if testnet is not None else config.BYBIT_TESTNET

        self.session = HTTP(
            api_key=self.api_key,
            api_secret=self.api_secret,
            testnet=self.testnet,
        )

        mode = "TESTNET" if self.testnet else "PRODUCTION"
        logger.info(f"Bybit клиент инициализирован [{mode}]")

    def get_klines(
        self,
        symbol: str,
        interval: str = "D",
        limit: int = 50
    ) -> Dict[str, List[float]]:
        """
        Получить свечи (OHLCV данные).

        Args:
            symbol: Торговая пара (напр. BTCUSDT).
            interval: Таймфрейм ('1', '5', '15', '60', '240', 'D', 'W').
            limit: Количество свечей.

        Returns:
            Словарь с массивами: opens, highs, lows, closes, volumes, timestamps.
        """
        try:
            response = self.session.get_kline(
                category="spot",
                symbol=symbol,
                interval=interval,
                limit=limit,
            )

            klines = response["result"]["list"]

            # Bybit возвращает данные от новых к старым, переворачиваем
            klines.reverse()

            data = {
                "timestamps": [],
                "opens": [],
                "highs": [],
                "lows": [],
                "closes": [],
                "volumes": [],
            }

            for k in klines:
                data["timestamps"].append(int(k[0]))
                data["opens"].append(float(k[1]))
                data["highs"].append(float(k[2]))
                data["lows"].append(float(k[3]))
                data["closes"].append(float(k[4]))
                data["volumes"].append(float(k[5]))

            logger.debug(
                f"Получено {len(klines)} свечей для {symbol} ({interval})"
            )
            return data

        except Exception as e:
            logger.error(f"Ошибка получения свечей: {e}")
            raise

    def get_current_price(self, symbol: str) -> float:
        """
        Получить текущую цену (last traded price).

        Args:
            symbol: Торговая пара.

        Returns:
            Текущая цена.
        """
        try:
            response = self.session.get_tickers(
                category="spot",
                symbol=symbol,
            )
            price = float(response["result"]["list"][0]["lastPrice"])
            logger.debug(f"Текущая цена {symbol}: ${price}")
            return price

        except Exception as e:
            logger.error(f"Ошибка получения цены: {e}")
            raise

    def get_instrument_info(self, symbol: str) -> Dict[str, Any]:
        """
        Получить информацию о торговом инструменте (мин. количество, шаг цены и т.д.).

        Args:
            symbol: Торговая пара.

        Returns:
            Словарь с информацией об инструменте.
        """
        try:
            response = self.session.get_instruments_info(
                category="spot",
                symbol=symbol,
            )
            info = response["result"]["list"][0]

            lot_filter = info.get("lotSizeFilter", {})
            price_filter = info.get("priceFilter", {})

            result = {
                "symbol": info["symbol"],
                "base_coin": info.get("baseCoin", ""),
                "quote_coin": info.get("quoteCoin", ""),
                "min_qty": float(lot_filter.get("minOrderQty", "0")),
                "max_qty": float(lot_filter.get("maxOrderQty", "0")),
                "qty_step": float(lot_filter.get("basePrecision", "0")),
                "min_price": float(price_filter.get("minPrice", "0")),
                "max_price": float(price_filter.get("maxPrice", "0")),
                "price_step": float(price_filter.get("tickSize", "0")),
                "min_order_amt": float(lot_filter.get("minOrderAmt", "0")),
            }

            logger.debug(f"Инструмент {symbol}: {result}")
            return result

        except Exception as e:
            logger.error(f"Ошибка получения инструмента: {e}")
            raise

    def place_limit_buy(
        self,
        symbol: str,
        qty: float,
        price: float,
        order_link_id: str = None
    ) -> Optional[str]:
        """
        Выставить лимитный ордер на покупку.

        Args:
            symbol: Торговая пара.
            qty: Количество (base coin).
            price: Цена ордера.
            order_link_id: Пользовательский ID ордера.

        Returns:
            ID ордера или None при ошибке.
        """
        try:
            params = {
                "category": "spot",
                "symbol": symbol,
                "side": "Buy",
                "orderType": "Limit",
                "qty": str(qty),
                "price": str(price),
                "timeInForce": "GTC",  # Good Till Cancelled
            }
            if order_link_id:
                params["orderLinkId"] = order_link_id

            response = self.session.place_order(**params)
            order_id = response["result"]["orderId"]

            logger.info(
                f"📥 BUY ордер создан: {symbol} | "
                f"qty={qty} | price=${price} | "
                f"orderId={order_id}"
            )
            return order_id

        except Exception as e:
            logger.error(f"Ошибка создания BUY ордера: {e}")
            return None

    def place_limit_sell(
        self,
        symbol: str,
        qty: float,
        price: float,
        order_link_id: str = None
    ) -> Optional[str]:
        """
        Выставить лимитный ордер на продажу.

        Args:
            symbol: Торговая пара.
            qty: Количество (base coin).
            price: Цена ордера.
            order_link_id: Пользовательский ID ордера.

        Returns:
            ID ордера или None при ошибке.
        """
        try:
            params = {
                "category": "spot",
                "symbol": symbol,
                "side": "Sell",
                "orderType": "Limit",
                "qty": str(qty),
                "price": str(price),
                "timeInForce": "GTC",
            }
            if order_link_id:
                params["orderLinkId"] = order_link_id

            response = self.session.place_order(**params)
            order_id = response["result"]["orderId"]

            logger.info(
                f"📤 SELL ордер создан: {symbol} | "
                f"qty={qty} | price=${price} | "
                f"orderId={order_id}"
            )
            return order_id

        except Exception as e:
            logger.error(f"Ошибка создания SELL ордера: {e}")
            return None

    def place_market_sell(
        self,
        symbol: str,
        qty: float,
        order_link_id: str = None
    ) -> Optional[str]:
        """
        Выставить маркет-ордер на продажу (весь объём).

        Args:
            symbol: Торговая пара.
            qty: Количество.
            order_link_id: Пользовательский ID ордера.

        Returns:
            ID ордера или None при ошибке.
        """
        try:
            params = {
                "category": "spot",
                "symbol": symbol,
                "side": "Sell",
                "orderType": "Market",
                "qty": str(qty),
            }
            if order_link_id:
                params["orderLinkId"] = order_link_id

            response = self.session.place_order(**params)
            order_id = response["result"]["orderId"]

            logger.info(
                f"📤 MARKET SELL: {symbol} | qty={qty} | orderId={order_id}"
            )
            return order_id

        except Exception as e:
            logger.error(f"Ошибка маркет-продажи: {e}")
            return None

    def get_order_status(self, symbol: str, order_id: str) -> Dict[str, Any]:
        """
        Получить статус ордера.

        Args:
            symbol: Торговая пара.
            order_id: ID ордера.

        Returns:
            Словарь с информацией об ордере.
        """
        try:
            response = self.session.get_open_orders(
                category="spot",
                symbol=symbol,
                orderId=order_id,
            )

            orders = response["result"]["list"]
            if orders:
                order = orders[0]
                return {
                    "order_id": order["orderId"],
                    "status": order["orderStatus"],
                    "side": order["side"],
                    "price": float(order["price"]),
                    "qty": float(order["qty"]),
                    "filled_qty": float(order.get("cumExecQty", "0")),
                    "avg_price": float(order.get("avgPrice", "0")),
                }

            # Если не в открытых, проверяем историю
            response = self.session.get_order_history(
                category="spot",
                symbol=symbol,
                orderId=order_id,
            )
            orders = response["result"]["list"]
            if orders:
                order = orders[0]
                return {
                    "order_id": order["orderId"],
                    "status": order["orderStatus"],
                    "side": order["side"],
                    "price": float(order["price"]),
                    "qty": float(order["qty"]),
                    "filled_qty": float(order.get("cumExecQty", "0")),
                    "avg_price": float(order.get("avgPrice", "0")),
                }

            return {"status": "NotFound"}

        except Exception as e:
            logger.error(f"Ошибка получения статуса ордера: {e}")
            return {"status": "Error", "error": str(e)}

    def cancel_order(self, symbol: str, order_id: str) -> bool:
        """
        Отменить ордер.

        Args:
            symbol: Торговая пара.
            order_id: ID ордера.

        Returns:
            True если отмена успешна.
        """
        try:
            self.session.cancel_order(
                category="spot",
                symbol=symbol,
                orderId=order_id,
            )
            logger.info(f"❌ Ордер отменён: {order_id}")
            return True

        except Exception as e:
            logger.error(f"Ошибка отмены ордера {order_id}: {e}")
            return False

    def cancel_all_orders(self, symbol: str) -> bool:
        """
        Отменить все открытые ордера по паре.

        Args:
            symbol: Торговая пара.

        Returns:
            True если отмена успешна.
        """
        try:
            self.session.cancel_all_orders(
                category="spot",
                symbol=symbol,
            )
            logger.info(f"❌ Все ордера отменены: {symbol}")
            return True

        except Exception as e:
            logger.error(f"Ошибка отмены всех ордеров: {e}")
            return False

    def get_wallet_balance(self, coin: str = "USDT") -> float:
        """
        Получить баланс кошелька.

        Args:
            coin: Монета для проверки.

        Returns:
            Доступный баланс.
        """
        try:
            response = self.session.get_wallet_balance(
                accountType="UNIFIED",
                coin=coin,
            )

            coins = response["result"]["list"][0]["coin"]
            for c in coins:
                if c["coin"] == coin:
                    balance = float(c.get("availableToWithdraw", "0"))
                    logger.debug(f"Баланс {coin}: {balance}")
                    return balance

            return 0.0

        except Exception as e:
            logger.error(f"Ошибка получения баланса: {e}")
            return 0.0
