# %%
# Statistical Arbitrage Strategy for Binance Futures Library
import ccxt.async_support as ccxt
import pandas as pd
import numpy as np
import asyncio
import logging
import json
import websockets
import os
import time
from dotenv import load_dotenv

# %%
# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# %%
# Get API credentials
api_key = os.getenv("BINANCE_API_KEY")
api_secret = os.getenv("BINANCE_API_SECRET")

# %%
# Trading pair symbols (format: BASE/QUOTE)
SYMBOL1 = 'ALCH/USDT:USDT'  # First trading pair (Alchemy token vs USDT)
SYMBOL2 = 'BTC/USDT'   # Second trading pair (Bitcoin vs USDT)

# WebSocket endpoints for real-time order book data (Binance Futures)
WS_URL1 = 'wss://fstream.binance.com/ws/alchusdt@depth10@100ms'  # ALCH/USDT order book (top 10 levels, 100ms updates)
WS_URL2 = 'wss://fstream.binance.com/ws/btcusdt@depth10@100ms'   # BTC/USDT order book (top 10 levels, 100ms updates)

# Risk management parameters
RISK_AMOUNT = 10.0     # Maximum capital to risk per trade (in USDT)
LEVERAGE = 5           # Trading leverage multiplier (5x)
FEE_RATE = 0.002       # Taker fee rate (0.2% per trade)

# Strategy configuration
LOOKBACK = 500         # Historical data window size for calculations
SIGMA_THRESHOLD = 1.0  # Standard deviation threshold for trade signals
ATR_PERIOD = 14        # Period for Average True Range indicator
TP_MULTIPLIER = 2.0    # Take-profit multiplier (relative to ATR)
SL_MULTIPLIER = 1.0    # Stop-loss multiplier (relative to ATR)

# Position management
MAX_POSITION = 2       # Maximum concurrent open positions allowed
CHECK_INTERVAL = 0.1   # Seconds between strategy condition checks

# Tracking open positions (list of active trades)
OPEN_POSITIONS = []    # Stores currently active positions

# %% [markdown]
# Fetch Data

# %%
async def fetch_data(exchange, symbol: str, timeframe: str, limit: int = 100) -> pd.DataFrame:
    """
    Fetches OHLCV (Open, High, Low, Close, Volume) data from an exchange
    and returns it as a formatted pandas DataFrame.

    Args:
        exchange: Exchange API instance
        symbol: Trading pair symbol (e.g., 'BTC/USDT')
        timeframe: Candle timeframe (e.g., '1m', '5m', '1h')
        limit: Number of candles to fetch (default: 100)

    Returns:
        pd.DataFrame: Formatted OHLCV data with timestamp as datetime index
    """
    try:
        # Fetch OHLCV data from exchange
        ohlcv = await exchange.fetch_ohlcv(symbol, timeframe, limit=limit)

        # Create DataFrame with proper column names
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])

        # Convert timestamp from milliseconds to datetime
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')

        return df

    except Exception as e:
        # Log error and return empty DataFrame on failure
        logger.error(f"Error fetching OHLCV data for {symbol}: {str(e)}")
        return pd.DataFrame()

# %% [markdown]
# Fetch Quotes

# %%
async def fetch_quotes(exchange, symbol: str) -> tuple:
    """
    Fetches real-time bid/ask prices from exchange order book
    Returns (bid, ask) tuple with validation checks

    Args:
        exchange: Connected exchange instance
        symbol: Trading pair (e.g., 'BTC/USDT')

    Returns:
        tuple: (bid_price, ask_price) or (0.0, inf) on error
    """
    try:
        # 📊 Fetch full order book data
        order_book = await exchange.fetch_order_book(symbol)

        # 🎯 Extract best bid (highest buy price)
        bid = order_book['bids'][0][0] if order_book['bids'] else 0.0

        # 🎯 Extract best ask (lowest sell price)
        ask = order_book['asks'][0][0] if order_book['asks'] else float('inf')

        # ⚠️ Validate quotes
        if bid <= 0 or ask <= 0 or bid > ask:
            logger.warning(f"🚨 Invalid quotes for {symbol}: bid={bid}, ask={ask}")
            return 0.0, float('inf')

        # 💹 Log market data with spread calculation
        spread = ask - bid
        logger.info(f"📈 {symbol} | Bid: {bid:.8f} | Ask: {ask:.8f} | Spread: {spread:.8f}")

        return bid, ask

    except ccxt.BaseError as e:
        logger.error(f"🔌 Exchange error fetching {symbol}: {str(e)}")
        return 0.0, float('inf')
    except Exception as e:
        logger.error(f"⚠️ General error fetching {symbol}: {str(e)}")
        return 0.0, float('inf')

# %% [markdown]
# Calculate Indicator

# %%
def calculate_indicator(df1: pd.DataFrame,df2: pd.DataFrame,alch_price: float,btc_price: float) -> tuple:
    """
    📊 Calculate statistical arbitrage indicators for ALCH/BTC pair

    Args:
        df1: ALCH/USDT OHLCV DataFrame
        df2: BTC/USDT OHLCV DataFrame
        alch_price: Current ALCH price
        btc_price: Current BTC price

    Returns:
        tuple: (DataFrame with indicators, current spread value)
    """
    try:
        # 🚨 Validate inputs
        if df1.empty or df2.empty or alch_price <= 0 or btc_price <= 0:
            logger.warning("🚫 Invalid inputs: Empty DF or zero prices")
            return pd.DataFrame(), 0.0

        # 🧩 Create combined dataframe
        df = pd.DataFrame({
            'timestamp': df1['timestamp'],
            'alch': df1['close'],    # ALCH/USDT closing prices
            'btc': df2['close']      # BTC/USDT closing prices
        })

        # 📈 Log-transform prices for spread calculation
        df['log_alch'] = np.log(df['alch'])
        df['log_btc'] = np.log(df['btc'])

        # 🔄 Calculate daily returns (log differences)
        df['return_alch'] = df['log_alch'].diff()  # ✅ corrected: np.long -> diff()
        df['return_btc'] = df['log_btc'].diff()   # ✅ corrected: np.log -> diff()

        # 📐 Calculate beta (hedge ratio) using covariance
        cov_matrix = df[['return_alch', 'return_btc']].cov()
        beta = cov_matrix.iloc[0, 1] / df['return_btc'].var()  # β = Cov(Alch,Btc) / Var(Btc)
        logger.info(f"🧮 Beta (Hedge Ratio): {beta:.6f}")

        # ⚖️ Calculate spread: log(ALCH) - β * log(BTC)
        df['spread'] = df['log_alch'] - beta * df['log_btc']

        # 📊 Calculate rolling statistics
        df['mu'] = df['spread'].rolling(LOOKBACK).mean()    # Rolling mean
        df['sigma'] = df['spread'].rolling(LOOKBACK).std()  # Rolling standard deviation

        # 🌡️ Calculate True Range (TR) for volatility
        hl = df1['high'] - df1['low']  # High-Low range
        hc = (df1['high'] - df1['close'].shift()).abs()  # |High - Prev Close|
        lc = (df1['low'] - df1['close'].shift()).abs()   # |Low - Prev Close|
        df['tr'] = pd.concat([hl, hc, lc], axis=1).max(axis=1)  # Max of three values

        # 📏 Calculate Average True Range (ATR)
        df['atr'] = df['tr'].rolling(ATR_PERIOD).mean()

        # 🧮 Calculate current spread using live prices
        current_spread = np.log(alch_price) - beta * np.log(btc_price)
        logger.info(f"📐 Current Spread: {current_spread:.6f} | "
                   f"σ: {df['sigma'].iloc[-1]:.6f} | ATR: {df['atr'].iloc[-1]:.6f}")

        return df, current_spread

    except Exception as e:
        logger.error(f"💥 CRITICAL ERROR in indicator calculation: {str(e)}")
        logger.error(f"🔍 ALCH DF: {len(df1)} rows | BTC DF: {len(df2)} rows")
        logger.error(f"💰 ALCH Price: {alch_price} | BTC Price: {btc_price}")
        return pd.DataFrame(), 0.0

# %% [markdown]
# Arbitrage Signal

# %%
def get_arbitrage_signal(spread: float, mu: float, sigma: float) -> str:
    """
    📈 Generate trading signal based on spread deviation from mean

    Args:
        spread: Current spread value (log(ALCH) - β*log(BTC))
        mu: Rolling mean of spread
        sigma: Rolling standard deviation of spread

    Returns:
        str: Trading signal ('bullish', 'bearish', or 'neutral')
    """
    try:
        # 🚨 Validate inputs for NaN/None
        if any(pd.isna(x) for x in [spread, mu, sigma]):
            logger.warning("⚠️ NaN values in signal inputs - returning neutral")
            return 'neutral'

        # 🧮 Calculate deviation thresholds
        lower_bound = mu - SIGMA_THRESHOLD * sigma
        upper_bound = mu + SIGMA_THRESHOLD * sigma

        # 📊 Generate signals based on spread position
        if spread < lower_bound:
            logger.info(f"🐂 BULLISH Signal | Spread: {spread:.6f} < μ-{SIGMA_THRESHOLD}σ ({lower_bound:.6f})")
            return 'bullish'
        elif spread > upper_bound:
            logger.info(f"🐻 BEARISH Signal | Spread: {spread:.6f} > μ+{SIGMA_THRESHOLD}σ ({upper_bound:.6f})")
            return 'bearish'

        # 📍 Neutral zone
        logger.debug(f"➖ NEUTRAL | Spread: {spread:.6f} ∈ [μ-σ: {lower_bound:.6f}, μ+σ: {upper_bound:.6f}]")
        return 'neutral'

    except Exception as e:
        logger.error(f"💥 CRITICAL Signal Error: {str(e)}")
        logger.error(f"🔍 Spread: {spread} | μ: {mu} | σ: {sigma}")
        return 'neutral'

# %% [markdown]
# Manage Position

# %%
async def manage_positions(exchange, current_price: float, symbol: str):
    """
    🔄 Manage open positions by checking TP/SL conditions
    Executes market orders to close positions when triggered

    Args:
        exchange: Connected exchange instance
        current_price: Current market price for the symbol
        symbol: Trading pair (e.g., 'ALCH/USDT')
    """
    global OPEN_POSITIONS
    positions_to_remove = []

    try:
        logger.info(f"🔍 Checking {len(OPEN_POSITIONS)} positions for {symbol}")

        for pos in OPEN_POSITIONS:
            # Filter positions for current symbol
            if pos['symbol'] != symbol:
                continue

            # 📊 Extract position details
            entry_price = pos['entry_price']
            qty = pos['quantity']
            side = pos['side']
            tp_price = pos['tp_price']
            sl_price = pos['sl_price']

            # 💰 Calculate P&L
            leverage = LEVERAGE  # Corrected spelling
            if side == 'long':
                profit = (current_price - entry_price) * qty * leverage
            else:  # short position
                profit = (entry_price - current_price) * qty * leverage

            try:
                # 🟢 LONG position management
                if side == 'long':
                    if current_price >= tp_price:
                        # ✅ TP hit - close with profit
                        logger.info(f"✅ LONG TP HIT | {symbol} | "
                                   f"Entry: {entry_price:.6f} | Exit: {current_price:.6f} | "
                                   f"Profit: {profit:.4f} USDT")
                        await exchange.create_market_sell_order(symbol, qty)
                        positions_to_remove.append(pos)

                    elif current_price <= sl_price:
                        # ❌ SL hit - close with loss
                        logger.info(f"❌ LONG SL HIT | {symbol} | "
                                   f"Entry: {entry_price:.6f} | Exit: {current_price:.6f} | "
                                   f"Loss: {abs(profit):.4f} USDT")
                        await exchange.create_market_sell_order(symbol, qty)
                        positions_to_remove.append(pos)

                # 🔴 SHORT position management
                elif side == 'short':
                    if current_price <= tp_price:
                        # ✅ TP hit - close with profit
                        logger.info(f"✅ SHORT TP HIT | {symbol} | "
                                   f"Entry: {entry_price:.6f} | Exit: {current_price:.6f} | "
                                   f"Profit: {profit:.4f} USDT")
                        await exchange.create_market_buy_order(symbol, qty)
                        positions_to_remove.append(pos)

                    elif current_price >= sl_price:
                        # ❌ SL hit - close with loss
                        logger.info(f"❌ SHORT SL HIT | {symbol} | "
                                   f"Entry: {entry_price:.6f} | Exit: {current_price:.6f} | "
                                   f"Loss: {abs(profit):.4f} USDT")
                        await exchange.create_market_buy_order(symbol, qty)
                        positions_to_remove.append(pos)

            except ccxt.InsufficientFunds:
                logger.error(f"💸 Insufficient funds to close {side} position for {symbol}")
            except ccxt.NetworkError:
                logger.warning(f"🌐 Network error closing {side} position - will retry")
            except Exception as e:
                logger.error(f"💥 Error closing {side} position: {str(e)}")

        # 🗑️ Remove closed positions
        OPEN_POSITIONS = [pos for pos in OPEN_POSITIONS if pos not in positions_to_remove]
        logger.info(f"📊 Open positions: {len(OPEN_POSITIONS)}")

    except Exception as e:
        logger.error(f"💥 CRITICAL position management error: {str(e)}")
        logger.error(f"🔍 Symbol: {symbol} | Price: {current_price}")

# %% [markdown]
# Place Order

# %%
async def place_order(exchange, signal: str, price: float, atr: float, symbol: str):
    """
    📤 Place new orders based on trading signals with hedging
    Manages position opening and automatic hedging in correlated pair

    Args:
        exchange: Connected exchange instance
        signal: Trading signal ('bullish' or 'bearish')
        price: Current market price for entry
        atr: Current Average True Range value
        symbol: Trading pair (e.g., 'ALCH/USDT')
    """
    global OPEN_POSITIONS

    try:
        # 🚨 Validate inputs
        if price <= 0 or atr <= 0 or np.isnan(atr):
            logger.warning("⚠️ Invalid order parameters - price or ATR invalid")
            return

        # 🛑 Check position limit
        if len(OPEN_POSITIONS) >= MAX_POSITION:
            logger.info("🚫 MAX positions reached - skipping new order")
            return

        # 🧮 Calculate position size
        qty = RISK_AMOUNT / price
        logger.info(f"🧾 {signal.upper()} Signal | {symbol} | Price: {price:.6f} | Qty: {qty:.6f}")

        try:
            if signal == 'bullish':
                # 🟢 Open LONG position
                await exchange.create_market_buy_order(symbol, qty)

                # 📊 Set TP/SL prices
                tp_price = price + TP_MULTIPLIER * atr
                sl_price = price - SL_MULTIPLIER * atr

                # 📝 Record position
                position = {
                    'symbol': symbol,
                    'side': 'long',
                    'entry_price': price,
                    'quantity': qty,
                    'tp_price': tp_price,
                    'sl_price': sl_price
                }
                OPEN_POSITIONS.append(position)
                logger.info(f"🟢 OPENED LONG | {symbol} | Entry: {price:.6f} | "
                           f"TP: {tp_price:.6f} | SL: {sl_price:.6f}")

                # ⚖️ HEDGE with SHORT on correlated pair (SYMBOL2)
                try:
                    hedge_symbol = SYMBOL2
                    hedge_price = (await exchange.fetch_ticker(hedge_symbol))['last']
                    hedge_qty = (qty * price) / hedge_price
                    await exchange.create_market_sell_order(hedge_symbol, hedge_qty)
                    logger.info(f"⚖️ HEDGE SHORT | {hedge_symbol} | Qty: {hedge_qty:.6f} | "
                               f"Price: {hedge_price:.6f}")
                except Exception as e:
                    logger.error(f"⚖️❌ Hedge order failed: {str(e)}")

            elif signal == 'bearish':
                # 🔴 Open SHORT position
                await exchange.create_market_sell_order(symbol, qty)

                # 📊 Set TP/SL prices
                tp_price = price - TP_MULTIPLIER * atr
                sl_price = price + SL_MULTIPLIER * atr

                # 📝 Record position
                position = {
                    'symbol': symbol,
                    'side': 'short',
                    'entry_price': price,
                    'quantity': qty,
                    'tp_price': tp_price,
                    'sl_price': sl_price
                }
                OPEN_POSITIONS.append(position)
                logger.info(f"🔴 OPENED SHORT | {symbol} | Entry: {price:.6f} | "
                           f"TP: {tp_price:.6f} | SL: {sl_price:.6f}")

                # ⚖️ HEDGE with LONG on correlated pair (SYMBOL2)
                try:
                    hedge_symbol = SYMBOL2
                    hedge_price = (await exchange.fetch_ticker(hedge_symbol))['last']
                    hedge_qty = (qty * price) / hedge_price
                    await exchange.create_market_buy_order(hedge_symbol, hedge_qty)
                    logger.info(f"⚖️ HEDGE LONG | {hedge_symbol} | Qty: {hedge_qty:.6f} | "
                              f"Price: {hedge_price:.6f}")
                except Exception as e:
                    logger.error(f"⚖️❌ Hedge order failed: {str(e)}")

        except ccxt.InsufficientFunds:
            logger.error("💸❌ Insufficient funds to open position")
        except ccxt.NetworkError:
            logger.warning("🌐⚠️ Network error - position opening failed (will retry)")
        except Exception as e:
            logger.error(f"💥 Position opening error: {str(e)}")

        logger.info(f"📊 Total Open Positions: {len(OPEN_POSITIONS)}/{MAX_POSITION}")

    except Exception as e:
        logger.error(f"💥 CRITICAL order placement error: {str(e)}")
        logger.error(f"🔍 Signal: {signal} | Symbol: {symbol} | Price: {price} | ATR: {atr}")

# %% [markdown]
# Main Function

# %%
async def main():
    """
    🚀 Main trading algorithm execution
    Manages exchange connection, market data processing, and trading decisions
    """
    exchange = None
    try:
        # 🏦 Initialize exchange connection
        exchange = ccxt.binance({
            'apiKey': api_key,
            'secret': api_secret,
            'enableRateLimit': True,
            'options': {
                'defaultType': 'future',
                'recvWindow': 10000,
                'adjustForTimeDifference': True,
            }
        })
        logger.info("🔌 Connecting to Binance Futures...")
        await exchange.load_markets()
        logger.info(f"📊 Loaded {len(exchange.markets)} markets")

        # 🎚️ Set leverage for both symbols
        await exchange.set_leverage(LEVERAGE, SYMBOL1)
        await exchange.set_leverage(LEVERAGE, SYMBOL2)
        logger.info(f"⚖️ Leverage set to {LEVERAGE}x for {SYMBOL1} and {SYMBOL2}")

        # 📈 Main trading loop
        logger.info("🚀 Starting trading algorithm")
        while True:
            try:
                # 📥 Fetch OHLCV data
                df1 = await fetch_data(exchange, SYMBOL1, '1m', 1000)
                df2 = await fetch_data(exchange, SYMBOL2, '1m', 1000)

                # 🚨 Check data sufficiency
                if len(df1) < LOOKBACK or len(df2) < LOOKBACK:
                    logger.warning(f"⚠️ Insufficient data: {SYMBOL1}={len(df1)}, {SYMBOL2}={len(df2)} < {LOOKBACK}")
                    await asyncio.sleep(CHECK_INTERVAL)
                    continue

                # 💹 Fetch real-time quotes
                bid1, ask1 = await fetch_quotes(exchange, SYMBOL1)
                bid2, ask2 = await fetch_quotes(exchange, SYMBOL2)

                # ✅ Validate market prices
                if (bid1 <= 0 or ask1 == float('inf') or
                    bid2 <= 0 or ask2 == float('inf')):
                    logger.warning("⚠️ Invalid market prices - skipping iteration")
                    await asyncio.sleep(CHECK_INTERVAL)
                    continue

                # 🧮 Calculate mid prices
                price1 = (bid1 + ask1) / 2
                price2 = (bid2 + ask2) / 2
                logger.debug(f"💰 {SYMBOL1}: {price1:.6f} | {SYMBOL2}: {price2:.6f}")

                # 📊 Calculate indicators
                df_indicator, spread = calculate_indicator(df1, df2, price1, price2)

                # 🚫 Skip if indicator calculation failed
                if df_indicator.empty:
                    logger.warning("⚠️ Indicator calculation failed - skipping")
                    await asyncio.sleep(CHECK_INTERVAL)
                    continue

                # 📈 Extract latest indicator values
                mu = df_indicator['mu'].iloc[-1]
                sigma = df_indicator['sigma'].iloc[-1]  # ✅ Fixed 'sima' typo
                atr = df_indicator['atr'].iloc[-1]

                # 📶 Get trading signal
                signal = get_arbitrage_signal(spread, mu, sigma)  # ✅ Fixed 'aritrage' typo

                # 🧾 Manage existing positions
                await manage_positions(exchange, price1, SYMBOL1)

                # 🆕 Place new order if valid signal
                if signal != 'neutral':
                    logger.info(f"🚨 NEW {signal.upper()} SIGNAL DETECTED")
                    await place_order(exchange, signal, price1, atr, SYMBOL1)

                # 📝 Status update
                logger.info(f"📈 {SYMBOL1}: {price1:.6f} | Spread: {spread:.6f} | "
                          f"μ: {mu:.6f} | σ: {sigma:.6f} | ATR: {atr:.6f} | Signal: {signal}")

                await asyncio.sleep(CHECK_INTERVAL)

            except KeyboardInterrupt:
                logger.info("🛑 Interrupted by user — shutting down gracefully...")
                break
            except Exception as e:
                logger.error(f"💥 Main loop error: {str(e)}")
                logger.error("🔄 Retrying in 5 seconds...")
                await asyncio.sleep(5)  # Wait longer after errors

    except Exception as e:
        logger.error(f"💥 CRITICAL initialization error: {str(e)}")
        traceback.print_exc()
    finally:
        if exchange:
            logger.info("🔌 Closing exchange connection")
            await exchange.close()
            logger.info("✅ Exchange connection closed")

if __name__ == "__main__":
    try:
        logger.info("🚀 Starting trading bot")
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("🛑 Script stopped by user")
    finally:
        logger.info("👋 Trading bot shutdown complete")


