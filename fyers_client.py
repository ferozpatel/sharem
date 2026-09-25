from fyers_apiv3 import fyersModel
# import helper_fyers as helper

app_id = open("fyers_client_id.txt",'r').read()
access_token = open("fyers_access_token.txt",'r').read()
fyers = fyersModel.FyersModel(token=access_token,is_async=False,client_id=app_id)