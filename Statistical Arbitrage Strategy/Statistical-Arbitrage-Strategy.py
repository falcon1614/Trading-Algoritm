import ccxt.async_support as ccxt
import pandas as pd
import numpy as np
import asyncio
import websockets
import json
import logging
import nest_asyncio
from dotenv import load_dotenv
import os
import time
load_dotenv()
nest_asyncio.apply()


# Correct spelling of 'level' and fix format string placeholder
logging.basicConfig(level=logging.INFO, format='%(levelname)s - %(message)s')

logger = logging.getLogger(__name__)


api_key = os.getenv("BINANCE_API_KEY")
api_secret = os.getenv("BINANCE_API_SECRET")


# SYMBOL1 = 'ALCHUSDT'
# SYMBOL2 = 'BTCUSDT'
SYMBOL1 = 'BTCUSDT'
SYMBOL2 = 'ETHUSDT'
WS_URL1 = 'wss://fstream.binance.com/ws/alchusdt@depth10@100ms'
WS_URL2 = 'wss://fstream.binance.com/ws/btcusdt@depth10@100ms'
RISK_AMOUNT = 10.0
LEVERAGE = 5
LOOKBACK = 500
SIGMA_THRESOLD = 1.0
ATR_PERIOD = 14
TP_MULTIPLEAR = 2.0
SL_MULTIPLEAR = 1.0
FEE_RATE = 0.002
MAX_POSITION = 2
CHECK_INTERVAL = 0.1

OPEN_POSITION = []

async def fetch_data(exchange, symbol: str, timeframe: str, limit: int = 100) -> pd.DataFrame:
    try:
        ohlcv = await exchange.fetch_ohlcv(symbol, timeframe, limit=limit)  # ✅ fixed typo: fetch_olcv -> fetch_ohlcv
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        return df
    except Exception as e:
        logger.error(f"Error Fetching OHLCV Data: {str(e)}")
        return pd.DataFrame()

async def fetch_quotes(exchange, symbol: str) -> tuple:
    try:
        ticker = await exchange.fetch_ticker(symbol)
        # return ticker['bid'], ticker['ask']
        bid = ticker.get('bid')
        ask = ticker.get('ask')
        if bid is None or ask is None:
            logger.warning(f"No bid/ask for {symbol} — market illiquid?")
            return 0.0, float('inf')
        return bid, ask
    except Exception as e:
        logger.error(f"Error Fetching Ticker Data: {str(e)}")
        return 0.0, float('inf')  # ✅ fixed float('int') -> float('inf')

def calculate_indicator(
    df1: pd.DataFrame,
    df2: pd.DataFrame,
    alch_price: float,
    btc_price: float
) -> tuple:
    try:
        df = pd.DataFrame({
            'timestamp': df1['timestamp'],
            'alch': df1['close'],
            'btc': df2['close']
        })

        # Log prices
        df['log_alch'] = np.log(df['alch'])
        df['log_btc'] = np.log(df['btc'])

        # Log returns
        df['return_alch'] = df['log_alch'].diff()
        df['return_btc'] = df['log_btc'].diff()

        # Correlation beta
        beta = df[['return_alch', 'return_btc']].corr().iloc[0, 1]

        # Spread
        df['spread'] = df['log_alch'] - beta * df['log_btc']

        # Rolling mean and std of spread
        df['mu'] = df['spread'].rolling(LOOKBACK).mean()
        df['sigma'] = df['spread'].rolling(LOOKBACK).std()

        # True Range (TR) components
        high_low = df1['high'] - df1['low']
        high_close_prev = (df1['high'] - df1['close'].shift()).abs()
        low_close_prev = (df1['low'] - df1['close'].shift()).abs()

        df['tr'] = pd.concat(
            [high_low, high_close_prev, low_close_prev],
            axis=1
        ).max(axis=1)

        # ATR
        df['atr'] = df['tr'].rolling(ATR_PERIOD).mean()

        # Current spread
        current_spread = np.log(alch_price) - beta * np.log(btc_price)

        return df, current_spread

    except Exception as e:
        logger.error(f"Error calculating indicator: {str(e)}")
        return pd.DataFrame(), 0.0

def get_arbitrage_signal(spread: float, mu: float, sigma: float) -> str:
    try:
        # Check for NaN values properly
        if pd.isna(spread) or pd.isna(mu) or pd.isna(sigma):
            return 'neutral'

        # Check thresholds
        if spread < mu - SIGMA_THRESOLD * sigma:
            return 'bullish'
        elif spread > mu + SIGMA_THRESOLD * sigma:
            return 'bearish'

        return 'neutral'

    except Exception as e:
        logger.error(f"Error in Signal Generation: {str(e)}")
        return 'neutral'

async def manage_positions(exchange, current_price: float, symbol: str):
    global OPEN_POSITION
    positions_to_remove = []  # 🧹 fixed name here

    try:
        for pos in OPEN_POSITION:
            if pos['symbol'] != symbol:
                continue

            entry_price = pos['entry_price']
            qty = pos['quantity']
            side = pos['side']
            tp_price = pos['tp_price']
            sl_price = pos['sl_price']

            if side == 'long':
                if current_price >= tp_price:
                    logger.info(
                        f"[✔️] Long TP hit at {current_price:.2f}, Profit: {(current_price - entry_price) * qty * LEVERAGE:.2f}"
                    )
                    await exchange.create_market_sell_order(symbol, qty)
                    positions_to_remove.append(pos)

                elif current_price <= sl_price:
                    logger.info(
                        f"[❌] Long SL hit at {current_price:.2f}, Loss: {(entry_price - current_price) * qty * LEVERAGE:.2f}"
                    )
                    await exchange.create_market_sell_order(symbol, qty)
                    positions_to_remove.append(pos)

            elif side == 'short':
                if current_price <= tp_price:
                    logger.info(
                        f"[✔️] Short TP hit at {current_price:.2f}, Profit: {(entry_price - current_price) * qty * LEVERAGE:.2f}"
                    )
                    await exchange.create_market_buy_order(symbol, qty)
                    positions_to_remove.append(pos)

                elif current_price >= sl_price:
                    logger.info(
                        f"[❌] Short SL hit at {current_price:.2f}, Loss: {(current_price - entry_price) * qty * LEVERAGE:.2f}"
                    )
                    await exchange.create_market_buy_order(symbol, qty)
                    positions_to_remove.append(pos)

        # Remove closed positions
        OPEN_POSITION = [pos for pos in OPEN_POSITION if pos not in positions_to_remove]

    except Exception as e:
        logger.error(f"Error managing positions: {str(e)}")

async def place_order(exchange, signal: str, price: float, atr: float, symbol: str):
    global OPEN_POSITION  # you modify the global position list
    try:
        if len(OPEN_POSITION) >= MAX_POSITION:
            logger.info("🚫 Max position reached — skipping new order")
            logger.info(f"Quotes: ALCH bid={alch_bid}, ask={alch_ask} | BTC bid={btc_bid}, ask={btc_ask}")
            return

        qty = RISK_AMOUNT / price

        if signal == 'bullish':
            # 🔷 FIX: typo — exchange.create_market_buy_order
            order = await exchange.create_market_buy_order(symbol, qty)

            tp_price = price + TP_MULTIPLEAR * atr
            sl_price = price - SL_MULTIPLEAR * atr

            OPEN_POSITION.append({
                'symbol': symbol,
                'side': 'long',
                'entry_price': price,
                'quantity': qty,
                'tp_price': tp_price,
                'sl_price': sl_price
            })

            logger.info(
                f"🟢 Opened LONG at {price:.2f}, Qty: {qty:.4f}, TP: {tp_price:.2f}, SL: {sl_price:.2f}"
            )

            # Hedge with short BTC/USDT
            btc_ticker = await exchange.fetch_ticker(SYMBOL2)
            btc_qty = qty * price / btc_ticker['last']
            await exchange.create_market_sell_order(SYMBOL2, btc_qty)

        elif signal == 'bearish':
            order = await exchange.create_market_sell_order(symbol, qty)

            tp_price = price - TP_MULTIPLEAR * atr
            sl_price = price + SL_MULTIPLEAR * atr

            OPEN_POSITION.append({
                'symbol': symbol,
                'side': 'short',
                'entry_price': price,
                'quantity': qty,
                'tp_price': tp_price,
                'sl_price': sl_price
            })

            logger.info(
                f"🔴 Opened SHORT at {price:.2f}, Qty: {qty:.4f}, TP: {tp_price:.2f}, SL: {sl_price:.2f}"
            )

            # Hedge with long BTC/USDT
            btc_ticker = await exchange.fetch_ticker(SYMBOL2)
            btc_qty = qty * price / btc_ticker['last']
            await exchange.create_market_buy_order(SYMBOL2, btc_qty)

    except Exception as e:
        logger.error(f"Error placing order: {str(e)}")

async def main():
    try:
        exchange = ccxt.binance({
            'enableRateLimit': True,
            'apiKey': api_key,
            'secret': api_secret,
            'enableLeverage': True,
            'options': {
                'defaultType': 'future',  # ✅ FIX: set default type to 'future'
            }
        })
        # print(exchange.fetch_balance())

        await exchange.load_markets()

        # ✅ FIX: typo LEVERAGE → LEVERAGE
        await exchange.set_leverage(LEVERAGE, SYMBOL1)
        await exchange.set_leverage(LEVERAGE, SYMBOL2)

        async with websockets.connect(WS_URL1) as ws1, websockets.connect(WS_URL2) as ws2:
            while True:
                try:
                    df1 = await fetch_data(exchange, SYMBOL1, '1m')
                    df2 = await fetch_data(exchange, SYMBOL2, '1m')

                    alch_bid, alch_ask = await fetch_quotes(exchange, SYMBOL1)
                    btc_bid, btc_ask = await fetch_quotes(exchange, SYMBOL2)

                    if df1.empty or df2.empty:
                        await asyncio.sleep(CHECK_INTERVAL)
                        continue

                    alch_price = (alch_bid + alch_ask) / 2
                    btc_price = (btc_bid + btc_ask) / 2  # ✅ FIX: was using alch_ask instead of btc_ask

                    df, spread = calculate_indicator(df1, df2, alch_price, btc_price)

                    mu = df['mu'].iloc[-1]
                    sigma = df['sigma'].iloc[-1]  # ✅ FIX: typo 'sima' → 'sigma'
                    atr = df['atr'].iloc[-1]

                    signal = get_arbitrage_signal(spread, mu, sigma)  # ✅ FIX: function name typo

                    await manage_positions(exchange, alch_price, SYMBOL1)

                    if signal != 'neutral':
                        await place_order(exchange, signal, alch_price, atr, SYMBOL1)

                    logger.info(f"ALCH PRICE: {alch_price:.2f} | SPREAD: {spread:.4f} | SIGNAL: {signal}")

                    await asyncio.sleep(CHECK_INTERVAL)

                except Exception as e:
                    logger.error(f"Error during loop: {str(e)}")

    except Exception as e:
        logger.error(f"Error initializing exchange: {str(e)}")

    finally:
        await exchange.close()  # ✅ FIX: make it a call

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())

