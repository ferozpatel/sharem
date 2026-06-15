#DISCLAIMER:
#1) This sample code is for learning purposes only.
#2) Always be very careful when dealing with codes in which you can place orders in your account.
#3) The actual results may or may not be similar to backtested results. The historical results do not guarantee any profits or losses in the future.
#4) You are responsible for any losses/profits that occur in your account in case you plan to take trades in your account.
#5) TFU and Aseem Singhal do not take any responsibility of you running these codes on your account and the corresponding profits and losses that might occur.
#6) The running of the code properly is dependent on a lot of factors such as internet, broker, what changes you have made, etc. So it is always better to keep checking the trades as technology error can come anytime.
#7) This is NOT a tip providing service/code.
#8) This is NOT a software. Its a tool that works as per the inputs given by you.
#9) Slippage is dependent on market conditions.
#10) Option trading and automatic API trading are subject to market risks

from fyers_apiv3 import fyersModel
import datetime
import time
import requests
from datetime import timedelta
from pytz import timezone
import pandas as pd
import pytz
import threading

lock = threading.Lock()

######PIVOT POINTS##########################
####################__INPUT__#####################

# getNiftyExpiryDate all expirydate functios are till 2024 ,update it later on or check googledrive
# shared file from aseem singal
# get updated helper file
def getNiftyExpiryDate():
    nifty_expiry = {
        datetime.datetime(2024, 1, 4).date(): "24104",
        datetime.datetime(2024, 1, 11).date(): "24111",
        datetime.datetime(2024, 1, 18).date(): "24118",
        datetime.datetime(2024, 1, 25).date(): "24JAN",
        datetime.datetime(2024, 2, 1).date(): "24201",
        datetime.datetime(2024, 2, 8).date(): "24208",
        datetime.datetime(2024, 2, 15).date(): "24215",
        datetime.datetime(2024, 2, 22).date(): "24222",
        datetime.datetime(2024, 2, 29).date(): "24FEB",
        datetime.datetime(2024, 3, 7).date(): "24307",
        datetime.datetime(2024, 3, 14).date(): "24314",
        datetime.datetime(2024, 3, 21).date(): "24321",
        datetime.datetime(2024, 3, 28).date(): "24MAR",
        datetime.datetime(2024, 4, 4).date(): "24404",
        datetime.datetime(2024, 4, 10).date(): "24410",
        datetime.datetime(2024, 4, 18).date(): "24418",
        datetime.datetime(2024, 4, 25).date(): "24APR",
        datetime.datetime(2024, 5, 2).date(): "24502",
        datetime.datetime(2024, 5, 9).date(): "24509",
        datetime.datetime(2024, 5, 16).date(): "24516",
        datetime.datetime(2024, 5, 23).date(): "24523",
        datetime.datetime(2024, 5, 30).date(): "24MAY",
        datetime.datetime(2024, 6, 6).date(): "24606",
        datetime.datetime(2024, 6, 13).date(): "24613",
        datetime.datetime(2024, 6, 20).date(): "24620",
        datetime.datetime(2024, 6, 27).date(): "24JUN",

        datetime.datetime(2024, 12, 5).date(): "24D05",
        datetime.datetime(2024, 12, 12).date(): "24D12",
        datetime.datetime(2024, 12, 19).date(): "24D19",
        datetime.datetime(2024, 12, 26).date(): "24DEC",
        datetime.datetime(2025, 2, 6).date(): "25206",
        datetime.datetime(2025, 2, 13).date(): "25213",
        datetime.datetime(2025, 2, 20).date(): "25220",
        datetime.datetime(2025, 2, 27).date(): "25FEB",
        datetime.datetime(2025, 3, 6).date(): "25306",
        datetime.datetime(2025, 3, 13).date(): "25313",
        datetime.datetime(2025, 3, 20).date(): "25320",
        datetime.datetime(2025, 3, 27).date(): "25MAR",
        datetime.datetime(2025, 4, 3).date(): "25403",
        datetime.datetime(2025, 4, 9).date(): "25409",
        datetime.datetime(2025, 4, 17).date(): "25417",
        datetime.datetime(2025, 4, 24).date(): "25424",
        datetime.datetime(2025, 4, 30).date(): "25APR",
        datetime.datetime(2025, 5, 8).date(): "25508",
        datetime.datetime(2025, 5, 15).date(): "25515",
        datetime.datetime(2025, 5, 22).date(): "25522",
        datetime.datetime(2025, 5, 29).date(): "25MAY",
        datetime.datetime(2025, 6, 5).date(): "25605",
        datetime.datetime(2025, 6, 12).date(): "25612",
        datetime.datetime(2025, 6, 19).date(): "25619",
        datetime.datetime(2025, 6, 26).date(): "25JUN",
        datetime.datetime(2025, 7, 10).date(): "25710",
        datetime.datetime(2025, 7, 17).date(): "25717",
        datetime.datetime(2025, 7, 24).date(): "25724",
        datetime.datetime(2025, 7, 31).date(): "25JUL"
    }

    today = datetime.datetime.now().date()

    for date_key, value in nifty_expiry.items():
        if today <= date_key:
            print(value)
            return value

def getBankNiftyExpiryDate():
    banknifty_expiry = {
        datetime.datetime(2024, 8, 7).date(): "24807",
        datetime.datetime(2024, 8, 14).date(): "24814",
        datetime.datetime(2024, 8, 21).date(): "24821",
        datetime.datetime(2024, 8, 28).date(): "24AUG",
        datetime.datetime(2024, 9, 4).date(): "24904",
        datetime.datetime(2024, 9, 11).date(): "24911",
        datetime.datetime(2024, 9, 18).date(): "24918",
        datetime.datetime(2024, 9, 25).date(): "24SEP",
        datetime.datetime(2024, 10, 1).date(): "24O01",
        datetime.datetime(2024, 10, 9).date(): "24O09",
        datetime.datetime(2024, 10, 16).date(): "24O16",
        datetime.datetime(2024, 10, 23).date(): "24O23",
        datetime.datetime(2024, 10, 30).date(): "24OCT",
        datetime.datetime(2024, 11, 6).date(): "24N06",
        datetime.datetime(2024, 11, 13).date(): "24N13",
        datetime.datetime(2024, 11, 27).date(): "24NOV",
        datetime.datetime(2024, 12, 24).date(): "24DEC",
        datetime.datetime(2025, 1, 29).date(): "25JAN",
        datetime.datetime(2025, 2, 27).date(): "25FEB",
        datetime.datetime(2025, 3, 27).date(): "25MAR",
        datetime.datetime(2025, 4, 24).date(): "25APR",
        datetime.datetime(2025, 5, 29).date(): "25MAY",
        datetime.datetime(2025, 6, 26).date(): "25JUN",
        datetime.datetime(2025, 7, 31).date(): "25JUL",
        datetime.datetime(2025, 8, 28).date(): "25AUG",
        datetime.datetime(2025, 9, 30).date(): "25SEP",
        datetime.datetime(2025, 10, 28).date(): "25OCT",
        datetime.datetime(2025, 11, 25).date(): "25NOV",
        datetime.datetime(2025, 12, 30).date(): "25DEC",
        datetime.datetime(2026, 1, 27).date(): "26JAN",
        datetime.datetime(2026, 2, 24).date(): "26FEB",
        datetime.datetime(2026, 3, 30).date(): "26MAR",
        datetime.datetime(2026, 4, 28).date(): "26APR",
        datetime.datetime(2026, 5, 26).date(): "26MAY",
        datetime.datetime(2026, 6, 30).date(): "26JUN",
        datetime.datetime(2026, 7, 28).date(): "26JUL",
        datetime.datetime(2026, 8, 25).date(): "26AUG",
        datetime.datetime(2026, 9, 29).date(): "26SEP",
        datetime.datetime(2026, 10, 27).date(): "26OCT",
        datetime.datetime(2026, 11, 24).date(): "26NOV",
        datetime.datetime(2026, 12, 29).date(): "26DEC"
    }

    today = datetime.datetime.now().date()

    for date_key, value in banknifty_expiry.items():
        if today <= date_key:
            print(value)
            # return '25OCT'
            return value


def getSensexExpiryDate():
    sensex_expiry = {
        datetime.datetime(2025, 3, 11).date(): "25311",
        datetime.datetime(2025, 3, 18).date(): "25318",
        datetime.datetime(2025, 3, 25).date(): "25MAR"
    }

    today = datetime.datetime.now().date()

    for date_key, value in sensex_expiry.items():
        if today <= date_key:
            print(value)
            return value


def getMidcapNiftyExpiryDate():
    banknifty_expiry = {
        datetime.datetime(2024, 8, 7).date(): "24807",
        datetime.datetime(2024, 8, 14).date(): "24814",
        datetime.datetime(2024, 8, 21).date(): "24821",
        datetime.datetime(2024, 8, 28).date(): "24AUG",
        datetime.datetime(2024, 9, 4).date(): "24904",
        datetime.datetime(2024, 9, 11).date(): "24911",
        datetime.datetime(2024, 9, 18).date(): "24918",
        datetime.datetime(2024, 9, 25).date(): "24SEP",
        datetime.datetime(2024, 10, 1).date(): "24O01",
        datetime.datetime(2024, 10, 9).date(): "24O09",
        datetime.datetime(2024, 10, 16).date(): "24O16",
        datetime.datetime(2024, 10, 23).date(): "24O23",
        datetime.datetime(2024, 10, 30).date(): "24OCT",
        datetime.datetime(2024, 11, 6).date(): "24N06",
        datetime.datetime(2024, 11, 13).date(): "24N13",
        datetime.datetime(2024, 11, 27).date(): "24NOV",
        datetime.datetime(2024, 12, 24).date(): "24DEC",
        datetime.datetime(2025, 1, 29).date(): "25JAN",
        datetime.datetime(2025, 2, 27).date(): "25FEB",
        datetime.datetime(2025, 3, 27).date(): "25MAR"
    }

    today = datetime.datetime.now().date()

    for date_key, value in banknifty_expiry.items():
        if today <= date_key:
            print(value)
            return value


def getFinNiftyExpiryDate():
    finnifty_expiry = {
        datetime.datetime(2024, 2, 20).date(): "24220",
        datetime.datetime(2024, 2, 27).date(): "24FEB",
        datetime.datetime(2024, 3, 5).date(): "24305",
        datetime.datetime(2024, 3, 12).date(): "24312",
        datetime.datetime(2024, 3, 19).date(): "24319",
        datetime.datetime(2024, 3, 26).date(): "24MAR",
        datetime.datetime(2024, 4, 2).date(): "24402",
        datetime.datetime(2024, 4, 9).date(): "24409",
        datetime.datetime(2024, 4, 16).date(): "24416",
        datetime.datetime(2024, 4, 23).date(): "24423",
        datetime.datetime(2024, 4, 30).date(): "24APR",
        datetime.datetime(2024, 5, 7).date(): "24507",
        datetime.datetime(2024, 5, 14).date(): "24514",
        datetime.datetime(2024, 5, 21).date(): "24521",
        datetime.datetime(2024, 5, 28).date(): "24MAY",
        datetime.datetime(2024, 6, 4).date(): "24604",
        datetime.datetime(2024, 6, 11).date(): "24611",
        datetime.datetime(2024, 6, 18).date(): "24618",
        datetime.datetime(2024, 6, 25).date(): "24JUN",
    }

    today = datetime.datetime.now().date()

    for date_key, value in finnifty_expiry.items():
        if today <= date_key:
            print(value)
            return value

def getExpiryFormat(year, month, day, monthly):
    if monthly == 0:
        day1 = day
        if month == "JAN":
            month1 = 1
        elif month == "FEB":
            month1 = 2
        elif month == "MAR":
            month1 = 3
        elif month == "APR":
            month1 = 4
        elif month == "MAY":
            month1 = 5
        elif month == "JUN":
            month1 = 6
        elif month == "JUL":
            month1 = 7
        elif month == "AUG":
            month1 = 8
        elif month == "SEP":
            month1 = 9
        elif month == "OCT":
            month1 = "O"
        elif month == "NOV":
            month1 = "N"
        elif month == "DEC":
            month1 = "D"
    elif monthly == 1:
        day1 = ""
        month1 = month

    return str(year)+str(month1)+str(day1)

def getIndexSpot(stock):
    if stock == "BANKNIFTY":
        name = "NSE:NIFTYBANK-INDEX"
    elif stock == "NIFTY":
        name = "NSE:NIFTY50-INDEX"
    elif stock == "FINNIFTY":
        name = "NSE:FINNIFTY-INDEX"
    elif stock == "SENSEX":
        name = "BSE:SENSEX-INDEX"

    return name

def getOptionFormat(stock, intExpiry, strike, ce_pe):
    return "NSE:" + str(stock) + str(intExpiry)+str(strike)+str(ce_pe)
    # return "NSE:" + str(stock) + str(24)+"AUG"+str(strike)+str(ce_pe)

def getLTP(instrument):
    url = "http://localhost:4001/ltp?instrument=" + instrument
    try:
        resp = requests.get(url)
    except Exception as e:
        print(e)
    data = resp.json()
    return data

def manualLTP(symbol, fyers):
    data = {'symbols' : symbol}
    last_err = None
    for attempt in range(3):
        try:
            temp = fyers.quotes(data=data)
            time.sleep(0.25)
            if isinstance(temp, dict) and temp.get('d') and temp['d'][0].get('v', {}).get('lp') is not None:
                return float(temp['d'][0]['v']['lp'])
            last_err = temp
        except Exception as e:
            last_err = e
        if attempt < 2:
            time.sleep(0.3 * (2 ** attempt))
    raise KeyError(f"fyers.quotes returned no LTP for {symbol} after 3 retries. last_response={last_err}")

def exitAll(orderId,fyres):
    data =  {}
    # data = {
    #     "id": [orderId]
    # }
    # data = {
    #     "id":"NSE:SBIN-EQ-BO"
    # }

    # data = {
    #     "segment":[11],
    #     "side":[1,-1],
    #     "productType":["INTRADAY","CNC"]
    # }

    response = fyres.exit_positions(data=data)
    print("resp exit =",response)

def placeOrder(inst ,t_type,qty,order_type,price,variety,fyers,papertrading=0):
    exch = inst[:3]
    symb = inst[4:]
    dt = datetime.datetime.now()
    #papertrading = 0 #if this is 1, then actual trades will get placed
    print(dt.hour,":",dt.minute,":",dt.second ," => ",t_type," ",symb," ",qty," ",order_type)
    # for SL-L i.e stoploss limit order code will update soon

    # for bracket order BO => "productType" : "BO" and stopLoss is a mandatory input takeProfit is a mandatory input
    # Order type can be either market, limit, stop, or stop limit, Validity should be “DAY” Disclosed quantity should be 0
    if(order_type=="MARKET"):
        type1 = 2
        price = 0
    elif(order_type=="LIMIT"):
        type1 = 1

    if(t_type=="BUY"):
        side1=1
    elif(t_type=="SELL"):
        side1=-1

    data =  {
        "symbol":inst,
        "qty":qty,
        "type":type1,
        "side":side1,
        "productType":"MARGIN",  #MARGIN  -for positional
        "limitPrice":0,
        "stopPrice":0,
        "validity":"DAY",
        "disclosedQty":0,
        "offlineOrder":False,
        "stopLoss":0,
        "takeProfit":0
    }
    try:
        if (papertrading == 1):
            orderid = fyers.place_order(data)
            print(dt.hour,":",dt.minute,":",dt.second ," => ", symb , orderid)
            return orderid
        else:
            return 0


    except Exception as e:
        print(dt.hour,":",dt.minute,":",dt.second ," => ", symb , "Failed : {} ".format(e))

def placeBOOrder(inst ,t_type,qty,order_type,executedPrice,sl,target,fyers,papertrading=0):
    exch = inst[:3]
    symb = inst[4:]
    dt = datetime.datetime.now()
    #papertrading = 0 #if this is 1, then actual trades will get placed
    print(dt.hour,":",dt.minute,":",dt.second ," => ",t_type," ",symb," ",qty," ",order_type)
    # for SL-L i.e stoploss limit order code will update soon

    # for bracket order BO => "productType" : "BO" and stopLoss is a mandatory input takeProfit is a mandatory input
    # Order type can be either market, limit, stop, or stop limit, Validity should be “DAY” Disclosed quantity should be 0
    if(order_type=="MARKET"):
        type1 = 2
    elif(order_type=="LIMIT"):
        type1 = 1

    if(t_type=="BUY"):
        side1=1
    elif(t_type=="SELL"):
        side1=-1

    data =  {
        "symbol":inst,
        "qty":qty,
        "type":type1,
        "side":side1,
        "productType":"BO",  #MARGIN  -for positional
        "limitPrice":0,
        "stopPrice":0,
        "validity":"DAY",
        "disclosedQty":0,
        "offlineOrder":False,
        "stopLoss":executedPrice - sl,
        "takeProfit":target - executedPrice
    }
    try:
        if (papertrading == 1):
            orderid = fyers.place_order(data)
            print(dt.hour,":",dt.minute,":",dt.second ," => ", symb , orderid)
            return orderid
        else:
            return 0


    except Exception as e:
        print(dt.hour,":",dt.minute,":",dt.second ," => ", symb , "Failed : {} ".format(e))

def placeTargetOrder(inst ,t_type,qty,order_type,executedPrice,sl,target,fyers,papertrading=0):
    exch = inst[:3]
    symb = inst[4:]
    dt = datetime.datetime.now()
    #papertrading = 0 #if this is 1, then actual trades will get placed
    print(dt.hour,":",dt.minute,":",dt.second ," => ",t_type," ",symb," ",qty," ",order_type)
    # for SL-L i.e stoploss limit order code will update soon

    # for bracket order BO => "productType" : "BO" and stopLoss is a mandatory input takeProfit is a mandatory input
    # Order type can be either market, limit, stop, or stop limit, Validity should be “DAY” Disclosed quantity should be 0
    if(order_type=="MARKET"):
        type1 = 2
        limitPrice = 0
        stopPrice = 0
    elif(order_type=="LIMIT"):
        type1 = 1
        limitPrice = executedPrice
        stopPrice = 0
    elif(order_type=="SL-L"):
        type1 = 4
        limitPrice = executedPrice -1
        stopPrice = executedPrice
    if(t_type=="BUY"):
        side1=1
    elif(t_type=="SELL"):
        side1=-1

    data =  {
        "symbol":inst,
        "qty":qty,
        "type":type1,
        "side":side1,
        "productType":"MARGIN",  #MARGIN  -for positional, INTRADAY
        "limitPrice":limitPrice,
        "stopPrice":stopPrice,
        "validity":"DAY",
        "disclosedQty":0,
        "offlineOrder":False,
        "stopLoss":0,
        "takeProfit":0
    }
    try:
        if (papertrading == 1):
            orderid = fyers.place_order(data)
            print(dt.hour,":",dt.minute,":",dt.second ," => ", symb , orderid)
            return orderid
        else:
            return 0


    except Exception as e:
        print(dt.hour,":",dt.minute,":",dt.second ," => ", symb , "Failed : {} ".format(e))

def _fyers_history_with_retry(fyers, data, max_retries=3, base_delay=0.3):
    """
    Call fyers.history with retry on transient failures (rate limit, network blip).
    Returns the 'candles' list. Raises only after all retries exhausted.
    NOTE: retries only run on failure — successful calls have zero added latency.
    """
    last_err = None
    for attempt in range(max_retries):
        try:
            resp = fyers.history(data=data)
            if isinstance(resp, dict) and 'candles' in resp and resp['candles']:
                return resp['candles']
            # No candles — likely rate limit / error response from Fyers
            last_err = resp
        except Exception as e:
            last_err = e
        # Exponential-ish backoff: 0.3s, 0.6s, 1.2s — only on failure
        if attempt < max_retries - 1:
            time.sleep(base_delay * (2 ** attempt))
    raise KeyError(f"fyers.history returned no 'candles' after {max_retries} retries. last_response={last_err}")


def getHistorical(ticker,interval,duration,fyers):
    with lock:
        range_from = datetime.datetime.today()-timedelta(duration)
        range_to = datetime.datetime.today()
        # print("",ticker," ",interval," ", duration)
        from_date_string = range_from.strftime("%Y-%m-%d")
        to_date_string = range_to.strftime("%Y-%m-%d")
        data = {
            "symbol":ticker,
            "resolution":1,
            "date_format":"1",
            "range_from":from_date_string,
            "range_to":to_date_string,
            "cont_flag":"1"
        }

        response = _fyers_history_with_retry(fyers, data)

        # Create a DataFrame
        columns = ['Timestamp','open','high','low','close','volume']
        df = pd.DataFrame(response, columns=columns)

        # Convert Timestamp to datetime in UTC
        df['Timestamp2'] = pd.to_datetime(df['Timestamp'],unit='s').dt.tz_localize(pytz.utc)

        # Convert Timestamp to IST
        ist = pytz.timezone('Asia/Kolkata')
        df['Timestamp2'] = df['Timestamp2'].dt.tz_convert(ist)

        # =====
        # Filter rows where 'Timestamp2' is less than 15:30
        filtered_df = df[df['Timestamp2'].dt.time < pd.to_datetime('15:30').time()]
        filtered_df['datetime2'] = filtered_df['Timestamp2'].copy()
        # =====
        # Set 'Timestamp2' as the index
        filtered_df.set_index('Timestamp2', inplace=True)

        # Update the format of the datetime index and add 5 hours and 30 minutes for IST
        #filtered_df.index = filtered_df.index.floor('min')  # Floor to minutes
        #print(hist_data)

        finaltimeframe = str(interval)  + "min"

        # Resample to a specific time frame, for example, 30 minutes
        resampled_df = filtered_df.resample(finaltimeframe).agg({
            'open': 'first',
            'high': 'max',
            'low': 'min',
            'close': 'last',
            'volume': 'sum',
            'datetime2': 'first'
        })

        # If you want to fill any missing values with a specific method, you can use fillna
        #resampled_df = resampled_df.fillna(method='ffill')  # Forward fill

        #print(resampled_df)
        resampled_df = resampled_df.dropna(subset=['open'])

        return resampled_df


def getHistorical_old(ticker,interval,duration,fyers):
    range_from = datetime.datetime.today()-timedelta(duration)
    range_to = datetime.datetime.today()

    from_date_string = range_from.strftime("%Y-%m-%d")
    to_date_string = range_to.strftime("%Y-%m-%d")
    data = {
        "symbol":ticker,
        "resolution":interval,
        "date_format":"1",
        "range_from":from_date_string,
        "range_to":to_date_string,
        "cont_flag":"1"
    }

    response = fyers.history(data=data)['candles']

    # Create a DataFrame
    columns = ['Timestamp','open','high','low','close','volume']
    df = pd.DataFrame(response, columns=columns)

    # Convert Timestamp to datetime in UTC
    df['Timestamp2'] = pd.to_datetime(df['Timestamp'],unit='s').dt.tz_localize(pytz.utc)

    # Convert Timestamp to IST
    ist = pytz.timezone('Asia/Kolkata')
    df['Timestamp2'] = df['Timestamp2'].dt.tz_convert(ist)
    # Filter rows where 'Timestamp2' is less than 15:30
    filtered_df = df[(df['Timestamp2'].dt.time >= pd.to_datetime("09:15:00").time()) & (df['Timestamp2'].dt.time <= pd.to_datetime("15:29:00").time())]

    return (filtered_df)

def getHistoricalNew(ticker,interval,duration,fyers):
    range_from = datetime.datetime.today()-timedelta(duration)
    range_to = datetime.datetime.today()

    from_date_string = range_from.strftime("%Y-%m-%d")
    to_date_string = range_to.strftime("%Y-%m-%d")
    data = {
        "symbol":ticker,
        "resolution":interval,
        "date_format":"1",
        "range_from":from_date_string,
        "range_to":to_date_string,
        "cont_flag":"1"
    }

    response = fyers.history(data=data)['candles']

    # Create a DataFrame
    columns = ['Timestamp','open','high','low','close','volume']
    df = pd.DataFrame(response, columns=columns)

    # Convert Timestamp to datetime in UTC
    df['Timestamp2'] = pd.to_datetime(df['Timestamp'],unit='s').dt.tz_localize(pytz.utc)
    # print("==in new historical11==")
    # df['Date'] = df['Timestamp2'].dt.date()
    # print(df)
    # Convert Timestamp to IST
    ist = pytz.timezone('Asia/Kolkata')
    df['Timestamp2'] = df['Timestamp2'].dt.tz_convert(ist)


    # Filter rows where 'Timestamp2' is less than 15:30
    filtered_df = df[(df['Timestamp2'].dt.time >= pd.to_datetime("09:15:00").time()) & (df['Timestamp2'].dt.time <= pd.to_datetime("15:29:00").time())]

    # Set 'Timestamp2' as the index
    # filtered_df.set_index('Timestamp2', inplace=True)

    return (filtered_df)

def getHistoricalSeconds(ticker,interval,duration,fyers):
    range_from = datetime.datetime.today()-timedelta(duration)
    range_to = datetime.datetime.today()

    from_date_string = range_from.strftime("%Y-%m-%d")
    to_date_string = range_to.strftime("%Y-%m-%d")
    data = {
        "symbol":ticker,
        "resolution":"5S",
        "date_format":"1",
        "range_from":from_date_string,
        "range_to":to_date_string,
        "cont_flag":"1"
    }

    response = fyers.history(data=data)['candles']

    # Create a DataFrame
    columns = ['Timestamp','open','high','low','close','volume']
    df = pd.DataFrame(response, columns=columns)

    # Convert Timestamp to datetime in UTC
    df['Timestamp2'] = pd.to_datetime(df['Timestamp'],unit='s').dt.tz_localize(pytz.utc)

    # Convert Timestamp to IST
    ist = pytz.timezone('Asia/Kolkata')
    df['Timestamp2'] = df['Timestamp2'].dt.tz_convert(ist)

    # =====
    # Filter rows where 'Timestamp2' is less than 15:30
    filtered_df = df[df['Timestamp2'].dt.time < pd.to_datetime('15:30').time()]
    filtered_df['datetime2'] = filtered_df['Timestamp2'].copy()
    # =====
    # Set 'Timestamp2' as the index
    filtered_df.set_index('Timestamp2', inplace=True)
    # print("sec resp")
    # print(filtered_df)
    return filtered_df
    # quit()
    # Update the format of the datetime index and add 5 hours and 30 minutes for IST
    #filtered_df.index = filtered_df.index.floor('min')  # Floor to minutes
    #print(hist_data)
    #
    # finaltimeframe = str(interval)  + "sec"
    # # Resample to a specific time frame, for example, 30 minutes
    # resampled_df = filtered_df.resample(finaltimeframe).agg({
    #     'open': 'first',
    #     'high': 'max',
    #     'low': 'min',
    #     'close': 'last',
    #     'volume': 'sum',
    #     'datetime2': 'first'
    # })
    # resampled_df = resampled_df.dropna(subset=['open'])
    # return resampled_df

def getOptionChain(strikecount, ticker, fyers):
    print(ticker," strikecount=",strikecount, "  fyers",fyers)
    data = {
        # "symbol":"NSE:TCS-EQ",
        "symbol":ticker,
        "strikecount":strikecount,
        "timestamp": ""
    }
    response = fyers.optionchain(data=data)
    # print("==check getOptionChain==")
    # print(response)
    return response
# Function to extract general data
def getTotalOI(response_data):
    if response_data.get("code") == 200:
        data = response_data.get("data", {})
        call_oi = data.get("callOi")
        put_oi = data.get("putOi")
        return {
            "callOi": call_oi,
            "putOi": put_oi
        }
    return None

# Function to extract expiry data
def extract_expiry_data(response_data):
    if response_data.get("code") == 200:
        expiry_data = response_data.get("data", {}).get("expiryData", [])
        return [{"date": expiry["date"], "expiry": expiry["expiry"]} for expiry in expiry_data]
    return []

# Function to extract India VIX data
def extract_indiavix_data(response_data):
    if response_data.get("code") == 200:
        indiavix_data = response_data.get("data", {}).get("indiavixData", {})
        return {
            "ltp": indiavix_data.get("ltp"),
            "ltpch": indiavix_data.get("ltpch"),
            "ltpchp": indiavix_data.get("ltpchp")
        }
    return None

# Function to extract options chain data
def getClosestOptions(response_data):
    if response_data.get("code") == 200:
        options_chain = response_data.get("data", {}).get("optionsChain", [])
        return [{
            "symbol": option.get("symbol"),
            "option_type": option.get("option_type"),
            "strike_price": option.get("strike_price"),
            "ltp": option.get("ltp"),
            "volume": option.get("volume"),
            "oi": option.get("oi")
        } for option in options_chain]
    return []

def getClosestOptions1(response_data):
    if response_data.get("code") == 200:
        options_chain = response_data.get("data", {}).get("optionsChain", [])
        return [{
            "symbol": option.get("symbol"),
            "option_type": option.get("option_type"),
            # "strike_price": option.get("strike_price"),
            # "ltp": option.get("ltp"),
            "volume": option.get("volume"),
            "oi": option.get("oi"),
            "oich": option.get("oich"),
            # "oichp": option.get("oichp"),
            # "prev_oi": option.get("prev_oi")
        } for option in options_chain]
    return []

def getSyntheticFUTStrike(stock,fyers):
    name = getIndexSpot(stock)
    prev_diff = 10000
    closest_Strike=10000

    # BnFut = "NSE:BANKNIFTY25SEPFUT"
    ltp = manualLTP(name,fyers)
    # ltp = manualLTP(BnFut,fyers)

    # print("spot name",name, " ltp=",ltp)
    if stock == 'BANKNIFTY':
        intExpiry= getBankNiftyExpiryDate()
        closest_Strike = int(round((ltp / 100),0) * 100)
    elif stock == 'NIFTY':
        intExpiry= getNiftyExpiryDate()
        closest_Strike = int(round((ltp / 50),0) * 50)
    print('helper closest_Strike = ',closest_Strike)

    atmCE = getOptionFormat(stock, intExpiry, closest_Strike, "CE")
    atmPE = getOptionFormat(stock, intExpiry, closest_Strike, "PE")
    # print('atmCE = ',atmCE)

    atmCEPremium = manualLTP(atmCE,fyers)
    atmPEPremium = manualLTP(atmPE,fyers)
    # print('atmCEPremium = ',atmCEPremium)

    print(ltp, " cepre =",atmCEPremium, " atmPEPremium =",atmPEPremium)
    syntheticATMStrike = ltp + atmCEPremium - atmPEPremium
    print('chk= = ',syntheticATMStrike)
    if stock == 'BANKNIFTY':
        syntheticATMStrike = int(round((syntheticATMStrike / 100),0) * 100)
    elif stock == 'NIFTY':
        syntheticATMStrike = int(round((syntheticATMStrike / 50),0) * 50)
    print('syntheticATMStrike = ',syntheticATMStrike, "atmCEPE =",atmCE, " ",atmPE)

    return syntheticATMStrike