"""
Визуализация индикатора Average Range на графике.

Строит интерактивный график с:
- Свечами (candlestick)
- Линией точки покупки (buy point)
- DCA-уровнями
- Линией безубыточности
- Целью продажи

Использование:
    pip install plotly
    python chart.py
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pybit.unified_trading import HTTP
from indicator import calculate_buy_point, calculate_average_range
from datetime import datetime

try:
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots
except ImportError:
    print("❌ Установи plotly: pip install plotly")
    sys.exit(1)

import config


def main():
    # === НАСТРОЙКИ ===
    SYMBOL = config.SYMBOL
    TIMEFRAME = config.TIMEFRAME
    LOOKBACK = config.LOOKBACK_PERIOD
    LIMIT = 60  # Свечей на графике

    print(f"📊 Загрузка данных {SYMBOL} ({TIMEFRAME})...")

    # Загружаем свечи
    session = HTTP(testnet=False)
    response = session.get_kline(
        category="spot",
        symbol=SYMBOL,
        interval=TIMEFRAME,
        limit=LIMIT,
    )

    klines = response["result"]["list"]
    klines.reverse()

    dates = []
    opens = []
    highs = []
    lows = []
    closes = []

    for k in klines:
        ts = int(k[0]) / 1000
        dates.append(datetime.fromtimestamp(ts))
        opens.append(float(k[1]))
        highs.append(float(k[2]))
        lows.append(float(k[3]))
        closes.append(float(k[4]))

    # Рассчитываем buy_point для каждого бара
    buy_points = []
    signals = []

    for i in range(len(closes)):
        if i < LOOKBACK:
            buy_points.append(None)
            signals.append(False)
            continue

        h_slice = highs[:i + 1]
        l_slice = lows[:i + 1]
        c_slice = closes[:i + 1]

        bp = calculate_buy_point(c_slice, h_slice, l_slice, LOOKBACK)
        buy_points.append(bp)

        # Сигнал: low текущего бара <= buy_point предыдущего бара
        if i > LOOKBACK and buy_points[i - 1] is not None:
            signals.append(lows[i] <= buy_points[i - 1])
        else:
            signals.append(False)

    # DCA-уровни на основе последнего buy_point
    last_bp = buy_points[-1]
    dca_levels = {}
    for point in config.DCA_POINTS:
        level = point["level"]
        drop = point["drop_pct"]
        price = last_bp * (1 - drop / 100)
        dca_levels[f"DCA-{level} (-{drop}%)"] = price

    # Безубыточность (если бы все DCA сработали)
    total_cost = 0
    total_qty = 0
    breakeven_levels = {}

    for point in config.DCA_POINTS:
        level = point["level"]
        drop = point["drop_pct"]
        balance_pct = point["balance_pct"]
        price = last_bp * (1 - drop / 100)
        size = config.TOTAL_BALANCE * balance_pct / 100
        qty = size / price

        total_cost += qty * price
        total_qty += qty
        be = total_cost / total_qty
        breakeven_levels[f"Безубыток после DCA-{level}"] = be

    # Строим график
    fig = make_subplots(rows=1, cols=1)

    # Свечной график
    fig.add_trace(go.Candlestick(
        x=dates,
        open=opens,
        high=highs,
        low=lows,
        close=closes,
        name=SYMBOL,
        increasing_line_color='#26a69a',
        decreasing_line_color='#ef5350',
    ))

    # Линия Buy Point
    fig.add_trace(go.Scatter(
        x=dates,
        y=buy_points,
        mode='lines',
        name=f'Buy Point (lookback={LOOKBACK})',
        line=dict(color='#00e676', width=2, dash='dot'),
    ))

    # Точки сигналов покупки
    signal_dates = [dates[i] for i in range(len(signals)) if signals[i]]
    signal_prices = [lows[i] for i in range(len(signals)) if signals[i]]

    if signal_dates:
        fig.add_trace(go.Scatter(
            x=signal_dates,
            y=signal_prices,
            mode='markers',
            name='🟢 Сигнал покупки',
            marker=dict(
                color='#00e676',
                size=14,
                symbol='triangle-up',
                line=dict(color='white', width=1)
            ),
        ))

    # DCA-уровни (горизонтальные линии)
    colors_dca = ['#4caf50', '#ff9800', '#f44336', '#9c27b0', '#2196f3']
    for idx, (label, price) in enumerate(dca_levels.items()):
        color = colors_dca[idx % len(colors_dca)]
        fig.add_hline(
            y=price,
            line_dash="dash",
            line_color=color,
            line_width=1,
            annotation_text=f"{label}: ${price:,.2f}",
            annotation_position="left",
            annotation_font_color=color,
        )

    # Безубыточность после всех входов
    if breakeven_levels:
        last_be_label = list(breakeven_levels.keys())[-1]
        last_be = list(breakeven_levels.values())[-1]
        sell_target = last_be * (1 + config.SELL_PROFIT_PCT / 100)

        fig.add_hline(
            y=last_be,
            line_dash="solid",
            line_color="#ffeb3b",
            line_width=2,
            annotation_text=f"Безубыточность: ${last_be:,.2f}",
            annotation_position="right",
            annotation_font_color="#ffeb3b",
        )

        fig.add_hline(
            y=sell_target,
            line_dash="solid",
            line_color="#00e5ff",
            line_width=2,
            annotation_text=f"Продажа (+{config.SELL_PROFIT_PCT}%): ${sell_target:,.2f}",
            annotation_position="right",
            annotation_font_color="#00e5ff",
        )

    # Оформление
    fig.update_layout(
        title=dict(
            text=f"Average Range Strategy — {SYMBOL} ({TIMEFRAME})",
            font=dict(size=20, color='white'),
        ),
        template='plotly_dark',
        xaxis_rangeslider_visible=False,
        height=800,
        font=dict(family="Segoe UI, Arial", size=12),
        legend=dict(
            yanchor="top",
            y=0.99,
            xanchor="left",
            x=0.01,
            bgcolor="rgba(0,0,0,0.5)",
        ),
        annotations=[
            dict(
                text=(
                    f"Balance: ${config.TOTAL_BALANCE} | "
                    f"DCA: {len(config.DCA_POINTS)} levels | "
                    f"Sell: +{config.SELL_PROFIT_PCT}% от безубыточности"
                ),
                xref="paper", yref="paper",
                x=0.5, y=1.05,
                showarrow=False,
                font=dict(size=13, color='#aaa'),
            )
        ],
    )

    # Сохраняем и открываем
    output_file = "chart.html"
    fig.write_html(output_file, auto_open=True)
    print(f"\n✅ График сохранён: {os.path.abspath(output_file)}")
    print(f"   Откроется автоматически в браузере")


if __name__ == "__main__":
    main()
