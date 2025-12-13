import MetaTrader5 as mt
import os

LOGIN = 52643494
PASSWORD = "580MG!@erMeVOY"
SERVER = "ICMarketsSC-Demo"

MT5_PATH = r"C:\Program Files\MetaTrader 5\terminal64.exe"

if not os.path.exists(MT5_PATH):
    print("MT5 terminal not found at:", MT5_PATH)
    quit()

if not mt.initialize(path=MT5_PATH):
    print("MT5 initialize failed")
    print("Error:", mt.last_error())
    quit()

print("MT5 initialized successfully")

account = mt.account_info()
if account is None:
    print("Authorization failed")
    print("Error:", mt.last_error())
else:
    print("Account OK")
    print("Login:", account.login)
    print("Server:", account.server)

mt.shutdown()
