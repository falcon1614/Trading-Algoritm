from fastapi import FastAPI

app = FastAPI()

@app.get("/")
def health():
    return {"status": "Volume Imbalance Momentum running"}

@app.get("/signal")
def signal():
    return {"signal": "BUY"}  # later connect your logic
