import MetaTrader5 as mt

if not mt.initialize():
    print("Init failed:", mt.last_error())
    quit()

info = mt.account_info()
if info is None:
    print("Auth failed:", mt.last_error())
else:
    print("Connected ✅")
    print(info.login, info.server)

mt.shutdown()
