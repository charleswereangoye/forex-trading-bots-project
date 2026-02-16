import MetaTrader5 as mt5
import pandas as pd
import time
from datetime import datetime

# ==============================
# CONFIGURATION
# ==============================
SYMBOL = "XAUUSD"
TIMEFRAME = mt5.TIMEFRAME_M1
MAGIC_NUMBER = 10001
SPREAD_LIMIT = 100
LOT_FIXED = 0.03

BREAK_EVEN_BUFFER = 10        # points beyond entry
ATR_PERIOD = 14
ATR_MULTIPLIER_SL = 1.5       # SL = ATR * multiplier
TAKE_PROFIT_MULTIPLIER = 2    # TP = SL * multiplier
PARTIAL_CLOSE_RATIO = 0.5     # close 50% at 1R
TRAIL_START_MULTIPLIER = 1.5  # start trailing after 1.5R
TRAIL_DISTANCE = 30            # trail SL 30 points behind

# ==============================
# CONNECT TO MT5
# ==============================
if not mt5.initialize():
    print("MT5 initialization failed")
    quit()

account_info = mt5.account_info()
if account_info is None:
    print("Failed to get account info")
    mt5.shutdown()
    quit()

print(f"Connected to account: {account_info.login}")
print(f"Balance: {account_info.balance}")

symbol_info = mt5.symbol_info(SYMBOL)
if symbol_info is None:
    print("Symbol not found")
    mt5.shutdown()
    quit()

if not symbol_info.visible:
    mt5.symbol_select(SYMBOL, True)

POINT = symbol_info.point
MIN_STOP = symbol_info.trade_stops_level + 1
print("Trade mode:", symbol_info.trade_mode)

# ==============================
# TRADE STATE TRACKER
# ==============================
trade_flags = {}  # {ticket: {'partial_closed': False, 'sl_moved_be': False, 'sl_set': False}}

# ==============================
# DATA FUNCTIONS
# ==============================
def get_data():
    rates = mt5.copy_rates_from_pos(SYMBOL, TIMEFRAME, 0, 200)
    if rates is None:
        return None
    df = pd.DataFrame(rates)
    df['ema20'] = df['close'].ewm(span=20).mean()
    df['ema50'] = df['close'].ewm(span=50).mean()

    # ATR calculation
    df['high_low'] = df['high'] - df['low']
    df['high_close_prev'] = abs(df['high'] - df['close'].shift(1))
    df['low_close_prev'] = abs(df['low'] - df['close'].shift(1))
    df['tr'] = df[['high_low', 'high_close_prev', 'low_close_prev']].max(axis=1)
    df['atr'] = df['tr'].rolling(ATR_PERIOD).mean()
    return df

def check_signal(df):
    if df['ema20'].iloc[-1] > df['ema50'].iloc[-1]:
        return "buy"
    elif df['ema20'].iloc[-1] < df['ema50'].iloc[-1]:
        return "sell"
    return None

def spread_ok():
    tick = mt5.symbol_info_tick(SYMBOL)
    spread = (tick.ask - tick.bid) / POINT
    return spread <= SPREAD_LIMIT

def has_open_position():
    positions = mt5.positions_get(symbol=SYMBOL)
    return positions is not None and len(positions) > 0

# ==============================
# TRADE FUNCTIONS
# ==============================
def place_trade(signal, df):
    if not spread_ok():
        print("Spread too high.")
        return

    tick = mt5.symbol_info_tick(SYMBOL)

    # Pending stop order prices
    if signal == "buy":
        price = tick.ask + POINT
        order_type = mt5.ORDER_TYPE_BUY_STOP
        # dummy SL/TP far away to avoid immediate close
        sl = price - 1000 * POINT
        tp = price + 1000 * POINT
    else:
        price = tick.bid - POINT
        order_type = mt5.ORDER_TYPE_SELL_STOP
        sl = price + 1000 * POINT
        tp = price - 1000 * POINT

    request = {
        "action": mt5.TRADE_ACTION_PENDING,
        "symbol": SYMBOL,
        "volume": LOT_FIXED,
        "type": order_type,
        "price": price,
        "sl": sl,
        "tp": tp,
        "deviation": 20,
        "magic": MAGIC_NUMBER,
        "comment": "XAUUSD M1 Scalp",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_FOK
    }

    result = mt5.order_send(request)
    if result.retcode == mt5.TRADE_RETCODE_DONE:
        print(f"{signal.upper()} STOP order placed at {price} (SL/TP set after execution)")
    else:
        print(f"Failed to place order: {result.retcode}, {result.comment}")

# ==============================
# TRADE MANAGEMENT
# ==============================
def manage_trades(df):
    positions = mt5.positions_get(symbol=SYMBOL)
    if positions is None:
        return

    tick = mt5.symbol_info_tick(SYMBOL)
    atr = df['atr'].iloc[-1]
    sl_points = int(atr * ATR_MULTIPLIER_SL)
    trail_start = sl_points * TRAIL_START_MULTIPLIER

    for pos in positions:
        ticket = pos.ticket
        entry = pos.price_open
        sl = pos.sl
        tp = pos.tp
        volume = pos.volume

        # initialize flags
        if ticket not in trade_flags:
            trade_flags[ticket] = {'partial_closed': False, 'sl_moved_be': False, 'sl_set': False}

        # 1️⃣ Set ATR-based SL & TP if dummy values
        if not trade_flags[ticket]['sl_set'] and (sl > 100 * POINT or sl == 0.0):
            sl_new = entry - sl_points * POINT if pos.type == mt5.ORDER_TYPE_BUY else entry + sl_points * POINT
            tp_new = entry + sl_points * TAKE_PROFIT_MULTIPLIER * POINT if pos.type == mt5.ORDER_TYPE_BUY else entry - sl_points * TAKE_PROFIT_MULTIPLIER * POINT
            mt5.order_send({
                "action": mt5.TRADE_ACTION_SLTP,
                "position": ticket,
                "symbol": SYMBOL,
                "sl": sl_new,
                "tp": tp_new
            })
            trade_flags[ticket]['sl_set'] = True
            print(f"{'BUY' if pos.type==mt5.ORDER_TYPE_BUY else 'SELL'} SL/TP set after execution")

        # Determine current price & profit points
        current_price = tick.bid if pos.type == mt5.ORDER_TYPE_BUY else tick.ask
        profit_points = (current_price - entry) / POINT if pos.type == mt5.ORDER_TYPE_BUY else (entry - current_price) / POINT

        # 2️⃣ Move SL to Breakeven
        if profit_points >= sl_points and not trade_flags[ticket]['sl_moved_be']:
            new_sl = entry + BREAK_EVEN_BUFFER * POINT if pos.type == mt5.ORDER_TYPE_BUY else entry - BREAK_EVEN_BUFFER * POINT
            mt5.order_send({"action": mt5.TRADE_ACTION_SLTP, "position": ticket, "symbol": SYMBOL, "sl": new_sl, "tp": tp})
            trade_flags[ticket]['sl_moved_be'] = True
            print(f"{'BUY' if pos.type==mt5.ORDER_TYPE_BUY else 'SELL'} moved to BE")

        # 3️⃣ Partial close at 1R
        if profit_points >= sl_points and not trade_flags[ticket]['partial_closed']:
            close_volume = volume * PARTIAL_CLOSE_RATIO
            order_type = mt5.ORDER_TYPE_SELL if pos.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY
            mt5.order_send({"action": mt5.TRADE_ACTION_DEAL, "position": ticket, "volume": close_volume, "type": order_type, "price": current_price, "deviation": 20})
            trade_flags[ticket]['partial_closed'] = True
            print(f"{'BUY' if pos.type==mt5.ORDER_TYPE_BUY else 'SELL'} partial close executed")

        # 4️⃣ Trailing stop after 1.5R
        if profit_points >= trail_start:
            if pos.type == mt5.ORDER_TYPE_BUY:
                new_sl = max(sl, current_price - TRAIL_DISTANCE * POINT)
            else:
                new_sl = min(sl, current_price + TRAIL_DISTANCE * POINT)
            if new_sl != sl:
                mt5.order_send({"action": mt5.TRADE_ACTION_SLTP, "position": ticket, "symbol": SYMBOL, "sl": new_sl, "tp": tp})
                print(f"{'BUY' if pos.type==mt5.ORDER_TYPE_BUY else 'SELL'} trailing SL updated")

# ==============================
# MAIN LOOP
# ==============================
print("Starting XAUUSD Scalper...")
last_candle_time = None

while True:
    try:
        df = get_data()
        if df is None:
            time.sleep(1)
            continue

        manage_trades(df)

        current_candle_time = df['time'].iloc[-1]
        if last_candle_time != current_candle_time:
            last_candle_time = current_candle_time
            print("New Candle:", datetime.now())
            print("EMA20:", df['ema20'].iloc[-1], "EMA50:", df['ema50'].iloc[-1])

            signal = check_signal(df)
            if signal:
                print("Signal:", signal)
                place_trade(signal, df)

        time.sleep(1)

    except Exception as e:
        print("Error:", e)
        time.sleep(1)
