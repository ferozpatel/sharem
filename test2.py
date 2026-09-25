from fyers_apiv3.FyersWebsocket import data_ws


def onmessage(message):
    """
    Callback function to handle incoming messages from the FyersDataSocket WebSocket.

    Parameters:
        message (dict): The received message from the WebSocket.

    """
    print("Response:", message)


def onerror(message):
    """
    Callback function to handle WebSocket errors.

    Parameters:
        message (dict): The error message received from the WebSocket.


    """
    print("Error:", message)


def onclose(message):
    """
    Callback function to handle WebSocket connection close events.
    """
    print("Connection closed:", message)


def onopen():
    """
    Callback function to subscribe to data type and symbols upon WebSocket connection.

    """
    # Specify the data type and symbols you want to subscribe to
    data_type = "SymbolUpdate"

    # Subscribe to the specified symbols and data type
    symbols = ['NSE:NIFTYBANK-INDEX','NSE:NIFTY50-INDEX','BSE:SENSEX-INDEX']
    fyers.subscribe(symbols=symbols, data_type=data_type)

    # Keep the socket running to receive real-time data
    fyers.keep_running()


# Replace the sample access token with your actual access token obtained from Fyers
# access_token = "MCO4RO1YDC-100:eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJpc3MiOiJhcGkuZnllcnMuaW4iLCJpYXQiOjE3MjAxNTE1MzAsImV4cCI6MTcyMDIyNTgxMCwibmJmIjoxNzIwMTUxNTMwLCJhdWQiOlsieDowIiwieDoxIiwieDoyIiwiZDoxIiwiZDoyIiwieDoxIiwieDowIl0sInN1YiI6ImFjY2Vzc190b2tlbiIsImF0X2hhc2giOiJnQUFBQUFCbWgyM3FkOElKR2dQMnNYM3hUa2ZKSHJmNTlrWDJMdFhRaDZ4cUVLWnh2LTVMcF9ja0xoNmVMaWFyT2RCa0NDeHNSM0MzX3VUOUkxM2tQeE1RdWNWR3JWTGRJLVJSQnJWN1ZxLUdJLUJFUElnQWhhaz0iLCJkaXNwbGF5X25hbWUiOiJGSVJPWiBTSUtBTkRBUiBQQVRFTCIsIm9tcyI6IksxIiwiaHNtX2tleSI6ImI2NzNjOGU3YTc5OGIxYjA3YjYxOWU3OTYxZjU4ZjYxNTY2M2FmZmU1MWQ3NWQ4MzBiMTBjODI2IiwiZnlfaWQiOiJZRjAwMzY4IiwiYXBwVHlwZSI6MTAwLCJwb2FfZmxhZyI6Ik4ifQ.CmW5eLtlfwSyaMhVGrd3NEAGwgDHW5LudE54YFXbu1o"

app_id = open("fyers_client_id.txt",'r').read()
access_token = open("fyers_access_token.txt",'r').read()
access_token_websocket = app_id + ":" + access_token
print("by loginfile access_token_websocket=",access_token_websocket)

# access_token = open("fyers_access_token.txt",'r').read()
# access_token_websocket = access_token
# print("access_token_websocket=",access_token_websocket)
# Create a FyersDataSocket instance with the provided parameters
fyers = data_ws.FyersDataSocket(
    access_token=access_token_websocket,       # Access token in the format "appid:accesstoken"
    log_path="",                     # Path to save logs. Leave empty to auto-create logs in the current directory.
    litemode=False,                  # Lite mode disabled. Set to True if you want a lite response.
    write_to_file=False,              # Save response in a log file instead of printing it.
    reconnect=True,                  # Enable auto-reconnection to WebSocket on disconnection.
    on_connect=onopen,               # Callback function to subscribe to data upon connection.
    on_close=onclose,                # Callback function to handle WebSocket connection close events.
    on_error=onerror,                # Callback function to handle WebSocket errors.
    on_message=onmessage             # Callback function to handle incoming messages from the WebSocket.
)

# Establish a connection to the Fyers WebSocket
fyers.connect()

