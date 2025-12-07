# prepare_dataset.py
# Fetch OHLCV and snapshot orderbooks for each candle close to compute V1 (approx).
# WARNING: This will make many requests to the exchange. Use caching/backfill politely.

import ccxt
import pandas as pd
import numpy as np
import time
import argparse
from tqdm import tqdm
import os
import dotenv

def fetch_ohlcv(exchange, symbol, timeframe='1m', since=None, limit=500):
    all_ = []
    while True:
        batch = exchange.fetch_ohlcv(symbol, timeframe=timeframe, since=since, limit=limit)
        if not batch:
            break
        all_.extend(batch)
        if len(batch) < limit:
            break
        since = batch[-1][0] + 1
        time.sleep(exchange.rateLimit / 1000.0)
    df = pd.DataFrame(all_, columns=['timestamp','open','high','low','close','volume'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    return df.reset_index(drop=True)

def fetch_orderbook_snapshot(exchange, symbol, limit=10):
    ob = exchange.fetch_order_book(symbol, limit=limit)
    return ob

def compute_v1_from_orderbook(orderbook):
    # buyer-initiated approx = sum sizes at asks (aggressive buys take asks)
    vbuy = sum([lvl[1] for lvl in orderbook.get('asks', [])])
    vsell = sum([lvl[1] for lvl in orderbook.get('bids', [])])
    return vbuy, vsell, vbuy - vsell

def add_features(df):
    # expects df with timestamp, open, high, low, close, volume, V1 columns present
    df = df.copy()
    df['close'] = df['close'].astype(float)
    df['ret1'] = np.log(df['close'] / df['close'].shift(1))
    df['ret3'] = np.log(df['close'] / df['close'].shift(3))
    df['vol_ma3'] = df['volume'].rolling(3).mean()
    df['vol_ma10'] = df['volume'].rolling(10).mean()

    # ATR
    high_low = df['high'] - df['low']
    high_pc = (df['high'] - df['close'].shift(1)).abs()
    low_pc = (df['low'] - df['close'].shift(1)).abs()
    tr = pd.concat([high_low, high_pc, low_pc], axis=1).max(axis=1)
    df['atr'] = tr.rolling(14, min_periods=1).mean()

    df['V1_ma50'] = df['V1'].rolling(50).mean()
    df['V1_std50'] = df['V1'].rolling(50).std()

    df['ret1_z'] = (df['ret1'] - df['ret1'].rolling(50).mean()) / (df['ret1'].rolling(50).std() + 1e-9)
    df = df.dropna().reset_index(drop=True)
    return df

def label_target(df, k=3, thr=0.002):
    df = df.copy()
    df['future_ret_k'] = df['close'].shift(-k) / df['close'] - 1.0
    df['target'] = (df['future_ret_k'] >= thr).astype(int)
    df = df.dropna(subset=['target'])
    return df

def main(api_key, api_secret, symbol='ALCHUSDT', timeframe='1m', candles=2000, ob_limit=10, outfile='dataset.parquet'):
    exchange = ccxt.binance({
        'apiKey': api_key,
        'secret': api_secret,
        'enableRateLimit': True,
        'options': {'defaultType': 'future'}
    })

    # fetch recent candles
    print("Fetching OHLCV...")
    df = fetch_ohlcv(exchange, symbol, timeframe=timeframe, limit=1000)
    # if candles > available, we attempt to backfill by 'since' loop - simplified here
    if len(df) < candles:
        # increase window (user can modify)
        pass

    # for each candle timestamp get orderbook snapshot near the candle close
    print("Fetching orderbook snapshots (this can be slow)...")
    v1_list = []
    asks0 = []
    bids0 = []
    for ts in tqdm(df['timestamp'].tolist()):
        # be polite: small sleep
        time.sleep(exchange.rateLimit / 1000.0)
        ob = fetch_orderbook_snapshot(exchange, symbol, limit=ob_limit)
        vbuy, vsell, v1 = compute_v1_from_orderbook(ob)
        v1_list.append(v1)
        asks0.append(ob['asks'][0][0] if ob['asks'] else np.nan)
        bids0.append(ob['bids'][0][0] if ob['bids'] else np.nan)

    df['V1'] = pd.Series(v1_list)
    df['ask0'] = pd.Series(asks0)
    df['bid0'] = pd.Series(bids0)

    # features + labels
    df_feat = add_features(df)
    df_labeled = label_target(df_feat, k=3, thr=0.002)

    print(f"Saving dataset to {outfile} (rows: {len(df_labeled)})")
    df_labeled.to_parquet(outfile)
    print("done.")

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--api-key', required=False, default=os.getenv('API_KEY'))
    parser.add_argument('--api-secret', required=False, default=os.getenv('SECRET_KEY'))
    parser.add_argument('--symbol', default='ALCHUSDT')
    parser.add_argument('--out', default='dataset.parquet')
    args = parser.parse_args()
    main(args.api_key, args.api_secret, symbol=args.symbol, outfile=args.out)
