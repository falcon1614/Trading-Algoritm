import ccxt.async_support as ccxt
import pandas as pd
import asyncio
import time
import logging
import os
import dotenv

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")
logger = logging.getLogger(__name__)

dotenv.load_dotenv()

API_KEY = os.getenv("API_KEY")
SECRET_KEY = os.getenv("SECRET_KEY")


async def create_ccxt_exchange():
    exchange = ccxt.binance({
        "apiKey": API_KEY,
        "secret": SECRET_KEY,
        "enableRateLimit": True,
        "options": {
            "defaultType": "spot"  # use "future" if you want futures
        }
    })
    await exchange.load_markets()
    return exchange

SYMBOL = "ALCH/USDT"
LOOKBACK_SECONDS = 60 * 60  # 1 hour


async def main_loop(exchange, symbol):
    while True:
        try:
            since = int((time.time() - LOOKBACK_SECONDS) * 1000)

            trades = await exchange.fetch_trades(
                symbol=symbol,
                since=since,
                limit=1000
            )

            if not trades:
                logger.info("No trades fetched.")
                await asyncio.sleep(60)
                continue

            df = pd.DataFrame(trades)

            # Convert timestamp
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")

            # Trade value in USDT
            df["trade_value"] = df["amount"] * df["price"]

            # Inflow and Outflow
            inflow = df[df["side"] == "buy"]["trade_value"].sum()
            outflow = df[df["side"] == "sell"]["trade_value"].sum()

            net_flow = inflow - outflow

            print("\n==============================")
            print(f"Symbol     : {symbol}")
            print(f"Inflow     : ${inflow:,.2f}")
            print(f"Outflow    : ${outflow:,.2f}")
            print(f"Net Flow   : ${net_flow:,.2f}")
            print("==============================\n")

            # Sleep until next hour
            sleep_time = 3600 - (time.time() % 3600)
            await asyncio.sleep(sleep_time)

        except Exception as e:
            logger.error(f"Error: {e}")
            await asyncio.sleep(10)


async def run():
    exchange = await create_ccxt_exchange()
    try:
        await main_loop(exchange, SYMBOL)
    finally:
        await exchange.close()

if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        logger.info("Stopped by user")
