# backtest.py
# Quantile-based ML probability filter backtest

import os
import argparse
import joblib
import pandas as pd
import numpy as np
import lightgbm as lgb

# ---------------------------
# Load feature list
# ---------------------------
FEATURE_FILE = "lgb_model.txt.features.pkl"

if os.path.exists(FEATURE_FILE):
    FEATURES = joblib.load(FEATURE_FILE)
else:
    FEATURES = [
        'ret1',
        'ret3',
        'vol_ma3',
        'vol_ma10',
        'atr',
        'ret1_z',
        'V1',
        'V1_ma50',
        'V1_std50',
        'spread'
    ]

# ---------------------------
# Backtest function
# ---------------------------
def backtest(data_path, model_path, quantile=0.8):
    print("Loading dataset...")
    df = pd.read_parquet(data_path)

    df = df.dropna(subset=FEATURES + ['target']).reset_index(drop=True)
    if df.empty:
        raise RuntimeError("Dataset is empty after cleaning.")

    print("Loading model...")
    model = lgb.Booster(model_file=model_path)

    # ---------------------------
    # Predict probabilities
    # ---------------------------
    df['prob'] = model.predict(df[FEATURES])

    # Adaptive probability threshold
    cutoff = df['prob'].quantile(quantile)
    df['signal'] = (df['prob'] >= cutoff).astype(int)

    # ---------------------------
    # Future return (same horizon as labels)
    # ---------------------------
    horizon = 3
    df['future_ret'] = df['close'].shift(-horizon) / df['close'] - 1.0

    # Strategy return
    df['strategy_ret'] = df['signal'] * df['future_ret']
    df['bh_ret'] = df['future_ret']

    df = df.dropna().reset_index(drop=True)

    # ---------------------------
    # Metrics
    # ---------------------------
    trades = int(df['signal'].sum())
    winrate = (df.loc[df['signal'] == 1, 'future_ret'] > 0).mean()

    equity = (1 + df['strategy_ret']).cumprod()
    bh_equity = (1 + df['bh_ret']).cumprod()

    total_return = equity.iloc[-1] - 1
    bh_return = bh_equity.iloc[-1] - 1
    max_dd = (equity / equity.cummax() - 1).min()

    print("\n===== BACKTEST RESULTS =====")
    print(f"Trades taken      : {trades}")
    print(f"Win rate          : {winrate:.2%}")
    print(f"Strategy return   : {total_return:.2%}")
    print(f"Buy & Hold return : {bh_return:.2%}")
    print(f"Max drawdown      : {max_dd:.2%}")

    return df

# ---------------------------
# Runner
# ---------------------------
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", default="dataset.parquet")
    parser.add_argument("--model", default="lgb_model.txt")
    parser.add_argument(
        "--q",
        type=float,
        default=0.8,
        help="Top probability quantile to trade (0.7–0.9 recommended)"
    )
    args = parser.parse_args()

    backtest(
        data_path=args.data,
        model_path=args.model,
        quantile=args.q
    )
