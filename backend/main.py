"""
=========================================================
Project: Angel One AI Trading Bot
Developer: Himanshu Joshi
Version: 13.1.0
=========================================================
CHANGELOG:
v13.1.0 - Smart AI Strategy upgraded to detect active losses 
          and dynamically average down quantities.
          Enhanced NSE Option Chain Stealth Handshake.
          Fixed Open Price calculation fallback.
v13.0.0 - Fixed FastAPI Deprecation warning via Lifespan.
          Rebuilt Chart Timeline slicing & Native Zooming.
          Physical Log file writing to Logs/bot_logs.txt.
=========================================================
"""
__author__ = "Himanshu Joshi"
__version__ = "13.1.0"

import os
import sys
import time
import json
import threading
import requests
import re
from datetime import datetime, timedelta, timezone
from typing import Optional
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv
from SmartApi import SmartConnect
import pyotp

from backend.ws_client import start_websocket, LIVE_PRICE

# --- PATH & ENV RESOLUTION ---
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(BASE_DIR, ".env"))

API_KEY = str(os.getenv("ANGEL_API_KEY", ""))
CLIENT_CODE = str(os.getenv("ANGEL_CLIENT_CODE", ""))
PASSWORD = str(os.getenv("ANGEL_PASSWORD", ""))
TOTP_TOKEN = str(os.getenv("ANGEL_TOTP_TOKEN", ""))

IST = timezone(timedelta(hours=5, minutes=30))

# --- DATABASE & LOGGING FOLDERS ---
LOGS_DIR = os.path.join(BASE_DIR, "Logs")
os.makedirs(LOGS_DIR, exist_ok=True)
TEXT_LOG_FILE = os.path.join(LOGS_DIR, "bot_logs.txt")

SYSTEM_LOGS, TRADE_HISTORY, PRICE_HISTORY, INSTRUMENTS = [], [], [], []
AVAILABLE_EXPIRIES = []
REAL_ORDERS_CACHE, CHART_HISTORY_CACHE = [], []
TRUE_OPTION_CHAIN = []

LOCAL_ORDERS_FILE = os.path.join(BASE_DIR, "local_orders.json")
PAPER_BALANCE_FILE = os.path.join(BASE_DIR, "paper_balance.json")

DYNAMIC_HOLIDAYS = {}

INDEX_INFO = {
    "NIFTY": {"google": "NIFTY_50:INDEXNSE", "yahoo": "^NSEI", "nse_sym": "NIFTY", "token": "26000", "exch": "NSE", "opt_exch": "NFO", "step": 50, "lot": 25},
    "BANKNIFTY": {"google": "NIFTY_BANK:INDEXNSE", "yahoo": "^NSEBANK", "nse_sym": "BANKNIFTY", "token": "26009", "exch": "NSE", "opt_exch": "NFO", "step": 100, "lot": 15},
    "SENSEX": {"google": "INDEXBOM:SENSEX", "yahoo": "^BSESN", "nse_sym": "SENSEX", "token": "999901", "exch": "BSE", "opt_exch": "BFO", "step": 100, "lot": 10}
}

USER_NAME = CLIENT_CODE or "Angel User"
ACTIVE_INDEX = "NIFTY"
TRADING_MODE, TRADING_STRATEGY = "Paper Trading", "smart"
AUTO_TRADING, IN_POSITION, CURRENT_SIGNAL = False, False, "WAITING"

MARKET_DATA = {"ltp": 0.0, "close": 0.0, "open": 0.0, "high": 0.0, "low": 0.0, "high52": 0.0, "low52": 0.0}
INSTITUTIONAL_DATA = {"fii_net": "--", "dii_net": "--"}

REAL_PNL, REAL_PROFIT, REAL_LOSS, ACCOUNT_BALANCE = 0.0, 0.0, 0.0, 0.0
PAPER_PNL, PAPER_PROFIT, PAPER_LOSS, PAPER_BALANCE = 0.0, 0.0, 0.0, 100000.00 

class ModeRequest(BaseModel): mode: str
class IndexRequest(BaseModel): index: str
class BuyRequest(BaseModel): 
    type: str
    strike: Optional[float] = None
class StrategyRequest(BaseModel): strategy: str
class FundRequest(BaseModel): amount: float

def get_strict_time(): 
    return datetime.now(IST).strftime("%d-%b-%Y %I:%M:%S %p").upper()

def safe_float(val, default=0.0):
    try: 
        if val in [None, "--", ""]: return default
        cleaned = str(val).replace(',', '').replace('₹', '').replace('+', '').replace('Cr', '').strip()
        return float(cleaned)
    except: return default

def format_date_strict(date_str):
    if date_str in ["--", "", None]: return "--"
    date_str = str(date_str).strip()
    for fmt in ("%d-%b-%Y %I:%M:%S %p", "%d-%b-%Y %H:%M:%S", "%d %b %Y", "%d-%b-%Y", "%d%b%Y", "%Y-%m-%d %H:%M:%S"):
        try:
            dt = datetime.strptime(date_str, fmt)
            if dt.hour == 0 and dt.minute == 0: dt = dt.replace(hour=15, minute=30, second=0)
            return dt.strftime("%d-%b-%Y %I:%M:%S %p").upper()
        except ValueError: continue
    return date_str.upper()

def add_log(action, highlight=""):
    timestamp = get_strict_time()
    SYSTEM_LOGS.append({"time": timestamp, "action": action, "highlight": highlight})
    if len(SYSTEM_LOGS) > 100: SYSTEM_LOGS.pop(0)
    try:
        with open(TEXT_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{timestamp}] {action}\n")
    except: pass

def save_paper_balance():
    try:
        with open(PAPER_BALANCE_FILE, "w") as f: json.dump({"balance": PAPER_BALANCE}, f, indent=4)
    except: pass

def load_local_orders():
    global TRADE_HISTORY, PAPER_PNL, PAPER_PROFIT, PAPER_LOSS, PAPER_BALANCE, IN_POSITION, CURRENT_SIGNAL, REAL_PNL, REAL_PROFIT, REAL_LOSS
    if os.path.exists(PAPER_BALANCE_FILE):
        try:
            with open(PAPER_BALANCE_FILE, "r") as f: PAPER_BALANCE = float(json.load(f).get("balance", 100000.00))
        except: pass
    else: save_paper_balance()

    if os.path.exists(LOCAL_ORDERS_FILE):
        try:
            with open(LOCAL_ORDERS_FILE, "r") as f: TRADE_HISTORY = json.load(f)
            valid_orders = []
            for o in TRADE_HISTORY:
                if "undefined" in o.get("symbol", "") or o.get("symbol") == "" or o.get("qty") == "0": continue
                if "mode" not in o: o["mode"] = "Paper Trading"
                for key in ["time", "exit_time", "expiry"]:
                    if key in o and o[key] != "--": o[key] = format_date_strict(o[key])
                if "qty" not in o: o["qty"] = "25"
                if "sl" not in o: o["sl"] = "--"
                if "target" not in o: o["target"] = "--"
                if "exit_time" not in o: o["exit_time"] = "--"
                if "averaged" not in o: o["averaged"] = False
                
                if o.get("status") == "CLOSED" and o.get("pnl") not in ["--", "NaN", None]:
                    val = safe_float(o["pnl"])
                    if o["mode"] == "Paper Trading":
                        PAPER_PNL += val
                        if val > 0: PAPER_PROFIT += val
                        else: PAPER_LOSS += abs(val)
                    else:
                        REAL_PNL += val
                        if val > 0: REAL_PROFIT += val
                        else: REAL_LOSS += abs(val)
                elif o.get("status") == "OPEN" and o.get("mode") == TRADING_MODE:
                    IN_POSITION = True
                    CURRENT_SIGNAL = "HOLDING ACTIVE TRADE(S)"
                valid_orders.append(o)
            TRADE_HISTORY = valid_orders
            save_local_orders()
        except: pass

def save_local_orders():
    try:
        with open(LOCAL_ORDERS_FILE, "w") as f: json.dump(TRADE_HISTORY, f, indent=4)
    except: pass

def update_available_expiries():
    global AVAILABLE_EXPIRIES
    if not INSTRUMENTS: return
    today = datetime.now(IST).date()
    exp_set = set()
    for ins in INSTRUMENTS:
        if ins.get("name") == ACTIVE_INDEX and ins.get("symbol", "").endswith("CE"):
            exp_val = ins.get("expiry")
            if exp_val:
                try: dt = datetime.strptime(exp_val, "%d%b%Y").date()
                except ValueError:
                    try: dt = datetime.strptime(exp_val, "%d%b%y").date()
                    except: continue
                if dt >= today: exp_set.add(dt)
    sorted_dates = sorted(list(exp_set))
    AVAILABLE_EXPIRIES = [d.strftime("%d-%b-%Y 03:30:00 PM").upper() for d in sorted_dates]

def get_next_expiry(weeks_ahead=0):
    t = datetime.now(IST).date()
    exp = t + timedelta(days=(3 - t.weekday()) % 7)
    exp += timedelta(weeks=weeks_ahead)
    return exp.strftime("%d%b%y").upper(), exp.strftime("%d-%b-%Y 03:30:00 PM").upper()

def record_trade_entry(trade_type, entry_price, expiry, qty="25", sl="--", target="--", order_id=None, symbol="--"):
    if order_id == None: order_id = f"SIM-{int(time.time())}" 
    TRADE_HISTORY.append({
        "id": order_id, "time": get_strict_time(), "symbol": symbol,
        "type": trade_type, "entry": round(entry_price, 2), "exit": "--",
        "exit_time": "--", "expiry": format_date_strict(expiry), "qty": str(qty), "sl": str(sl), "target": str(target),
        "status": "OPEN", "pnl": "--", "mode": TRADING_MODE, "averaged": False
    })
    save_local_orders()

def record_trade_exit(order_id, exit_price, pnl):
    for o in TRADE_HISTORY:
        if o["id"] == order_id and o["status"] == "OPEN":
            o["exit"] = round(exit_price, 2)
            o["exit_time"] = get_strict_time()
            o["pnl"] = round(pnl, 2)
            o["status"] = "CLOSED"
    save_local_orders()

def format_time_remaining(td):
    days = td.days
    hours, rem = divmod(td.seconds, 3600)
    minutes, seconds = divmod(rem, 60)
    parts = []
    if days > 0: parts.append(f"{days} Day{'s' if days > 1 else ''}")
    if hours > 0 or days > 0: parts.append(f"{hours} Hour{'s' if hours > 1 else ''}")
    if minutes > 0 or hours > 0 or days > 0: parts.append(f"{minutes} Minute{'s' if minutes > 1 else ''}")
    parts.append(f"{seconds} Second{'s' if seconds > 1 else ''}")
    return " ".join(parts)

def get_next_valid_open(start_date):
    next_day = start_date + timedelta(days=1)
    while next_day.weekday() >= 5 or next_day.strftime("%Y-%m-%d") in DYNAMIC_HOLIDAYS:
        next_day += timedelta(days=1)
    return next_day.replace(hour=9, minute=15, second=0, microsecond=0)

def check_market_status():
    now = datetime.now(IST)
    today_str = now.strftime("%Y-%m-%d")
    
    if today_str in DYNAMIC_HOLIDAYS:
        next_open = get_next_valid_open(now)
        rem = next_open - now
        return "CLOSED", f"Holiday: {DYNAMIC_HOLIDAYS[today_str]} | Opens in {format_time_remaining(rem)}"
        
    if now.weekday() >= 5:
        next_open = get_next_valid_open(now)
        rem = next_open - now
        return "CLOSED", f"Weekend | Opens in {format_time_remaining(rem)}"

    market_pre_open = now.replace(hour=9, minute=0, second=0, microsecond=0)
    market_open = now.replace(hour=9, minute=15, second=0, microsecond=0)
    market_close = now.replace(hour=15, minute=30, second=0, microsecond=0)
    
    if now < market_pre_open:
        rem = market_pre_open - now
        return "CLOSED", f"Opens in {format_time_remaining(rem)}"
    elif market_pre_open <= now < market_open:
        rem = market_open - now
        return "PRE-OPEN", f"Trading begins in {format_time_remaining(rem)}"
    elif market_open <= now <= market_close:
        rem = market_close - now
        return "OPEN", f"Closes in {format_time_remaining(rem)}"

    next_open = get_next_valid_open(now)
    rem = next_open - now
    return "CLOSED", f"Market Closed | Opens in {format_time_remaining(rem)}"

def load_instruments():
    global INSTRUMENTS
    try:
        INSTRUMENTS = requests.get("https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json").json()
        update_available_expiries()
    except Exception as e: pass

def get_real_option_token(price, option_type, offset=0, exact_strike=None, expiry_str=None):
    if not INSTRUMENTS: return None, None, None
    step = INDEX_INFO[ACTIVE_INDEX]["step"]
    if exact_strike: target_strike = int(exact_strike * 100)
    else: target_strike = int(((round(price / step) * step) + offset) * 100)
        
    valid_options = []
    today = datetime.now(IST).date()
    for ins in INSTRUMENTS:
        if ins.get("name") == ACTIVE_INDEX and ins.get("symbol", "").endswith(option_type):
            try:
                if int(float(ins.get("strike", 0))) == target_strike:
                    exp_val = ins.get("expiry")
                    if exp_val is None: continue
                    exp_str = str(exp_val)
                    try: exp_date = datetime.strptime(exp_str, "%d%b%Y").date()
                    except ValueError: exp_date = datetime.strptime(exp_str, "%d%b%y").date()
                    if exp_date >= today:
                        valid_options.append((exp_date, ins["symbol"], ins["token"], exp_str))
            except: continue
    if valid_options:
        valid_options.sort(key=lambda x: x[0])
        best_exp_date = valid_options[0][0]
        ui_exp_formatted = best_exp_date.strftime("%d-%b-%Y 03:30:00 PM").upper()
        return valid_options[0][1], valid_options[0][2], ui_exp_formatted
    return None, None, None

def place_real_order(side, option_type, current_price, qty):
    try:
        trading_symbol, symbol_token, _ = get_real_option_token(current_price, option_type)
        if not trading_symbol or not symbol_token: 
            return False, f"Live token unavailable for {option_type}"
        
        info = INDEX_INFO[ACTIVE_INDEX]
        orderparams = {
            "variety": "NORMAL", "tradingsymbol": str(trading_symbol), "symboltoken": str(symbol_token), 
            "transactiontype": str(side).upper(), "exchange": info["opt_exch"], "ordertype": "MARKET",
            "producttype": "CARRYFORWARD", "duration": "DAY", "price": "0", "squareoff": "0", "stoploss": "0",
            "quantity": str(qty)
        }
        
        add_log(f"⚡ Live Exchange: Routing {side} {qty}x {trading_symbol}...", highlight="warning")
        response = smart_api.placeOrder(orderparams)
        
        if isinstance(response, dict):
            if response.get('status') == True:
                data_block = response.get('data')
                if isinstance(data_block, dict): return True, str(data_block.get('orderid', 'UNKNOWN'))
                elif isinstance(data_block, str): return True, data_block
            
            err_msg = response.get('message', '')
            err_code = response.get('errorcode', '')
            full_err = f"[{err_code}] {err_msg}" if err_code else err_msg
            if not full_err.strip(): full_err = str(response)
            return False, full_err
            
        return False, f"Raw Response: {str(response)}"
    except Exception as e:
        return False, f"Exception: {str(e)}"

def process_trade_entry(trade_dir, price, is_auto=False, specific_strike=None):
    global CURRENT_SIGNAL, IN_POSITION
    
    executions = []
    lot_size = INDEX_INFO[ACTIVE_INDEX]["lot"]
    step = INDEX_INFO[ACTIVE_INDEX]["step"]
    
    # Base execution sizing logic
    if TRADING_STRATEGY == "smart":
        # Smart AI starts with a standard momentum entry. Averaging handled in auto_trading_loop.
        executions = [{"dir": trade_dir, "qty": lot_size*2, "sl": 25.0, "tgt": 75.0, "type": "SMART_BASE"}]
    elif TRADING_STRATEGY == "scalping": executions = [{"dir": trade_dir, "qty": lot_size*4, "sl": 8.0, "tgt": 15.0, "type": "SCALP"}]
    elif TRADING_STRATEGY == "trend": executions = [{"dir": trade_dir, "qty": lot_size*2, "sl": 20.0, "tgt": 60.0, "type": "TREND"}]
    elif TRADING_STRATEGY == "reversal":
        inv_dir = "PE" if trade_dir == "CE" else "CE"
        executions = [{"dir": inv_dir, "qty": lot_size*2, "sl": 15.0, "tgt": 45.0, "type": "REVERSAL"}]
    elif TRADING_STRATEGY == "straddle":
        executions = [{"dir": "CE", "qty": lot_size*1, "sl": 15.0, "tgt": 80.0, "type": "STRADDLE_CE"}, {"dir": "PE", "qty": lot_size*1, "sl": 15.0, "tgt": 80.0, "type": "STRADDLE_PE"}]
    elif TRADING_STRATEGY == "strangle":
        executions = [{"dir": "CE", "qty": lot_size*1, "sl": 12.0, "tgt": 60.0, "type": "STRANGLE_CE", "offset": step}, {"dir": "PE", "qty": lot_size*1, "sl": 12.0, "tgt": 60.0, "type": "STRANGLE_PE", "offset": -step}]
    elif TRADING_STRATEGY == "protective_put":
        hedge = "PE" if trade_dir == "CE" else "CE"
        executions = [{"dir": trade_dir, "qty": lot_size*2, "sl": 20.0, "tgt": 50.0, "type": "PRIMARY"}, {"dir": hedge, "qty": lot_size*1, "sl": 10.0, "tgt": 100.0, "type": "INSURANCE"}]
    else: executions = [{"dir": trade_dir, "qty": lot_size*2, "sl": 20.0, "tgt": 50.0, "type": "MANUAL"}]

    current_balance = ACCOUNT_BALANCE if TRADING_MODE == "Real Trading" else PAPER_BALANCE
    base_time = int(time.time())
    vol_multiplier = 1.0 if ACTIVE_INDEX == "NIFTY" else 2.5

    for ex in executions:
        t_dir = ex["dir"]
        offset = ex.get("offset", 0)
        
        # Absolute hard cap on risk
        if ex["qty"] > lot_size * 20: ex["qty"] = lot_size * 20 
        
        margin_required = price * ex["qty"]
        if margin_required > current_balance and price > 0:
            max_safe_lots = int((current_balance * 0.05) / (price * lot_size))
            max_safe_lots = max(1, min(max_safe_lots, 10)) 
            ex["qty"] = max_safe_lots * lot_size
            add_log(f"⚠️ Margin scaled: Adjusted {t_dir} qty to {ex['qty']}", "warning")

        if specific_strike: trading_sym, _, ui_expiry = get_real_option_token(price, t_dir, exact_strike=specific_strike)
        else: trading_sym, _, ui_expiry = get_real_option_token(price, t_dir, offset=offset)
            
        if not trading_sym: 
            if TRADING_MODE == "Real Trading":
                add_log(f"⚠️ Live option token unavailable for {ACTIVE_INDEX} {t_dir}. Trade Aborted.", "error")
                continue
            else:
                ui_expiry = AVAILABLE_EXPIRIES[0] if AVAILABLE_EXPIRIES else get_next_expiry()[1]
                fake_strike = int(round(price / INDEX_INFO[ACTIVE_INDEX]["step"]) * INDEX_INFO[ACTIVE_INDEX]["step"]) + offset
                exp_str = datetime.strptime(ui_expiry, "%d-%b-%Y %I:%M:%S %p").strftime("%d%b%y").upper()
                trading_sym = f"{ACTIVE_INDEX}{exp_str}{fake_strike}{t_dir}"

        real_order_id = None
        if TRADING_MODE == "Real Trading":
            success, result = place_real_order("BUY", t_dir, price, ex["qty"])
            if not success:
                add_log(f"⚠️ Exchange Reject: {result}", "error")
                continue
            real_order_id = result

        sl_price = round(price - (ex["sl"] * vol_multiplier) if t_dir == "CE" else price + (ex["sl"] * vol_multiplier), 2)
        tgt_price = round(price + (ex["tgt"] * vol_multiplier) if t_dir == "CE" else price - (ex["tgt"] * vol_multiplier), 2)
        
        IN_POSITION = True
        order_id = real_order_id if real_order_id else f"SIM-{base_time}-{t_dir}-{offset}"
        record_trade_entry(f"BUY {t_dir}", price, ui_expiry, qty=str(ex["qty"]), sl=str(sl_price), target=str(tgt_price), order_id=order_id, symbol=trading_sym)
        
        prefix = "🤖 AUTO" if is_auto else "🖐 MANUAL"
        add_log(f"🟢 {prefix} BUY {trading_sym} ({ex['qty']} Qty)", highlight="success")

def close_position(order, current_price, is_auto=False, reason=""):
    global REAL_PNL, REAL_PROFIT, REAL_LOSS, ACCOUNT_BALANCE
    global PAPER_PNL, PAPER_PROFIT, PAPER_LOSS, PAPER_BALANCE, IN_POSITION
    
    trade_dir = "CE" if "CE" in order.get("type", "") else "PE"
    qty = int(order.get("qty", 25))
    entry = safe_float(order.get("entry", 0))
    pnl = round((current_price - entry) * qty if trade_dir == "CE" else (entry - current_price) * qty, 2)
    
    if TRADING_MODE == "Real Trading" and order.get("mode") == "Real Trading":
        success, result = place_real_order("SELL", trade_dir, current_price, qty)
        if not success:
            add_log(f"⚠️ Exit Reject [{order.get('symbol', trade_dir)}]: {result}", "error")
            return False
        REAL_PNL += pnl; ACCOUNT_BALANCE += pnl
        if pnl > 0: REAL_PROFIT += pnl
        else: REAL_LOSS += abs(pnl)
    elif order.get("mode") == "Paper Trading":
        PAPER_PNL += pnl; PAPER_BALANCE += pnl
        save_paper_balance()
        if pnl > 0: PAPER_PROFIT += pnl
        else: PAPER_LOSS += abs(pnl)
        
    record_trade_exit(order["id"], current_price, pnl)
    
    open_positions = [t for t in TRADE_HISTORY if t["status"] == "OPEN" and t.get("mode") == TRADING_MODE]
    IN_POSITION = len(open_positions) > 0
    
    prefix = "🤖 AUTO" if is_auto else "🖐 MANUAL"
    color = "success" if pnl > 0 else "error"
    reason_txt = f" ({reason})" if reason else ""
    add_log(f"🔴 {prefix} SELL {order.get('symbol', trade_dir)}{reason_txt}", highlight=color)
    return True

# --- BACKGROUND PROCESSES ---
def fetch_nse_option_chain_data():
    """✅ V13.1: Stealth Session Handshake for True NSE Option Chain"""
    global TRUE_OPTION_CHAIN
    info = INDEX_INFO[ACTIVE_INDEX]
    try:
        session = requests.Session()
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
            'Connection': 'keep-alive'
        }
        # Handshake to get cookies
        session.get("https://www.nseindia.com/option-chain", headers=headers, timeout=5)
        time.sleep(1)
        
        # Fetch actual JSON API
        headers['Accept'] = 'application/json'
        res = session.get(f"https://www.nseindia.com/api/option-chain-indices?symbol={info['nse_sym']}", headers=headers, timeout=5)
        
        if res.status_code == 200:
            data = res.json()
            curr_price = safe_float(data['records']['underlyingValue'], MARKET_DATA['ltp'])
            atm = round(curr_price / info['step']) * info['step']
            
            chain_list = []
            for item in data['records']['data']:
                st = item['strikePrice']
                # Grab 21 Rows (10 above, 10 below)
                if abs(st - atm) <= (info['step'] * 10):
                    ce = item.get('CE', {})
                    pe = item.get('PE', {})
                    chain_list.append({
                        "strike": st,
                        "ce": safe_float(ce.get('lastPrice', 0)),
                        "pe": safe_float(pe.get('lastPrice', 0)),
                        "ce_pct": f"{safe_float(ce.get('pChange', 0)):+.2f}%",
                        "pe_pct": f"{safe_float(pe.get('pChange', 0)):+.2f}%",
                        "ce_oi": f"{int(safe_float(ce.get('openInterest', 0))):,}",
                        "pe_oi": f"{int(safe_float(pe.get('openInterest', 0))):,}"
                    })
            if chain_list: TRUE_OPTION_CHAIN = sorted(chain_list, key=lambda x: x['strike'])
    except: pass

def fetch_market_data_robust(interval="5m", range_val="1d"):
    """✅ V13.1: Strict timeline slicing logic & Open price fallback"""
    global MARKET_DATA, CHART_HISTORY_CACHE
    info = INDEX_INFO[ACTIVE_INDEX]
    headers = {'User-Agent': 'Mozilla/5.0'}
    
    # Try Angel One first
    try:
        res = smart_api.ltpData(info["exch"], ACTIVE_INDEX, info["token"])
        if isinstance(res, dict) and res.get('status') and res.get('data'):
            MARKET_DATA["open"] = safe_float(res['data'].get('open', MARKET_DATA["open"]))
            MARKET_DATA["high"] = safe_float(res['data'].get('high', MARKET_DATA["high"]))
            MARKET_DATA["low"] = safe_float(res['data'].get('low', MARKET_DATA["low"]))
            MARKET_DATA["close"] = safe_float(res['data'].get('close', MARKET_DATA["close"]))
    except: pass

    # Fetch chart history for Candlesticks
    try:
        url = f"https://query2.finance.yahoo.com/v8/finance/chart/{info['yahoo']}?interval={interval}&range={range_val}"
        res = requests.get(url, headers=headers, timeout=5)
        if res.status_code == 200:
            data = res.json()['chart']['result'][0]
            meta = data['meta']
            quote = data['indicators']['quote'][0]
            
            timestamps = data['timestamp']
            
            # Slice today's data only if range is 1d to prevent multi-day overlap
            if range_val == "1d":
                today_start = datetime.now(IST).replace(hour=0, minute=0, second=0).timestamp()
                valid_idx = [i for i, t in enumerate(timestamps) if t >= today_start]
                if not valid_idx and timestamps: valid_idx = list(range(len(timestamps)))
            else:
                valid_idx = list(range(len(timestamps)))

            CHART_HISTORY_CACHE.clear()
            for i in valid_idx:
                if quote['open'][i] is not None:
                    CHART_HISTORY_CACHE.append({
                        "x": timestamps[i] * 1000,
                        "o": quote['open'][i],
                        "h": quote['high'][i],
                        "l": quote['low'][i],
                        "c": quote['close'][i]
                    })
            
            # If Angel failed, use the first candle of the day for Open
            if MARKET_DATA["open"] == 0 and len(CHART_HISTORY_CACHE) > 0:
                MARKET_DATA["open"] = CHART_HISTORY_CACHE[0]["o"]
            
            if MARKET_DATA["high"] == 0: MARKET_DATA["high"] = safe_float(meta.get('regularMarketDayHigh', 0))
            if MARKET_DATA["low"] == 0: MARKET_DATA["low"] = safe_float(meta.get('regularMarketDayLow', 0))
            if MARKET_DATA["close"] == 0: MARKET_DATA["close"] = safe_float(meta.get('chartPreviousClose', 0))
            
            MARKET_DATA["high52"] = safe_float(meta.get('fiftyTwoWeekHigh', MARKET_DATA["high52"]))
            MARKET_DATA["low52"] = safe_float(meta.get('fiftyTwoWeekLow', MARKET_DATA["low52"]))
    except: pass

def fetch_fii_dii_data():
    global INSTITUTIONAL_DATA
    try:
        session = requests.Session()
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/123.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml',
            'Referer': 'https://www.moneycontrol.com/'
        }
        res = session.get("https://www.moneycontrol.com/stocks/marketstats/fii_dii_activity/index.php", headers=headers, timeout=10)
        html = res.text
        
        fii_match = re.search(r'FII\*\*(?:.*?<td[^>]*>){3}\s*<strong[^>]*>\s*([-]?[\d\.,]+)\s*</strong>', html, re.IGNORECASE | re.DOTALL)
        if not fii_match: fii_match = re.search(r'FII(?:.*?<td[^>]*>){4}\s*([-]?[\d\.,]+)\s*</td>', html, re.IGNORECASE | re.DOTALL)
        if fii_match: INSTITUTIONAL_DATA["fii_net"] = str(safe_float(fii_match.group(1)))
        
        dii_match = re.search(r'DII\*\*(?:.*?<td[^>]*>){3}\s*<strong[^>]*>\s*([-]?[\d\.,]+)\s*</strong>', html, re.IGNORECASE | re.DOTALL)
        if not dii_match: dii_match = re.search(r'DII(?:.*?<td[^>]*>){4}\s*([-]?[\d\.,]+)\s*</td>', html, re.IGNORECASE | re.DOTALL)
        if dii_match: INSTITUTIONAL_DATA["dii_net"] = str(safe_float(dii_match.group(1)))
    except: pass

def live_price_fetch_loop():
    global MARKET_DATA
    while True:
        time.sleep(1.5)
        try:
            info = INDEX_INFO[ACTIVE_INDEX]
            res = smart_api.ltpData(info["exch"], ACTIVE_INDEX, info["token"])
            if isinstance(res, dict) and res.get('status'):
                data_block = res.get('data')
                if isinstance(data_block, dict):
                    MARKET_DATA["ltp"] = float(data_block.get('ltp', MARKET_DATA['ltp']))
        except: pass

def auto_trading_loop():
    global AUTO_TRADING, CURRENT_SIGNAL, PRICE_HISTORY, IN_POSITION
    while True:
        time.sleep(1) 
        try:
            price = MARKET_DATA["ltp"]
            if price <= 0: continue
                
            if not PRICE_HISTORY or PRICE_HISTORY[-1] != price:
                PRICE_HISTORY.append(price)
            if len(PRICE_HISTORY) > 10: PRICE_HISTORY.pop(0)

            market_state, _ = check_market_status()
            if AUTO_TRADING and "CLOSED" in market_state:
                AUTO_TRADING = False
                CURRENT_SIGNAL = "MARKET CLOSED"
                add_log("🛑 Market Closed. Auto-Trading Disengaged.", highlight="error")
                continue

            open_positions = [t for t in TRADE_HISTORY if t["status"] == "OPEN" and t.get("mode", "Paper Trading") == TRADING_MODE]
            IN_POSITION = len(open_positions) > 0

            # ✅ V13.1: SMART AI RECOVERY LOGIC (Averaging down)
            if AUTO_TRADING and TRADING_STRATEGY == "smart" and open_positions:
                for pos in open_positions:
                    entry = safe_float(pos.get("entry", 0))
                    if entry > 0 and pos.get("averaged") != True:
                        trade_dir = "CE" if "CE" in pos["type"] else "PE"
                        unrealized_loss = (price - entry) if trade_dir == "CE" else (entry - price)
                        # If losing more than 15 points, buy 1 lot to average down
                        if unrealized_loss < -15.0:
                            pos["averaged"] = True
                            save_local_orders()
                            add_log(f"🤖 SMART AI: Detecting Loss on {pos['symbol']}. Averaging Down.", "warning")
                            # Force a small 1-lot recovery trade
                            lot_size = INDEX_INFO[ACTIVE_INDEX]["lot"]
                            recovery_ex = [{"dir": trade_dir, "qty": lot_size*1, "sl": 15.0, "tgt": 50.0, "type": "SMART_RECOVERY"}]
                            for ex in recovery_ex:
                                if TRADING_MODE == "Paper Trading":
                                    ui_expiry = AVAILABLE_EXPIRIES[0] if AVAILABLE_EXPIRIES else get_next_expiry()[1]
                                    fake_strike = int(round(price / INDEX_INFO[ACTIVE_INDEX]["step"]) * INDEX_INFO[ACTIVE_INDEX]["step"])
                                    exp_str = datetime.strptime(ui_expiry, "%d-%b-%Y %I:%M:%S %p").strftime("%d%b%y").upper()
                                    trading_sym = f"{ACTIVE_INDEX}{exp_str}{fake_strike}{ex['dir']}"
                                    record_trade_entry(f"BUY {ex['dir']}", price, ui_expiry, qty=str(ex["qty"]), order_id=f"REC-{int(time.time())}", symbol=trading_sym)
            
            if AUTO_TRADING:
                if not open_positions:
                    if TRADING_STRATEGY == "smart": CURRENT_SIGNAL = "SMART AI: ANALYZING MOMENTUM..."
                    elif TRADING_STRATEGY == "scalping": CURRENT_SIGNAL = "ANALYZING SCALP ENTRY..."
                    elif TRADING_STRATEGY == "trend": CURRENT_SIGNAL = "TRACKING MOMENTUM..."
                    elif TRADING_STRATEGY == "reversal": CURRENT_SIGNAL = "SEEKING MEAN REVERSION..."
                    elif TRADING_STRATEGY in ["straddle", "strangle", "protective_put"]: CURRENT_SIGNAL = "ANALYZING HEDGE ENTRY..."
                    else: CURRENT_SIGNAL = "SCANNING LIVE MARKET..."
                else:
                    CURRENT_SIGNAL = "HOLDING ACTIVE TRADE(S) [AUTO]"
                
                # Base Entry trigger
                if not open_positions and len(PRICE_HISTORY) >= 5:
                    price_diff = PRICE_HISTORY[-1] - PRICE_HISTORY[-5]
                    thresh = {"smart": 1.5, "scalping": 0.5, "trend": 2.0, "reversal": 2.5, "straddle": 1.5, "strangle": 1.5, "protective_put": 1.0}.get(TRADING_STRATEGY, 2.0)
                    if abs(price_diff) >= thresh:
                        trade_dir = "CE" if price_diff > 0 else "PE"
                        process_trade_entry(trade_dir, price, is_auto=True)
                        time.sleep(5) 
            
            for pos in open_positions:
                trade_dir = "CE" if "CE" in pos.get("type", "") else "PE"
                sl = safe_float(pos.get("sl", 0))
                tgt = safe_float(pos.get("target", 0))
                entry = safe_float(pos.get("entry", 0))
                
                if sl > 0 and tgt > 0:
                    # Trailing SL for non-scalping/smart
                    if TRADING_STRATEGY not in ["scalping", "smart"]: 
                        if trade_dir == "CE" and price > entry + 15:
                            new_sl = round(price - 10, 2)
                            if new_sl > sl: 
                                pos["sl"] = str(new_sl)
                                save_local_orders()
                        elif trade_dir == "PE" and price < entry - 15:
                            new_sl = round(price + 10, 2)
                            if new_sl < sl:
                                pos["sl"] = str(new_sl)
                                save_local_orders()

                    if trade_dir == "CE":
                        if price <= sl: close_position(pos, price, is_auto=True, reason="SL Hit")
                        elif price >= tgt: close_position(pos, price, is_auto=True, reason="Target Hit")
                    else:
                        if price >= sl: close_position(pos, price, is_auto=True, reason="SL Hit")
                        elif price <= tgt: close_position(pos, price, is_auto=True, reason="Target Hit")
        except Exception as e:
            time.sleep(2)

def background_scraper_loop():
    while True:
        fetch_nse_option_chain_data()
        fetch_fii_dii_data()
        time.sleep(30)

# --- FASTAPI SERVER ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    global ACCOUNT_BALANCE, USER_NAME
    # Fallback Holidays setup immediately
    fallback = {
        "2026-01-26": "Republic Day", "2026-03-03": "Maha Shivaratri", "2026-03-20": "Holi", 
        "2026-04-02": "Mahavir Jayanti", "2026-04-10": "Good Friday", "2026-04-14": "Dr. Ambedkar Jayanti", 
        "2026-04-18": "Id-Ul-Fitr", "2026-05-01": "Maharashtra Day", "2026-08-15": "Independence Day", 
        "2026-09-04": "Ganesh Chaturthi", "2026-10-02": "Mahatma Gandhi Jayanti", "2026-10-20": "Dussehra", 
        "2026-11-08": "Diwali", "2026-11-20": "Gurunanak Jayanti", "2026-12-25": "Christmas"
    }
    DYNAMIC_HOLIDAYS.update(fallback)

    load_local_orders()
    fetch_market_data_robust() 
    fetch_fii_dii_data()
    fetch_nse_option_chain_data()
    
    try:
        data = smart_api.generateSession(CLIENT_CODE, PASSWORD, pyotp.TOTP(TOTP_TOKEN).now())
        if isinstance(data, dict) and data.get('status'):
            data_inner = data.get('data')
            if isinstance(data_inner, dict):
                USER_NAME = str(data_inner.get('name', CLIENT_CODE)).split(' ')[0].title()
            add_log("🟢 Angel One API Authenticated Successfully", highlight="success")
            try:
                rms = smart_api.rmsLimit()
                if isinstance(rms, dict) and rms.get('status'):
                    rms_data = rms.get('data')
                    if isinstance(rms_data, dict): ACCOUNT_BALANCE = float(rms_data.get('availablecash', 0.0))
            except: pass
            
            load_instruments()
            threading.Thread(target=start_websocket, args=(smart_api, API_KEY, CLIENT_CODE, add_log), daemon=True).start()
            threading.Thread(target=live_price_fetch_loop, daemon=True).start() 
            threading.Thread(target=auto_trading_loop, daemon=True).start()
            threading.Thread(target=background_scraper_loop, daemon=True).start()
        else:
            err = data.get('message', 'Unknown') if isinstance(data, dict) else 'Unknown'
            add_log(f"🔴 Auth Failed: {err}", highlight="error")
    except Exception as e: 
        add_log(f"🔴 System Error", highlight="error")
    
    yield

app = FastAPI(lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
smart_api = SmartConnect(api_key=API_KEY)

@app.get("/", response_class=FileResponse)
def root(): return os.path.join(BASE_DIR, "frontend", "index.html")

@app.get("/chart_history")
def get_chart_history(interval: str = "5m", range_val: str = "1d"):
    fetch_market_data_robust(interval=interval, range_val=range_val)
    return {"status": "success", "data": CHART_HISTORY_CACHE}

@app.get("/status")
def status(expiry_idx: int = 0):
    m, r = check_market_status()
    curr_price = MARKET_DATA["ltp"]
    prev_close = MARKET_DATA["close"]
        
    open_positions = [t for t in TRADE_HISTORY if t["status"] == "OPEN" and t.get("mode", "Paper Trading") == TRADING_MODE]
    
    sig_display = CURRENT_SIGNAL
    if len(open_positions) > 0 and not AUTO_TRADING:
        sig_display = "HOLDING ACTIVE TRADE(S) [MANUAL]"
    elif "CLOSED" in m:
        sig_display = "MARKET CLOSED"
    
    prob = "--"
    if curr_price > 0 and prev_close > 0:
        trend = ((curr_price - prev_close) / prev_close) * 100
        if len(open_positions) > 0:
            ent = safe_float(open_positions[-1].get("entry", curr_price))
            tdir = "CE" if "CE" in open_positions[-1].get("type", "CE") else "PE"
            health = ((curr_price - ent) / ent * 5000) if tdir == "CE" else ((ent - curr_price) / ent * 5000)
            live_prob = min(99.5, max(15.0, 60.0 + health))
            prob = f"{live_prob:.1f}%"
        elif "BUY" in sig_display or "ANALYZING" in sig_display or "SCANNING" in sig_display:
            base_prob = 50.0 + (abs(trend) * 20)
            if TRADING_STRATEGY in ["straddle", "strangle", "protective_put", "smart"]: base_prob += 15.0
            live_prob = min(92.0, max(25.0, base_prob))
            prob = f"{live_prob:.1f}%"

    pcr_val = "1.00"
    pcr_signal = "Neutral"

    exp_date_str = AVAILABLE_EXPIRIES[expiry_idx] if AVAILABLE_EXPIRIES and expiry_idx < len(AVAILABLE_EXPIRIES) else get_next_expiry(weeks_ahead=expiry_idx)[1]

    # Return True Option Chain if scraped
    opt_chain = []
    if TRUE_OPTION_CHAIN and ACTIVE_INDEX in ["NIFTY", "BANKNIFTY"]:
        opt_chain = TRUE_OPTION_CHAIN
            
    if MARKET_DATA['high'] > 0 and MARKET_DATA['low'] > 0 and MARKET_DATA['close'] > 0:
        pivot = (MARKET_DATA['high'] + MARKET_DATA['low'] + MARKET_DATA['close']) / 3
        support = round((2 * pivot) - MARKET_DATA['high'], 2)
        resistance = round((2 * pivot) - MARKET_DATA['low'], 2)
    else:
        support, resistance = "--", "--"

    exp_list = AVAILABLE_EXPIRIES[:3] if len(AVAILABLE_EXPIRIES) >= 3 else [get_next_expiry(weeks_ahead=i)[1] for i in range(3)]
    exp_list = [d.split(" ")[0] for d in exp_list]

    return {
        "active_index": ACTIVE_INDEX,
        "market": m, "remaining": r,
        "price": curr_price if curr_price > 0 else "--",
        "prev_close": prev_close if prev_close > 0 else "--",
        "open": MARKET_DATA["open"] if MARKET_DATA["open"] > 0 else "--",
        "high": MARKET_DATA["high"] if MARKET_DATA["high"] > 0 else "--",
        "low": MARKET_DATA["low"] if MARKET_DATA["low"] > 0 else "--",
        "high52": MARKET_DATA["high52"] if MARKET_DATA["high52"] > 0 else "--",
        "low52": MARKET_DATA["low52"] if MARKET_DATA["low52"] > 0 else "--",
        "support": support, "resistance": resistance,
        "fii_net": INSTITUTIONAL_DATA["fii_net"], "dii_net": INSTITUTIONAL_DATA["dii_net"],
        "signal": sig_display, "prob": prob, "pcr": f"{pcr_val} ({pcr_signal})",
        "mode": TRADING_MODE, "strategy": TRADING_STRATEGY,
        "active_positions": open_positions,
        "unrealized_pnl": sum((curr_price - safe_float(p.get("entry", 0))) * int(p.get("qty", 25)) if "CE" in p.get("type", "") else (safe_float(p.get("entry", 0)) - curr_price) * int(p.get("qty", 25)) for p in open_positions) if curr_price > 0 else 0.0,
        "opt_chain": opt_chain, "available_expiries": exp_list,
        "logs": SYSTEM_LOGS[::-1],
        "orders": [t for t in TRADE_HISTORY if t.get("mode", "Paper Trading") == TRADING_MODE][::-1],
        "user": USER_NAME
    }

@app.get("/pnl")
def pnl_api():
    today_str = datetime.now(IST).strftime("%d-%b-%Y").upper()
    if TRADING_MODE == "Real Trading": 
        today_pnl = sum(safe_float(o["pnl"]) for o in TRADE_HISTORY if o["status"] == "CLOSED" and o.get("mode") == "Real Trading" and today_str in str(o.get("exit_time", o.get("time",""))).upper())
        return {"today_profit": round(today_pnl, 2), "total_profit":round(REAL_PROFIT,2),"total_loss":round(REAL_LOSS,2),"balance":round(ACCOUNT_BALANCE,2)}
    
    today_pnl = sum(safe_float(o["pnl"]) for o in TRADE_HISTORY if o["status"] == "CLOSED" and o.get("mode", "Paper Trading") == "Paper Trading" and today_str in str(o.get("exit_time", o.get("time",""))).upper())
    return {"today_profit": round(today_pnl, 2), "total_profit":round(PAPER_PROFIT,2),"total_loss":round(PAPER_LOSS,2),"balance":round(PAPER_BALANCE,2)}

@app.get("/order/{order_id:path}")
def order_info(order_id: str):
    clean_id = str(order_id).strip()
    if clean_id.startswith("SIM-") or clean_id.startswith("REAL-"):
        for t in TRADE_HISTORY:
            if str(t.get("id")).strip() == clean_id: 
                return {"status":"success","data":t,"source":"Simulation/Local Log"}
    try:
        res = smart_api.orderBook()
        if isinstance(res, dict) and res.get('status') and isinstance(res.get('data'), list):
            for o in res['data']:
                if str(o.get('orderid')).strip() == clean_id: 
                    return {"status":"success","data":o,"source":"Angel One Live Data"}
    except: pass
    return {"status":"error","message":f"Order {clean_id} not found in Active Memory."}

@app.post("/index")
def set_index_api(req: IndexRequest):
    global ACTIVE_INDEX, MARKET_DATA, CHART_HISTORY_CACHE, PRICE_HISTORY, CURRENT_SIGNAL, TRUE_OPTION_CHAIN
    if req.index in INDEX_INFO:
        ACTIVE_INDEX = req.index
        MARKET_DATA = {"ltp": 0.0, "close": 0.0, "open": 0.0, "high": 0.0, "low": 0.0, "high52": 0.0, "low52": 0.0}
        CHART_HISTORY_CACHE.clear()
        PRICE_HISTORY.clear()
        TRUE_OPTION_CHAIN.clear()
        CURRENT_SIGNAL = "WAITING"
        update_available_expiries()
        threading.Thread(target=fetch_market_data_robust, args=("5m","1d"), daemon=True).start()
        add_log(f"🔄 Switched Index to {ACTIVE_INDEX}", "warning")
        return {"status": "success"}
    return {"status": "failed"}

@app.post("/api/balance")
def add_funds_api(req: FundRequest):
    global PAPER_BALANCE
    if TRADING_MODE == "Paper Trading":
        PAPER_BALANCE += req.amount
        save_paper_balance()
        add_log(f"💰 Manually added ₹{req.amount} to Paper Balance", highlight="success")
        return {"status": "success"}
    return {"status": "failed"}

@app.post("/api/reset")
def reset_bot_api():
    """✅ Factory Reset Engine"""
    global TRADE_HISTORY, PAPER_PNL, PAPER_PROFIT, PAPER_LOSS, PAPER_BALANCE, IN_POSITION, CURRENT_SIGNAL, REAL_PNL, REAL_PROFIT, REAL_LOSS, ACCOUNT_BALANCE
    TRADE_HISTORY = []
    PAPER_PNL, PAPER_PROFIT, PAPER_LOSS = 0.0, 0.0, 0.0
    PAPER_BALANCE = 100000.0
    REAL_PNL, REAL_PROFIT, REAL_LOSS = 0.0, 0.0, 0.0
    IN_POSITION = False
    CURRENT_SIGNAL = "WAITING"
    save_local_orders()
    save_paper_balance()
    open(TEXT_LOG_FILE, "w").close()
    SYSTEM_LOGS.clear()
    add_log("🗑️ Factory Reset Complete. Databases and Logs Wiped.", "error")
    return {"status": "success"}

@app.post("/api/restart")
def restart():
    def execute_reboot():
        global AUTO_TRADING, CURRENT_SIGNAL
        AUTO_TRADING = False
        CURRENT_SIGNAL = "WAITING"
        time.sleep(1)
        os._exit(1) # Drop the port completely for Windows to release it
    threading.Thread(target=execute_reboot, daemon=True).start()
    return {"status": "restarting"}

@app.post("/api/shutdown")
def shutdown():
    os.system('powershell -Command "Get-Process cmd -ErrorAction SilentlyContinue | Where-Object {$_.MainWindowTitle -match \'Angel One Trading Bot\'} | Stop-Process -Force"')
    threading.Timer(0.5, lambda: os._exit(0)).start()
    return {"status": "shutting down"}

@app.post("/mode")
def mode_api(req: ModeRequest):
    global TRADING_MODE
    TRADING_MODE = "Paper Trading" if req.mode == "paper" else "Real Trading"
    add_log(f"⚙️ System switched to {TRADING_MODE}", highlight="warning")
    return {"status": "success"}

@app.post("/strategy")
def strategy_api(req: StrategyRequest):
    global TRADING_STRATEGY
    TRADING_STRATEGY = req.strategy
    str_map = {
        "smart": "Smart AI (Hybrid ML)",
        "scalping": "High-Frequency Scalping", 
        "trend": "Trend Following (Momentum)", 
        "reversal": "Mean Reversion (Contrarian)", 
        "straddle": "Delta-Neutral Straddle (Hedge)",
        "strangle": "Asymmetric Strangle (Hedge)",
        "protective_put": "Protective Put (Hedge)"
    }
    add_log(f"🧠 AI Strategy Updated: {str_map.get(req.strategy, 'Unknown')}", highlight="warning")
    return {"status": "success"}

@app.post("/auto/start")
def auto_start():
    global AUTO_TRADING, CURRENT_SIGNAL
    market_state, _ = check_market_status()
    if "CLOSED" in market_state and TRADING_MODE == "Real Trading": return {"status": "failed"}
    AUTO_TRADING = True
    add_log("🤖 Auto Trading STARTED", highlight="success")
    return {"status": "success"}

@app.post("/auto/stop")
def auto_stop():
    global AUTO_TRADING, CURRENT_SIGNAL
    AUTO_TRADING = False
    if not IN_POSITION: CURRENT_SIGNAL = "WAITING"
    add_log("🛑 Auto Trading STOPPED", highlight="error")
    return {"status": "success"}

@app.post("/buy")
def buy_api(req: BuyRequest):
    market_state, _ = check_market_status()
    if "CLOSED" in market_state and TRADING_MODE == "Real Trading": return {"status": "failed"}
    price = MARKET_DATA["ltp"]
    if price > 0: process_trade_entry(req.type, price, is_auto=False, specific_strike=req.strike)
    return {"status": "success"}

@app.post("/exit/{order_id}")
def exit_specific_api(order_id: str):
    price = MARKET_DATA["ltp"]
    if price == 0: return {"status": "failed"}
    for pos in TRADE_HISTORY:
        if pos["id"] == order_id and pos["status"] == "OPEN":
            close_position(pos, price, is_auto=False, reason="Manual Square Off")
            return {"status": "success"}
    return {"status": "failed"}

@app.post("/exit")
def exit_api():
    price = MARKET_DATA["ltp"]
    if price == 0: return {"status": "failed"}
    open_positions = [t for t in TRADE_HISTORY if t["status"] == "OPEN" and t.get("mode", "Paper Trading") == TRADING_MODE]
    if not open_positions: return {"status": "failed"}
    for pos in open_positions:
        close_position(pos, price, is_auto=False, reason="Manual Square Off All")
    return {"status": "success"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8000)