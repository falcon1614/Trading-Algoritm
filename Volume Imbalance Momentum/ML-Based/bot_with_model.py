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
SYMBOL = "ALICEUSDT"
SYMBOL_CCXT = "ALICE/USDT"

WS_DEPTH_URL = "wss://fstream.binance.com/ws/aliceusdt@depth10@100ms"
WS_TRADE_URL = "wss://fstream.binance.com/ws/aliceusdt@trade"

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
HARD_MAX_SIZE = 100   # TRX contracts (safe cap)

# =====================================================
# GLOBAL STATE
# =====================================================
orderbook = {"bids": [], "asks": []}
last_trade_price = None

V1_history = []
prob_history = []

model = None
FEATURES = None

open_position = None   # {side, size, entry, atr}
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
    logger.info("Model and features loaded.")

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
        except Exception:
            await asyncio.sleep(1)

async def ws_trade_listener():
    global last_trade_price
    while RUNNING:
        try:
            async with websockets.connect(WS_TRADE_URL, ping_interval=20) as ws:
                async for msg in ws:
                    last_trade_price = float(json.loads(msg)["p"])
        except Exception:
            await asyncio.sleep(1)

# =====================================================
# INDICATORS
# =====================================================
def calculate_atr(df):
    hl = df["high"] - df["low"]
    hpc = (df["high"] - df["close"].shift(1)).abs()
    lpc = (df["low"] - df["close"].shift(1)).abs()
    tr = pd.concat([hl, hpc, lpc], axis=1).max(axis=1)
    atr = tr.rolling(ATR_PERIOD).mean().iloc[-1]
    return float(atr) if atr and not np.isnan(atr) else 0.0

# =====================================================
# FEATURE BUILDER
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
    if atr <= 0:
        return None, None

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

    try:
        return df.iloc[[-1]][FEATURES].fillna(0.0), atr
    except KeyError:
        logger.error("Feature mismatch with trained model")
        return None, None

# =====================================================
# POSITION MANAGEMENT (ATR EXIT)
# =====================================================
async def manage_position(exchange, price):
    global open_position

    if open_position is None:
        return

    side = open_position["side"]
    entry = open_position["entry"]
    atr = open_position["atr"]
    size = open_position["size"]

    if side == "LONG":
        if price <= entry - atr * SL_MULTIPLIER or price >= entry + atr * TP_MULTIPLIER:
            await exchange.create_order(SYMBOL_CCXT, "market", "sell", size)
            logger.info("LONG closed (ATR)")
            open_position = None

    elif side == "SHORT":
        if price >= entry + atr * SL_MULTIPLIER or price <= entry - atr * TP_MULTIPLIER:
            await exchange.create_order(SYMBOL_CCXT, "market", "buy", size)
            logger.info("SHORT closed (ATR)")
            open_position = None

# =====================================================
# ENTRY
# =====================================================
async def open_new_position(exchange, side, price, atr):
    global open_position

    stop_distance = atr * SL_MULTIPLIER
    if stop_distance <= 0:
        return

    size = RISK_AMOUNT / stop_distance
    size = min(size, HARD_MAX_SIZE)
    size = round(size, 1)

    if size * price < MIN_NOTIONAL:
        return

    order_side = "buy" if side == "LONG" else "sell"
    await exchange.create_order(SYMBOL_CCXT, "market", order_side, size)

    open_position = {
        "side": side,
        "entry": price,
        "size": size,
        "atr": atr
    }

    logger.info(f"{side} opened | size={size}")

# =====================================================
# MAIN LOOP
# =====================================================
async def trading_loop(exchange):
    load_model()
    logger.warning("🚨 LIVE TRADING ENABLED 🚨")

    while RUNNING:
        try:
            if not orderbook["bids"] or last_trade_price is None:
                await asyncio.sleep(CHECK_INTERVAL)
                continue

            v1 = sum(float(x[1]) for x in orderbook["asks"]) - \
                 sum(float(x[1]) for x in orderbook["bids"])
            V1_history.append(v1)

            ohlcv = await exchange.fetch_ohlcv(SYMBOL_CCXT, TIMEFRAME, limit=200)
            df = pd.DataFrame(
                ohlcv,
                columns=["ts", "open", "high", "low", "close", "volume"]
            ).astype(float)

            X, atr = build_features(df, v1)
            if X is None:
                await asyncio.sleep(CHECK_INTERVAL)
                continue

            prob = float(model.predict(X)[0])
            prob_history.append(prob)

            if len(prob_history) < 50:
                continue

            cutoff = np.quantile(prob_history[-200:], ML_QUANTILE)

            await manage_position(exchange, last_trade_price)

            if open_position is None:
                if prob >= cutoff:
                    await open_new_position(exchange, "LONG", last_trade_price, atr)
                elif prob <= 1 - cutoff:
                    await open_new_position(exchange, "SHORT", last_trade_price, atr)

            await asyncio.sleep(CHECK_INTERVAL)

        except Exception as e:
            logger.error(f"Runtime error: {e}")
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
