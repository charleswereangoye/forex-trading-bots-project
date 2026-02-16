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
LOT_FIXED = 0.02

BREAK_EVEN_BUFFER = 10        # points beyond entry
ATR_PERIOD = 14
ATR_MULTIPLIER_SL = 1.5       # SL = ATR * multiplier
TAKE_PROFIT_MULTIPLIER = 2    # TP = SL * multiplier
TRAIL_START_MULTIPLIER = 1.5  # start trailing after 1.5R
TRAIL_DISTANCE = 30            # trail SL 30 points behind
SAFETY_BUFFER = 100            # extra points above broker minimum for SL/TP

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
print("Broker min stop level:", symbol_info.trade_stops_level)

# ==============================
# TRADE STATE TRACKER
# ==============================
trade_flags = {}  # {ticket: {'sl_moved_be': False}}

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

# ==============================
# MARKET ORDER FUNCTION
# ==============================
def place_market_order(signal, df):
    if not spread_ok():
        print("Spread too high. Skipping trade.")
        return

    tick = mt5.symbol_info_tick(SYMBOL)
    atr = df['atr'].iloc[-1]
    sl_points = int(atr * ATR_MULTIPLIER_SL)
    min_stop_distance = symbol_info.trade_stops_level * POINT + SAFETY_BUFFER

    if signal == "buy":
        price = tick.ask
        sl = price - max(sl_points * POINT, min_stop_distance)
        tp = price + max(sl_points * TAKE_PROFIT_MULTIPLIER * POINT, min_stop_distance)
        order_type = mt5.ORDER_TYPE_BUY

    else:  # sell
        price = tick.bid
        sl = price + max(sl_points * POINT, min_stop_distance)
        tp = price - max(sl_points * TAKE_PROFIT_MULTIPLIER * POINT, min_stop_distance)
        order_type = mt5.ORDER_TYPE_SELL

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": SYMBOL,
        "volume": LOT_FIXED,
        "type": order_type,
        "price": price,
        "sl": sl,
        "tp": tp,
        "deviation": 20,
        "magic": MAGIC_NUMBER,
        "comment": "Market Order Scalp",
        "type_filling": mt5.ORDER_FILLING_FOK
    }

    result = mt5.order_send(request)
    if result.retcode == mt5.TRADE_RETCODE_DONE:
        print(f"{signal.upper()} MARKET order placed at {price} (SL: {sl}, TP: {tp})")
    else:
        print(f"Failed to place market order: {result.retcode}, {result.comment}")

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

        if ticket not in trade_flags:
            trade_flags[ticket] = {'sl_moved_be': False}

        # Move SL to breakeven at 1R
        current_price = tick.bid if pos.type == mt5.ORDER_TYPE_BUY else tick.ask
        profit_points = (current_price - entry) / POINT if pos.type == mt5.ORDER_TYPE_BUY else (entry - current_price) / POINT

        if profit_points >= sl_points and not trade_flags[ticket]['sl_moved_be']:
            new_sl = entry + BREAK_EVEN_BUFFER * POINT if pos.type == mt5.ORDER_TYPE_BUY else entry - BREAK_EVEN_BUFFER * POINT
            mt5.order_send({"action": mt5.TRADE_ACTION_SLTP, "position": ticket, "symbol": SYMBOL, "sl": new_sl, "tp": tp})
            trade_flags[ticket]['sl_moved_be'] = True
            print(f"{'BUY' if pos.type==mt5.ORDER_TYPE_BUY else 'SELL'} moved to BE at 1R")

        # Trailing stop after 1.5R
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
print("Starting XAUUSD Market Scalper...")
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
                place_market_order(signal, df)

        time.sleep(1)

    except Exception as e:
        print("Error:", e)
        time.sleep(1)
