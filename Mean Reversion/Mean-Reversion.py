import ccxt.async_support as ccxt
import pandas as pd
import numpy as np
import asyncio
import logging
import os
import dotenv

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)
dotenv.load_dotenv()

API_KEY = os.getenv("API_KEY")
SECRET_KEY = os.getenv("SECRET_KEY")

#  Variables

SYMBOL = "BTCUSDT"
WS_DEPTH_URL = "wss://fstream.binance.com/ws/alchusdt@depth10@100ms"
WS_TRADE_URL = "wss://fstream.binance.com/ws/alchusdt@trade"
