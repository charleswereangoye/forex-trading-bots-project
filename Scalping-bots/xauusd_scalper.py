import MetaTrader5 as mt5
import pandas as pd
import time
from datetime import datetime

# ==============================
# CONFIGURATION
# ==============================

SYMBOL = "XAUUSD"
TIMEFRAME = mt5.TIMEFRAME_M1
STOP_LOSS_POINTS = 150
TAKE_PROFIT_POINTS = 300
MAGIC_NUMBER = 10001
SPREAD_LIMIT = 100
LOT_FIXED = 0.01
BREAK_EVEN_BUFFER = 10  # move SL 10 points beyond entry to cover spread

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

print("Trade mode:", symbol_info.trade_mode)

POINT = symbol_info.point

# ==============================
# FUNCTIONS
# ==============================

def get_data():
    rates = mt5.copy_rates_from_pos(SYMBOL, TIMEFRAME, 0, 200)
    if rates is None:
        return None

    df = pd.DataFrame(rates)
    df['ema20'] = df['close'].ewm(span=20).mean()
    df['ema50'] = df['close'].ewm(span=50).mean()
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
    print("Current Spread:", spread)
    return spread <= SPREAD_LIMIT


def has_open_position():
    positions = mt5.positions_get(symbol=SYMBOL)
    return positions is not None and len(positions) > 0


def place_trade(signal):

    if not spread_ok():
        print("Spread too high.")
        return

    if has_open_position():
        print("Position already open.")
        return

    tick = mt5.symbol_info_tick(SYMBOL)

    if signal == "buy":
        price = tick.ask
        sl = price - STOP_LOSS_POINTS * POINT
        tp = price + TAKE_PROFIT_POINTS * POINT
        order_type = mt5.ORDER_TYPE_BUY
    else:
        price = tick.bid
        sl = price + STOP_LOSS_POINTS * POINT
        tp = price - TAKE_PROFIT_POINTS * POINT
        order_type = mt5.ORDER_TYPE_SELL

    filling_modes = [
        mt5.ORDER_FILLING_FOK,
        mt5.ORDER_FILLING_IOC,
        mt5.ORDER_FILLING_RETURN
    ]

    for filling in filling_modes:

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
            "comment": "XAUUSD M1 Scalper",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": filling,
        }

        result = mt5.order_send(request)

        if result.retcode == mt5.TRADE_RETCODE_DONE:
            print(f"{signal.upper()} trade executed at {price}")
            return
        else:
            print("Filling mode failed:", filling, result.comment)

    print("All filling modes failed.")


def manage_break_even():
    positions = mt5.positions_get(symbol=SYMBOL)
    if positions is None:
        return

    tick = mt5.symbol_info_tick(SYMBOL)

    for position in positions:

        entry = position.price_open
        sl = position.sl
        tp = position.tp
        ticket = position.ticket

        # BUY position
        if position.type == mt5.ORDER_TYPE_BUY:

            current_price = tick.bid
            profit_points = (current_price - entry) / POINT

            # Move SL only if 1R reached AND not already moved
            if profit_points >= STOP_LOSS_POINTS and sl < entry:

                new_sl = entry + BREAK_EVEN_BUFFER * POINT

                request = {
                    "action": mt5.TRADE_ACTION_SLTP,
                    "position": ticket,
                    "symbol": SYMBOL,
                    "sl": new_sl,
                    "tp": tp,
                }

                result = mt5.order_send(request)
                print("BUY moved to BE:", result)

        # SELL position
        elif position.type == mt5.ORDER_TYPE_SELL:

            current_price = tick.ask
            profit_points = (entry - current_price) / POINT

            if profit_points >= STOP_LOSS_POINTS and (sl > entry or sl == 0.0):

                new_sl = entry - BREAK_EVEN_BUFFER * POINT

                request = {
                    "action": mt5.TRADE_ACTION_SLTP,
                    "position": ticket,
                    "symbol": SYMBOL,
                    "sl": new_sl,
                    "tp": tp,
                }

                result = mt5.order_send(request)
                print("SELL moved to BE:", result)


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

        manage_break_even()  # always manage open trades

        current_candle_time = df['time'].iloc[-1]

        if last_candle_time != current_candle_time:
            last_candle_time = current_candle_time

            print("New Candle:", datetime.now())
            print("EMA20:", df['ema20'].iloc[-1],
                  "EMA50:", df['ema50'].iloc[-1])

            signal = check_signal(df)

            if signal:
                print("Signal:", signal)
                place_trade(signal)

        time.sleep(1)

    except Exception as e:
        print("Error:", e)
        time.sleep(1)
