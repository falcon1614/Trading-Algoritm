# train_model.py
import lightgbm as lgb
import pandas as pd
import numpy as np
from sklearn.metrics import roc_auc_score, precision_recall_fscore_support
import argparse
import joblib

FEATURES = [
    'ret1', 'ret3', 'vol_ma3', 'vol_ma10', 'atr',
    'ret1_z', 'V1', 'V1_ma50', 'V1_std50', 'spread'
]

def prepare_for_training(df):
    df = df.copy()
    # compute spread if not present
    if 'spread' not in df.columns:
        df['spread'] = (df['ask0'] - df['bid0']) / ((df['ask0'] + df['bid0'])/2 + 1e-9)
    df = df.dropna(subset=FEATURES + ['target'])
    return df

def train(df_path='dataset.parquet', model_out='lgb_model.txt'):
    df = pd.read_parquet(df_path)
    df = prepare_for_training(df)

    split_idx = int(len(df) * 0.8)
    train = df.iloc[:split_idx]
    val = df.iloc[split_idx:]

    X_train = train[FEATURES]
    y_train = train['target']
    X_val = val[FEATURES]
    y_val = val['target']

    train_data = lgb.Dataset(X_train, label=y_train)
    val_data = lgb.Dataset(X_val, label=y_val, reference=train_data)

    params = {
        'objective': 'binary',
        'metric': 'auc',
        'boosting_type': 'gbdt',
        'learning_rate': 0.05,
        'num_leaves': 31,
        'min_data_in_leaf': 20,
        'feature_fraction': 0.8,
        'bagging_fraction': 0.8,
        'bagging_freq': 5,
        'seed': 42,
        'verbose': -1,
    }

    model = lgb.train(params,
                      train_data,
                      valid_sets=[val_data],
                      early_stopping_rounds=50,
                      num_boost_round=1000)

    print("Validation AUC:", roc_auc_score(y_val, model.predict(X_val)))
    model.save_model(model_out)
    # also save a joblib wrapper for predict_proba easily
    joblib.dump(FEATURES, model_out + ".features.pkl")
    print("Saved model:", model_out)

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--data', default='dataset.parquet')
    parser.add_argument('--out', default='lgb_model.txt')
    args = parser.parse_args()
    train(df_path=args.data, model_out=args.out)
