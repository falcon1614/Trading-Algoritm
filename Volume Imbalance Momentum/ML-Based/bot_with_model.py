import ccxt.async_support as ccxt
import asyncio
import websockets
import json
import numpy as np
import pandas as pd
import lightgbm as lgb
import joblib
import logging
import os
import dotenv

# =====================================================
# ENV & LOGGING
# =====================================================
dotenv.load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")
logger = logging.getLogger(__name__)

API_KEY = os.getenv("API_KEY")
SECRET_KEY = os.getenv("SECRET_KEY")

# =====================================================
# PATHS
# =====================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MODEL_PATH = os.path.join(BASE_DIR, "lgb_model.txt")
FEATURE_PATH = MODEL_PATH + ".features.pkl"

# =====================================================
# CONFIG
# =====================================================
SYMBOL = "ALCHUSDT"
SYMBOL_CCXT = "ALCH/USDT"

WS_DEPTH_URL = "wss://fstream.binance.com/ws/alchusdt@depth10@100ms"
WS_TRADE_URL = "wss://fstream.binance.com/ws/alchusdt@trade"

TIMEFRAME = "1m"
CHECK_INTERVAL = 0.1

LOOKBACK_V1 = 50
ATR_PERIOD = 14
ML_QUANTILE = 0.75

RISK_AMOUNT = 0.5        # USDT risk per trade
LEVERAGE = 5
MAX_POSITIONS = 1

SL_MULTIPLIER = 1.0
TP_MULTIPLIER = 2.0

MIN_NOTIONAL = 5
HARD_MAX_SIZE = 50       # HARD SAFETY CAP

# =====================================================
# GLOBAL STATE
# =====================================================
orderbook = {"bids": [], "asks": []}
last_trade_price = None

V1_history = []
prob_history = []

model = None
FEATURES = None

position_open = False
RUNNING = True

# =====================================================
# EXCHANGE
# =====================================================
async def create_exchange():
    exchange = ccxt.binance({
        "apiKey": API_KEY,
        "secret": SECRET_KEY,
        "enableRateLimit": True,
        "options": {"defaultType": "future"},
    })
    await exchange.load_markets()
    await exchange.set_leverage(LEVERAGE, SYMBOL_CCXT)
    return exchange

# =====================================================
# LOAD MODEL
# =====================================================
def load_model():
    global model, FEATURES
    model = lgb.Booster(model_file=MODEL_PATH)
    FEATURES = joblib.load(FEATURE_PATH)
    logger.info("Model and features loaded successfully.")

# =====================================================
# WEBSOCKETS
# =====================================================
async def ws_depth_listener():
    global orderbook
    while RUNNING:
        try:
            async with websockets.connect(WS_DEPTH_URL, ping_interval=20) as ws:
                async for msg in ws:
                    d = json.loads(msg)
                    orderbook["bids"] = d.get("b", [])
                    orderbook["asks"] = d.get("a", [])
        except Exception as e:
            logger.warning(f"Depth WS reconnecting: {e}")
            await asyncio.sleep(2)

async def ws_trade_listener():
    global last_trade_price
    while RUNNING:
        try:
            async with websockets.connect(WS_TRADE_URL, ping_interval=20) as ws:
                async for msg in ws:
                    d = json.loads(msg)
                    last_trade_price = float(d["p"])
        except Exception as e:
            logger.warning(f"Trade WS reconnecting: {e}")
            await asyncio.sleep(2)

# =====================================================
# INDICATORS
# =====================================================
def calculate_atr(df):
    hl = df["high"] - df["low"]
    hpc = (df["high"] - df["close"].shift(1)).abs()
    lpc = (df["low"] - df["close"].shift(1)).abs()
    tr = pd.concat([hl, hpc, lpc], axis=1).max(axis=1)
    atr = tr.rolling(ATR_PERIOD).mean().iloc[-1]
    return float(atr) if not np.isnan(atr) else 0.0

# =====================================================
# FEATURE BUILDER (MATCH TRAINING)
# =====================================================
def build_features(df, v1):
    if len(df) < 50 or not orderbook["bids"] or not orderbook["asks"]:
        return None, None

    df = df.copy()

    df["ret1"] = np.log(df["close"] / df["close"].shift(1))
    df["ret3"] = np.log(df["close"] / df["close"].shift(3))
    df["vol_ma3"] = df["volume"].rolling(3).mean()
    df["vol_ma10"] = df["volume"].rolling(10).mean()

    atr = calculate_atr(df)
    df["atr"] = atr

    df["ret1_z"] = (
        (df["ret1"] - df["ret1"].rolling(50).mean()) /
        (df["ret1"].rolling(50).std() + 1e-9)
    )

    df.loc[df.index[-1], "V1"] = v1
    df.loc[df.index[-1], "V1_ma50"] = np.mean(V1_history[-50:])
    df.loc[df.index[-1], "V1_std50"] = np.std(V1_history[-50:])

    bid = float(orderbook["bids"][0][0])
    ask = float(orderbook["asks"][0][0])
    df.loc[df.index[-1], "spread"] = (ask - bid) / ((ask + bid) / 2)

    X = df.iloc[[-1]][FEATURES].fillna(0.0)
    return X, atr

# =====================================================
# REAL ORDER WITH EXCHANGE SL / TP
# =====================================================
async def place_real_order(exchange, side, price, atr):
    global position_open

    if atr <= 0 or position_open:
        return

    # --- Correct size formula ---
    raw_size = (RISK_AMOUNT / atr) * LEVERAGE

    # --- Balance cap ---
    bal = await exchange.fetch_balance()
    free_usdt = bal["USDT"]["free"]
    max_size_by_balance = (free_usdt * LEVERAGE) / price

    size = min(raw_size, max_size_by_balance, HARD_MAX_SIZE)
    size = float(round(size, 3))

    if size * price < MIN_NOTIONAL:
        return

    sl = price - atr * SL_MULTIPLIER if side == "BUY" else price + atr * SL_MULTIPLIER
    tp = price + atr * TP_MULTIPLIER if side == "BUY" else price - atr * TP_MULTIPLIER

    entry_side = "buy" if side == "BUY" else "sell"
    exit_side = "sell" if side == "BUY" else "buy"

    await exchange.create_order(SYMBOL_CCXT, "market", entry_side, size)

    await exchange.create_order(
        SYMBOL_CCXT,
        "STOP_MARKET",
        exit_side,
        size,
        params={"stopPrice": round(sl, 4), "reduceOnly": True}
    )

    await exchange.create_order(
        SYMBOL_CCXT,
        "TAKE_PROFIT_MARKET",
        exit_side,
        size,
        params={"stopPrice": round(tp, 4), "reduceOnly": True}
    )

    position_open = True
    logger.info(f"REAL ORDER {side} | size={size}")

# =====================================================
# MAIN TRADING LOOP
# =====================================================
async def trading_loop(exchange):
    global position_open
    load_model()
    logger.warning("🚨 LIVE TRADING ENABLED 🚨")

    while RUNNING:
        try:
            if not orderbook["bids"] or last_trade_price is None:
                await asyncio.sleep(CHECK_INTERVAL)
                continue

            vbuy = sum(float(x[1]) for x in orderbook["asks"])
            vsell = sum(float(x[1]) for x in orderbook["bids"])
            v1 = vbuy - vsell
            V1_history.append(v1)

            ohlcv = await exchange.fetch_ohlcv(SYMBOL_CCXT, TIMEFRAME, limit=200)
            df = pd.DataFrame(
                ohlcv,
                columns=["ts","open","high","low","close","volume"]
            ).astype(float)

            X, atr = build_features(df, v1)
            if X is None:
                continue

            prob = float(model.predict(X)[0])
            prob_history.append(prob)

            if len(prob_history) < 50:
                continue

            cutoff = np.quantile(prob_history[-200:], ML_QUANTILE)

            if not position_open:
                if prob >= cutoff:
                    await place_real_order(exchange, "BUY", last_trade_price, atr)
                elif prob <= 1 - cutoff:
                    await place_real_order(exchange, "SELL", last_trade_price, atr)

            await asyncio.sleep(CHECK_INTERVAL)

        except Exception as e:
            logger.error(f"Trading error: {e}")
            await asyncio.sleep(1)

# =====================================================
# RUN
# =====================================================
async def main():
    exchange = await create_exchange()
    try:
        await asyncio.gather(
            ws_depth_listener(),
            ws_trade_listener(),
            trading_loop(exchange)
        )
    finally:
        await exchange.close()

if __name__ == "__main__":
    asyncio.run(main())
