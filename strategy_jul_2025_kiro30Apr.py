import json
import numpy as np
import time
import re
import math
from datetime import datetime, timedelta
from pytz import timezone
import ta  # Python TA Lib
import pandas as pd
import pandas_ta as pta  # Pandas TA Libv
import requests
import os
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from fyers_client import fyers
import helper_fyers as helper

entryHour = 9
entryMinute = 24
entrySecond = 0

entryHour1 = 9
entryMinute1 = 17  # 15
entrySecond1 = 0


def importLibrary():
    global fyers_broker
    global zerodha_broker
    global upstox_broker
    global helper
    global api_connect
    global breeze
    global alice
    global fyers
    global kc
    global api

    if fyers_broker == 1:
        from fyers_apiv3 import fyersModel
        import helper_fyers as helper

        app_id = open("fyers_client_id.txt", 'r').read()
        access_token = open("fyers_access_token.txt", 'r').read()
        fyers = fyersModel.FyersModel(token=access_token, is_async=False, client_id=app_id)

    if zerodha_broker == 1:
        from kiteconnect import KiteTicker
        from kiteconnect import KiteConnect
        import helper_zerodha as helper
        apiKey = open("zerodha_api_key.txt", 'r').read()
        accessToken = open("zerodha_access_token.txt", 'r').read()
        kc = KiteConnect(api_key=apiKey)
        kc.set_access_token(accessToken)


fyers_broker = 1
zerodha_broker = 0
# importLibrary()


# If you dont want to trade option, make below as 0
tradeOption = 1
stock = "BANKNIFTY"  # BANKNIFTY , NIFTY , FINNIFTY

if tradeOption == 1:
    checkInstrument = helper.getIndexSpot(stock);
    bnExpDate = helper.getBankNiftyExpiryDate();
else:
    checkInstrument = stock

print("checkInstrument = ", checkInstrument)
print("bnExpDate = ", bnExpDate)

st = 0
# st=-1

# 0 means no trde, but want to enter.
# 1 means buy trade.
# 2 means sell trade.
# -1 no trade and dont want to enter.

timeFrame = 3  # in minutes
# timeFrame = 1  # in minutes
timeFrame2 = 1  # in minutes

qty = 150 #  # 5 lots x 30 = 150
sl_point = 50  # 75 #180
target_point = 60  # 75 #180

# sl_point = 65 #75 #180
# target_point = 65 #75 #180

# capital = 46000
bullBar = False
bearBar = False

doNotTrade = True
capital = 700000
buyPremium = 500
hedgeBuyPremium = 15
qty2 = 30
otm = 100  # or try to fetch 150 to 190 rs premium
itm = 200
atmCE1 = ""
atmPE1 = ""
hedereturnOption = ""
# capital = 10000
vol = 10000
volPE = 10000
tradeCEoption = ""
tradePEoption = ""
papertrading = 1  # If paper trading is 0, then paper trading will be done. If paper trading is 1, then live trade

sl_perc = 9
target_perc = 6  # 15% target 12% sl # risk management on 5.5% squareoff 80% quantity remaining 20% quantity 12%
# for second trade keep 7% target and 10% sl

# order's details dataframe
tradesDF = pd.DataFrame(columns=["Date", "Symbol", "Direction", "Price", "Qty", "PaperTrading"])

x = 1
y = 1
close = []
opens = []
high = []
low = []
volume = []

candle_formed = 0
oneMinCandle_Formed = 0

slHit = 0
targetHit = 0
tradeCount = 0
sl = 0
target = 0
slCount = 0
targetCount = 0

# BNFut =   "NSE:BANKNIFTY25SEPFUT" # "NSE:BANKNIFTY25MAYFUT" #"NSE:BANKNIFTY25APRFUT" #"NSE:BANKNIFTY25MARFUT"   # "NSE:BANKNIFTY25FEBFUT"
# Dynamic BNFut — auto-generates futures symbol based on contract expiry
# BankNifty futures expire on last Thursday of the month
# Use current month contract till expiry day (inclusive), roll to next month only after expiry
def _get_last_tuesday(year, month):
    """Get last Tuesday of given month (BankNifty futures expiry day)."""
    import calendar
    last_day = calendar.monthrange(year, month)[1]
    d = datetime(year, month, last_day)
    while d.weekday() != 1:  # 1 = Tuesday
        d -= timedelta(days=1)
    return d

_now = datetime.now()
_expiry = _get_last_tuesday(_now.year, _now.month)
# Roll to next month only AFTER expiry day (not on expiry day itself)
if _now.date() > _expiry.date():
    _next = _now.replace(day=28) + timedelta(days=4)  # jump to next month
    _yy = _next.strftime("%y")
    _mmm = _next.strftime("%b").upper()
else:
    _yy = _now.strftime("%y")
    _mmm = _now.strftime("%b").upper()
BNFut = f"NSE:BANKNIFTY{_yy}{_mmm}FUT"
print("BNFut (auto) =", BNFut)


indiaVix = "NSE:INDIAVIX-INDEX"


# ============================================================
# IV-BASED DYNAMIC PARAMETERS
# ============================================================
def getIVRegime(fyers_client):
    """
    Fetch India VIX and compute dynamic parameters using the verified formula:
    BankNifty Expected Daily Range (1σ) = (VIX × BN Price) ÷ 700
    3-min candle range = daily range ÷ √130 (approx 130 three-min candles per day)
    ATM option move ≈ 0.45 × underlying move (delta)
    SL = 2.5 × ATM option move per candle + 15% buffer (survive 2-3 adverse candles)
    Target = same as SL (1:1 R:R)

    Sources:
    - TradingView VIX Range Calculator (BN multiplier = 700)
    - Macroption volatility conversion (√252 rule)
    """
    vix_ltp = helper.manualLTP(indiaVix, fyers_client)
    bn_name = helper.getIndexSpot(stock)
    bn_price = helper.manualLTP(bn_name, fyers_client)
    print("India VIX =", vix_ltp, " BN Price =", bn_price)

    # Step 1: Expected daily range (1σ, 68% probability)
    daily_range = (vix_ltp * bn_price) / 700.0

    # Step 2: Expected 3-min candle range
    # ~130 three-min candles in a trading day (6.25 hrs × 20 candles/hr)
    candle_range = daily_range / math.sqrt(130)

    # Step 3: ATM option move per candle (delta ≈ 0.45)
    atm_option_move = candle_range * 0.45

    # Step 4: SL = survive 2 adverse candles + 10% buffer for wicks
    # Using 2.0x (not 2.5x) because real-world avg candle is ~60% of 1σ theoretical,
    # so 2.0 × 1σ candle ≈ surviving ~3 real-world average adverse candles
    raw_sl = atm_option_move * 2.0
    sl_pts = round(raw_sl * 1.10)  # 10% buffer for wicks
    target_pts = sl_pts  # 1:1 R:R

    # Step 5: Spread width — roughly 2x the daily range / 4 (quarter of daily move)
    # In low vol keep tighter, in high vol keep wider
    spread_width = round(daily_range / 4.0 / 100) * 100  # round to nearest 100
    spread_width = max(200, min(spread_width, 800))  # clamp between 200-800

    # Step 6: Hedge premium divisor — in low vol go further OTM (cheaper hedge),
    # in high vol stay closer (more protection)
    if vix_ltp <= 13:
        regime = "LOW"
        hedge_premium_divisor = 3.0
        support_resistance_buffer = 20
    elif vix_ltp <= 17:
        regime = "NORMAL"
        hedge_premium_divisor = 2.5
        support_resistance_buffer = 30
    elif vix_ltp <= 22:
        regime = "ELEVATED"
        hedge_premium_divisor = 2.0
        support_resistance_buffer = 50
    else:
        regime = "HIGH"
        hedge_premium_divisor = 1.6
        support_resistance_buffer = 80

    params = {
        "vix": vix_ltp,
        "bn_price": bn_price,
        "regime": regime,
        "daily_range": round(daily_range, 1),
        "candle_range": round(candle_range, 1),
        "atm_option_move": round(atm_option_move, 1),
        "sl_point": sl_pts,
        "target_point": target_pts,
        "trail_trigger": round(atm_option_move),  # trail SL to breakeven after 1 candle move in favor
        "spread_width": spread_width,
        "hedge_premium_divisor": hedge_premium_divisor,
        "support_resistance_buffer": support_resistance_buffer
    }
    print("IV Regime =", regime,
          "| DailyRange =", round(daily_range, 1),
          "| 3minCandleRange =", round(candle_range, 1),
          "| ATMoptionMove =", round(atm_option_move, 1),
          "| SL =", sl_pts, "| Target =", target_pts,
          "| TrailTrigger =", round(atm_option_move),
          "| Spread =", spread_width,
          "| HedgeDiv =", hedge_premium_divisor)
    return params


# Global IV params — refreshed each 3-min candle
iv_params = {}


def getQtyByCapital(capital, entryPrice):
    quantity = int(capital / entryPrice)
    remainder = quantity % 15
    # Subtract the remainder from n to get the largest multiple of 15 less than or equal to n
    return quantity - remainder


def getChangeInOI(dfochain, index1, index2):
    oich = dfochain['oich'].to_numpy()
    chInOI = round(oich[index1])
    chInOI2 = round(oich[index2])
    option_type = dfochain['option_type'].to_numpy()

    option_type1 = option_type[index1]
    option_type2 = option_type[index2]

    pcr = round(oich[index1] / oich[index2], 2) if option_type[index1] != '' and option_type[index2] != '' and \
                                                   option_type[index1] == 'PE' else round(oich[index2] / oich[index1],
                                                                                          2)

    if option_type1 == 'CE':
        changOICE = str(pcr) + " CALL added " + str(chInOI) if chInOI > 0 else str(pcr) + " CALL unwind " + str(chInOI)
    elif option_type1 == 'PE':
        changOIPE = "   PUT added " + str(chInOI) if chInOI > 0 else "   PUT unwind " + str(chInOI)
    if option_type2 == 'CE':
        changOICE = str(pcr) + " CALL added " + str(chInOI2) if chInOI2 > 0 else str(pcr) + " CALL unwind " + str(
            chInOI2)
    elif option_type2 == 'PE':
        changOIPE = "   PUT added " + str(chInOI2) if chInOI2 > 0 else "   PUT unwind " + str(chInOI2)

    changOI = changOICE + changOIPE

    # if "CALL added" in changOI and "PUT unwind" in changOI :
    #     print("pcr show negative")
    #     return changOI
    # elif "PUT added" in changOI and "CALL unwind" in changOI :
    #     print("pcr show positive")
    #     # just flip sign
    #     match = re.search(r'-?\d+\.?\d*', changOI)  # Find first number (including negative)
    #     if match:
    #         num = float(match.group())  # Convert to float
    #         flipped_num = str(int(-num) if num.is_integer() else -num)  # Flip sign
    #         return changOI[:match.start()] + flipped_num + changOI[match.end():]  # Replace in string

    return changOI


def checkCriteriaAndTakeTrade():
    global st
    global tradeCEoption
    global tradePEoption
    global sl
    global target
    global slCount
    global targetCount
    name = helper.getIndexSpot(stock)
    # print(name)
    prev_diff = 10000
    closest_Strike = 10000
    now = datetime.now()

    intExpiry = helper.getBankNiftyExpiryDate()
    # print("Exp="+intExpiry)
    # dataFUT=getHistorical1(BNFut,timeFrame,3)
    dataFUT = helper.getHistorical(BNFut, timeFrame, 3, fyers)
    # print(dataFUT)
    opens = dataFUT['open'].to_numpy()
    high = dataFUT['high'].to_numpy()
    low = dataFUT['low'].to_numpy()
    close = dataFUT['close'].to_numpy()
    # identify open close to check wicks calculation

    isCurrCandlHaveWicks = False
    isPrevCandlHaveWicks = False

    if close[-2] - opens[-2] > 0:
        isCurrCandlHaveWicks = True if high[-2] - close[-2] > 0 and opens[-2] - low[-2] > 0 else False
    elif close[-2] - opens[-2] < 0:
        isCurrCandlHaveWicks = True if high[-2] - opens[-2] > 0 and close[-2] - low[-2] > 0 else False
    else:
        isCurrCandlHaveWicks = True if high[-2] - opens[-2] > 0 and close[-2] - low[-2] > 0 else False
        print("for currcandle open close same")

    isCurrDoji = True if abs(opens[-2] - close[-2]) <= 3 and isCurrCandlHaveWicks else False

    if close[-3] - opens[-3] > 0:
        isPrevCandlHaveWicks = True if high[-3] - close[-3] > 0 and opens[-3] - low[-3] > 0 else False
    elif close[-3] - opens[-3] < 0:
        isPrevCandlHaveWicks = True if high[-3] - opens[-3] > 0 and close[-3] - low[-3] > 0 else False
    else:
        isPrevCandlHaveWicks = True if high[-3] - close[-3] > 0 and opens[-3] - low[-3] > 0 else False
        print("for prevcandle open close same")

    isPrevDoji = True if abs(opens[-3] - close[-3]) <= 3 and isPrevCandlHaveWicks else False

    # print("abs1 = ",round(abs(opens[-2] - close[-2]),2), "now =",now )
    # print("abs2 = ",round(abs(opens[-3] - close[-3]),2), " now=",now)
    # print("isCurrDoji =",isCurrDoji," isPrevDoji=",isPrevDoji)
    # print("opens[-2] = ",opens[-2], " close[-2] =",close[-2])
    # print("opens[-3] = ",opens[-3], " close[-3] =",close[-3])
    closest_Strike22 = int(round((opens[-2] / 100), 0) * 100)
    # print("closest_Strike=",closest_Strike22)
    currCandleStrike = math.floor(opens[-2] / 100) * 100
    prevCandleStrike = math.floor(opens[-3] / 100) * 100
    # print("currCandleStrike ==",currCandleStrike, " prevCandleStrike =",prevCandleStrike)

    currCEStrike = helper.getOptionFormat(stock, intExpiry, currCandleStrike, "CE")
    currPEStrike = helper.getOptionFormat(stock, intExpiry, currCandleStrike, "PE")

    prevCEStrike = helper.getOptionFormat(stock, intExpiry, prevCandleStrike, "CE")
    prevPEStrike = helper.getOptionFormat(stock, intExpiry, prevCandleStrike, "PE")

    currOptiondataCE = helper.getHistorical(currCEStrike, timeFrame, 3, fyers)
    currOptiondataPE = helper.getHistorical(currPEStrike, timeFrame, 3, fyers)
    prevOptiondataCE = helper.getHistorical(prevCEStrike, timeFrame, 3, fyers)
    prevOptiondataPE = helper.getHistorical(prevPEStrike, timeFrame, 3, fyers)

    print("curr Doji", isCurrDoji)
    # if isCurrDoji != True and isPrevDoji != True :
    # if isCurrDoji != True :
    currVolumeCE = currOptiondataCE['volume'].to_numpy()
    currVolumePE = currOptiondataPE['volume'].to_numpy()
    # print("currVolumePE",currVolumePE[-2], " currVolumeCE",currVolumeCE[-2])

    prevVolumeCE = prevOptiondataCE['volume'].to_numpy()
    prevVolumePE = prevOptiondataPE['volume'].to_numpy()
    # print("prevVolumePE",prevVolumePE[-3], " prevVolumeCE",prevVolumeCE[-3])

    currPCR = round(currVolumePE[-2] / currVolumeCE[-2], 2)
    prevPCR = round(prevVolumePE[-3] / prevVolumeCE[-3], 2)

    currCPR = round(currVolumeCE[-2] / currVolumePE[-2], 2)
    prevCPR = round(prevVolumeCE[-3] / prevVolumePE[-3], 2)

    print(currPEStrike, " curr candle vol pcr =", currPCR, " PE=", round(currVolumePE[-2], 2), " CE=",
          round(currVolumeCE[-2], 2), "    ", "cpr = ", currCPR)
    print(prevPEStrike, " prev candle vol pcr =", prevPCR, " PE=", round(prevVolumePE[-3], 2), " CE=",
          round(prevVolumeCE[-3], 2), "    ", "cpr = ", prevCPR)

    # --- Buying opportunity analysis logs ---
    # ATM option premiums (CE and PE) for tracking premium movement per candle
    currCEclose = currOptiondataCE['close'].to_numpy()
    currPEclose = currOptiondataPE['close'].to_numpy()
    print("ATM_CE_premium=", round(currCEclose[-2], 2), " ATM_PE_premium=", round(currPEclose[-2], 2))

    # Candle body size (open-close) and direction for momentum detection
    candleBody = round(close[-2] - opens[-2], 1)
    prevCandleBody = round(close[-3] - opens[-3], 1)
    candleRange = round(high[-2] - low[-2], 1)
    print("CandleBody=", candleBody, " PrevBody=", prevCandleBody,
          " CandleRange=", candleRange,
          " Bullish" if candleBody > 0 else " Bearish" if candleBody < 0 else " Doji")

    # Volume spike: current candle CE+PE volume vs previous candle
    currTotalVol = currVolumeCE[-2] + currVolumePE[-2]
    prevTotalVol = prevVolumeCE[-3] + prevVolumePE[-3]
    volSpike = round(currTotalVol / prevTotalVol, 2) if prevTotalVol > 0 else 0
    print("TotalVol=", round(currTotalVol), " PrevTotalVol=", round(prevTotalVol),
          " VolSpike=", volSpike, "x", " SPIKE!" if volSpike >= 2.0 else "")

    sname = "NSE:NIFTYBANK-INDEX"
    strikecount = 3
    ochainresponse = helper.getOptionChain(strikecount, sname, fyers)
    ochain = helper.getClosestOptions(ochainresponse)
    dfochain = pd.DataFrame(ochain)
    # print(dfochain)
    symbol = dfochain['symbol'].to_numpy()
    option_type = dfochain['option_type'].to_numpy()
    oi = dfochain['oi'].to_numpy()
    # print(oi)
    # synFutAtmStrike = helper.getSyntheticFUTStrike(stock,fyers)
    # print('=syntheicFUT=',synFutAtmStrike)
    oipcr1 = round(oi[-1] / oi[-2], 2) if option_type[-1] != '' and option_type[-2] != '' and option_type[
        -1] == 'PE' else round(oi[-2] / oi[-1], 2)
    oipcr2 = round(oi[-3] / oi[-4], 2) if option_type[-3] != '' and option_type[-4] != '' and option_type[
        -3] == 'PE' else round(oi[-4] / oi[-3], 2)
    oipcr3 = round(oi[-5] / oi[-6], 2) if option_type[-5] != '' and option_type[-6] != '' and option_type[
        -5] == 'PE' else round(oi[-6] / oi[-5], 2)

    # print("atmCE ==",atmCE1, "atmPE ==",atmPE1)
    print("======================================")
    # print("Synthetic Future (ATM)")
    print("oipcr1 = ", symbol[-2], " ", oipcr1)
    print("oipcr2 = ", symbol[-4], " ", oipcr2)
    print("oipcr3 = ", symbol[-6], " ", oipcr3)
    count = 0
    if oipcr1 >= 1:
        count += 1
    if oipcr2 >= 1:
        count += 1
    if oipcr3 >= 1:
        count += 1

    bearCount = 0
    if oipcr1 < 1:
        bearCount += 1
    if oipcr2 < 1:
        bearCount += 1
    if oipcr3 < 1:
        bearCount += 1

    oipcrBull = "NoEntry"
    oipcrBear = "NoEntry"
    if count > 1:
        oipcrBull = "BullTrade"
    elif bearCount > 1:
        oipcrBear = "BearTrade"

    print("oipcrBull =", oipcrBull, " bulcount=", count, " bearcount=", bearCount, "oipcrBear=", oipcrBear)

    # ochainresponse = helper.getOptionChain(3,sname ,fyers)
    # ochain = helper.getClosestOptions(ochainresponse)
    # dfochain = pd.DataFrame(ochain)
    #
    # symbol = dfochain['symbol'].to_numpy()
    # option_type = dfochain['option_type'].to_numpy()
    # oi = dfochain['oi'].to_numpy()
    #
    volume = dfochain['volume'].to_numpy()
    pcr3 = round(oi[-5] / oi[-6], 2) if option_type[-5] != '' and option_type[-6] != '' and option_type[
        -5] == 'PE' else round(oi[-6] / oi[-5], 2)
    pcr4 = round(oi[-7] / oi[-8], 2) if option_type[-7] != '' and option_type[-8] != '' and option_type[
        -7] == 'PE' else round(oi[-8] / oi[-7], 2)
    pcr5 = round(oi[-9] / oi[-10], 2) if option_type[-9] != '' and option_type[-10] != '' and option_type[
        -9] == 'PE' else round(oi[-10] / oi[-9], 2)

    print("atmpcr5 pcr6=", pcr4, "  ", pcr5)

    if currPCR >= 1 and prevPCR >= 1 and oipcrBull == "BullTrade" and doNotTrade == False:
        # st = 1
        return 0
    elif currPCR < 1 and oipcrBear == "BearTrade" and doNotTrade == False and pcr4 < 1 and pcr5 < 1:
        # st = 1
        return 0
    else:
        print("No Entry Yet")
    # else:
    #     print("Doji formed No Entry Yet")


def sum_around_key(my_map, substring, window=4):
    keys = list(my_map.keys())  # preserve insertion order
    values = list(my_map.values())

    # find index of the key containing substring
    index = next((i for i, k in enumerate(keys) if substring in k), None)
    if index is None:
        return None  # substring not found

    # calculate start and end (strictly 4 before + 1 self + 4 after)
    start = max(0, index - window)
    end = min(len(values), index + window + 1)

    total = sum(values[start:end])
    return keys[index], total


def sum_with_neighbors(data_map, search_substr):
    # Convert map (dict) to ordered list of (key, value)
    items = list(data_map.items())

    # Find the index of the key containing the search substring
    match_index = None
    for i, (k, v) in enumerate(items):
        if search_substr in k:  # substring match
            match_index = i
            break

    if match_index is None:
        # Nothing found
        return None

    # Collect 4 before, self, and 4 after
    start = max(0, match_index - 4)
    end = min(len(items), match_index + 5)  # +5 because range end is exclusive

    selected = items[start:end]
    total_sum = sum(v for k, v in selected)
    included_keys = [k for k, v in selected]

    return total_sum, included_keys


def is_difference_greater_than_25(val1, val2):
    larger = max(val1, val2)
    if larger == 0:  # avoid division by zero
        return False
    difference = abs(val1 - val2)
    percent_diff = (difference / larger) * 100
    return percent_diff > 25


def takeEntry(isBullish, isBearish, qty, fyers, papertrading):
    global hedgeOrderId
    global tradeATMOption
    global tradeHedgeOption
    global mainOrderId
    global iv_params

    # Use dynamic IV-based parameters
    dynamic_sl = iv_params.get("sl_point", sl_point)
    dynamic_target = iv_params.get("target_point", target_point)
    dynamic_spread = iv_params.get("spread_width", 300)
    dynamic_hedge_div = iv_params.get("hedge_premium_divisor", 1.25)

    print("qty ===", qty)
    print("IV-Dynamic: SL=", dynamic_sl, " Target=", dynamic_target,
          " Spread=", dynamic_spread, " HedgeDiv=", dynamic_hedge_div)

    name = helper.getIndexSpot(stock)
    ltp = helper.manualLTP(name, fyers)
    if stock == 'BANKNIFTY':
        intExpiry = helper.getBankNiftyExpiryDate()
        closest_Strike = int(round((ltp / 100), 0) * 100)
    elif stock == 'NIFTY':
        intExpiry = helper.getNiftyExpiryDate()
        closest_Strike = int(round((ltp / 50), 0) * 50)
    print('closest_Strike = ', closest_Strike)

    atmCE = helper.getOptionFormat(stock, intExpiry, closest_Strike, "CE")
    atmPE = helper.getOptionFormat(stock, intExpiry, closest_Strike, "PE")
    atmCEPremium = helper.manualLTP(atmCE, fyers)
    atmPEPremium = helper.manualLTP(atmPE, fyers)
    syntheticATMStrike = ltp + atmCEPremium - atmPEPremium
    print('syntheticATMStrike = ', syntheticATMStrike)
    syntheticATMStrike = int(round((syntheticATMStrike / 100), 0) * 100)

    # Dynamic spread width based on IV regime
    Hedge_Strike_CE_OTMBuy = syntheticATMStrike + dynamic_spread
    Hedge_Strike_PE_OTMBuy = syntheticATMStrike - dynamic_spread
    closest_Strike_CE = syntheticATMStrike
    closest_Strike_PE = syntheticATMStrike
    atmCE = helper.getOptionFormat(stock, intExpiry, closest_Strike_CE, "CE")
    atmPE = helper.getOptionFormat(stock, intExpiry, closest_Strike_PE, "PE")

    otmCE = helper.getOptionFormat(stock, intExpiry, Hedge_Strike_CE_OTMBuy, "CE")
    otmPE = helper.getOptionFormat(stock, intExpiry, Hedge_Strike_PE_OTMBuy, "PE")

    if isBullish:
        entryPrice = helper.manualLTP(atmPE, fyers)
        # Use the OTM PE already calculated from dynamic spread width (no premium-based override)
        print("=atmPE=", atmPE)
        print("=otmPE==", otmPE, " spread=", dynamic_spread, " entryPrice=", entryPrice)

        hedge_entry_price = helper.manualLTP(otmPE, fyers)
        print("hedge_entry_price =", hedge_entry_price)

        hedgeOrderId = helper.placeTargetOrder(otmPE, "BUY", qty, "MARKET", hedge_entry_price, 0, 0, fyers,
                                               papertrading)
        tradeHedgeOption = otmPE
        time.sleep(0.5)

        # Dynamic SL/target based on IV
        ceTarget = round(entryPrice - dynamic_target)
        ceSL = round(entryPrice + dynamic_sl)
        print("entryPrice==", entryPrice, "Target =", ceTarget, "", atmPE, " SL =", ceSL)
        mainOrderId = helper.placeTargetOrder(atmPE, "SELL", qty, "MARKET", entryPrice, ceSL, ceTarget, fyers,
                                              papertrading)
        tradeATMOption = atmPE
        print("atmPE=", atmPE, " ", tradeATMOption)
        print("Exit OID: ", mainOrderId, " ", hedgeOrderId)
    if isBearish:
        entryPrice = helper.manualLTP(atmCE, fyers)
        # Use the OTM CE already calculated from dynamic spread width (no premium-based override)
        print("=atmCE=", atmCE)
        print("=otmCE==", otmCE, " spread=", dynamic_spread, " entryPrice=", entryPrice)

        hedge_entry_price = helper.manualLTP(otmCE, fyers)
        print("hedge_entry_price =", hedge_entry_price)
        hedgeOrderId = helper.placeTargetOrder(otmCE, "BUY", qty, "MARKET", hedge_entry_price, 0, 0, fyers,
                                               papertrading)
        tradeHedgeOption = otmCE
        time.sleep(0.5)

        # Dynamic SL/target based on IV
        ceTarget = round(entryPrice - dynamic_target)
        ceSL = round(entryPrice + dynamic_sl)
        print("entryPrice==", entryPrice, "Target =", ceTarget, "", atmCE, " SL =", ceSL)
        mainOrderId = helper.placeTargetOrder(atmCE, "SELL", qty, "MARKET", entryPrice, ceSL, ceTarget, fyers,
                                              papertrading)
        tradeATMOption = atmCE
        print("Exit OID: ", mainOrderId, " ", hedgeOrderId)

    return mainOrderId


def exitPosition(tradeOption):
    # Sell existing option
    oidentry = helper.placeOrder(tradeOption, "SELL", qty, "MARKET", 0, "regular", fyers, papertrading)
    print("Exit OID: ", oidentry)
    return oidentry


def exitSpreadPosition(mainATMOption, hedgeOption):
    # exit existing option
    mainOidentry = helper.placeOrder(mainATMOption, "BUY", qty, "MARKET", 0, "regular", fyers, papertrading)
    print("Exit main OID: ", mainOidentry)
    time.sleep(0.5)
    oidentry = helper.placeOrder(hedgeOption, "SELL", qty, "MARKET", 0, "regular", fyers, papertrading)
    print("Exit hedge OID: ", oidentry)
    time.sleep(0.5)
    return mainOidentry


def findStrikePricePremium(optionName, premium, premiumType):
    name = helper.getIndexSpot(stock)
    closest_Strike_PE = ''
    closest_Strike_CE = ''
    strikeList = []
    prev_diff = 10000
    closest_Strike = 10000

    intExpiry = helper.getBankNiftyExpiryDate()

    ######################################################
    # FINDING ATM
    # ltp =  helper.getLTP(name)
    ltp = helper.manualLTP(name, fyers)
    print("intExpiry==", intExpiry)
    print("closestPrimium fun LTP", ltp)
    if optionName == "CE" and premiumType == "":
        start = -8
        end = 4
    elif optionName == "CE" and premiumType != "":
        start = 1
        end = 14
    if optionName == "PE" and premiumType == "":
        start = -4
        end = 8
    elif optionName == "PE" and premiumType != "":
        start = -14
        end = -1

    for i in range(start, end):
        strike = (int(ltp / 100) + i) * 100
        strikeList.append(strike)
    print(strikeList)

    if optionName == "CE":
        # FOR CE
        prev_diff = 10000
        for strike in strikeList:
            ceOptionFormat = helper.getOptionFormat(stock, intExpiry, strike, "CE")
            ltp_option = helper.manualLTP(ceOptionFormat, fyers)
            # print(ltp_option)
            diff = abs(ltp_option - premium)
            # print("diff==>", diff)
            if (diff < prev_diff):
                closest_Strike_CE = strike
                prev_diff = diff
    if optionName == "PE":
        # FOR PE
        prev_diff = 10000
        for strike in strikeList:
            peOptionFormat = helper.getOptionFormat(stock, intExpiry, strike, "PE")
            ltp_option = helper.manualLTP(peOptionFormat, fyers)
            diff = abs(ltp_option - premium)
            # print("diff==>", diff)
            if (diff < prev_diff):
                closest_Strike_PE = strike
                prev_diff = diff

    print("closest CE", closest_Strike_CE)
    print("closest PE", closest_Strike_PE)

    atmCE = helper.getOptionFormat(stock, intExpiry, closest_Strike_CE, "CE")
    atmPE = helper.getOptionFormat(stock, intExpiry, closest_Strike_PE, "PE")

    print(atmCE)
    print(atmPE)

    # ltp = helper.manualLTP(atmCE,fyers)
    # print(ltp)
    # ltp = helper.manualLTP(atmPE,fyers)
    # print(ltp)
    if optionName == "CE":
        # hedereturnOption = atmCE
        return atmCE
    elif optionName == "PE":
        return atmPE
        # hedereturnOption = atmPE
    # return hedereturnOption


def get_support_resistance(futltp, step=500, buffer=None):
    """
    Calculate support/resistance with NOTRADEZONE based on FUT_LTP.
    Buffer is now dynamic based on IV regime if iv_params is available.

    FUT_LTP: current future price
    step: gap between support/resistance levels (default = 500 for BankNifty)
    buffer: half-width of no-trade zone — auto-set from IV regime if None
    """
    # Use IV-based buffer if available, otherwise default 30
    if buffer is None:
        buffer = iv_params.get("support_resistance_buffer", 30) if iv_params else 30

    # Nearest support (lower multiple of step)
    support = (futltp // step) * step
    resistance = support + step

    # Middle & no-trade zone range
    middle = (support + resistance) / 2
    no_trade_low = middle - buffer
    no_trade_high = middle + buffer

    # Decision logic
    if futltp <= no_trade_low:
        return support
    elif futltp >= no_trade_high:
        return resistance
    else:
        # return 9999999999
        return "NOTRADEZONE"


####################__INPUT__#####################


avgOiPcrMap = {}
avgOiPcr = {}
SUPP_RES_STRIKE = ''
AVGOI_PCR = 0
TOTAL_PCR = 0
SYNTH_FUT_STRIKE = ''
FUT_LTP = 0
IS_STRIKE_SHIFT = False
IS_CONSECUTIVELY_2TIMES_PCR_INCREASED = False
IS_CONSECUTIVELY_2TIMES_PCR_DECREASED = False

IS_CONSECUTIVELY_2TIMES_PCR_INCREASED2 = False
IS_CONSECUTIVELY_2TIMES_PCR_DECREASED2 = False

mapFutStrike = {}
avgOiPcrList = []
count = 0
notStrikeShiftCount = 1
IS_CHOI_DIFF_GT_25PERC = False
RSI_VAL = 0
suppResCeChOi = 0
suppResPeChOi = 0

mapStrike = {}
ATM_STRIKE = 0
IS_ATM_STRIKE_SHIFT = False
atmStrikeNotShiftedCount = 1
spotLTP = 0  # banknifty index ltp
avgOiPcrList2 = []

isBullTrade = False
isBearTrade = False

hedgeOrderId = ''
mainOrderId = ''
tradeHedgeOption = ''
tradeATMOption = ''
entryPremium = 0  # track entry premium for trailing SL
slTrailed = False  # whether SL has been trailed to breakeven
slConfirmCount = 0  # count consecutive 1-min candle closes above SL (need 2 to trigger)

while x == 1:

    dt1 = datetime.now()

    now = datetime.now()
    custom_time = datetime(now.year, now.month, now.day, entryHour, entryMinute)
    custom_time1 = datetime(now.year, now.month, now.day, entryHour1, entryMinute1)

    # if dt1.hour >= 9 and dt1.minute >= 34:   ##and dt1.minute >= 34 change this instead of min convert to == bcoz if i run file at 12:7 pm it waits next 34 mins
    # if dt1.hour >= 0:
    if now >= custom_time1:

        if dt1.second <= 1 and dt1.minute % timeFrame == 0:
            count += 1
            # if dt1.second % timeFrame == 0:

            # print("formed == ",dt1.second, "  ",dt1.minute % timeFrame)
            candle_formed = 1
            optionInstum = BNFut

            # Refresh IV regime parameters every 3-min candle
            iv_params = getIVRegime(fyers)
            print("IV Params refreshed:", iv_params)

            # sname = helper.getIndexSpot(stock)
            sname = "NSE:NIFTYBANK-INDEX"
            # sname = BNFut
            # strikecount = 6
            # strikecount = 7
            strikecount = 8
            pcrList = []
            volPcrList = []
            choipcrList = []
            symbolList = []
            ochainresponse = helper.getOptionChain(strikecount, sname, fyers)
            print('===')
            # print(ochainresponse)
            totalOI = helper.getTotalOI(ochainresponse)

            calloi = totalOI.get('callOi')
            putoi = totalOI.get('putOi')
            totalOIPCR = round((putoi / calloi), 2)

            ochain = helper.getClosestOptions1(ochainresponse)
            # print(ochain)
            dfochain = pd.DataFrame(ochain)
            print("====after 3 min ochain====", now)

            symbol = dfochain['symbol'].to_numpy()
            option_type = dfochain['option_type'].to_numpy()
            oi = dfochain['oi'].to_numpy()
            volume = dfochain['volume'].to_numpy()

            chInOi = dfochain['oich'].to_numpy()
            # percentageChangeInOi = dfochain['oichp'].to_numpy()
            # prevOI = dfochain['prev_oi'].to_numpy()

            name = helper.getIndexSpot(stock)
            closest_Strike = 10000

            intExpiry = helper.getBankNiftyExpiryDate()

            # ltp = helper.manualLTP(name, fyers)
            # closest_Strike = int(round((ltp / 100), 0) * 100)
            #
            # closest_Strike_CE = closest_Strike
            # closest_Strike_PE = closest_Strike
            #
            # atmCE = helper.getOptionFormat(stock, intExpiry, closest_Strike_CE, "CE")
            # atmPE = helper.getOptionFormat(stock, intExpiry, closest_Strike_PE, "PE")

            # voule in tradding each and every day its new , OI is carry forwarded so its positional
            # so change in OI we need to check or consider
            pcr1 = round(oi[-1] / oi[-2], 2) if option_type[-1] != '' and option_type[-2] != '' and option_type[
                -1] == 'PE' else round(oi[-2] / oi[-1], 2)

            # changOI = 'PUT added ',chInOi[-1] if option_type[-1] == 'PE' and chInOi[-1] > 0 else 'PUT unwind ',chInOi[-1]

            # s = getChangeInOI (dfochain)
            changOI = getChangeInOI(dfochain, -1, -2)

            pcr2 = round(oi[-3] / oi[-4], 2) if option_type[-3] != '' and option_type[-4] != '' and option_type[
                -3] == 'PE' else round(oi[-4] / oi[-3], 2)
            changOIpcr2 = getChangeInOI(dfochain, -3, -4)
            pcr3 = round(oi[-5] / oi[-6], 2) if option_type[-5] != '' and option_type[-6] != '' and option_type[
                -5] == 'PE' else round(oi[-6] / oi[-5], 2)
            changOIpcr3 = getChangeInOI(dfochain, -5, -6)
            volpcr1 = round(volume[-1] / volume[-2], 2) if option_type[-1] != '' and option_type[-2] != '' and \
                                                           option_type[-1] == 'PE' else round(volume[-2] / volume[-1],
                                                                                              2)
            volpcr2 = round(volume[-3] / volume[-4], 2) if option_type[-3] != '' and option_type[-4] != '' and \
                                                           option_type[-3] == 'PE' else round(volume[-4] / volume[-3],
                                                                                              2)
            volpcr3 = round(volume[-5] / volume[-6], 2) if option_type[-5] != '' and option_type[-6] != '' and \
                                                           option_type[-5] == 'PE' else round(volume[-6] / volume[-5],
                                                                                              2)

            if strikecount >= 2:
                pcr4 = round(oi[-7] / oi[-8], 2) if option_type[-7] != '' and option_type[-8] != '' and option_type[
                    -7] == 'PE' else round(oi[-8] / oi[-7], 2)
                changOIpcr4 = getChangeInOI(dfochain, -7, -8)
                pcr5 = round(oi[-9] / oi[-10], 2) if option_type[-9] != '' and option_type[-10] != '' and option_type[
                    -9] == 'PE' else round(oi[-10] / oi[-9], 2)
                changOIpcr5 = getChangeInOI(dfochain, -9, -10)
                pcr6 = round(oi[-11] / oi[-12], 2) if option_type[-11] != '' and option_type[-12] != '' and option_type[
                    -11] == 'PE' else round(oi[-12] / oi[-11], 2)
                changOIpcr6 = getChangeInOI(dfochain, -11, -12)
                pcr7 = round(oi[-13] / oi[-14], 2) if option_type[-13] != '' and option_type[-14] != '' and option_type[
                    -13] == 'PE' else round(oi[-14] / oi[-13], 2)
                changOIpcr7 = getChangeInOI(dfochain, -13, -14)
                pcr8 = round(oi[-15] / oi[-16], 2) if option_type[-15] != '' and option_type[-16] != '' and option_type[
                    -15] == 'PE' else round(oi[-16] / oi[-15], 2)
                changOIpcr8 = getChangeInOI(dfochain, -15, -16)
                pcr9 = round(oi[-17] / oi[-18], 2) if option_type[-17] != '' and option_type[-18] != '' and option_type[
                    -17] == 'PE' else round(oi[-18] / oi[-17], 2)
                changOIpcr9 = getChangeInOI(dfochain, -17, -18)
                pcr10 = round(oi[-19] / oi[-20], 2) if option_type[-19] != '' and option_type[-20] != '' and \
                                                       option_type[-19] == 'PE' else round(oi[-20] / oi[-19], 2)
                changOIpcr10 = getChangeInOI(dfochain, -19, -20)
                pcr11 = round(oi[-21] / oi[-22], 2) if option_type[-21] != '' and option_type[-22] != '' and \
                                                       option_type[-21] == 'PE' else round(oi[-22] / oi[-21], 2)
                changOIpcr11 = getChangeInOI(dfochain, -21, -22)
                pcr12 = round(oi[-23] / oi[-24], 2) if option_type[-23] != '' and option_type[-24] != '' and \
                                                       option_type[-23] == 'PE' else round(oi[-24] / oi[-23], 2)
                changOIpcr12 = getChangeInOI(dfochain, -23, -24)
                pcr13 = round(oi[-25] / oi[-26], 2) if option_type[-25] != '' and option_type[-26] != '' and \
                                                       option_type[-25] == 'PE' else round(oi[-26] / oi[-25], 2)
                changOIpcr13 = getChangeInOI(dfochain, -25, -26)

                pcr14 = round(oi[-27] / oi[-28], 2) if option_type[-27] != '' and option_type[-28] != '' and \
                                                       option_type[-27] == 'PE' else round(oi[-28] / oi[-27], 2)
                changOIpcr14 = getChangeInOI(dfochain, -27, -28)
                pcr15 = round(oi[-29] / oi[-30], 2) if option_type[-29] != '' and option_type[-30] != '' and \
                                                       option_type[-29] == 'PE' else round(oi[-30] / oi[-29], 2)
                changOIpcr15 = getChangeInOI(dfochain, -29, -30)
                pcr16 = round(oi[-31] / oi[-32], 2) if option_type[-31] != '' and option_type[-32] != '' and \
                                                       option_type[-31] == 'PE' else round(oi[-32] / oi[-31], 2)
                changOIpcr16 = getChangeInOI(dfochain, -31, -32)
                pcr17 = round(oi[-33] / oi[-34], 2) if option_type[-33] != '' and option_type[-34] != '' and \
                                                       option_type[-33] == 'PE' else round(oi[-34] / oi[-33], 2)
                changOIpcr17 = getChangeInOI(dfochain, -33, -34)

                # pcr10 = round(oi[-17]/oi[-18],2) if option_type[-17] !='' and option_type[-18] !='' and option_type[-17] == 'PE' else round(oi[-18]/oi[-17],2)
                # changOIpcr10 = getChangeInOI(dfochain,-17,-18)

                volpcr4 = round(volume[-7] / volume[-8], 2) if option_type[-7] != '' and option_type[-8] != '' and \
                                                               option_type[-7] == 'PE' else round(
                    volume[-8] / volume[-7], 2)
                volpcr5 = round(volume[-9] / volume[-10], 2) if option_type[-9] != '' and option_type[-10] != '' and \
                                                                option_type[-9] == 'PE' else round(
                    volume[-10] / volume[-9], 2)
                volpcr6 = round(volume[-11] / volume[-12], 2) if option_type[-11] != '' and option_type[-12] != '' and \
                                                                 option_type[-11] == 'PE' else round(
                    volume[-12] / volume[-11], 2)
                volpcr7 = round(volume[-13] / volume[-14], 2) if option_type[-13] != '' and option_type[-14] != '' and \
                                                                 option_type[-13] == 'PE' else round(
                    volume[-14] / volume[-13], 2)
                volpcr8 = round(volume[-15] / volume[-16], 2) if option_type[-15] != '' and option_type[-16] != '' and \
                                                                 option_type[-15] == 'PE' else round(
                    volume[-16] / volume[-15], 2)
                volpcr9 = round(volume[-17] / volume[-18], 2) if option_type[-17] != '' and option_type[-18] != '' and \
                                                                 option_type[-17] == 'PE' else round(
                    volume[-18] / volume[-17], 2)

            # pcrList = [pcr1,pcr2,pcr3,pcr4,pcr5,pcr6,pcr7,pcr8,pcr9]
            pcrList = [pcr1, pcr2, pcr3, pcr4, pcr5, pcr6, pcr7, pcr8, pcr9, pcr10, pcr11, pcr12, pcr13, pcr14, pcr15,
                       pcr16, pcr17]
            # choipcrList = [pcr1,pcr2,pcr3,pcr4,pcr5,pcr6,pcr7,pcr8,pcr9]

            volPcrList = [volpcr1, volpcr2, volpcr3, volpcr4, volpcr5, volpcr6, volpcr7, volpcr8, volpcr9]

            print("".ljust(35), "oipcr", " ", "choipcr")
            print("pcr1 = ", symbol[-2], " ", pcr1, " ", changOI)
            print("pcr2 = ", symbol[-4], " ", pcr2, " ", changOIpcr2)
            print("pcr3 = ", symbol[-6], " ", pcr3, " ", changOIpcr3)
            if strikecount >= 2:
                print("pcr4 = ", symbol[-8], " ", pcr4, " ", changOIpcr4)
                print("pcr5 = ", symbol[-10], " ", pcr5, " ", changOIpcr5)
                print("pcr6 = ", symbol[-12], " ", pcr6, "  ", changOIpcr6)
                print("pcr7 = ", symbol[-14], " ", pcr7, " ", changOIpcr7)
                print("pcr8 = ", symbol[-16], " ", pcr8, " ", changOIpcr8)
                print("pcr9 = ", symbol[-18], " ", pcr9, " ", changOIpcr9)
                print("pcr10 = ", symbol[-20], " ", pcr10, " ", changOIpcr10)
                print("pcr11 = ", symbol[-22], " ", pcr11, " ", changOIpcr11)
                print("pcr12 = ", symbol[-24], " ", pcr12, " ", changOIpcr12)
                print("pcr13 = ", symbol[-26], " ", pcr13, " ", changOIpcr13)

                print("pcr14 = ", symbol[-28], " ", pcr14, " ", changOIpcr14)
                print("pcr15 = ", symbol[-30], " ", pcr15, " ", changOIpcr15)
                print("pcr16 = ", symbol[-32], " ", pcr16, " ", changOIpcr16)
                print("pcr17 = ", symbol[-34], " ", pcr17, " ", changOIpcr17)

                # symbolList = [symbol[-2],symbol[-4],symbol[-2],symbol[-2]]
                symbolPcrMap = {
                    symbol[-2]: pcr1, symbol[-4]: pcr2, symbol[-6]: pcr3, symbol[-8]: pcr4,
                    symbol[-10]: pcr5, symbol[-12]: pcr6, symbol[-14]: pcr7, symbol[-16]: pcr8,
                    symbol[-18]: pcr9, symbol[-20]: pcr10, symbol[-22]: pcr11, symbol[-24]: pcr12,
                    symbol[-26]: pcr13, symbol[-28]: pcr14, symbol[-30]: pcr15, symbol[-32]: pcr16,
                    symbol[-34]: pcr17
                }

                FUT_LTP = helper.manualLTP(BNFut, fyers)
                # FUT_LTP = ltp
                SYNTH_FUT_STRIKE = round(FUT_LTP / 100) * 100

                # SYNTH_FUT_STRIKE = helper.getSyntheticFUTStrike(stock,fyers)

                # keySynthAtmFutSymbol = helper.getOptionFormat(stock, intExpiry, SYNTH_FUT_STRIKE, "CE")
                # print("=keySynthAtmFutSymbol==",keySynthAtmFutSymbol)
                # print("=symbolPcrMap=")
                # print(symbolPcrMap)
                # sum_with_neighbors gives pcrSummation of 9 strikes oi ,from syntheicFutATM strike ->4 above strike
                # and 4 below strike and synthecfutATM strike
                pcrSummation, result = sum_with_neighbors(symbolPcrMap, str(SYNTH_FUT_STRIKE))
                print("pcrSummation=", pcrSummation, " result = ", result)

                pcrSum = round(sum(pcrList), 2)
                print("PCRSUM==", pcrSum)
                avgoiPCROld = round(pcrSum / 17, 2)

                pcrSummation = round(pcrSummation, 2)
                print("pcrSummation==", pcrSummation)
                avgoiPCR = round(pcrSummation / 9, 2)

                volpcrsum = sum(volPcrList)
                print("VOLPCRSUM==", volpcrsum)
                avgvolPCR = round(volpcrsum / 9, 2)

                avgOiPcrList.append(avgoiPCR)
                avgOiPcrList2.append(avgoiPCROld)
            # if count == 2:
            #     SYNTH_FUT_STRIKE = 55400
            #     print("",mapFutStrike," count==",count)
            #

            # Below code to prepare map and identify synthFutATM strike shifted or not in 3 min candle time frame
            # if it is shfted up/down IS_STRIKE_SHIFT becomes true ,if not IS_STRIKE_SHIFT remains false and
            # maintain the count also of keeping at same level notStrikeShiftCount

            bankNiftyIndex = helper.getIndexSpot(stock)
            spotLTP = helper.manualLTP(bankNiftyIndex, fyers)
            ATM_STRIKE = round(spotLTP / 100) * 100
            print("spotLTP = ", spotLTP, " ATM_STRIKE = ", ATM_STRIKE)

            if mapStrike:
                print("check containskey22")

                last_key = list(mapStrike.keys())[-1]
                last_value = mapStrike[last_key]
                prevATMStrike = last_value
                if ATM_STRIKE == prevATMStrike:
                    IS_ATM_STRIKE_SHIFT = False
                    atmStrikeNotShiftedCount += 1
                else:
                    mapStrike.clear()
                    mapStrike[ATM_STRIKE] = ATM_STRIKE
                    IS_ATM_STRIKE_SHIFT = True
                    atmStrikeNotShiftedCount = 1
                    # slicing -> keep only last element only
                    avgOiPcrList2 = avgOiPcrList2[-1:]
            else:
                mapStrike[ATM_STRIKE] = ATM_STRIKE
            print("IS_ATM_STRIKE_SHIFT =", IS_ATM_STRIKE_SHIFT, " mapStrike =", mapStrike)

            # Add new PCR value

            # if not IS_STRIKE_SHIFT and notStrikeShiftCount == 3:
            # print("avgOiPcrList =",avgOiPcrList, "notStrikeShiftCount=",notStrikeShiftCount)
            print("avgOiPcrList2 =", avgOiPcrList2, "atmStrikeNotShiftedCount=", atmStrikeNotShiftedCount)

            # print(IS_STRIKE_SHIFT," ",notStrikeShiftCount, " ",len(avgOiPcrList))
            print(IS_ATM_STRIKE_SHIFT, " ", atmStrikeNotShiftedCount, " ", len(avgOiPcrList2))

            # SUPP_RES = round(SYNTH_FUT_STRIKE / 500) * 500
            # SUPP_RES = 54100

            # SUPP_RES = round(get_support_resistance(FUT_LTP))
            SUPP_RES = get_support_resistance(FUT_LTP)
            if SUPP_RES != "NOTRADEZONE":
                SUPP_RES = round(SUPP_RES)

            print("SUPP_RES===", SUPP_RES, " Buffer=", iv_params.get("support_resistance_buffer", 30))
            suppResCE = helper.getOptionFormat(stock, intExpiry, SUPP_RES, "CE")
            suppResPE = helper.getOptionFormat(stock, intExpiry, SUPP_RES, "PE")

            row1 = dfochain[dfochain['symbol'] == suppResCE]  # filter by symbol
            row2 = dfochain[dfochain['symbol'] == suppResPE]
            # row = dfochain[dfochain['symbol'] == "NSE:BANKNIFTY25SEP54100PE"]   # filter by symbol
            # print("oich=",row.iloc[0]['oich'])
            if not row1.empty and not row2.empty:
                suppResCeChOi = row1.iloc[0]['oich']
                suppResPeChOi = row2.iloc[0]['oich']
                print("CEoich val = ", suppResCeChOi, " PEoich val = ", suppResPeChOi)
            else:
                print("not found")

            if (suppResCeChOi > 0 and suppResPeChOi > 0):
                IS_CHOI_DIFF_GT_25PERC = is_difference_greater_than_25(suppResCeChOi, suppResPeChOi)
            else:
                # print(" both CE PE at suppRes unwinded i.e negative")
                print(" Either or both CE PE at suppRes unwinded i.e negative")
                IS_CHOI_DIFF_GT_25PERC = False
            print("================================================")
            print("IS_CHOI_DIFF_GT_25PERC==", IS_CHOI_DIFF_GT_25PERC)

            # dataFUT=getHistorical1(BNFut,timeFrame,3)  # add duration 2
            dataFUT = helper.getHistorical(BNFut, timeFrame, 3, fyers)
            # print(dataFUT)
            opens = dataFUT['open'].to_numpy()
            high = dataFUT['high'].to_numpy()
            low = dataFUT['low'].to_numpy()
            close = dataFUT['close'].to_numpy()
            # rsi value of FUT chart on 3 min time frame
            RSI_VAL1 = round(ta.momentum.RSIIndicator(pd.Series(close), 14, False).rsi().iloc[-1], 2)
            RSI_VAL2 = round(ta.momentum.RSIIndicator(pd.Series(close), 14, False).rsi().iloc[-2], 2)
            RSI_VAL = round(ta.momentum.RSIIndicator(pd.Series(close), 14, False).rsi().iloc[-2], 2)
            print("RSI_VAL1==", RSI_VAL1)
            print("RSI_VAL2==", RSI_VAL2)
            # 3-min FUT candle OHLC for analysis
            print("FUT_3m_OHLC O=", round(opens[-2], 1), " H=", round(high[-2], 1),
                  " L=", round(low[-2], 1), " C=", round(close[-2], 1),
                  " Range=", round(high[-2] - low[-2], 1))

            if not IS_ATM_STRIKE_SHIFT and atmStrikeNotShiftedCount >= 3 and len(avgOiPcrList2) == 3:
                if avgOiPcrList2[0] < avgOiPcrList2[1] < avgOiPcrList2[2]:
                    IS_CONSECUTIVELY_2TIMES_PCR_INCREASED2 = True
                    print("isPcrInc =", IS_CONSECUTIVELY_2TIMES_PCR_INCREASED2)
                elif avgOiPcrList2[0] > avgOiPcrList2[1] > avgOiPcrList2[2]:
                    IS_CONSECUTIVELY_2TIMES_PCR_DECREASED2 = True
                    print("isPcrDecr =", IS_CONSECUTIVELY_2TIMES_PCR_DECREASED2)
                else:
                    IS_CONSECUTIVELY_2TIMES_PCR_INCREASED2 = False
                    IS_CONSECUTIVELY_2TIMES_PCR_DECREASED2 = False
                    if avgOiPcrList2[1] < avgOiPcrList2[2] or avgOiPcrList2[1] > avgOiPcrList2[2]:
                        avgOiPcrList2 = avgOiPcrList2[1:]
                        print("recent two =", avgOiPcrList2)
                    else:
                        # Remove first two values if not strictly increasing/decreasing
                        avgOiPcrList2 = avgOiPcrList2[2:]
                        print("neither =", avgOiPcrList2)
            elif len(avgOiPcrList2) == 3:
                remove = 3 - atmStrikeNotShiftedCount
                # avgOiPcrList2 = avgOiPcrList2[2:]
                avgOiPcrList2 = avgOiPcrList2[remove:]
                # set some val for atmStrikeNotShiftedCount if needed
                print("none =", avgOiPcrList2)

            print("newSynthFut = ", SYNTH_FUT_STRIKE)
            print("ATMStrike = ", ATM_STRIKE)
            print("AVG_OIPCR=", avgoiPCR)
            print("avgoiPCROld= ", avgoiPCROld)
            print("SUPP_RES =", SUPP_RES)
            print("FUT LTP =", FUT_LTP)
            print("CEchoi  PechOi =",suppResCeChOi,"  ", suppResPeChOi)
            print("====================================")

            print("totalOIPCR =", totalOIPCR)
            print("AVG_VOLPCR=", avgvolPCR)
            print("atmPCR=", pcr5, " belowATM ", pcr6)
            print("atmVolPCR=", volpcr5, " belowATM ", volpcr6)

            # FOR BULL , # FUT_LTP > SUPP_RES
            print(IS_CHOI_DIFF_GT_25PERC, " ", FUT_LTP, " ", SUPP_RES, " ", IS_CONSECUTIVELY_2TIMES_PCR_INCREASED2, " ",
                  IS_CONSECUTIVELY_2TIMES_PCR_DECREASED2, " ", RSI_VAL)

            # 3rd feb 2026 change = suppResCeChOi > suppResPeChOi
            if suppResCeChOi > suppResPeChOi and slCount != 2 and dt1.hour <= 15 and SUPP_RES != "NOTRADEZONE" and st == 0 and IS_CHOI_DIFF_GT_25PERC and FUT_LTP > SUPP_RES and IS_CONSECUTIVELY_2TIMES_PCR_INCREASED2:
            # if st == 0 and IS_CHOI_DIFF_GT_25PERC:
                print("In Bull trade, slCount = ",slCount)
                isBullTrade = True
                st = 1
                takeEntry(isBullTrade, False, qty, fyers, papertrading)
                print("after entry atmPE tradeATMOption =", tradeATMOption)

                # data1minFUT = helper.getHistorical(BNFut,1,2,fyers)

                data1minFUT = helper.getHistorical(tradeATMOption, 1, 3, fyers)
                opens = data1minFUT['open'].to_numpy()
                # high = data1minFUT['high'].to_numpy()
                # low = data1minFUT['low'].to_numpy()
                close = data1minFUT['close'].to_numpy()
                # Dynamic SL/target from IV regime
                dynamic_sl_pt = iv_params.get("sl_point", sl_point)
                dynamic_tgt_pt = iv_params.get("target_point", target_point)
                sl = float(close[-1]) + dynamic_sl_pt
                target = float(close[-1]) - dynamic_tgt_pt
                entryPremium = float(close[-1])  # track for trailing SL
                slTrailed = False
                slConfirmCount = 0
                print("IV-Dynamic Bull SL=", sl, " Target=", target, " (sl_pts=", dynamic_sl_pt, " tgt_pts=", dynamic_tgt_pt, ")")

                IS_CONSECUTIVELY_2TIMES_PCR_INCREASED2 = False
                mapStrike.clear()
                IS_ATM_STRIKE_SHIFT = False
                atmStrikeNotShiftedCount = 1
                avgOiPcrList2 = []

            elif suppResCeChOi < suppResPeChOi and slCount != 2 and dt1.hour <= 15 and SUPP_RES != "NOTRADEZONE" and st == 0 and IS_CHOI_DIFF_GT_25PERC and FUT_LTP < SUPP_RES and IS_CONSECUTIVELY_2TIMES_PCR_DECREASED2:
                # elif st == 0 and not IS_CHOI_DIFF_GT_25PERC:
                print("In Bear trade,slCount= ",slCount)
                isBearTrade = True
                st = 2
                takeEntry(False, isBearTrade, qty, fyers, papertrading)

                # data1minFUT = helper.getHistorical(BNFut,1,2,fyers)
                data1minFUT = helper.getHistorical(tradeATMOption, 1, 3, fyers)
                opens = data1minFUT['open'].to_numpy()
                # high = data1minFUT['high'].to_numpy()
                # low = data1minFUT['low'].to_numpy()
                close = data1minFUT['close'].to_numpy()
                # Dynamic SL/target from IV regime
                dynamic_sl_pt = iv_params.get("sl_point", sl_point)
                dynamic_tgt_pt = iv_params.get("target_point", target_point)
                sl = float(close[-1]) + dynamic_sl_pt
                target = float(close[-1]) - dynamic_tgt_pt
                entryPremium = float(close[-1])  # track for trailing SL
                slTrailed = False
                slConfirmCount = 0
                print("IV-Dynamic Bear SL=", sl, " Target=", target, " (sl_pts=", dynamic_sl_pt, " tgt_pts=", dynamic_tgt_pt, ")")

                IS_CONSECUTIVELY_2TIMES_PCR_DECREASED2 = False
                mapStrike.clear()
                IS_ATM_STRIKE_SHIFT = False
                atmStrikeNotShiftedCount = 1
                avgOiPcrList2 = []

            # if already in bull trade and yoy went into loss or it gone against u and bearish criteria met
            # then check below and take bearish trade, exit from bull
            #
            # elif dt1.hour <= 15 and SUPP_RES != "NOTRADEZONE" and st == 1 and isBullTrade and IS_CHOI_DIFF_GT_25PERC and FUT_LTP < SUPP_RES and IS_CONSECUTIVELY_2TIMES_PCR_DECREASED2:
            #     print("=In reverse trade bear=")
            #     isBearTrade = True
            #     st = 2
            #     isBullTrade = False
            #     oidexit = exitSpreadPosition(tradeATMOption, tradeHedgeOption)
            #     time.sleep(2)
            #     takeEntry(False, isBearTrade, qty, fyers, papertrading)
            #     time.sleep(1)
            #     data1minFUT = helper.getHistorical(tradeATMOption, 1, 3, fyers)
            #     opens = data1minFUT['open'].to_numpy()
            #     close = data1minFUT['close'].to_numpy()
            #     sl = float(close[-1]) + sl_point
            #     target = float(close[-1]) - target_point
            #     IS_CONSECUTIVELY_2TIMES_PCR_DECREASED2 = False
            #     mapStrike.clear()
            #     IS_ATM_STRIKE_SHIFT = False
            #     atmStrikeNotShiftedCount = 1
            #     avgOiPcrList2 = []
            #
            # elif dt1.hour <= 15 and SUPP_RES != "NOTRADEZONE" and st == 2 and isBearTrade and IS_CHOI_DIFF_GT_25PERC and FUT_LTP > SUPP_RES and IS_CONSECUTIVELY_2TIMES_PCR_INCREASED2:
            #     print("=In reverse trade bull =")
            #     isBullTrade = True
            #     st = 1
            #     isBearTrade = False
            #     oidexit = exitSpreadPosition(tradeATMOption, tradeHedgeOption)
            #     time.sleep(2)
            #     takeEntry(isBullTrade, False, qty, fyers, papertrading)
            #     time.sleep(1)
            #     data1minFUT = helper.getHistorical(tradeATMOption, 1, 3, fyers)
            #     opens = data1minFUT['open'].to_numpy()
            #     close = data1minFUT['close'].to_numpy()
            #     sl = float(close[-1]) + sl_point
            #     target = float(close[-1]) - target_point
            #     IS_CONSECUTIVELY_2TIMES_PCR_INCREASED2 = False
            #     mapStrike.clear()
            #     IS_ATM_STRIKE_SHIFT = False
            #     atmStrikeNotShiftedCount = 1
            #     avgOiPcrList2 = []
            #
            elif len(avgOiPcrList2) == 3:
                # logic if not in trade reset above flags accordingly
                print("no trade yet =", avgOiPcrList2)
                IS_CONSECUTIVELY_2TIMES_PCR_DECREASED2 = False
                IS_CONSECUTIVELY_2TIMES_PCR_INCREASED2 = False
                avgOiPcrList2 = avgOiPcrList2[
                                1:]  # if not trade then remove first val,so in next one more val gets added and it will check again pcr with 3 values

            # order_id = checkCriteriaAndTakeTrade()

            order_id = checkCriteriaAndTakeTrade()

            if tradeCEoption != "":
                optionInstum = tradeCEoption
            elif tradePEoption != "":
                optionInstum = tradePEoption

            time.sleep(1)  # this sleep is important because once candle formed and above logic printed and if this
            # time sleep is not there it will print twice and you map logic get wrong

        elif candle_formed == 1:
            if st == 1 or st == 2:
                # y = 1
                # print("after entry tradeATMOption =",tradeATMOption)
                data1minFUT = helper.getHistorical(tradeATMOption, 1, 3, fyers)
                opens2 = data1minFUT['open'].to_numpy()
                high2 = data1minFUT['high'].to_numpy()
                low2 = data1minFUT['low'].to_numpy()
                close2 = data1minFUT['close'].to_numpy()
                while y == 1:
                    dt2 = datetime.now()
                    if dt2.second <= 1 and dt2.minute % timeFrame2 == 0:
                        oneMinCandle_Formed = 1
                        # Refresh 1-min candle data for fresh close value
                        data1minFUT = helper.getHistorical(tradeATMOption, 1, 3, fyers)
                        close2 = data1minFUT['close'].to_numpy()

                        # IN BULL TRADE
                        if st == 1:
                            # Trailing SL: if premium dropped by trail_trigger pts, move SL to breakeven
                            trail_trigger = iv_params.get("trail_trigger", 50)
                            if not slTrailed and close2[-1] <= (entryPremium - trail_trigger):
                                sl = entryPremium  # move SL to breakeven
                                slTrailed = True
                                print("TRAILING SL activated! SL moved to breakeven =", sl, " (trigger=", trail_trigger, ")")

                            if close2[-1] >= sl:
                                slConfirmCount += 1
                                if slConfirmCount >= 2:
                                    print('SL Hit (confirmed 2 candles)')
                                    st = 0
                                    slCount = slCount + 1
                                    print('from bull trade slCount =',slCount)
                                    oidexit = exitSpreadPosition(tradeATMOption, tradeHedgeOption)
                                    break
                                else:
                                    print("SL breached candle", slConfirmCount, "of 2. Waiting for confirmation. close=", close2[-1], " SL=", sl)
                                    time.sleep(1)
                                    break
                            elif close2[-1] <= target:
                                print('Target hit')
                                st = -1
                                targetCount = targetCount + 1
                                # st = 0
                                oidexit = exitSpreadPosition(tradeATMOption, tradeHedgeOption)
                                break
                            else:
                                slConfirmCount = 0  # reset if candle closes below SL
                                print("In Bull Trade. No Exit Yet. Current Second", dt2.second)
                                print(" curr closing ", close2[-1], " SL=", sl, " target", target)
                                time.sleep(1)
                                # y = 2
                                break
                        # IN BEAR TRADE
                        elif st == 2:
                            # Trailing SL: if premium dropped by trail_trigger pts, move SL to breakeven
                            trail_trigger = iv_params.get("trail_trigger", 50)
                            if not slTrailed and close2[-1] <= (entryPremium - trail_trigger):
                                sl = entryPremium  # move SL to breakeven
                                slTrailed = True
                                print("TRAILING SL activated! SL moved to breakeven =", sl, " (trigger=", trail_trigger, ")")

                            if close2[-1] >= sl:
                                slConfirmCount += 1
                                if slConfirmCount >= 2:
                                    print('SL Hit (confirmed 2 candles)')
                                    st = 0
                                    slCount = slCount + 1
                                    print('from bear trade slCount =',slCount)
                                    oidexit = exitSpreadPosition(tradeATMOption, tradeHedgeOption)
                                    break
                                else:
                                    print("SL breached candle", slConfirmCount, "of 2. Waiting for confirmation. close=", close2[-1], " SL=", sl)
                                    time.sleep(1)
                                    break
                            elif close2[-1] <= target:
                                print('Target hit')
                                st = -1
                                targetCount = targetCount + 1
                                # st = 0
                                oidexit = exitSpreadPosition(tradeATMOption, tradeHedgeOption)
                                break
                            else:
                                slConfirmCount = 0  # reset if candle closes below SL
                                print("In Bear Trade. No Exit Yet. Current Second", dt2.second)
                                print(" curr closing ", close2[-1], " SL=", sl, " target", target)
                                time.sleep(1)
                                break

                    elif oneMinCandle_Formed == 1:
                        # print("Waiting for 1min candle to form. Current Second == ",dt2.second, "  ",dt2.minute % timeFrame2)
                        time.sleep(1)
                    else:
                        print("Waiting for first 1min candle to form. Current Second == ", dt2.second, "  ",
                              dt2.minute % timeFrame2)
                        time.sleep(1)

            # TIME EXIT 3.15 pm
            if dt1.hour >= 15 and dt1.minute >= 15:
            # if (dt1.hour >= 23 and dt1.minute >= 33):   # 11.45 pm
                if st == 1:
                    print("EOD Exit")
                    oidexit = exitSpreadPosition(tradeATMOption, tradeHedgeOption)
                elif st == 2:
                    print("EOD Exit")
                    oidexit = exitSpreadPosition(tradeATMOption, tradeHedgeOption)
                print('End Of the Day')
                x = 2
                break
        else:
            print("Waiting for first candle to form. Current Second == ", dt1.second, "  ", dt1.minute % timeFrame)
            time.sleep(1)
    else:
        print(" -- not entered waiting for time---", dt1.hour, ":", dt1.minute)
        time.sleep(1)
# ------------Saving the final lists in csv file------------------
tradesDF.to_csv("template_indicator.csv")
