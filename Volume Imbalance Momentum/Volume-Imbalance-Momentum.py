import ccxt.async_support as ccxt
import pandas as pd
import numpy as np
import asyncio
import logging
import os
import dotenv

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)
dotenv.load_dotenv()

API_KEY = os.getenv("DEMO_API_KEY")
SECRET_KEY = os.getenv("DEMO_SECRET_KEY")

# ---------------------------
# Variables
# ---------------------------
SYMBOL = "ALCHUSDT"
WS_DEPTH_URL = "wss://fstream.binance.com/ws/alchusdt@depth10@100ms"
WS_TRADE_URL = "wss://fstream.binance.com/ws/alchusdt@trade"

TIMEFRAME = "1m"
LOOKBACK_V1 = 50
SIGMA_MULTIPLIER = 1.0

VBUY_KEY = "V_buy"
VSELL_KEY = "V_sell"
V1_KEY = "V_imbalance"

RISK_AMOUNT = 0.5
LEVERAGE = 5
MAX_POSITIONS = 2
FEE_RATE = 0.0002
SL_MULTIPLIER = 1.0
TP_MULTIPLIER = 2.0

ATR_PERIOD = 14

CHECK_INTERVAL = 0.1
open_positions = []

# ---------------------------
# Utility / Exchange helper
# ---------------------------
async def create_ccxt_exchange(api_key: str = API_KEY, secret: str = SECRET_KEY):
    exchange = ccxt.binance({
        "apiKey": api_key,
        "secret": secret,
        "enableRateLimit": True,
        "options": {"defaultType": "future"},  # change if using spot
    })
    await exchange.load_markets()
    return exchange

# ---------------------------
# Fetch OHLCV
# ---------------------------
async def fetch_data(exchange, symbol: str, timeframe: str, limit: int = 100) -> pd.DataFrame:
    try:
        ohlcv = await exchange.fetch_ohlcv(symbol, timeframe, limit=limit)

        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        # ensure numeric types
        for col in ['open', 'high', 'low', 'close', 'volume']:
            df[col] = pd.to_numeric(df[col], errors='coerce')
        return df.reset_index(drop=True)

    except Exception as e:
        logger.error(f"Error fetching data: {str(e)}")
        return pd.DataFrame()

# ---------------------------
# Fetch Order Book
# ---------------------------
async def fetch_orderbook(exchange, symbol: str, limit: int = 10):
    try:
        orderbook = await exchange.fetch_order_book(symbol, limit=limit)
        return {
            "bids": orderbook.get("bids", []),
            "asks": orderbook.get("asks", []),
            "timestamp": orderbook.get("timestamp"),
        }
    except Exception as e:
        logger.error(f"Error fetching order book: {str(e)}")
        return {"bids": [], "asks": [], "timestamp": None}

# ---------------------------
# Indicators
# ---------------------------
def calculate_atr(df: pd.DataFrame, period: int = 14) -> float:
    """
    Returns the latest ATR value. Safe to call even if df is small.
    """
    if df is None or df.empty:
        return 0.0

    # work on a small copy to avoid adding columns to original df
    tmp = df[['high', 'low', 'close']].copy().astype(float)

    tmp['H-L'] = tmp['high'] - tmp['low']
    tmp['H-PC'] = (tmp['high'] - tmp['close'].shift(1)).abs()
    tmp['L-PC'] = (tmp['low'] - tmp['close'].shift(1)).abs()

    tr = tmp[['H-L', 'H-PC', 'L-PC']].max(axis=1)

    if len(tr) < period:
        # fallback: use mean of available true ranges
        return float(tr.mean()) if not tr.empty else 0.0

    atr_series = tr.rolling(period, min_periods=1).mean()
    latest_atr = atr_series.iloc[-1]
    return float(latest_atr) if not np.isnan(latest_atr) else 0.0


def calculate_volume_imbalance(vbuy: float, vsell: float) -> float:
    return float(vbuy) - float(vsell)


def calculate_sigma(values: list, lookback: int = 50) -> float:
    if not values:
        return 0.0
    s = pd.Series(values[-lookback:])
    return float(s.std(ddof=0)) if len(s) > 0 else 0.0

# ---------------------------
# Signal generation
# ---------------------------
def generate_signal(v1: float, sigma: float) -> str:
    if sigma is None or sigma == 0:
        # smaller-sample behavior: use sign only
        if v1 > 0:
            return "BUY"
        elif v1 < 0:
            return "SELL"
        else:
            return "NEUTRAL"

    if v1 > sigma:
        return "STRONG_BUY"
    if v1 > 0:
        return "BUY"
    if v1 == 0:
        return "NEUTRAL"
    if v1 < -sigma:
        return "STRONG_SELL"
    if v1 < 0:
        return "SELL"
    return "NEUTRAL"

# ---------------------------
# Position management
# ---------------------------
async def manage_positions(exchange, open_positions: list, current_price: float):
    """
    Check SL/TP and close positions with market orders.
    open_positions is a list of dicts with keys:
      symbol, side ("long"/"short"), size, entry, sl, tp
    """
    to_close = []

    for pos in list(open_positions):  # iterate over copy to be safe
        side = pos.get("side")
        sl = pos.get("sl")
        tp = pos.get("tp")
        symbol = pos.get("symbol")
        size = pos.get("size")

        try:
            if side == "long":
                if sl is not None and current_price <= sl:
                    logger.info(f"Long SL hit for {symbol} at {current_price}, closing size {size}")
                    await exchange.create_order(symbol, "market", "sell", size)
                    to_close.append(pos)
                elif tp is not None and current_price >= tp:
                    logger.info(f"Long TP hit for {symbol} at {current_price}, closing size {size}")
                    await exchange.create_order(symbol, "market", "sell", size)
                    to_close.append(pos)

            elif side == "short":
                if sl is not None and current_price >= sl:
                    logger.info(f"Short SL hit for {symbol} at {current_price}, closing size {size}")
                    await exchange.create_order(symbol, "market", "buy", size)
                    to_close.append(pos)
                elif tp is not None and current_price <= tp:
                    logger.info(f"Short TP hit for {symbol} at {current_price}, closing size {size}")
                    await exchange.create_order(symbol, "market", "buy", size)
                    to_close.append(pos)

        except Exception as e:
            logger.error(f"Error closing position {pos}: {e}")

    # Remove closed positions
    for pos in to_close:
        try:
            open_positions.remove(pos)
        except ValueError:
            pass

    return open_positions

# ---------------------------
# Place orders
# ---------------------------
async def place_order(exchange, symbol: str, side: str, entry: float, atr: float,
                      risk_amount: float, sl_mult: float, tp_mult: float,
                      leverage: int, open_positions: list):
    """
    Final clean and correct order placement logic.
    Uses:
      - ATR based stop distance
      - Risk per trade
      - Balance-based max size
      - Hard contract size cap
      - Binance min notional rule
    """

    # 1. Calculate SL distance
    sl_dist = float(atr) * float(sl_mult)
    if sl_dist <= 0:
        logger.warning("SL distance is zero or negative, skipping order.")
        return None

    # 2. ATR-based size (raw)
    raw_size = (float(risk_amount) / (sl_dist * float(entry))) * float(leverage)

    # 3. Hard cap for safety
    HARD_MAX_SIZE = 50   # safe for your 7 USDT balance
    size = min(raw_size, HARD_MAX_SIZE)

    # 4. Fetch balance
    balance = await exchange.fetch_balance()
    available_balance = balance["USDT"]["free"]

    # 5. Dynamic maximum position size based on balance
    max_notional = available_balance * leverage
    dynamic_max_size = max_notional / entry

    # Enforce dynamic limit
    size = min(size, dynamic_max_size)

    # 6. Enforce Binance minimum notional requirement
    MIN_NOTIONAL = 5
    if size * entry < MIN_NOTIONAL:
        logger.warning(f"Order skipped: Notional {size*entry:.4f} < Binance minimum {MIN_NOTIONAL} USDT")
        return None

    # 7. Check if margin is sufficient
    required_margin = (entry * size) / leverage
    if required_margin > available_balance:
        logger.warning(
            f"Order rejected: required margin {required_margin:.4f} > available {available_balance:.4f}"
        )
        return None

    # 8. Place order
    order_side = "buy" if side == "long" else "sell"

    try:
        logger.info(f"Placing {order_side} market order for {symbol} size {size:.3f}")
        order = await exchange.create_order(symbol, "market", order_side, size)

        # 9. Record SL/TP
        sl = entry - sl_dist if side == "long" else entry + sl_dist
        tp = entry + atr * tp_mult if side == "long" else entry - atr * tp_mult

        open_positions.append({
            "symbol": symbol,
            "side": side,
            "size": size,
            "entry": entry,
            "sl": sl,
            "tp": tp,
        })
        return order

    except Exception as e:
        logger.error(f"Error placing order: {e}")
        return None

# ---------------------------
# Main loop
# ---------------------------
async def main_loop(exchange, symbol: str):
    V1_history = []
    open_positions = []

    while True:
        try:
            # --- Fetch Market Data ---
            df = await fetch_data(exchange, symbol, TIMEFRAME, limit=500)
            orderbook = await fetch_orderbook(exchange, symbol, 10)

            # if data missing, wait a bit (avoid tight loop)
            if df.empty or not orderbook["bids"] or not orderbook["asks"]:
                await asyncio.sleep(CHECK_INTERVAL)
                continue

            atr = calculate_atr(df, period=ATR_PERIOD)

            best_bid = float(orderbook["bids"][0][0])
            best_ask = float(orderbook["asks"][0][0])
            mid_price = (best_bid + best_ask) / 2.0

            # --- Volume Imbalance ---
            # buyer-initiated volume = sum of amounts at asks
            vbuy = float(sum([float(level[1]) for level in orderbook["asks"]]))
            # seller-initiated volume = sum of amounts at bids
            vsell = float(sum([float(level[1]) for level in orderbook["bids"]]))

            v1 = calculate_volume_imbalance(vbuy, vsell)

            V1_history.append(v1)
            sigma = calculate_sigma(V1_history, lookback=LOOKBACK_V1)

            # --- Generate Signal ---
            signal = generate_signal(v1, sigma)

            logger.debug(f"mid={mid_price:.2f} v1={v1:.4f} sigma={sigma:.4f} signal={signal}")

            # --- Position Management ---
            open_positions = await manage_positions(exchange, open_positions, mid_price)

            # --- Entry Conditions ---
            if len(open_positions) < MAX_POSITIONS:
                if signal in ["STRONG_BUY", "BUY"]:
                    await place_order(exchange, symbol, "long", mid_price, atr,
                                      RISK_AMOUNT, SL_MULTIPLIER, TP_MULTIPLIER,
                                      LEVERAGE, open_positions)

                if signal in ["STRONG_SELL", "SELL"]:
                    await place_order(exchange, symbol, "short", mid_price, atr,
                                      RISK_AMOUNT, SL_MULTIPLIER, TP_MULTIPLIER,
                                      LEVERAGE, open_positions)

            await asyncio.sleep(CHECK_INTERVAL)

        except Exception as e:
            logger.error(f"Main loop error: {str(e)}")
            await asyncio.sleep(0.5)

# ---------------------------
# Example runner
# ---------------------------
async def run():
    exchange = await create_ccxt_exchange()
    try:
        await main_loop(exchange, SYMBOL)
    finally:
        await exchange.close()

if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        logger.info("Stopped by user")
