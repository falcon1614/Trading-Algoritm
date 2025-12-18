from dhanhq import marketfeed
import time

# --- Configuration ---
CLIENT_ID = "YOUR_DHAN_CLIENT_ID"
ACCESS_TOKEN = "YOUR_ACCESS_TOKEN"

# --- Subscription List ---
# Format: (Exchange, Security ID, Subscription Mode)
# Modes: marketfeed.Ticker (LTP), marketfeed.Quote (Full Quote), marketfeed.Full (Depth)
instruments = [
    (marketfeed.NSE, "1333", marketfeed.Ticker), # HDFC Bank
    (marketfeed.NSE, "2885", marketfeed.Quote)   # Reliance
]

# --- Main Logic ---
def main():
    try:
        # 1. Initialize the Feed
        # version="v2" is recommended for the latest features
        feed = marketfeed.DhanFeed(
            CLIENT_ID, 
            ACCESS_TOKEN, 
            instruments, 
            version="v2"
        )

        print("Connecting to Dhan WebSocket...")

        # 2. Start the connection loop
        # run_forever() keeps the connection alive and handles pings automatically
        while True:
            feed.run_forever()
            
            # 3. Retrieve and print data
            response = feed.get_data()
            if response:
                print(f"New Feed Received: {response}")
            
            # Small sleep to prevent CPU spiking in the loop
            time.sleep(0.01)

    except Exception as e:
        print(f"An error occurred: {e}")
    
    finally:
        # 4. Clean up connection
        if 'feed' in locals():
            feed.disconnect()
            print("WebSocket Disconnected.")

if __name__ == "__main__":
    main()