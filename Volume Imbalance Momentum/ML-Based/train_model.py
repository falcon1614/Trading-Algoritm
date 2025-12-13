# train_model.py
# Robust LightGBM training for financial time-series data

import lightgbm as lgb
import pandas as pd
import numpy as np
import argparse
import joblib
from sklearn.metrics import roc_auc_score

# ---------------------------
# Feature list (MUST match dataset)
# ---------------------------
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
# Train function
# ---------------------------
def train(df_path: str, model_out: str):
    print("Loading dataset...")
    df = pd.read_parquet(df_path)

    # Drop rows with missing values
    df = df.dropna(subset=FEATURES + ['target']).reset_index(drop=True)

    if df.empty:
        raise RuntimeError("Dataset is empty after cleaning. Check feature generation.")

    # ---------------------------
    # Check class distribution
    # ---------------------------
    class_counts = df['target'].value_counts()
    print("\nTarget distribution:")
    print(class_counts)

    if len(class_counts) < 2:
        raise RuntimeError(
            "Only ONE class present in target.\n"
            "Fix label generation (threshold too high / dataset too small)."
        )

    # ---------------------------
    # Time-based split (NO shuffle)
    # ---------------------------
    split_idx = int(len(df) * 0.8)
    train_df = df.iloc[:split_idx]
    val_df = df.iloc[split_idx:]

    # Validation set must also contain both classes
    if val_df['target'].nunique() < 2:
        raise RuntimeError(
            "Validation set has only ONE class.\n"
            "Increase dataset size or adjust label threshold."
        )

    X_train = train_df[FEATURES]
    y_train = train_df['target']
    X_val = val_df[FEATURES]
    y_val = val_df['target']

    # ---------------------------
    # LightGBM datasets
    # ---------------------------
    train_data = lgb.Dataset(X_train, label=y_train)
    val_data = lgb.Dataset(X_val, label=y_val)

    # ---------------------------
    # Model parameters
    # ---------------------------
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
        'verbosity': -1,
    }

    print("\nTraining LightGBM model...")

    model = lgb.train(
        params,
        train_data,
        num_boost_round=1000,
        valid_sets=[val_data],
        callbacks=[
            lgb.early_stopping(stopping_rounds=50),
            lgb.log_evaluation(period=50),
        ],
    )

    # ---------------------------
    # Validation performance
    # ---------------------------
    y_pred = model.predict(X_val, num_iteration=model.best_iteration)

    try:
        auc = roc_auc_score(y_val, y_pred)
        print(f"\nValidation AUC: {auc:.4f}")
    except Exception as e:
        print("\nAUC could not be computed:", e)
        auc = None

    # ---------------------------
    # Save model + features
    # ---------------------------
    model.save_model(model_out)
    joblib.dump(FEATURES, model_out + ".features.pkl")

    print(f"\nModel saved to: {model_out}")
    print("Feature schema saved to:", model_out + ".features.pkl")

    return auc

# ---------------------------
# Runner
# ---------------------------
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", default="dataset.parquet")
    parser.add_argument("--out", default="lgb_model.txt")
    args = parser.parse_args()

    train(df_path=args.data, model_out=args.out)
