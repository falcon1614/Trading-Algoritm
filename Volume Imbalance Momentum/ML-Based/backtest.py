# backtest.py
import pandas as pd
import numpy as np
import lightgbm as lgb
import joblib

FEATURES = joblib.load('lgb_model.txt.features.pkl') if os.path.exists('lgb_model.txt.features.pkl') else [
    'ret1','ret3','vol_ma3','vol_ma10','atr','ret1_z','V1','V1_ma50','V1_std50','spread'
]

def load_model(model_path='lgb_model.txt'):
    model = lgb.Booster(model_file=model_path)
    return model

def simulate(df_path='dataset.parquet', model_path='lgb_model.txt', p_threshold=0.6, risk_amount=0.5, leverage=5):
    model = load_model(model_path)
    df = pd.read_parquet(df_path)
    df = df.dropna(subset=FEATURES + ['target']).reset_index(drop=True)

    initial_balance = 1000.0
    balance = initial_balance
    positions = []
    equity_curve = []

    for i in range(len(df)-3):
        row = df.iloc[i]
        X = row[FEATURES].values.reshape(1,-1)
        p = model.predict(X)[0]
        # your imbalance logic
        v1 = row['V1']
        sigma = df['V1'].rolling(50).std().iloc[i] if i>=50 else 0.0
        signal = "NEUTRAL"
        if sigma == 0:
            signal = "BUY" if v1>0 else ("SELL" if v1<0 else "NEUTRAL")
        else:
            if v1 > sigma:
                signal = "STRONG_BUY"
            elif v1 > 0:
                signal = "BUY"
            elif v1 < -sigma:
                signal = "STRONG_SELL"
            elif v1 < 0:
                signal = "SELL"

        # entry rule: require both imbalance and model
        entry_price = row['close']
        if signal in ["STRONG_BUY","BUY"] and p > p_threshold:
            # buy for k=3 horizon
            future_price = df['close'].iloc[i+3]
            ret = (future_price - entry_price)/entry_price
            pnl = ret * (risk_amount / (abs(ret) + 1e-9)) # naive sizing mimic
            balance += pnl
        elif signal in ["STRONG_SELL","SELL"] and p < (1 - p_threshold):
            future_price = df['close'].iloc[i+3]
            ret = (entry_price - future_price)/entry_price
            pnl = ret * (risk_amount / (abs(ret)+1e-9))
            balance += pnl

        equity_curve.append(balance)

    import matplotlib.pyplot as plt
    plt.plot(equity_curve)
    plt.title('Backtest equity')
    plt.show()
    print("Final balance:", balance)

if __name__ == '__main__':
    import argparse, os
    parser = argparse.ArgumentParser()
    parser.add_argument('--data', default='dataset.parquet')
    parser.add_argument('--model', default='lgb_model.txt')
    parser.add_argument('--p', type=float, default=0.6)
    args = parser.parse_args()
    simulate(df_path=args.data, model_path=args.model, p_threshold=args.p)
