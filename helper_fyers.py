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

# Small throttle after each successful quote call. Was 0.25s, which added ~1.5s to the entry
# path (manualLTP is called 6+ times per entry) and stretched the 2s LTP monitoring poll to
# ~2.25s+. Only Sensex runs now (no parallel BN), and we sit far below Fyers' rate limit
# (~26 req/min while monitoring), so a lighter throttle is safe and tightens both entry
# slippage and SL/target detection latency.
QUOTE_THROTTLE_SEC = 0.05


def manualLTP(symbol, fyers):
    """
    Return the live price for `symbol`.

    Prefers 'lp' (last traded price). IMPORTANT: 'lp' is 0 for a strike that has not traded
    yet today — common on deep-OTM hedge strikes, which is exactly where our hedge search
    walks. Returning 0 as if it were a real price would let the hedge search accept an
    untraded/illiquid strike (0 <= max_premium is True) and would also zero out the hedge
    offset in risk sizing. So a 0/missing 'lp' falls back to the bid/ask midpoint, and if
    that is unusable too the call is retried and ultimately raises rather than lying.
    """
    data = {'symbols' : symbol}
    last_err = None
    for attempt in range(3):
        try:
            temp = fyers.quotes(data=data)
            time.sleep(QUOTE_THROTTLE_SEC)
            if isinstance(temp, dict) and temp.get('d'):
                v = temp['d'][0].get('v', {}) or {}
                lp = v.get('lp')

                # Normal path: a genuine traded price.
                if lp is not None and float(lp) > 0:
                    return float(lp)

                # No trade yet today -> derive a fair price from the live book.
                bid = v.get('bid')
                ask = v.get('ask')
                try:
                    bid = float(bid) if bid is not None else 0.0
                    ask = float(ask) if ask is not None else 0.0
                except (TypeError, ValueError):
                    bid = ask = 0.0

                if bid > 0 and ask > 0:
                    mid = round((bid + ask) / 2.0, 2)
                    print(f"MANUAL_LTP_MID_FALLBACK: {symbol} lp={lp} (untraded) -> "
                          f"bid={bid} ask={ask} mid={mid}")
                    return mid
                if ask > 0:
                    print(f"MANUAL_LTP_ASK_FALLBACK: {symbol} lp={lp} bid={bid} -> ask={ask}")
                    return ask

                # lp==0 and no usable book — treat as no price, retry.
                last_err = f"no usable price (lp={lp}, bid={bid}, ask={ask})"
            else:
                last_err = temp
        except Exception as e:
            last_err = e
        if attempt < 2:
            time.sleep(0.3 * (2 ** attempt))
    raise KeyError(f"fyers.quotes returned no LTP for {symbol} after 3 retries. last_response={last_err}")

def getSpreadMargin(legs, fyers):
    """
    Calculate the actual broker margin required for a basket of orders (spread).
    Uses Fyers v3 multiorder/margin API which accounts for hedge benefit.

    NOTE: The fyers_apiv3 Python SDK does NOT wrap this endpoint (no
    fyers.multiorder_margin method exists) - it must be called directly via
    the REST API. We reuse the same auth header format the SDK itself uses
    internally: "{client_id}:{token}".

    Args:
        legs: list of dicts, each with keys: symbol, qty, side (1=buy,-1=sell), type (2=market)
        fyers: FyersModel instance (used only to read client_id/token for auth header)

    Returns:
        tuple (margin_total, margin_avail): required margin for the basket AND the account's
        real available funds (both floats), or (None, None) on failure. margin_avail is the
        same live "Available" balance the broker enforces — use it (not a hardcoded cap) to
        size within actual funds.
    """
    order_data = []
    for leg in legs:
        order_data.append({
            "symbol": leg["symbol"],
            "qty": leg["qty"],
            "side": leg["side"],
            "type": leg.get("type", 2),          # 2 = market
            "productType": leg.get("productType", "MARGIN"),
            "limitPrice": leg.get("limitPrice", 0.0),
            "stopLoss": 0.0,
            "stopPrice": 0.0,
            "takeProfit": 0.0
        })
    data = {"data": order_data}
    url = "https://api-t1.fyers.in/api/v3/multiorder/margin"
    header = "{}:{}".format(fyers.client_id, fyers.token)
    headers = {"Authorization": header, "Content-Type": "application/json"}
    last_err = None
    for attempt in range(3):
        try:
            resp = requests.post(url, json=data, headers=headers, timeout=10)
            resp_json = resp.json()
            if isinstance(resp_json, dict) and resp_json.get('data') and resp_json['data'].get('margin_total') is not None:
                _total = float(resp_json['data']['margin_total'])
                _avail = resp_json['data'].get('margin_avail')
                _avail = float(_avail) if _avail is not None else None
                return (_total, _avail)
            last_err = resp_json
        except Exception as e:
            last_err = e
        if attempt < 2:
            time.sleep(0.3 * (2 ** attempt))
    print("getSpreadMargin: failed to fetch margin. last_response=", last_err)
    return (None, None)

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
        "productType":"INTRADAY",  # same-day exit only, no overnight carry -> intraday margin discount
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
        "productType":"INTRADAY",  # same-day exit only, no overnight carry -> intraday margin discount
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
            "cont_flag":"1",
            # Pin OI off explicitly. We previously omitted oi_flag and relied on the Fyers
            # default; on 2026-08-03 that default changed and candles started arriving with a
            # 7th field (open interest), which crashed DataFrame construction. Setting it
            # explicitly makes the response shape deterministic instead of vendor-default.
            "oi_flag":0
        }

        response = _fyers_history_with_retry(fyers, data)

        # Create a DataFrame. Fyers candle rows are [timestamp, open, high, low, close, volume, ...]
        # — build without forcing column count (broke on 2026-08-03 when Fyers started returning
        # a 7th field, e.g. open interest, on F&O symbols: "6 columns passed, had 7 columns" ->
        # unhandled ValueError that crashed the whole process). Only the first 6 fields are used;
        # any extra trailing fields are ignored so future schema additions don't break us again.
        df = pd.DataFrame(response)
        expected_cols = ['Timestamp', 'open', 'high', 'low', 'close', 'volume']
        # .copy() matters: a bare .iloc slice is a VIEW, and that "slice of a DataFrame"
        # lineage propagates downstream, making the later filtered_df column assignment emit
        # SettingWithCopyWarning (floods the log) with no guarantee the write propagates.
        # Copying restores the standalone-DataFrame ownership the old constructor gave us.
        df = df.iloc[:, :len(expected_cols)].copy()
        df.columns = expected_cols

        # Convert Timestamp to datetime in UTC
        df['Timestamp2'] = pd.to_datetime(df['Timestamp'],unit='s').dt.tz_localize(pytz.utc)

        # Convert Timestamp to IST
        ist = pytz.timezone('Asia/Kolkata')
        df['Timestamp2'] = df['Timestamp2'].dt.tz_convert(ist)

        # =====
        # Filter rows where 'Timestamp2' is less than 15:30
        # .copy() so this is an owned DataFrame, not a filtered view — the column assignment
        # and inplace set_index below are both writes, which on a view emit
        # SettingWithCopyWarning and are not guaranteed to propagate.
        filtered_df = df[df['Timestamp2'].dt.time < pd.to_datetime('15:30').time()].copy()
        filtered_df['datetime2'] = filtered_df['Timestamp2']
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


def getOptionChainWithGreeks(strikecount, ticker, fyers):
    """
    Same as getOptionChain but requests Greeks (delta/gamma/theta/vega/iv) via greeks="1".
    Kept as a SEPARATE function (not merged into getOptionChain) because the main-loop calls
    to getOptionChain run every 3-min cycle and are used purely for PCR/CHOI — adding greeks
    there would be unused payload on every cycle. This is for one-off/entry-time delta lookups
    only, called explicitly where the delta ratio is actually needed.

    NOTE (2026-08-10): the documented sample response returns an IDENTICAL greeks block
    (delta/gamma/theta/vega/iv) across every strike shown — almost certainly placeholder/mock
    data in the docs, not a realistic live response. Real strike-to-strike delta variation is
    UNVERIFIED until tested against a live Sensex option chain.
    """
    data = {
        "symbol": ticker,
        "strikecount": strikecount,
        "timestamp": "",
        "greeks": "1"
    }
    response = fyers.optionchain(data=data)
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


def getDeltaForSymbols(response_data, symbols):
    """
    Extract delta (and the rest of the greeks block, for reference) for specific option
    symbols from a getOptionChainWithGreeks(...) response.

    Args:
        response_data: raw response from getOptionChainWithGreeks (must have been called
                        with greeks="1" — the plain getOptionChain response has no "greeks" key).
        symbols: list of exact option symbols to look up, e.g.
                 ["BSE:SENSEX2681378700CE", "BSE:SENSEX2681379500CE"]

    Returns:
        dict: {symbol: {"delta": ..., "gamma": ..., "theta": ..., "vega": ..., "iv": ...} or None}
        None per-symbol if that symbol wasn't found in the response, or had no "greeks" block
        (e.g. the underlying/spot row, which always has option_type="").
    """
    result = {s: None for s in symbols}
    if response_data.get("code") != 200:
        print("getDeltaForSymbols: response code != 200 — no data")
        return result

    options_chain = response_data.get("data", {}).get("optionsChain", [])
    wanted = set(symbols)
    for option in options_chain:
        sym = option.get("symbol")
        if sym in wanted:
            greeks = option.get("greeks")
            if greeks:
                result[sym] = {
                    "delta": greeks.get("delta"),
                    "gamma": greeks.get("gamma"),
                    "theta": greeks.get("theta"),
                    "vega": greeks.get("vega"),
                    "iv": greeks.get("iv")
                }
            else:
                print(f"getDeltaForSymbols: {sym} found but has no 'greeks' block")
    for sym in symbols:
        if sym not in {o.get("symbol") for o in options_chain}:
            print(f"getDeltaForSymbols: {sym} not found in optionsChain — check strikecount is "
                  f"wide enough to cover both the main and hedge strike")
    return result


def printDeltaComparison(strikecount, ticker, main_symbol, hedge_symbol, fyers):
    """
    Convenience one-shot: fetch the chain WITH greeks and print delta/gamma/theta/vega/iv for
    the main leg and hedge leg side by side, plus the delta-ratio they imply.

    This is a standalone diagnostic — it does NOT feed into calc_lots_by_risk or any live
    sizing. Intended for manual/local runs to sanity-check whether delta ratio (hedge_delta /
    main_delta) would have predicted the realized offset ratio better than the current
    candle-range ratio, using REALIZED_OFFSET log lines from exitSpreadPosition for comparison.
    """
    response = getOptionChainWithGreeks(strikecount, ticker, fyers)
    deltas = getDeltaForSymbols(response, [main_symbol, hedge_symbol])

    main_g = deltas.get(main_symbol)
    hedge_g = deltas.get(hedge_symbol)

    print(f"DELTA_CHECK: main={main_symbol} -> {main_g}")
    print(f"DELTA_CHECK: hedge={hedge_symbol} -> {hedge_g}")

    if main_g and hedge_g and main_g.get("delta") and hedge_g.get("delta"):
        main_delta = abs(main_g["delta"])
        hedge_delta = abs(hedge_g["delta"])
        if main_delta > 0:
            delta_ratio = round(hedge_delta / main_delta, 3)
            print(f"DELTA_CHECK: delta_ratio (hedge/main) = {delta_ratio}"
                  f"  (main_delta={main_delta}, hedge_delta={hedge_delta})")
        else:
            print("DELTA_CHECK: main_delta is 0 — cannot compute ratio")
    else:
        print("DELTA_CHECK: missing delta on one or both legs — cannot compute ratio")

    return deltas

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