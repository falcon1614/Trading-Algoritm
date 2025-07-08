# import hashlib
# import hmac
# import requests
# import time

# base_url = 'https://api.india.delta.exchange'
# api_key = 'Apz01VwHRlrdGA7Nehuo4QNEUCLsBt'
# api_secret = '9CsbVIfnt7WvsQPThYxJI23SNajcUxfaOiqV0EMsPFWV8Ivw2iEidp62dohH'


# def generate_signature(secret, message):
#     message = bytes(message, 'utf-8')
#     secret = bytes(secret, 'utf-8')
#     hash = hmac.new(secret, message, hashlib.sha256)
#     return hash.hexdigest()

# # Get open orders
# method = 'GET'
# timestamp = str(int(time.time()))
# path = '/v2/orders'
# url = f'{base_url}{path}'
# query_string = '?product_id=1&state=open'
# payload = ''
# signature_data = method + timestamp + path + query_string + payload
# signature = generate_signature(api_secret, signature_data)

# req_headers = {
#   'api-key': api_key,
#   'timestamp': timestamp,
#   'signature': signature,
#   'User-Agent': 'python-rest-client',
#   'Content-Type': 'application/json'
# }

# query = {"product_id": 1, "state": 'open'}

# response = requests.request(
#     method, url, data=payload, params=query, timeout=(3, 27), headers=req_headers
# )

# # Place new order
# method = 'POST'
# timestamp = str(int(time.time()))
# path = '/v2/orders'
# url = f'{base_url}{path}'
# query_string = ''
# payload = "{\"order_type\":\"limit_order\",\"size\":3,\"side\":\"buy\",\"limit_price\":\"0.0005\",\"product_id\":16}"
# signature_data = method + timestamp + path + query_string + payload
# signature = generate_signature(api_secret, signature_data)

# req_headers = {
#   'api-key': api_key,
#   'timestamp': timestamp,
#   'signature': signature,
#   'User-Agent': 'rest-client',
#   'Content-Type': 'application/json'
# }

# response = requests.request(
#     method, url, data=payload, params={}, timeout=(3, 27), headers=req_headers
# )
import aiohttp
import asyncio
import time
import os
import hmac
import hashlib
import json
import logging
from dotenv import load_dotenv
import pandas as pd

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

API_KEY = os.getenv("DELTA_API_KEY")
API_SECRET = os.getenv("DELTA_API_SECRET")
BASE_URL = "https://api.delta.exchange"

PRODUCT_ID = 1  # replace with actual product_id for your instrument
INTERVAL = '1m'

async def generate_signature(method, timestamp, path, query, body):
    msg = f"{method}{timestamp}{path}{query}{body}"
    return hmac.new(API_SECRET.encode(), msg.encode(), hashlib.sha256).hexdigest()


async def delta_request(method, path, params=None, payload=None, auth=True):
    url = BASE_URL + path
    timestamp = str(int(time.time()))
    query_str = ''
    if params:
        query_str = '?' + '&'.join(f"{k}={v}" for k, v in params.items())
    body_str = json.dumps(payload) if payload else ''

    headers = {
        'Content-Type': 'application/json'
    }

    if auth:
        signature = await generate_signature(method, timestamp, path, query_str, body_str)
        headers.update({
            'api-key': API_KEY,
            'timestamp': timestamp,
            'signature': signature
        })

    async with aiohttp.ClientSession() as session:
        async with session.request(
            method, url, params=params, data=body_str if payload else None, headers=headers
        ) as resp:
            if resp.status >= 400:
                text = await resp.text()
                logger.error(f"HTTP {resp.status} - {text}")
            resp.raise_for_status()
            return await resp.json()


async def fetch_ohlcv(product_id, interval='1m', limit=100):
    now = int(time.time())
    from_ts = now - limit * 60
    params = {
        "product_id": product_id,
        "interval": interval,
        "from": from_ts,
        "to": now
    }
    data = await delta_request("GET", "/v2/history/candles", params=params, auth=False)
    df = pd.DataFrame(data['candles'], columns=["timestamp", "open", "high", "low", "close", "volume"])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='s')
    return df


async def fetch_ticker(product_id):
    params = {"product_ids": product_id}
    data = await delta_request("GET", "/v2/tickers", params=params, auth=False)
    ticker = data['tickers'][0]
    bid = float(ticker['best_bid_price'])
    ask = float(ticker['best_ask_price'])
    return bid, ask


async def place_order(product_id, side, size, price=None, order_type="limit_order"):
    payload = {
        "product_id": product_id,
        "side": side,
        "order_type": order_type,
        "size": size
    }
    if price:
        payload["limit_price"] = str(price)
    data = await delta_request("POST", "/v2/orders", payload=payload, auth=True)
    return data


async def main():
    logger.info("Loading OHLCV")
    df = await fetch_ohlcv(PRODUCT_ID)
    logger.info(df.tail())

    bid, ask = await fetch_ticker(PRODUCT_ID)
    mid = (bid + ask) / 2
    logger.info(f"Bid: {bid}, Ask: {ask}, Mid: {mid}")

    logger.info("Placing test order")
    order = await place_order(PRODUCT_ID, side="buy", size=1, price=mid)
    logger.info(order)


if __name__ == "__main__":
    asyncio.run(main())
