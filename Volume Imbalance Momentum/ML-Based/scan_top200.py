import ccxt
import pandas as pd
import numpy as np
import argparse
import lightgbm as lgb
import joblib
import os
from tqdm import tqdm

# ===========================
# CONFIG
# ===========================
TIMEFRAME = "1m"
LOOKBACK = 1500
ML_QUANTILE = 0.75
ATR_PERIOD = 14

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MODEL_PATH = os.path.join(BASE_DIR, "lgb_model.txt")
FEATURE_PATH = MODEL_PATH + ".features.pkl"

# ===========================
# LOAD MODEL
# ===========================
model = lgb.Booster(model_file=MODEL_PATH)
FEATURES = joblib.load(FEATURE_PATH)

# ===========================
# INDICATORS
# ===========================
def calculate_atr(df):
    hl = df["high"] - df["low"]
    hpc = (df["high"] - df["close"].shift(1)).abs()
    lpc = (df["low"] - df["close"].shift(1)).abs()
    tr = pd.concat([hl, hpc, lpc], axis=1).max(axis=1)
    return tr.rolling(ATR_PERIOD).mean()

# ===========================
# FEATURE BUILDER (HISTORICAL)
# ===========================
def build_features(df):
    df = df.copy()

    df["ret1"] = np.log(df["close"] / df["close"].shift(1))
    df["ret3"] = np.log(df["close"] / df["close"].shift(3))
    df["vol_ma3"] = df["volume"].rolling(3).mean()
    df["vol_ma10"] = df["volume"].rolling(10).mean()
    df["atr"] = calculate_atr(df)

    df["ret1_z"] = (
        (df["ret1"] - df["ret1"].rolling(50).mean()) /
        (df["ret1"].rolling(50).std() + 1e-9)
    )

    # V1 proxy (since no orderbook historically)
    df["V1"] = df["volume"].diff()
    df["V1_ma50"] = df["V1"].rolling(50).mean()
    df["V1_std50"] = df["V1"].rolling(50).std()

    df["spread"] = 0.0

    return df.dropna()

# ===========================
# BACKTEST ONE SYMBOL
# ===========================
def backtest_symbol(exchange, symbol):
    try:
        ohlcv = exchange.fetch_ohlcv(symbol, TIMEFRAME, limit=LOOKBACK)
        if ohlcv is None or len(ohlcv) < 300:
            return None

        df = pd.DataFrame(
            ohlcv,
            columns=["ts", "open", "high", "low", "close", "volume"]
        )

        df[["open","high","low","close","volume"]] = df[
            ["open","high","low","close","volume"]
        ].astype(float)

        df = build_features(df)
        if len(df) < 200:
            return None

        probs = model.predict(df[FEATURES])
        df["prob"] = probs

        cutoff = np.quantile(probs, ML_QUANTILE)
        df["signal"] = (df["prob"] >= cutoff).astype(int)

        df["future_ret"] = df["close"].shift(-3) / df["close"] - 1
        df["strategy_ret"] = df["signal"] * df["future_ret"]

        df.dropna(inplace=True)

        if df["signal"].sum() < 20:
            return None

        equity = (1 + df["strategy_ret"]).cumprod()

        return {
            "symbol": symbol,
            "return": float(equity.iloc[-1] - 1),
            "max_dd": float((equity / equity.cummax() - 1).min()),
            "trades": int(df["signal"].sum()),
            "winrate": float(
                (df.loc[df["signal"] == 1, "future_ret"] > 0).mean()
            )
        }

    except Exception:
        return None

# ===========================
# MAIN SCAN
# ===========================
def main(top_n):
    exchange = ccxt.binance({
        "enableRateLimit": True,
        "options": {"defaultType": "future"}
    })

    markets = exchange.load_markets()

    symbols = []

    for s, m in markets.items():
        if (
            m.get("type") == "swap"              # futures
            and m.get("linear") is True           # USDT-margined
            and m.get("settle") == "USDT"         # USDT settle
            and m.get("active") is True
        ):
            symbols.append(s)

    symbols = symbols[:100]
    print(f"Scanning {len(symbols)} symbols...")


    print(f"Scanning {len(symbols)} symbols...")

    results = []

    for sym in tqdm(symbols, desc="Scanning"):
        res = backtest_symbol(exchange, sym)
        if res:
            results.append(res)

    if not results:
        print("\n❌ No symbols met the criteria. Try:")
        print("- Increase LOOKBACK")
        print("- Lower ML_QUANTILE")
        print("- Reduce min trade filter")
        return

    df = pd.DataFrame(results).sort_values("return", ascending=False)

    print("\n🏆 TOP PERFORMING COINS\n")
    print(df.head(top_n).to_string(index=False))

    df.to_csv("top200_results.csv", index=False)
    print("\nSaved → top200_results.csv")

# ===========================
# RUN
# ===========================
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--top", type=int, default=20)
    args = parser.parse_args()

    main(args.top)
