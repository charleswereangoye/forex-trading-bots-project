import MetaTrader5 as mt5
import pandas as pd
import numpy as np
import time
from datetime import datetime

# ==============================
# CONFIGURATION
# ==============================

SYMBOL = "XAUUSD"
TIMEFRAME = mt5.TIMEFRAME_M1
RISK_PERCENT = 0.01   # 1% risk
STOP_LOSS_POINTS = 150  # 15 pips (adjust for broker)
TAKE_PROFIT_POINTS = 300  # 30 pips
MAGIC_NUMBER = 10001
SPREAD_LIMIT = 50  # max allowed spread

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

# Ensure symbol is available
symbol_info = mt5.symbol_info(SYMBOL)
if symbol_info is None:
    print("Symbol not found")
    mt5.shutdown()
    quit()

if not symbol_info.visible:
    mt5.symbol_select(SYMBOL, True)

# ==============================
# FUNCTIONS
# ==============================

def get_data():
    rates = mt5.copy_rates_from_pos(SYMBOL, TIMEFRAME, 0, 200)
    df = pd.DataFrame(rates)
    df['ema20'] = df['close'].ewm(span=20).mean()
    df['ema50'] = df['close'].ewm(span=50).mean()
    return df


def check_signal(df):
    if df['ema20'].iloc[-2] < df['ema50'].iloc[-2] and df['ema20'].iloc[-1] > df['ema50'].iloc[-1]:
        return "buy"

    elif df['ema20'].iloc[-2] > df['ema50'].iloc[-2] and df['ema20'].iloc[-1] < df['ema50'].iloc[-1]:
        return "sell"

    return None


def calculate_lot():
    balance = mt5.account_info().balance
    risk_amount = balance * RISK_PERCENT

    tick_value = mt5.symbol_info(SYMBOL).trade_tick_value
    tick_size = mt5.symbol_info(SYMBOL).trade_tick_size

    lot = risk_amount / (STOP_LOSS_POINTS * tick_value / tick_size)

    lot = max(0.01, round(lot, 2))
    return lot


def spread_ok():
    tick = mt5.symbol_info_tick(SYMBOL)
    spread = (tick.ask - tick.bid) / mt5.symbol_info(SYMBOL).point
    return spread <= SPREAD_LIMIT


def has_open_position():
    positions = mt5.positions_get(symbol=SYMBOL)
    return positions is not None and len(positions) > 0


def place_trade(signal):

    if not spread_ok():
        print("Spread too high, skipping trade.")
        return

    if has_open_position():
        print("Position already open.")
        return

    lot = calculate_lot()
    tick = mt5.symbol_info_tick(SYMBOL)

    if signal == "buy":
        price = tick.ask
        sl = price - STOP_LOSS_POINTS * mt5.symbol_info(SYMBOL).point
        tp = price + TAKE_PROFIT_POINTS * mt5.symbol_info(SYMBOL).point
        order_type = mt5.ORDER_TYPE_BUY

    elif signal == "sell":
        price = tick.bid
        sl = price + STOP_LOSS_POINTS * mt5.symbol_info(SYMBOL).point
        tp = price - TAKE_PROFIT_POINTS * mt5.symbol_info(SYMBOL).point
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
        "comment": "XAUUSD Scalper",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }

    result = mt5.order_send(request)

    if result.retcode != mt5.TRADE_RETCODE_DONE:
        print(f"Trade failed: {result.retcode}")
    else:
        print(f"{signal.upper()} trade placed successfully at {price}")


# ==============================
# MAIN LOOP
# ==============================

print("Starting XAUUSD Scalper...")

while True:
    try:
        df = get_data()
        signal = check_signal(df)

        if signal:
            print(f"Signal detected: {signal} at {datetime.now()}")
            place_trade(signal)

        time.sleep(60)

    except Exception as e:
        print("Error:", e)
        time.sleep(60)
