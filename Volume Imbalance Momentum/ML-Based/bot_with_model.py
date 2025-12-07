# bot_with_model.py
import joblib
import lightgbm as lgb
import pandas as pd
import numpy as np
import asyncio
import logging
import os
# import your bot functions: fetch_data, fetch_orderbook, calculate_atr, calculate_sigma, generate_signal, place_order

MODEL_PATH = 'lgb_model.txt'
P_THRESHOLD = 0.60  # tune it (0.55-0.75 typical)

# load model once
model = None
FEATURES = None

def load_model():
    global model, FEATURES
    model = lgb.Booster(model_file=MODEL_PATH)
    import joblib
    FEATURES = joblib.load(MODEL_PATH + '.features.pkl')

def make_live_features(df_recent, orderbook, V1_history):
    # df_recent is a DataFrame with at least the latest candle as last row
    latest = df_recent.iloc[-1].copy()
    # compute features inline (must match training)
    feat = {}
    feat['ret1'] = np.log(latest['close'] / df_recent['close'].shift(1).iloc[-1])
    feat['ret3'] = np.log(latest['close'] / df_recent['close'].shift(3).iloc[-1])
    feat['vol_ma3'] = df_recent['volume'].rolling(3).mean().iloc[-1]
    feat['vol_ma10'] = df_recent['volume'].rolling(10).mean().iloc[-1]
    # ATR
    high_low = df_recent['high'] - df_recent['low']
    high_pc = (df_recent['high'] - df_recent['close'].shift(1)).abs()
    low_pc = (df_recent['low'] - df_recent['close'].shift(1)).abs()
    tr = pd.concat([high_low, high_pc, low_pc], axis=1).max(axis=1)
    feat['atr'] = tr.rolling(14, min_periods=1).mean().iloc[-1]
    feat['ret1_z'] = (feat['ret1'] - df_recent['ret1'].rolling(50).mean().iloc[-1]) / (df_recent['ret1'].rolling(50).std().iloc[-1] + 1e-9) if 'ret1' in df_recent else 0.0
    # V1 features:
    feat['V1'] = V1_history[-1] if V1_history else 0.0
    feat['V1_ma50'] = pd.Series(V1_history[-50:]).mean() if len(V1_history) >= 1 else 0.0
    feat['V1_std50'] = pd.Series(V1_history[-50:]).std() if len(V1_history) >= 1 else 0.0
    # spread from orderbook
    if orderbook and orderbook['bids'] and orderbook['asks']:
        bid0 = float(orderbook['bids'][0][0])
        ask0 = float(orderbook['asks'][0][0])
        feat['spread'] = (ask0 - bid0) / ((ask0 + bid0) / 2.0 + 1e-9)
    else:
        feat['spread'] = 0.0

    # return in model feature order
    X = pd.DataFrame([feat])[FEATURES].fillna(0.0)
    return X

async def main_loop_with_model(exchange, symbol):
    load_model()
    V1_history = []
    open_positions = []

    while True:
        try:
            df = await fetch_data(exchange, symbol, TIMEFRAME, limit=500)
            orderbook = await fetch_orderbook(exchange, symbol, limit=10)
            if df.empty or not orderbook['bids'] or not orderbook['asks']:
                await asyncio.sleep(CHECK_INTERVAL)
                continue

            # compute V1 approx
            vbuy = float(sum([float(lvl[1]) for lvl in orderbook['asks']]))
            vsell = float(sum([float(lvl[1]) for lvl in orderbook['bids']]))
            v1 = vbuy - vsell
            V1_history.append(v1)
            sigma = calculate_sigma(V1_history, lookback=LOOKBACK_V1)
            signal = generate_signal(v1, sigma)

            # build features and get model prob
            X_live = make_live_features(df, orderbook, V1_history)
            p = model.predict(X_live)[0]

            # combine rules
            best_bid = float(orderbook['bids'][0][0])
            best_ask = float(orderbook['asks'][0][0])
            mid_price = (best_bid + best_ask)/2.0

            open_positions = await manage_positions(exchange, open_positions, mid_price)

            if len(open_positions) < MAX_POSITIONS:
                # require both signals
                if signal in ['STRONG_BUY','BUY'] and p > P_THRESHOLD:
                    await place_order(exchange, symbol, 'long', mid_price, calculate_atr(df, ATR_PERIOD),
                                      RISK_AMOUNT, SL_MULTIPLIER, TP_MULTIPLIER, LEVERAGE, open_positions)
                if signal in ['STRONG_SELL','SELL'] and p < (1 - P_THRESHOLD):
                    await place_order(exchange, symbol, 'short', mid_price, calculate_atr(df, ATR_PERIOD),
                                      RISK_AMOUNT, SL_MULTIPLIER, TP_MULTIPLIER, LEVERAGE, open_positions)

            await asyncio.sleep(CHECK_INTERVAL)

        except Exception as e:
            logger.error("Main loop error: %s", e)
            await asyncio.sleep(0.5)
