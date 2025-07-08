import ccxt.async_support as ccxt
import pandas as pd
import numpy as np
import asyncio
import logging
import os
import time
from dotenv import load_dotenv
import pandas as pd

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Get API credentials
api_key = os.getenv("BINANCE_API_KEY")
api_secret = os.getenv("BINANCE_API_SECRET")

# Configuration - using valid Binance symbols

# 🚀 Recommended symbols:
# For stat-arb on ~$10, these work better:
# ✅ ALCH/USDT → already configured, min ~1 USDT, low price, small quantity
# ✅ DOGE/USDT → cheap, liquid, min ~1–2 USDT
# ✅ ADA/USDT → same
# ✅ XRP/USDT → min ~1–2 USDT, tight spreads
# ✅ TRX/USDT → min ~1 USDT

SYMBOL1 = 'XRP/USDT:USDT'   # or just ALCH/USDT if that worksSYMBOL1 = 'BTC/USDT'
SYMBOL2 = 'BTC/USDT'
RISK_AMOUNT = 10.0
LEVERAGE = 5
LOOKBACK = 500
SIGMA_THRESHOLD = 1.0
ATR_PERIOD = 14
TP_MULTIPLIER = 2.0
SL_MULTIPLIER = 1.0
FEE_RATE = 0.002
MAX_POSITION = 2
CHECK_INTERVAL = 1.0  # Increased for safety

OPEN_POSITIONS = []

async def fetch_data(exchange, symbol: str, timeframe: str, limit: int = 100) -> pd.DataFrame:
    try:
        ohlcv = await exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        return df
    except Exception as e:
        logger.error(f"Error fetching OHLCV data for {symbol}: {str(e)}")
        return pd.DataFrame()

# async def fetch_quotes(exchange, symbol: str) -> tuple:
    try:
        ticker = await exchange.fetch_ticker(symbol)
        order_book = await exchange.fetch_order_book('BTC/USDT')
        bid = order_book['bids'][0][0] if order_book['bids'] else None
        ask = order_book['asks'][0][0] if order_book['asks'] else None
        if bid is None or ask is None:
            logger.warning(f"Order book empty for BTC/USDT: bid={bid}, ask={ask}")
        else:
            logger.info(f"Bid: {bid}, Ask: {ask}, Spread: {ask-bid}")

        print(f"Ticker for {symbol}: {ticker}")  # Debugging line
        bid = ticker.get('bid', 0.0)
        ask = ticker.get('ask', float('inf'))

        # Validate prices
        if bid is None or ask is None or bid <= 0 or ask <= 0:
            logger.warning(f"Invalid bid/ask for {symbol}: bid={bid}, ask={ask}")
            return 0.0, float('inf')
        if bid is None or ask is None or bid <= 0 or ask <= 0:
            logger.warning(f"Invalid bid/ask for {symbol}: bid={bid}, ask={ask}")
            return 0.0, float('inf')

        if bid <= 0 or ask <= 0 or bid > ask:
            logger.warning(f"Invalid quotes for {symbol}: bid={bid}, ask={ask}")
            return 0.0, float('inf')

        return bid, ask
    except ccxt.BaseError as e:
        logger.error(f"Exchange error fetching {symbol}: {str(e)}")
        return 0.0, float('inf')
    except Exception as e:
        logger.error(f"General error fetching {symbol}: {str(e)}")
        return 0.0, float('inf')

async def fetch_quotes(exchange, symbol: str) -> tuple:
    try:
        order_book = await exchange.fetch_order_book(symbol)
        bid = order_book['bids'][0][0] if order_book['bids'] else 0.0
        ask = order_book['asks'][0][0] if order_book['asks'] else float('inf')

        if bid <= 0 or ask <= 0 or bid > ask:
            logger.warning(f"Invalid quotes for {symbol}: bid={bid}, ask={ask}")
            return 0.0, float('inf')

        logger.info(f"Bid: {bid}, Ask: {ask}, Spread: {ask-bid}")
        return bid, ask

    except ccxt.BaseError as e:
        logger.error(f"Exchange error fetching {symbol}: {str(e)}")
        return 0.0, float('inf')
    except Exception as e:
        logger.error(f"General error fetching {symbol}: {str(e)}")
        return 0.0, float('inf')


def calculate_indicator(
    df1: pd.DataFrame,
    df2: pd.DataFrame,
    price1: float,
    price2: float
) -> tuple:
    try:
        # Validate inputs
        if df1.empty or df2.empty or price1 <= 0 or price2 <= 0:
            return pd.DataFrame(), 0.0

        df = pd.DataFrame({
            'timestamp': df1['timestamp'],
            'asset1': df1['close'],
            'asset2': df2['close']
        })

        # Calculate log prices and returns
        df['log1'] = np.log(df['asset1'])
        df['log2'] = np.log(df['asset2'])
        df['return1'] = df['log1'].diff()
        df['return2'] = df['log2'].diff()

        # Calculate beta (correlation coefficient)
        beta = df[['return1', 'return2']].cov().iloc[0, 1] / df['return2'].var()

        # Spread calculation
        df['spread'] = df['log1'] - beta * df['log2']

        # Rolling statistics
        df['mu'] = df['spread'].rolling(LOOKBACK).mean()
        df['sigma'] = df['spread'].rolling(LOOKBACK).std()

        # True Range (TR) calculation
        hl = df1['high'] - df1['low']
        hc = (df1['high'] - df1['close'].shift()).abs()
        lc = (df1['low'] - df1['close'].shift()).abs()
        df['tr'] = pd.concat([hl, hc, lc], axis=1).max(axis=1)

        # Average True Range (ATR)
        df['atr'] = df['tr'].rolling(ATR_PERIOD).mean()

        # Current spread
        current_spread = np.log(price1) - beta * np.log(price2)

        return df, current_spread

    except Exception as e:
        logger.error(f"Error in indicator calculation: {str(e)}")
        return pd.DataFrame(), 0.0

def get_arbitrage_signal(spread: float, mu: float, sigma: float) -> str:
    try:
        # Check for NaN/None values
        if any(pd.isna(x) for x in [spread, mu, sigma]):
            return 'neutral'

        # Check thresholds
        if spread < mu - SIGMA_THRESHOLD * sigma:
            return 'bullish'
        elif spread > mu + SIGMA_THRESHOLD * sigma:
            return 'bearish'

        return 'neutral'
    except Exception as e:
        logger.error(f"Signal generation error: {str(e)}")
        return 'neutral'

async def manage_positions(exchange, current_price: float, symbol: str):
    global OPEN_POSITIONS
    positions_to_remove = []

    try:
        for pos in OPEN_POSITIONS:
            if pos['symbol'] != symbol:
                continue

            entry_price = pos['entry_price']
            qty = pos['quantity']
            side = pos['side']
            tp_price = pos['tp_price']
            sl_price = pos['sl_price']

            try:
                if side == 'long':
                    if current_price >= tp_price:
                        logger.info(f"TP hit for LONG {symbol} @ {current_price:.2f}")
                        await exchange.create_market_sell_order(symbol, qty)
                        positions_to_remove.append(pos)
                    elif current_price <= sl_price:
                        logger.info(f"SL hit for LONG {symbol} @ {current_price:.2f}")
                        await exchange.create_market_sell_order(symbol, qty)
                        positions_to_remove.append(pos)

                elif side == 'short':
                    if current_price <= tp_price:
                        logger.info(f"TP hit for SHORT {symbol} @ {current_price:.2f}")
                        await exchange.create_market_buy_order(symbol, qty)
                        positions_to_remove.append(pos)
                    elif current_price >= sl_price:
                        logger.info(f"SL hit for SHORT {symbol} @ {current_price:.2f}")
                        await exchange.create_market_buy_order(symbol, qty)
                        positions_to_remove.append(pos)
            except ccxt.InsufficientFunds:
                logger.error("Insufficient funds to close position")
            except ccxt.NetworkError:
                logger.warning("Network error closing position - will retry")
            except Exception as e:
                logger.error(f"Error closing position: {str(e)}")

        # Remove closed positions
        OPEN_POSITIONS = [pos for pos in OPEN_POSITIONS if pos not in positions_to_remove]

    except Exception as e:
        logger.error(f"Position management error: {str(e)}")

async def place_order(exchange, signal: str, price: float, atr: float, symbol: str):
    global OPEN_POSITIONS

    try:
        # Validate inputs
        if price <= 0 or atr <= 0 or atr != atr:  # Check for NaN
            logger.warning("Invalid price or ATR for order placement")
            return

        # Check position limit
        if len(OPEN_POSITIONS) >= MAX_POSITION:
            logger.info("Max positions reached - skipping new order")
            return

        # Calculate quantity
        qty = RISK_AMOUNT / price

        try:
            if signal == 'bullish':
                # Place buy order
                await exchange.create_market_buy_order(symbol, qty)

                # Calculate TP/SL
                tp_price = price + TP_MULTIPLIER * atr
                sl_price = price - SL_MULTIPLIER * atr

                # Track position
                OPEN_POSITIONS.append({
                    'symbol': symbol,
                    'side': 'long',
                    'entry_price': price,
                    'quantity': qty,
                    'tp_price': tp_price,
                    'sl_price': sl_price
                })

                logger.info(f"Opened LONG: {qty:.6f} {symbol} @ {price:.2f}")

            elif signal == 'bearish':
                # Place sell order
                await exchange.create_market_sell_order(symbol, qty)

                # Calculate TP/SL
                tp_price = price - TP_MULTIPLIER * atr
                sl_price = price + SL_MULTIPLIER * atr

                # Track position
                OPEN_POSITIONS.append({
                    'symbol': symbol,
                    'side': 'short',
                    'entry_price': price,
                    'quantity': qty,
                    'tp_price': tp_price,
                    'sl_price': sl_price
                })

                logger.info(f"Opened SHORT: {qty:.6f} {symbol} @ {price:.2f}")

        except ccxt.InsufficientFunds:
            logger.error("Insufficient funds to open position")
        except ccxt.NetworkError:
            logger.warning("Network error opening position - will retry")
        except Exception as e:
            logger.error(f"Error opening position: {str(e)}")

    except Exception as e:
        logger.error(f"Order placement error: {str(e)}")

async def main():
    exchange = None
    try:
        # Initialize exchange
        exchange = ccxt.binance({
            'apiKey': api_key,
            'secret': api_secret,
            'enableRateLimit': True,
            'options': {
                'defaultType': 'future',
                'adjustForTimeDifference': True,
            }
        })

        # Load markets
        await exchange.load_markets()

        # Get market symbols
        symbols = list(exchange.markets.keys())

        # Convert to DataFrame
        df_markets = pd.DataFrame(symbols, columns=['Symbol'])
        df_markets.to_csv("binance_markets.csv", index=False)
        df_markets = pd.DataFrame(exchange.markets).T
        print(df_markets[df_markets.index.str.contains("ALCH")])
        symbols = list(exchange.markets.keys())
        print([s for s in symbols if 'ALCH' in s])
        df_markets = pd.DataFrame(exchange.markets).T
        df_markets.to_csv("binance_markets.csv")
        print(df_markets[df_markets.index.str.contains("ALCH")])
        print([s for s in exchange.markets if 'BTC' in s])


        futures = [k for k, v in exchange.markets.items() if v['type'] == 'future' and v['active']]
        print(futures)


        # Log loaded markets
        logger.info("Markets loaded successfully")

        # Set leverage
        await exchange.set_leverage(LEVERAGE, SYMBOL1)
        logger.info(f"Leverage set to {LEVERAGE}x for {SYMBOL1}")

        # Main trading loop
        while True:
            try:
                # Fetch OHLCV data
                df1 = await fetch_data(exchange, SYMBOL1, '1m', 1000)
                df2 = await fetch_data(exchange, SYMBOL2, '1m', 1000)

                # Skip if data is insufficient
                if len(df1) < LOOKBACK or len(df2) < LOOKBACK:
                    logger.info("Insufficient data - waiting...")
                    await asyncio.sleep(CHECK_INTERVAL)
                    continue

                # Fetch latest quotes
                bid1, ask1 = await fetch_quotes(exchange, SYMBOL1)
                bid2, ask2 = await fetch_quotes(exchange, SYMBOL2)

                # Validate prices
                if (bid1 <= 0 or ask1 == float('inf') or
                    bid2 <= 0 or ask2 == float('inf')):
                    logger.warning("Invalid prices - skipping iteration")
                    await asyncio.sleep(CHECK_INTERVAL)
                    continue

                # Calculate mid prices
                price1 = (bid1 + ask1) / 2
                price2 = (bid2 + ask2) / 2

                # Calculate indicators
                df_indicator, spread = calculate_indicator(df1, df2, price1, price2)

                # Skip if indicator calculation failed
                if df_indicator.empty:
                    logger.info("Indicator calculation failed - skipping")
                    await asyncio.sleep(CHECK_INTERVAL)
                    continue

                # Get latest indicator values
                mu = df_indicator['mu'].iloc[-1]
                sigma = df_indicator['sigma'].iloc[-1]
                atr = df_indicator['atr'].iloc[-1]

                # Get trading signal
                signal = get_arbitrage_signal(spread, mu, sigma)

                # Manage existing positions
                await manage_positions(exchange, price1, SYMBOL1)

                # Place new order if signal is not neutral
                if signal != 'neutral':
                    await place_order(exchange, signal, price1, atr, SYMBOL1)

                # Log status
                logger.info(f"{SYMBOL1}: {price1:.2f} | Spread: {spread:.6f} | Signal: {signal}")

                await asyncio.sleep(CHECK_INTERVAL)

            except KeyboardInterrupt:
                logger.info("Interrupted by user — shutting down gracefully…")



                break
            except Exception as e:
                logger.error(f"Main loop error: {str(e)}")
                await asyncio.sleep(5)  # Wait longer after errors

    except Exception as e:
        logger.error(f"Initialization error: {str(e)}")
    finally:
        if exchange:
            await exchange.close()
            logger.info("Exchange connection closed")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Script stopped by user.")

