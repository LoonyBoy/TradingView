//@version=6
// ============================================================================
// FIBONACCI AUTO-TRADING INDICATOR v2.0
// ============================================================================
// 
// Логика работы:
// 1. Ищет последний минимум (с проверкой тени ≤15%)
// 2. Ждет рост 100% от минимума
// 3. Строит Фибоначчи от минимума до хая (с проверкой пламени ≤15%)
// 4. При перехае (без касания уровней Этапа 1) - перестраивает Фибо выше
// 5. Этап 1: ордера $100 на 0.618, $150 на 0.5, $250 на 0.382
// 6. После SELL #1 → Этап 2: ордера сдвигаются на 0.5/0.382/0.236
// 7. После SELL #2 - Фибо завершается, ожидание нового цикла
// 8. Новый цикл = новый минимум + 100% рост → новое Фибо с нуля
// 9. SELL = "на уровень выше по цене"
//
// ============================================================================

indicator("Fibonacci Trading Strategy v2.0", overlay=true, max_lines_count=500, max_labels_count=500)

// ============================================================================
// НАСТРОЙКИ ИНДИКАТОРА
// ============================================================================
lookbackPeriod = input.int(150, "Период поиска (дней)", minval=50, maxval=500, 
     tooltip="Период для поиска минимума в днях/барах")
minGrowthPercent = input.float(100.0, "Мин. рост %", minval=50.0, maxval=500.0, 
     tooltip="Минимальный рост от минимума для построения Fibonacci")
wickThreshold = input.float(15.0, "Макс. тень/пламя %", minval=5.0, maxval=100.0, 
     tooltip="Максимальный размер тени/пламени свечи в %")

// Настройки ордеров
orderAmount1 = input.float(100.0, "Ордер на 0.618 ($)", minval=10.0, group="Ордера")
orderAmount2 = input.float(150.0, "Ордер на 0.5 ($)", minval=10.0, group="Ордера")
orderAmount3 = input.float(250.0, "Ордер на 0.382 ($)", minval=10.0, group="Ордера")

// Настройки отображения
showFibLines = input.bool(true, "Показывать линии Фибо", group="Отображение")
showFibLevels = input.bool(true, "Показывать уровни Фибо (%)", group="Отображение", 
     tooltip="Отображение процентов и цен на уровнях Фибоначчи")
showLabels = input.bool(true, "Показывать сигналы Buy/Sell", group="Отображение")
showOrders = input.bool(true, "Показывать ордера", group="Отображение")
showInfo = input.bool(true, "Показывать таблицу", group="Отображение")

// ============================================================================
// ПЕРЕМЕННЫЕ СОСТОЯНИЯ (объявляем рано, т.к. используются в поиске минимума)
// ============================================================================

// Бар начала нового поиска (после завершения предыдущего цикла)
var int searchStartBar = 0

// Флаг что первый цикл уже начался
var bool firstCycleStarted = false

// ============================================================================
// ФУНКЦИИ ДЛЯ ПРОВЕРКИ ТЕНЕЙ СВЕЧЕЙ
// ============================================================================

// Проверка нижней тени - для минимума
// Формула: (min(Open,Close) - Low) / (High - Low) × 100
getLowerWickPercent(idx) =>
    bodyBottom = math.min(open[idx], close[idx])
    candleRange = high[idx] - low[idx]
    candleRange > 0 ? ((bodyBottom - low[idx]) / candleRange) * 100 : 0.0

// Проверка верхней тени (пламя) - для максимума
// Формула: (High - max(Open,Close)) / (High - Low) × 100
getUpperWickPercent(idx) =>
    bodyTop = math.max(open[idx], close[idx])
    candleRange = high[idx] - low[idx]
    candleRange > 0 ? ((high[idx] - bodyTop) / candleRange) * 100 : 0.0

// ============================================================================
// ПОИСК ОПТИМАЛЬНОГО МИНИМУМА (с учетом тени)
// ============================================================================

// Динамический период поиска - от searchStartBar до текущего бара
// Но не больше lookbackPeriod (150 дней) и не меньше 1
dynamicLookback = math.max(1, math.min(lookbackPeriod, bar_index - searchStartBar + 1))

// Находим бар с минимальным low за динамический период
rawLowestBar = ta.lowestbars(low, dynamicLookback)
rawLowestPrice = ta.lowest(low, dynamicLookback)

// Функция поиска оптимальной точки минимума
findOptimalMinimum() =>
    int resultBar = math.abs(nz(rawLowestBar, 0))
    float resultPrice = rawLowestPrice
    bool useBody = false
    
    // Проверяем тень свечи на баре минимума
    lowerWick = getLowerWickPercent(resultBar)
    
    if lowerWick > wickThreshold
        // Тень слишком большая - ищем рядом свечу без большой тени
        foundBetter = false
        // Ищем в радиусе 5 баров
        for i = 1 to 5
            // Проверяем бар слева
            if resultBar + i < dynamicLookback
                leftWick = getLowerWickPercent(resultBar + i)
                if leftWick <= wickThreshold and low[resultBar + i] <= resultPrice * 1.05
                    resultBar := resultBar + i
                    resultPrice := low[resultBar]
                    foundBetter := true
                    break
            // Проверяем бар справа
            if resultBar - i >= 0
                rightWick = getLowerWickPercent(resultBar - i)
                if rightWick <= wickThreshold and low[resultBar - i] <= resultPrice * 1.05
                    resultBar := resultBar - i
                    resultPrice := low[resultBar]
                    foundBetter := true
                    break
        
        // Если не нашли подходящую свечу - используем тело (не тень)
        if not foundBetter
            resultBar := math.abs(nz(rawLowestBar, 0))
            resultPrice := math.min(open[resultBar], close[resultBar])
            useBody := true
    
    [resultBar, resultPrice, useBody]

[minBarOffset, minPrice, minUseBody] = findOptimalMinimum()

// ============================================================================
// СОСТОЯНИЯ ТОРГОВОЙ СИСТЕМЫ
// ============================================================================

// Состояния системы
var int STATE_SEARCHING_MIN = 0      // Ищем минимум
var int STATE_WAITING_GROWTH = 1     // Ждем 100% роста
var int STATE_FIBO_ACTIVE = 2        // Фибо построено, торгуем
var int STATE_WAITING_RESET = 3      // Ждем нового мин/макс после 2-го sell

var int systemState = STATE_SEARCHING_MIN
var float confirmedMin = na          // Подтвержденный минимум
var int confirmedMinBar = na         // Бар минимума
var float confirmedMax = na          // Подтвержденный максимум (хай)
var int confirmedMaxBar = na         // Бар максимума
var bool minUsesBody = false         // Минимум по телу (а не по тени)
var bool maxUsesBody = false         // Максимум по телу (а не по пламени)

// Счетчик продаж (для ожидания после 2-го sell)
var int sellCount = 0

// Флаг касания зоны 0.6-0.7
var bool touchedZone06_07 = false

// Отслеживаем изменение Фибо для создания нового
var float prevConfirmedMax = na
var float prevConfirmedMin = na

// ============================================================================
// УРОВНИ ОРДЕРОВ (с возможностью сдвига)
// ============================================================================

// Начальные уровни ордеров (Этап 1: 0.618 / 0.5 / 0.382)
var float orderLevel1 = 0.618  // $100 ордер
var float orderLevel2 = 0.5    // $150 ордер
var float orderLevel3 = 0.382  // $250 ордер

// Уровни продажи для каждого ордера ("на уровень выше" по цене)
var float sellLevel1 = 0.786   // Продажа для ордера на 0.618 → выход на 0.786
var float sellLevel2 = 0.618   // Продажа для ордера на 0.5 → выход на 0.618  
var float sellLevel3 = 0.5     // Продажа для ордера на 0.382 → выход на 0.5

// Состояние позиций
var bool position1Open = false  // Позиция $100
var bool position2Open = false  // Позиция $150
var bool position3Open = false  // Позиция $250

// Цены входа для расчета выхода
var float entryPrice1 = na
var float entryPrice2 = na
var float entryPrice3 = na

// Флаги для сброса/сдвига ордеров (вместо функций, т.к. в v6 функции не могут менять var)
var bool doResetOrders = false
var bool doShiftOrders = false

// ============================================================================
// ЛОГИКА СОСТОЯНИЙ
// ============================================================================

// Обработка флагов сброса/сдвига ордеров
if doResetOrders
    // Этап 1: 0.618 / 0.5 / 0.382
    orderLevel1 := 0.618
    orderLevel2 := 0.5
    orderLevel3 := 0.382
    sellLevel1 := 0.786
    sellLevel2 := 0.618
    sellLevel3 := 0.5
    doResetOrders := false

if doShiftOrders
    // Этап 2: 0.5 / 0.382 / 0.236 (после первого SELL)
    orderLevel1 := 0.5
    orderLevel2 := 0.382
    orderLevel3 := 0.236
    sellLevel1 := 0.618   // Продажа для 0.5 → выход на 0.618
    sellLevel2 := 0.5     // Продажа для 0.382 → выход на 0.5
    sellLevel3 := 0.382   // Продажа для 0.236 → выход на 0.382
    doShiftOrders := false

// Расчет текущего роста от минимума
currentGrowth = confirmedMin > 0 ? ((high - confirmedMin) / confirmedMin) * 100 : 0

// Вычисляем barsFromMin на каждом баре (для ta.highest/ta.highestbars)
barsFromMinCalc = na(confirmedMinBar) ? 1 : math.max(1, bar_index - confirmedMinBar)

// Вызываем ta.highest и ta.highestbars на каждом баре (требование Pine Script v6)
maxPriceAfterMin = ta.highest(high, barsFromMinCalc)
maxBarAfterMin = ta.highestbars(high, barsFromMinCalc)

// Функция обработки максимума (без вызовов ta.* внутри)
processMaxAfterMin() =>
    if na(confirmedMinBar) or barsFromMinCalc < 2
        [float(na), int(na), false]
    else
        maxBarIdx = math.abs(nz(maxBarAfterMin, 0))
        
        // Проверяем пламя свечи максимума
        upperWick = getUpperWickPercent(maxBarIdx)
        usesBody = false
        finalPrice = maxPriceAfterMin
        finalBar = bar_index + maxBarAfterMin
        
        if upperWick > wickThreshold
            // Ищем рядом свечу без большого пламени
            foundBetter = false
            for i = 1 to 5
                if maxBarIdx + i < barsFromMinCalc
                    leftWick = getUpperWickPercent(maxBarIdx + i)
                    if leftWick <= wickThreshold and high[maxBarIdx + i] >= maxPriceAfterMin * 0.95
                        finalBar := bar_index - (maxBarIdx + i)
                        finalPrice := high[maxBarIdx + i]
                        foundBetter := true
                        break
                if maxBarIdx - i >= 0
                    rightWick = getUpperWickPercent(maxBarIdx - i)
                    if rightWick <= wickThreshold and high[maxBarIdx - i] >= maxPriceAfterMin * 0.95
                        finalBar := bar_index - (maxBarIdx - i)
                        finalPrice := high[maxBarIdx - i]
                        foundBetter := true
                        break
            
            if not foundBetter
                // Используем тело свечи
                finalPrice := math.max(open[maxBarIdx], close[maxBarIdx])
                usesBody := true
        
        [finalPrice, finalBar, usesBody]

// Вызываем на каждом баре (требование Pine Script v6)
[calcMaxP, calcMaxB, calcMaxBody] = processMaxAfterMin()

// ============================================================================
// ОБНОВЛЕНИЕ СОСТОЯНИЙ НА КАЖДОМ БАРЕ
// ============================================================================

// Инициализация первого цикла
if not firstCycleStarted
    searchStartBar := bar_index
    firstCycleStarted := true

// Обновляем минимум если нашли новый (только в состояниях поиска)
// Ищем минимум ТОЛЬКО в текущем диапазоне (от searchStartBar)
if systemState == STATE_SEARCHING_MIN or systemState == STATE_WAITING_GROWTH
    minBarAbsolute = bar_index - minBarOffset
    // Проверяем что минимум находится после начала поиска (не из предыдущего цикла)
    if minBarAbsolute >= searchStartBar
        if na(confirmedMin) or minPrice < confirmedMin
            confirmedMin := minPrice
            confirmedMinBar := minBarAbsolute
            minUsesBody := minUseBody
            systemState := STATE_WAITING_GROWTH

// Проверяем достижение 100% роста
if systemState == STATE_WAITING_GROWTH
    if currentGrowth >= minGrowthPercent
        if not na(calcMaxP)
            confirmedMax := calcMaxP
            confirmedMaxBar := calcMaxB
            maxUsesBody := calcMaxBody
            systemState := STATE_FIBO_ACTIVE
            touchedZone06_07 := false
            sellCount := 0
            doResetOrders := true

// Обновляем максимум при перехае:
// - ТОЛЬКО если уровни Этапа 1 (0.6/0.5/0.4) не были задеты
// - После касания уровней - перехай запрещён, Фибо фиксируется
if systemState == STATE_FIBO_ACTIVE and not touchedZone06_07
    if high > confirmedMax
        // Проверяем пламя новой свечи
        upperWick = getUpperWickPercent(0)
        if upperWick <= wickThreshold
            confirmedMax := high
            confirmedMaxBar := bar_index
            maxUsesBody := false
        else
            // Используем тело
            confirmedMax := math.max(open, close)
            confirmedMaxBar := bar_index
            maxUsesBody := true

// ============================================================================
// РАСЧЕТ УРОВНЕЙ FIBONACCI
// ============================================================================

fibRange = confirmedMax - confirmedMin
canBuildFibo = systemState == STATE_FIBO_ACTIVE and not na(confirmedMin) and not na(confirmedMax) and fibRange > 0

// Уровни Фибоначчи (от 1.0 = хай до 0.0 = минимум)
fib_1_000 = canBuildFibo ? confirmedMax : na
fib_0_786 = canBuildFibo ? confirmedMax - (fibRange * 0.214) : na
fib_0_700 = canBuildFibo ? confirmedMax - (fibRange * 0.300) : na
fib_0_650 = canBuildFibo ? confirmedMax - (fibRange * 0.350) : na
fib_0_618 = canBuildFibo ? confirmedMax - (fibRange * 0.382) : na
fib_0_500 = canBuildFibo ? confirmedMax - (fibRange * 0.500) : na
fib_0_400 = canBuildFibo ? confirmedMax - (fibRange * 0.600) : na
fib_0_382 = canBuildFibo ? confirmedMax - (fibRange * 0.618) : na
fib_0_300 = canBuildFibo ? confirmedMax - (fibRange * 0.700) : na
fib_0_236 = canBuildFibo ? confirmedMax - (fibRange * 0.764) : na
fib_0_000 = canBuildFibo ? confirmedMin : na

// Расчет цен для ордеров на основе текущих уровней
getOrderPrice(level) =>
    canBuildFibo ? confirmedMax - (fibRange * (1.0 - level)) : na

order1Price = getOrderPrice(orderLevel1)
order2Price = getOrderPrice(orderLevel2)
order3Price = getOrderPrice(orderLevel3)
sell1Price = getOrderPrice(sellLevel1)
sell2Price = getOrderPrice(sellLevel2)
sell3Price = getOrderPrice(sellLevel3)

// ============================================================================
// ОТСЛЕЖИВАНИЕ КАСАНИЯ ЗОНЫ 0.6-0.7
// ============================================================================

// Касание считается при достижении ЛЮБОГО уровня Этапа 1 (0.618/0.5/0.382)
if canBuildFibo and not touchedZone06_07
    level_0618 = confirmedMax - (fibRange * 0.382)  // Фибо 0.618
    level_05 = confirmedMax - (fibRange * 0.5)      // Фибо 0.5
    level_0382 = confirmedMax - (fibRange * 0.618)  // Фибо 0.382
    // Касание любого из уровней Этапа 1
    if low <= level_0618 or low <= level_05 or low <= level_0382
        touchedZone06_07 := true

// ============================================================================
// ТОРГОВАЯ ЛОГИКА
// ============================================================================

// Условия касания уровней ордеров
touchOrder1 = canBuildFibo and not na(order1Price) and low <= order1Price and high >= order1Price
touchOrder2 = canBuildFibo and not na(order2Price) and low <= order2Price and high >= order2Price
touchOrder3 = canBuildFibo and not na(order3Price) and low <= order3Price and high >= order3Price

touchSell1 = canBuildFibo and not na(sell1Price) and low <= sell1Price and high >= sell1Price
touchSell2 = canBuildFibo and not na(sell2Price) and low <= sell2Price and high >= sell2Price
touchSell3 = canBuildFibo and not na(sell3Price) and low <= sell3Price and high >= sell3Price

// Сигналы покупки
var label buyLabel1 = na
var label buyLabel2 = na
var label buyLabel3 = na

// BUY на уровне 1 ($100)
if showLabels and touchOrder1 and not position1Open and systemState == STATE_FIBO_ACTIVE
    buyLabel1 := label.new(bar_index, low, "BUY $" + str.tostring(orderAmount1, "#"), 
         color=color.new(color.green, 0), 
         textcolor=color.white, 
         style=label.style_label_up, 
         size=size.small)
    position1Open := true
    entryPrice1 := order1Price
    touchedZone06_07 := true  // Теперь точно касались зоны

// BUY на уровне 2 ($150)
if showLabels and touchOrder2 and not position2Open and systemState == STATE_FIBO_ACTIVE
    buyLabel2 := label.new(bar_index, low, "BUY $" + str.tostring(orderAmount2, "#"), 
         color=color.new(color.green, 0), 
         textcolor=color.white, 
         style=label.style_label_up, 
         size=size.small)
    position2Open := true
    entryPrice2 := order2Price

// BUY на уровне 3 ($250)
if showLabels and touchOrder3 and not position3Open and systemState == STATE_FIBO_ACTIVE
    buyLabel3 := label.new(bar_index, low, "BUY $" + str.tostring(orderAmount3, "#"), 
         color=color.new(color.green, 0), 
         textcolor=color.white, 
         style=label.style_label_up, 
         size=size.small)
    position3Open := true
    entryPrice3 := order3Price

// Сигналы продажи
var label sellLabel1 = na
var label sellLabel2 = na
var label sellLabel3 = na

// SELL для позиции 1 (если она открыта и достигли уровня sell)
if showLabels and touchSell1 and position1Open and systemState == STATE_FIBO_ACTIVE
    profit1 = ((sell1Price - entryPrice1) / entryPrice1) * orderAmount1
    sellLabel1 := label.new(bar_index, high, "SELL $" + str.tostring(orderAmount1, "#") + "\n+" + str.tostring(profit1, "#.##"), 
         color=color.new(color.red, 0), 
         textcolor=color.white, 
         style=label.style_label_down, 
         size=size.small)
    position1Open := false
    sellCount := sellCount + 1
    
    // После первого sell - сдвигаем ордера вниз
    if sellCount == 1
        doShiftOrders := true
    
    // После второго sell - ждем обновления
    if sellCount >= 2
        systemState := STATE_WAITING_RESET

// SELL для позиции 2
if showLabels and touchSell2 and position2Open and systemState == STATE_FIBO_ACTIVE
    // Если позиция 3 тоже открыта - продаем все вместе на уровне 2
    totalAmount = orderAmount2
    totalProfit = ((sell2Price - entryPrice2) / entryPrice2) * orderAmount2
    
    if position3Open
        // Продаем позицию 3 здесь же (выход в ~0)
        profit3 = ((sell2Price - entryPrice3) / entryPrice3) * orderAmount3
        totalAmount := orderAmount2 + orderAmount3
        totalProfit := totalProfit + profit3
        position3Open := false
    
    sellLabel2 := label.new(bar_index, high, "SELL $" + str.tostring(totalAmount, "#") + "\n" + (totalProfit >= 0 ? "+" : "") + str.tostring(totalProfit, "#.##"), 
         color=color.new(color.red, 0), 
         textcolor=color.white, 
         style=label.style_label_down, 
         size=size.small)
    position2Open := false
    sellCount := sellCount + 1
    
    if sellCount >= 2
        systemState := STATE_WAITING_RESET

// SELL для позиции 3 (только если идет вверх без позиции 2)
if showLabels and touchSell3 and position3Open and not position2Open and systemState == STATE_FIBO_ACTIVE
    profit3 = ((sell3Price - entryPrice3) / entryPrice3) * orderAmount3
    sellLabel3 := label.new(bar_index, high, "SELL $" + str.tostring(orderAmount3, "#") + "\n+" + str.tostring(profit3, "#.##"), 
         color=color.new(color.red, 0), 
         textcolor=color.white, 
         style=label.style_label_down, 
         size=size.small)
    position3Open := false
    sellCount := sellCount + 1
    
    if sellCount >= 2
        systemState := STATE_WAITING_RESET

// ============================================================================
// СТОП-ЛОСС НА УРОВНЕ 0 (МИНИМУМ)
// ============================================================================

// Условие касания уровня 0 (минимума)
touchLevel0 = canBuildFibo and not na(fib_0_000) and low <= fib_0_000

// Если цена достигла уровня 0 и есть открытые позиции - закрываем с убытком
if showLabels and touchLevel0 and (position1Open or position2Open or position3Open) and systemState == STATE_FIBO_ACTIVE
    // Считаем общий убыток по всем открытым позициям
    totalLoss = 0.0
    totalAmount = 0.0
    
    if position1Open
        loss1 = ((fib_0_000 - entryPrice1) / entryPrice1) * orderAmount1
        totalLoss := totalLoss + loss1
        totalAmount := totalAmount + orderAmount1
        position1Open := false
    
    if position2Open
        loss2 = ((fib_0_000 - entryPrice2) / entryPrice2) * orderAmount2
        totalLoss := totalLoss + loss2
        totalAmount := totalAmount + orderAmount2
        position2Open := false
    
    if position3Open
        loss3 = ((fib_0_000 - entryPrice3) / entryPrice3) * orderAmount3
        totalLoss := totalLoss + loss3
        totalAmount := totalAmount + orderAmount3
        position3Open := false
    
    // Показываем метку стоп-лосса
    label.new(bar_index, low, "STOP-LOSS\n$" + str.tostring(totalAmount, "#") + "\n" + str.tostring(totalLoss, "#.##"), 
         color=color.new(color.maroon, 0), 
         textcolor=color.white, 
         style=label.style_label_up, 
         size=size.small)
    
    // Переходим в режим ожидания нового минимума/максимума
    systemState := STATE_WAITING_RESET

// ============================================================================
// ОЖИДАНИЕ НОВОГО МИНИМУМА/МАКСИМУМА ПОСЛЕ RESET
// ============================================================================

if systemState == STATE_WAITING_RESET
    // Проверяем падение ниже уровня 0 (минимума) - ищем НОВЫЙ локальный минимум
    if low < confirmedMin * 0.98
        // Полный сброс - начинаем НОВЫЙ независимый цикл
        confirmedMin := na
        confirmedMinBar := na
        confirmedMax := na
        confirmedMaxBar := na
        searchStartBar := bar_index  // ВАЖНО: новый поиск начинается ЗДЕСЬ
        systemState := STATE_SEARCHING_MIN
        sellCount := 0
        touchedZone06_07 := false
        position1Open := false
        position2Open := false
        position3Open := false
        doResetOrders := true
    
    // Проверяем новый максимум (выше текущего на 10%+)
    // ВАЖНО: начинаем ПОЛНОСТЬЮ НОВЫЙ цикл - ищем новый минимум!
    else if high > confirmedMax * 1.10
        // Полный сброс - ищем НОВЫЙ минимум для нового цикла
        confirmedMin := na
        confirmedMinBar := na
        confirmedMax := na
        confirmedMaxBar := na
        searchStartBar := bar_index  // Новый поиск начинается С ЭТОГО бара
        systemState := STATE_SEARCHING_MIN  // Начинаем поиск минимума заново
        sellCount := 0
        touchedZone06_07 := false
        position1Open := false
        position2Open := false
        position3Open := false
        doResetOrders := true
        // Обнуляем prevConfirmedMax/Min чтобы создались НОВЫЕ линии
        prevConfirmedMax := na
        prevConfirmedMin := na

// ============================================================================
// ОТРИСОВКА УРОВНЕЙ FIBONACCI
// ============================================================================

// Текущие линии Фибо (для активного цикла)
var line line_1_000 = na
var line line_0_786 = na
var line line_0_700 = na
var line line_0_650 = na
var line line_0_618 = na
var line line_0_500 = na
var line line_0_400 = na
var line line_0_382 = na
var line line_0_300 = na
var line line_0_236 = na
var line line_0_000 = na

// Лейблы уровней Фибоначчи (отображение процентов и цен)
var label label_fib_1_000 = na
var label label_fib_0_786 = na
var label label_fib_0_700 = na
var label label_fib_0_650 = na
var label label_fib_0_618 = na
var label label_fib_0_500 = na
var label label_fib_0_400 = na
var label label_fib_0_382 = na
var label label_fib_0_300 = na
var label label_fib_0_236 = na
var label label_fib_0_000 = na

var label label_min = na
var label label_max = na

// Линии для ордеров (текущие)
var line lineOrder1 = na
var line lineOrder2 = na
var line lineOrder3 = na
var label labelOrder1 = na
var label labelOrder2 = na
var label labelOrder3 = na

// Линия стоп-лосса
var line lineStopLoss = na
var label labelStopLoss = na

var int prevSystemState = na
var bool fiboWasDrawn = false

// Бар окончания торгового цикла (для фиксации длины линий)
var int cycleEndBar = na

// Проверяем переход в WAITING_RESET (конец торгового цикла) - НЕ удаляем Фибо
// Линии остаются на графике для истории
if systemState == STATE_WAITING_RESET and prevSystemState == STATE_FIBO_ACTIVE
    cycleEndBar := bar_index
    // Линии Фибо остаются видимыми!

// При начале НОВОГО цикла - обнуляем ссылки, но НЕ удаляем старые линии
// Это позволяет видеть историю всех Фибо на графике
if systemState == STATE_SEARCHING_MIN and prevSystemState != STATE_SEARCHING_MIN
    // НЕ удаляем старые линии - они остаются для истории!
    // Просто обнуляем ссылки, чтобы создать новые линии для нового цикла
    line_1_000 := na
    line_0_786 := na
    line_0_700 := na
    line_0_650 := na
    line_0_618 := na
    line_0_500 := na
    line_0_400 := na
    line_0_382 := na
    line_0_300 := na
    line_0_236 := na
    line_0_000 := na
    label_min := na
    label_max := na
    // Сброс лейблов уровней Фибо
    label_fib_1_000 := na
    label_fib_0_786 := na
    label_fib_0_700 := na
    label_fib_0_650 := na
    label_fib_0_618 := na
    label_fib_0_500 := na
    label_fib_0_400 := na
    label_fib_0_382 := na
    label_fib_0_300 := na
    label_fib_0_236 := na
    label_fib_0_000 := na
    fiboWasDrawn := false

// Проверяем нужно ли перерисовать текущее Фибо
needRedraw = canBuildFibo and ((confirmedMax != prevConfirmedMax) or (confirmedMin != prevConfirmedMin))

// Это новый цикл (prevConfirmedMax/Min были сброшены в na) - создаём новые линии, не удаляя старые
isNewCycle = na(prevConfirmedMax) or na(prevConfirmedMin)

// Удаляем только текущие (активные) линии при изменении уровней внутри ОДНОГО цикла
// НЕ удаляем если это новый цикл - старые линии остаются для истории
if needRedraw and fiboWasDrawn and not isNewCycle
    line.delete(line_1_000)
    line.delete(line_0_786)
    line.delete(line_0_700)
    line.delete(line_0_650)
    line.delete(line_0_618)
    line.delete(line_0_500)
    line.delete(line_0_400)
    line.delete(line_0_382)
    line.delete(line_0_300)
    line.delete(line_0_236)
    line.delete(line_0_000)
    label.delete(label_min)
    label.delete(label_max)
    // Удаляем лейблы уровней
    label.delete(label_fib_1_000)
    label.delete(label_fib_0_786)
    label.delete(label_fib_0_700)
    label.delete(label_fib_0_650)
    label.delete(label_fib_0_618)
    label.delete(label_fib_0_500)
    label.delete(label_fib_0_400)
    label.delete(label_fib_0_382)
    label.delete(label_fib_0_300)
    label.delete(label_fib_0_236)
    label.delete(label_fib_0_000)

// Если это новый цикл - обнуляем ссылки (старые линии остаются)
if isNewCycle and fiboWasDrawn
    line_1_000 := na
    line_0_786 := na
    line_0_700 := na
    line_0_650 := na
    line_0_618 := na
    line_0_500 := na
    line_0_400 := na
    line_0_382 := na
    line_0_300 := na
    line_0_236 := na
    line_0_000 := na
    label_min := na
    label_max := na
    // Обнуляем лейблы уровней
    label_fib_1_000 := na
    label_fib_0_786 := na
    label_fib_0_700 := na
    label_fib_0_650 := na
    label_fib_0_618 := na
    label_fib_0_500 := na
    label_fib_0_400 := na
    label_fib_0_382 := na
    label_fib_0_300 := na
    label_fib_0_236 := na
    label_fib_0_000 := na
    fiboWasDrawn := false

// Рисуем линии Фибо когда активно
if showFibLines and canBuildFibo and (needRedraw or not fiboWasDrawn)
    startBar = math.max(0, confirmedMinBar)
    endBar = bar_index
    
    // Цвет линий
    alphaMain = 30
    alphaSecondary = 50
    
    // Основные уровни Фибо (от минимума до текущего бара)
    line_1_000 := line.new(startBar, fib_1_000, endBar, fib_1_000, color=color.new(color.blue, alphaSecondary), width=1)
    line_0_786 := line.new(startBar, fib_0_786, endBar, fib_0_786, color=color.new(color.purple, alphaMain), width=2)
    line_0_700 := line.new(startBar, fib_0_700, endBar, fib_0_700, color=color.new(color.orange, alphaSecondary), width=1, style=line.style_dashed)
    line_0_650 := line.new(startBar, fib_0_650, endBar, fib_0_650, color=color.new(color.orange, alphaSecondary), width=1, style=line.style_dashed)
    line_0_618 := line.new(startBar, fib_0_618, endBar, fib_0_618, color=color.new(color.green, alphaMain), width=2)
    line_0_500 := line.new(startBar, fib_0_500, endBar, fib_0_500, color=color.new(color.green, alphaMain), width=2)
    line_0_400 := line.new(startBar, fib_0_400, endBar, fib_0_400, color=color.new(color.teal, alphaSecondary), width=1, style=line.style_dashed)
    line_0_382 := line.new(startBar, fib_0_382, endBar, fib_0_382, color=color.new(color.green, alphaMain), width=2)
    line_0_300 := line.new(startBar, fib_0_300, endBar, fib_0_300, color=color.new(color.teal, alphaSecondary), width=1, style=line.style_dashed)
    line_0_236 := line.new(startBar, fib_0_236, endBar, fib_0_236, color=color.new(color.blue, alphaSecondary), width=1)
    line_0_000 := line.new(startBar, fib_0_000, endBar, fib_0_000, color=color.new(color.red, alphaMain), width=2)
    
    // Лейблы уровней Фибоначчи (справа от линий)
    if showFibLevels
        label_fib_1_000 := label.new(endBar, fib_1_000, "100% (" + str.tostring(fib_1_000, "#.####") + ")", 
             color=color.new(color.blue, 80), textcolor=color.blue, 
             style=label.style_label_left, size=size.small)
        label_fib_0_786 := label.new(endBar, fib_0_786, "78.6% (" + str.tostring(fib_0_786, "#.####") + ")", 
             color=color.new(color.purple, 80), textcolor=color.purple, 
             style=label.style_label_left, size=size.small)
        label_fib_0_700 := label.new(endBar, fib_0_700, "70% (" + str.tostring(fib_0_700, "#.####") + ")", 
             color=color.new(color.orange, 80), textcolor=color.orange, 
             style=label.style_label_left, size=size.tiny)
        label_fib_0_650 := label.new(endBar, fib_0_650, "65% (" + str.tostring(fib_0_650, "#.####") + ")", 
             color=color.new(color.orange, 80), textcolor=color.orange, 
             style=label.style_label_left, size=size.tiny)
        label_fib_0_618 := label.new(endBar, fib_0_618, "61.8% (" + str.tostring(fib_0_618, "#.####") + ")", 
             color=color.new(color.green, 80), textcolor=color.green, 
             style=label.style_label_left, size=size.small)
        label_fib_0_500 := label.new(endBar, fib_0_500, "50% (" + str.tostring(fib_0_500, "#.####") + ")", 
             color=color.new(color.green, 80), textcolor=color.green, 
             style=label.style_label_left, size=size.small)
        label_fib_0_400 := label.new(endBar, fib_0_400, "40% (" + str.tostring(fib_0_400, "#.####") + ")", 
             color=color.new(color.teal, 80), textcolor=color.teal, 
             style=label.style_label_left, size=size.tiny)
        label_fib_0_382 := label.new(endBar, fib_0_382, "38.2% (" + str.tostring(fib_0_382, "#.####") + ")", 
             color=color.new(color.green, 80), textcolor=color.green, 
             style=label.style_label_left, size=size.small)
        label_fib_0_300 := label.new(endBar, fib_0_300, "30% (" + str.tostring(fib_0_300, "#.####") + ")", 
             color=color.new(color.teal, 80), textcolor=color.teal, 
             style=label.style_label_left, size=size.tiny)
        label_fib_0_236 := label.new(endBar, fib_0_236, "23.6% (" + str.tostring(fib_0_236, "#.####") + ")", 
             color=color.new(color.blue, 80), textcolor=color.blue, 
             style=label.style_label_left, size=size.small)
        label_fib_0_000 := label.new(endBar, fib_0_000, "0% (" + str.tostring(fib_0_000, "#.####") + ")", 
             color=color.new(color.red, 80), textcolor=color.red, 
             style=label.style_label_left, size=size.small)
    
    // Метки Min/Max
    label_min := label.new(confirmedMinBar, confirmedMin, "MIN\n" + str.tostring(confirmedMin, "#.####") + (minUsesBody ? "\n(body)" : ""), 
         color=color.new(color.green, 0), textcolor=color.white, 
         style=label.style_label_up, size=size.small)
    
    label_max := label.new(confirmedMaxBar, confirmedMax, "MAX\n" + str.tostring(confirmedMax, "#.####") + (maxUsesBody ? "\n(body)" : ""), 
         color=color.new(color.red, 0), textcolor=color.white, 
         style=label.style_label_down, size=size.small)
    
    fiboWasDrawn := true
    prevConfirmedMax := confirmedMax
    prevConfirmedMin := confirmedMin

// Обновляем конечную точку линий ТОЛЬКО для активного Фибо (тянется за ценой)
if showFibLines and canBuildFibo and fiboWasDrawn and not na(line_1_000)
    line.set_x2(line_1_000, bar_index)
    line.set_x2(line_0_786, bar_index)
    line.set_x2(line_0_700, bar_index)
    line.set_x2(line_0_650, bar_index)
    line.set_x2(line_0_618, bar_index)
    line.set_x2(line_0_500, bar_index)
    line.set_x2(line_0_400, bar_index)
    line.set_x2(line_0_382, bar_index)
    line.set_x2(line_0_300, bar_index)
    line.set_x2(line_0_236, bar_index)
    line.set_x2(line_0_000, bar_index)
    // Обновляем позицию лейблов уровней (тянутся за линиями)
    if showFibLevels and not na(label_fib_1_000)
        label.set_x(label_fib_1_000, bar_index)
        label.set_x(label_fib_0_786, bar_index)
        label.set_x(label_fib_0_700, bar_index)
        label.set_x(label_fib_0_650, bar_index)
        label.set_x(label_fib_0_618, bar_index)
        label.set_x(label_fib_0_500, bar_index)
        label.set_x(label_fib_0_400, bar_index)
        label.set_x(label_fib_0_382, bar_index)
        label.set_x(label_fib_0_300, bar_index)
        label.set_x(label_fib_0_236, bar_index)
        label.set_x(label_fib_0_000, bar_index)

// После завершения цикла (STATE_WAITING_RESET) - продолжаем тянуть ТОЛЬКО линию минимума
// Остальные линии остаются зафиксированными на момент окончания цикла
if showFibLines and systemState == STATE_WAITING_RESET and fiboWasDrawn and not na(line_0_000)
    line.set_x2(line_0_000, bar_index)

prevSystemState := systemState

// Линии ордеров (обновляем на каждом баре для текущего цикла)
if showOrders and canBuildFibo
    // Удаляем старые линии ордеров
    line.delete(lineOrder1)
    line.delete(lineOrder2)
    line.delete(lineOrder3)
    label.delete(labelOrder1)
    label.delete(labelOrder2)
    label.delete(labelOrder3)
    line.delete(lineStopLoss)
    label.delete(labelStopLoss)
    
    // Ордер 1 ($100)
    if not position1Open
        lineOrder1 := line.new(bar_index - 20, order1Price, bar_index + 20, order1Price, 
             color=color.new(color.lime, 0), width=3, style=line.style_solid)
        labelOrder1 := label.new(bar_index, order1Price, str.tostring(orderLevel1, "#.#") + " BUY $" + str.tostring(orderAmount1, "#") + "\n@ " + str.tostring(order1Price, "#.####"), 
             color=color.new(color.lime, 0), textcolor=color.white, style=label.style_label_right, size=size.small)
    
    // Ордер 2 ($150)
    if not position2Open
        lineOrder2 := line.new(bar_index - 20, order2Price, bar_index + 20, order2Price, 
             color=color.new(color.yellow, 0), width=3, style=line.style_solid)
        labelOrder2 := label.new(bar_index, order2Price, str.tostring(orderLevel2, "#.#") + " BUY $" + str.tostring(orderAmount2, "#") + "\n@ " + str.tostring(order2Price, "#.####"), 
             color=color.new(color.yellow, 0), textcolor=color.black, style=label.style_label_right, size=size.small)
    
    // Ордер 3 ($250)
    if not position3Open
        lineOrder3 := line.new(bar_index - 20, order3Price, bar_index + 20, order3Price, 
             color=color.new(color.orange, 0), width=3, style=line.style_solid)
        labelOrder3 := label.new(bar_index, order3Price, str.tostring(orderLevel3, "#.#") + " BUY $" + str.tostring(orderAmount3, "#") + "\n@ " + str.tostring(order3Price, "#.####"), 
             color=color.new(color.orange, 0), textcolor=color.white, style=label.style_label_right, size=size.small)
    
    // Линия стоп-лосса на уровне 0
    lineStopLoss := line.new(bar_index - 20, fib_0_000, bar_index + 20, fib_0_000, 
         color=color.new(color.red, 0), width=2, style=line.style_dotted)
    labelStopLoss := label.new(bar_index, fib_0_000, "STOP-LOSS", 
         color=color.new(color.red, 0), textcolor=color.white, style=label.style_label_right, size=size.small)

// ============================================================================
// ИНФОРМАЦИОННАЯ ТАБЛИЦА
// ============================================================================

if showInfo
    var table infoTable = table.new(position.top_right, 2, 16, 
         bgcolor=color.new(color.gray, 85), 
         frame_color=color.new(color.gray, 50), 
         frame_width=1)
    
    // Заголовок
    table.cell(infoTable, 0, 0, "Параметр", 
         bgcolor=color.new(color.blue, 70), text_color=color.white, text_size=size.small)
    table.cell(infoTable, 1, 0, "Значение", 
         bgcolor=color.new(color.blue, 70), text_color=color.white, text_size=size.small)
    
    // Состояние системы
    stateText = switch systemState
        STATE_SEARCHING_MIN => "Поиск мин."
        STATE_WAITING_GROWTH => "Ожид. рост"
        STATE_FIBO_ACTIVE => "Фибо активно"
        STATE_WAITING_RESET => "Ожид. reset"
        => "—"
    table.cell(infoTable, 0, 1, "Состояние", text_size=size.small, bgcolor=color.new(color.orange, 80))
    table.cell(infoTable, 1, 1, stateText, text_size=size.small, bgcolor=color.new(color.orange, 80))
    
    // Минимум
    table.cell(infoTable, 0, 2, "Минимум", text_size=size.small)
    table.cell(infoTable, 1, 2, not na(confirmedMin) ? str.tostring(confirmedMin, "#.####") + (minUsesBody ? " (body)" : "") : "—", text_size=size.small)
    
    // Максимум
    table.cell(infoTable, 0, 3, "Максимум", text_size=size.small)
    table.cell(infoTable, 1, 3, not na(confirmedMax) ? str.tostring(confirmedMax, "#.####") + (maxUsesBody ? " (body)" : "") : "—", text_size=size.small)
    
    // Рост
    growthDisplay = not na(confirmedMin) ? ((high - confirmedMin) / confirmedMin) * 100 : 0
    table.cell(infoTable, 0, 4, "Рост от мин.", text_size=size.small)
    table.cell(infoTable, 1, 4, str.tostring(growthDisplay, "#.##") + "%" + (growthDisplay >= 100 ? " ✅" : ""), 
         text_color=growthDisplay >= 100 ? color.lime : color.white, text_size=size.small)
    
    // Разделитель
    table.cell(infoTable, 0, 5, "━━━━━━━━", text_size=size.tiny)
    table.cell(infoTable, 1, 5, "━━━━━━━━", text_size=size.tiny)
    
    // Этап торговли (Stage)
    stageText = sellCount == 0 ? "Этап 1 (0.618/0.5/0.382)" : "Этап 2 (0.5/0.382/0.236)"
    stageColor = sellCount == 0 ? color.aqua : color.yellow
    table.cell(infoTable, 0, 6, "Stage", text_size=size.small, bgcolor=color.new(stageColor, 70))
    table.cell(infoTable, 1, 6, stageText, text_size=size.small, bgcolor=color.new(stageColor, 70))
    
    // Ордера
    table.cell(infoTable, 0, 7, "Ордер $" + str.tostring(orderAmount1, "#"), text_size=size.small, 
         text_color=position1Open ? color.lime : color.white)
    table.cell(infoTable, 1, 7, str.tostring(orderLevel1, "#.#") + " → " + str.tostring(sellLevel1, "#.#") + (position1Open ? " ✅" : ""), 
         text_size=size.small, text_color=position1Open ? color.lime : color.white)
    
    table.cell(infoTable, 0, 8, "Ордер $" + str.tostring(orderAmount2, "#"), text_size=size.small,
         text_color=position2Open ? color.lime : color.white)
    table.cell(infoTable, 1, 8, str.tostring(orderLevel2, "#.#") + " → " + str.tostring(sellLevel2, "#.#") + (position2Open ? " ✅" : ""), 
         text_size=size.small, text_color=position2Open ? color.lime : color.white)
    
    table.cell(infoTable, 0, 9, "Ордер $" + str.tostring(orderAmount3, "#"), text_size=size.small,
         text_color=position3Open ? color.lime : color.white)
    table.cell(infoTable, 1, 9, str.tostring(orderLevel3, "#.#") + " → " + str.tostring(sellLevel3, "#.#") + (position3Open ? " ✅" : ""), 
         text_size=size.small, text_color=position3Open ? color.lime : color.white)
    
    // Разделитель
    table.cell(infoTable, 0, 10, "━━━━━━━━", text_size=size.tiny)
    table.cell(infoTable, 1, 10, "━━━━━━━━", text_size=size.tiny)
    
    // Перехай разрешён?
    table.cell(infoTable, 0, 11, "Перехай", text_size=size.small)
    table.cell(infoTable, 1, 11, touchedZone06_07 ? "Заблокирован ❌" : "Разрешён ✅", 
         text_color=touchedZone06_07 ? color.red : color.lime, text_size=size.small)
    
    // Счетчик sell
    table.cell(infoTable, 0, 12, "SELL count", text_size=size.small)
    table.cell(infoTable, 1, 12, str.tostring(sellCount) + "/2" + (sellCount >= 2 ? " (PAUSE)" : ""), 
         text_color=sellCount >= 2 ? color.yellow : color.white, text_size=size.small)
    
    // Текущая цена
    table.cell(infoTable, 0, 13, "Текущая цена", text_size=size.small)
    table.cell(infoTable, 1, 13, str.tostring(close, "#.####"), text_size=size.small)

// ============================================================================
// АЛЕРТЫ
// ============================================================================

// Алерт: Новый минимум найден
alertcondition(systemState[1] == STATE_SEARCHING_MIN and systemState == STATE_WAITING_GROWTH, 
     title="New Low найден", message="📍 Новый минимум найден, ждём +100%")

// Алерт: Импульс +100% выполнен (Фибо построено)
alertcondition(systemState[1] == STATE_WAITING_GROWTH and systemState == STATE_FIBO_ACTIVE, 
     title="Импульс +100%", message="✅ Импульс +100% выполнен! Фибо построено")

// Алерт: Перехай → Фибо обновлено
alertcondition(canBuildFibo and confirmedMax > confirmedMax[1] and not touchedZone06_07, 
     title="Перехай", message="📈 Перехай! Фибо обновлено")

// BUY алерты
alertcondition(touchOrder1 and not position1Open[1] and systemState == STATE_FIBO_ACTIVE, 
     title="BUY на уровне 1", message="🟢 BUY $100 сработал")
alertcondition(touchOrder2 and not position2Open[1] and systemState == STATE_FIBO_ACTIVE, 
     title="BUY на уровне 2", message="🟢 BUY $150 сработал")
alertcondition(touchOrder3 and not position3Open[1] and systemState == STATE_FIBO_ACTIVE, 
     title="BUY на уровне 3", message="🟢 BUY $250 сработал")

// SELL алерты
alertcondition(touchSell1 and position1Open[1] and sellCount[1] == 0, 
     title="SELL #1", message="🔴 SELL #1 выполнен! Переход на Этап 2")
alertcondition(touchSell1 and position1Open[1] and sellCount[1] >= 1, 
     title="SELL #2", message="🔴 SELL #2 выполнен! Торговля остановлена")
alertcondition(touchSell2 and position2Open[1], 
     title="SELL позиции 2", message="🔴 SELL $150 сработал")
alertcondition(touchSell3 and position3Open[1] and not position2Open[1], 
     title="SELL позиции 3", message="🔴 SELL $250 сработал")

// STOP-LOSS алерт
alertcondition(touchLevel0 and (position1Open[1] or position2Open[1] or position3Open[1]), 
     title="STOP-LOSS", message="⛔ STOP-LOSS на уровне 0!")

// Алерт: Stage 2 started
alertcondition(sellCount == 1 and sellCount[1] == 0, 
     title="Stage 2 started", message="📊 Stage 2 started - ордера сдвинуты на 0.5/0.382/0.236")

// Алерт: PAUSE режим
alertcondition(systemState == STATE_WAITING_RESET and systemState[1] == STATE_FIBO_ACTIVE, 
     title="PAUSE", message="⏸️ Торговля остановлена. Ждём новый хай или новый лой")

// Алерт: Новый лой в PAUSE → ждём снова +100%
alertcondition(systemState == STATE_SEARCHING_MIN and systemState[1] == STATE_WAITING_RESET, 
     title="Новый лой → Reset", message="📉 Новый лой! Ждём снова +100%")

// ============================================================================
// PLOTS для алертов
// ============================================================================
plot(confirmedMin, "Минимум", color=color.new(color.green, 100), display=display.data_window)
plot(confirmedMax, "Максимум", color=color.new(color.red, 100), display=display.data_window)
plot(canBuildFibo ? 1 : 0, "Фибо активно", color=color.new(color.blue, 100), display=display.data_window)
plotchar(systemState, "Состояние", "", location=location.top)
