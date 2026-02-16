import MetaTrader5 as mt5
import pandas as pd
import time
from datetime import datetime

# ==============================
# CONFIGURATION
# ==============================

SYMBOL = "XAUUSD"
TIMEFRAME = mt5.TIMEFRAME_M1
RISK_PERCENT = 0.01
STOP_LOSS_POINTS = 150
TAKE_PROFIT_POINTS = 300
MAGIC_NUMBER = 10001
SPREAD_LIMIT = 100
LOT_FIXED = 0.01   # Use fixed lot for stability (can switch to dynamic later)

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

# Ensure symbol exists
symbol_info = mt5.symbol_info(SYMBOL)
if symbol_info is None:
    print("Symbol not found")
    mt5.shutdown()
    quit()

if not symbol_info.visible:
    mt5.symbol_select(SYMBOL, True)

print("Trade mode:", symbol_info.trade_mode)

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
    # Trend-based logic (better for scalping)
    if df['ema20'].iloc[-1] > df['ema50'].iloc[-1]:
        return "buy"
    elif df['ema20'].iloc[-1] < df['ema50'].iloc[-1]:
        return "sell"
    return None


def spread_ok():
    tick = mt5.symbol_info_tick(SYMBOL)
    spread = (tick.ask - tick.bid) / mt5.symbol_info(SYMBOL).point
    print("Current Spread:", spread)
    return spread <= SPREAD_LIMIT


def has_open_position():
    positions = mt5.positions_get(symbol=SYMBOL)
    if positions is None:
        return False
    return len(positions) > 0


def place_trade(signal):

    if not spread_ok():
        print("Spread too high. Skipping trade.")
        return

    if has_open_position():
        print("Position already open.")
        return

    tick = mt5.symbol_info_tick(SYMBOL)
    symbol_info = mt5.symbol_info(SYMBOL)

    lot = LOT_FIXED  # Use fixed lot for now

    if signal == "buy":
        price = tick.ask
        sl = price - STOP_LOSS_POINTS * symbol_info.point
        tp = price + TAKE_PROFIT_POINTS * symbol_info.point
        order_type = mt5.ORDER_TYPE_BUY

    else:
        price = tick.bid
        sl = price + STOP_LOSS_POINTS * symbol_info.point
        tp = price - TAKE_PROFIT_POINTS * symbol_info.point
        order_type = mt5.ORDER_TYPE_SELL

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": SYMBOL,
        "volume": lot,
        "type": order_type,
        "price": price,
        "sl": sl,
        "tp": tp,
        "deviation": 20,
        "magic": MAGIC_NUMBER,
        "comment": "XAUUSD M1 Scalper",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_RETURN,
    }

    print("Sending Order...")
    result = mt5.order_send(request)

    print("Order Result:", result)

    if result.retcode != mt5.TRADE_RETCODE_DONE:
        print("Trade failed:", result.comment)
    else:
        print(f"{signal.upper()} trade executed at {price}")


# ==============================
# MAIN LOOP
# ==============================

print("Starting XAUUSD Scalper...")

last_candle_time = None

while True:
    try:
        df = get_data()
        if df is None:
            print("Failed to retrieve data")
            time.sleep(1)
            continue

        current_candle_time = df['time'].iloc[-1]

        # Trade only on new candle
        if last_candle_time != current_candle_time:
            last_candle_time = current_candle_time

            print("New Candle Formed:", datetime.now())

            signal = check_signal(df)

            print("EMA20:", df['ema20'].iloc[-1],
                  "EMA50:", df['ema50'].iloc[-1])

            if signal:
                print(f"Signal detected: {signal}")
                place_trade(signal)

        time.sleep(1)

    except Exception as e:
        print("Error:", e)
        time.sleep(1)

