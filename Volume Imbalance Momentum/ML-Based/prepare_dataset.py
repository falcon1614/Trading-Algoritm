# prepare_dataset.py
# Build ML dataset: OHLCV + Orderbook snapshot → V1 → features → labels

import ccxt
import pandas as pd
import numpy as np
import time
import argparse
import logging
import os
import dotenv
from tqdm import tqdm

# ---------------------------
# Logging & Env
# ---------------------------
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

dotenv.load_dotenv()
API_KEY = os.getenv("API_KEY")
SECRET_KEY = os.getenv("SECRET_KEY")

# ---------------------------
# Fetch OHLCV
# ---------------------------
def fetch_ohlcv(exchange, symbol, timeframe='1m', limit=1000):
    logger.info("Fetching OHLCV...")
    ohlcv = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
    df = pd.DataFrame(
        ohlcv,
        columns=['timestamp', 'open', 'high', 'low', 'close', 'volume']
    )
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    return df.reset_index(drop=True)

# ---------------------------
# Fetch Orderbook Snapshot (SAFE)
# ---------------------------
def fetch_orderbook_snapshot(exchange, symbol, limit=10, retries=3):
    for attempt in range(retries):
        try:
            return exchange.fetch_order_book(symbol, limit=limit)
        except Exception as e:
            logger.warning(
                f"Orderbook fetch failed ({attempt + 1}/{retries}): {e}"
            )
            time.sleep(2)
    return None

# ---------------------------
# Compute V1 (SAFE)
# ---------------------------
def compute_v1_from_orderbook(orderbook):
    if orderbook is None:
        return np.nan, np.nan, np.nan

    bids = orderbook.get("bids", [])
    asks = orderbook.get("asks", [])

    if not bids or not asks:
        return np.nan, np.nan, np.nan

    vbuy = sum(lvl[1] for lvl in asks)
    vsell = sum(lvl[1] for lvl in bids)
    return vbuy, vsell, vbuy - vsell

# ---------------------------
# Feature Engineering
# ---------------------------
def add_features(df):
    df = df.copy()

    df['ret1'] = np.log(df['close'] / df['close'].shift(1))
    df['ret3'] = np.log(df['close'] / df['close'].shift(3))

    df['vol_ma3'] = df['volume'].rolling(3).mean()
    df['vol_ma10'] = df['volume'].rolling(10).mean()

    # ATR
    hl = df['high'] - df['low']
    hpc = (df['high'] - df['close'].shift(1)).abs()
    lpc = (df['low'] - df['close'].shift(1)).abs()
    tr = pd.concat([hl, hpc, lpc], axis=1).max(axis=1)
    df['atr'] = tr.rolling(14, min_periods=1).mean()

    # V1 rolling stats
    df['V1_ma50'] = df['V1'].rolling(50).mean()
    df['V1_std50'] = df['V1'].rolling(50).std()

    # Spread
    df['spread'] = (df['ask0'] - df['bid0']) / (
        (df['ask0'] + df['bid0']) / 2 + 1e-9
    )

    # Normalized return
    df['ret1_z'] = (
        (df['ret1'] - df['ret1'].rolling(50).mean()) /
        (df['ret1'].rolling(50).std() + 1e-9)
    )

    return df.dropna().reset_index(drop=True)

# ---------------------------
# Label Creation
# ---------------------------
def label_target(df, horizon=3, upper_q=0.6, lower_q=0.4):
    df = df.copy()

    future_ret = df['close'].shift(-horizon) / df['close'] - 1.0

    q_up = future_ret.quantile(upper_q)
    q_dn = future_ret.quantile(lower_q)

    df['target'] = np.nan
    df.loc[future_ret > q_up, 'target'] = 1
    df.loc[future_ret < q_dn, 'target'] = 0

    return df.dropna().reset_index(drop=True)


# ---------------------------
# Main
# ---------------------------
def main(symbol, timeframe, limit, outfile):
    if not API_KEY or not SECRET_KEY:
        raise RuntimeError("API_KEY or SECRET_KEY missing in .env")

    exchange = ccxt.binance({
        "apiKey": API_KEY,
        "secret": SECRET_KEY,
        "enableRateLimit": True,
        "options": {"defaultType": "future"},
    })

    df = fetch_ohlcv(exchange, symbol, timeframe, limit)

    logger.info("Fetching orderbook snapshots (robust mode)...")

    v1_list, ask0_list, bid0_list = [], [], []

    for _ in tqdm(range(len(df))):
        time.sleep(exchange.rateLimit / 1000)

        ob = fetch_orderbook_snapshot(exchange, symbol, limit=10)
        vbuy, vsell, v1 = compute_v1_from_orderbook(ob)

        v1_list.append(v1)

        if ob and ob.get("asks") and ob.get("bids"):
            ask0_list.append(ob["asks"][0][0])
            bid0_list.append(ob["bids"][0][0])
        else:
            ask0_list.append(np.nan)
            bid0_list.append(np.nan)

    df["V1"] = v1_list
    df["ask0"] = ask0_list
    df["bid0"] = bid0_list

    df_feat = add_features(df)
    df_final = label_target(df_feat)

    logger.info(f"Saving dataset → {outfile} | rows={len(df_final)}")
    df_final.to_parquet(outfile)

    logger.info("Dataset preparation completed successfully.")

# ---------------------------
# Runner
# ---------------------------
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="ALCHUSDT")
    parser.add_argument("--timeframe", default="1m")
    parser.add_argument("--limit", type=int, default=300)
    parser.add_argument("--out", default="dataset.parquet")
    args = parser.parse_args()

    main(
        symbol=args.symbol,
        timeframe=args.timeframe,
        limit=args.limit,
        outfile=args.out,
    )
