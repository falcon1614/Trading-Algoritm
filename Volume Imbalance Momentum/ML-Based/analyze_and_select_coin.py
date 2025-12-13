import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from datetime import datetime

# =====================================================
# CONFIG
# =====================================================
CSV_PATH = "top200_results.csv"
TOP_N = 15

MIN_TRADES = 50
MAX_DRAWDOWN = -0.05
MIN_RETURN = 0.0

SYMBOL_OUTPUT_FILE = "selected_symbol.txt"
REGIME_OUTPUT_FILE = "market_regime.txt"

# =====================================================
# LOAD DATA
# =====================================================
df = pd.read_csv(CSV_PATH)

required_cols = {"symbol", "return", "max_dd", "trades", "winrate"}
if not required_cols.issubset(df.columns):
    raise RuntimeError("CSV missing required columns")

# convert metrics
df["return_pct"] = df["return"] * 100
df["winrate_pct"] = df["winrate"] * 100

# =====================================================
# FILTER BAD COINS
# =====================================================
df = df[
    (df["trades"] >= MIN_TRADES) &
    (df["max_dd"] >= MAX_DRAWDOWN) &
    (df["return"] > MIN_RETURN)
].copy()

if df.empty:
    raise RuntimeError("No symbols passed the filters")

# =====================================================
# 1️⃣ DRAW DOWN vs RETURN SCATTER
# =====================================================
plt.figure(figsize=(10, 6))
plt.scatter(df["max_dd"] * 100, df["return_pct"], alpha=0.7)

for _, r in df.iterrows():
    plt.text(r["max_dd"] * 100, r["return_pct"], r["symbol"], fontsize=8)

plt.xlabel("Max Drawdown (%)")
plt.ylabel("Return (%)")
plt.title("Drawdown vs Return")
plt.grid(True)
plt.tight_layout()
plt.savefig("drawdown_vs_return.png", dpi=120)
plt.close()

# =====================================================
# 2️⃣ WINRATE vs TRADES BUBBLE CHART
# =====================================================
plt.figure(figsize=(10, 6))
plt.scatter(
    df["trades"],
    df["winrate_pct"],
    s=df["return_pct"] * 5,
    alpha=0.6
)

for _, r in df.iterrows():
    plt.text(r["trades"], r["winrate_pct"], r["symbol"], fontsize=8)

plt.xlabel("Number of Trades")
plt.ylabel("Winrate (%)")
plt.title("Winrate vs Trades (Bubble = Return)")
plt.grid(True)
plt.tight_layout()
plt.savefig("winrate_vs_trades.png", dpi=120)
plt.close()

# =====================================================
# 3️⃣ MARKET REGIME FILTER (BULL / BEAR)
# =====================================================
# Simple rule:
# If median return of top coins > 0 → BULL else BEAR
median_return = df["return"].median()

market_regime = "BULL" if median_return > 0 else "BEAR"

with open(REGIME_OUTPUT_FILE, "w") as f:
    f.write(market_regime)

print(f"Market regime detected: {market_regime}")

# =====================================================
# 4️⃣ AUTO-PICK BEST COIN
# =====================================================
# Score = return / abs(drawdown)
df["score"] = df["return"] / df["max_dd"].abs()

best = df.sort_values("score", ascending=False).iloc[0]

selected_symbol = best["symbol"]

with open(SYMBOL_OUTPUT_FILE, "w") as f:
    f.write(selected_symbol)

print(f"Selected best coin: {selected_symbol}")

# =====================================================
# 5️⃣ SUMMARY OUTPUT
# =====================================================
summary = df.sort_values("score", ascending=False).head(TOP_N)[
    ["symbol", "return_pct", "max_dd", "trades", "winrate_pct", "score"]
]

summary.to_csv("top_selected_coins.csv", index=False)

print("\nTop selected coins saved to top_selected_coins.csv")
print("Charts saved:")
print("- drawdown_vs_return.png")
print("- winrate_vs_trades.png")
print(f"\nSymbol for live bot → {selected_symbol}")
print(f"Regime → {market_regime}")
