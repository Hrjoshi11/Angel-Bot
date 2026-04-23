import json
from SmartApi.smartWebSocketV2 import SmartWebSocketV2

# Global dictionary to store live prices
LIVE_PRICE = {}
NIFTY_TOKEN = "26000" # Angel One token for Nifty 50 Index (NSE)

def start_websocket(smart_api, api_key, client_code, log_callback=print):
    """Starts the Angel One WebSocket V2 to fetch live tick data."""
    log_callback("Initializing Live WebSocket V2...")
    
    try:
        auth_token = smart_api.access_token
        feed_token = smart_api.getfeedToken()
    except Exception as e:
        log_callback(f"🔴 Error fetching tokens: {e}")
        return

    sws = SmartWebSocketV2(auth_token, api_key, client_code, feed_token)

    def on_data(wsapp, message):
        """Triggered when tick data is received."""
        try:
            # Angel One sends the last traded price in the "last_traded_price" field
            # It is sent in integer format (paisa), so we divide by 100 to get Rupees
            if isinstance(message, dict):
                token = message.get("token")
                price = message.get("last_traded_price", 0)
                if token and price:
                    LIVE_PRICE[token] = price / 100.0
                    
        except Exception as e:
            pass # Ignore malformed ticks to prevent crashing

    def on_open(wsapp):
        log_callback("🟢 WebSocket Connected Successfully!")
        
        # Subscribe to Nifty 50 Live Data
        # Mode 1 = Last Traded Price (LTP), Exchange 1 = NSE
        token_list = [{"exchangeType": 1, "tokens": [NIFTY_TOKEN]}]
        sws.subscribe("nifty_live", 1, token_list)
        
        log_callback("📡 Subscribed to Nifty 50 Live Data!")
        
    def on_error(wsapp, error):
        log_callback(f"🔴 WebSocket Error: {error}")

    def on_close(wsapp):
        log_callback("⚪ WebSocket Connection Closed.")

    sws.on_open = on_open
    sws.on_data = on_data
    sws.on_error = on_error
    sws.on_close = on_close

    sws.connect()