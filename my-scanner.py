import requests
import time

# ==================== CONFIG ====================
TELEGRAM_BOT_TOKEN = "8301457069:AAEh6fE3oNQygNEqjxmifOuyG7Y0-lrOxRk"  # REPLACE
TELEGRAM_CHAT_ID = "5661202155"      # REPLACE

# --- FILTERS ---
MIN_24H_VOLUME = 2_000_000    # $2M minimum daily volume
EMA_CROSS_TOLERANCE = 0.002   # 0.2% tolerance for EXACT intersection
WICK_TOLERANCE = 0.001        # 0.1% buffer for exact wick touches
EMA_NEAR_TOLERANCE = 0.015    # 1.5% proximity to trigger lower timeframe check
EMA_CROSS_RSI = 45            # RSI(6) > 45 for Long, < 55 for Short
EMA_CROSS_VOL = 0.8           # Volume > 0.8x the 10-candle average

# --- LOWER TIMEFRAME CONFIRMATION ---
CONFIRMATION_TF = {
    "30m": "15m",   # Check 15m to confirm 30m signals
    "1h": "30m",    # Check 30m to confirm 1h signals
    "1d": "4h"      # Check 4h to confirm 1d signals
}

# --- SMART SIGNAL (WHALES) ---
USE_SMART_SIGNAL = True       
MIN_LONG_SHORT_RATIO = 1.0    # For Longs

# --- SAFETY NETS ---
COIN_COOLDOWN_MINUTES = 120   # 2-hour cooldown
MAX_ALERTS_PER_SCAN = 10      

# ==================== GLOBALS ====================
cooldown = {}                 

# ==================== TELEGRAM ====================
def send_telegram(msg):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML"}
    try:
        resp = requests.post(url, json=payload, timeout=10)
        if resp.status_code != 200:
            print(f"Telegram Error: {resp.text}")
    except Exception as e:
        print(f"Telegram connection error: {e}")

# ==================== INDICATORS ====================
def calculate_rsi(prices, period=6):
    if len(prices) < period + 1: return 50.0
    gains, losses = 0, 0
    for i in range(1, len(prices)):
        diff = prices[i] - prices[i-1]
        if diff >= 0: gains += diff
        else: losses += abs(diff)
    if losses == 0: return 100.0
    rs = (gains / period) / (losses / period)
    return 100.0 - (100.0 / (1.0 + rs))

def calculate_ema(prices, period):
    if len(prices) < period: return prices[-1]
    multiplier = 2 / (period + 1)
    ema = sum(prices[:period]) / period
    for price in prices[period:]:
        ema = (price - ema) * multiplier + ema
    return ema

# ==================== SMART SIGNAL (WHALES) ====================
def fetch_smart_signal(symbol, interval):
    url = "https://fapi.binance.com/futures/data/topLongShortAccountRatio"
    params = {"symbol": symbol, "period": interval, "limit": 1}
    try:
        resp = requests.get(url, params=params, timeout=5).json()
        if resp and len(resp) > 0:
            return float(resp[0]['longShortRatio'])
    except Exception:
        pass
    return 1.0 

# ==================== FETCH DATA ====================
def fetch_all_symbols():
    url = "https://fapi.binance.com/fapi/v1/ticker/24hr"
    try:
        resp = requests.get(url, timeout=10).json()
    except Exception as e:
        print(f"Error fetching symbols: {e}")
        return []
        
    symbols = []
    for item in resp:
        sym = item['symbol']
        if not sym.endswith('USDT'): continue
        if '_' in sym: continue
        if 'USDC' in sym: continue
        if 'BUSD' in sym: continue
        vol = float(item['quoteVolume'])
        if vol >= MIN_24H_VOLUME:
            symbols.append({'symbol': sym, 'volume': vol})
    symbols.sort(key=lambda x: x['volume'], reverse=True)
    return symbols

def fetch_candles(symbol, interval, limit=100):
    url = "https://fapi.binance.com/fapi/v1/klines"
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    try:
        resp = requests.get(url, params=params, timeout=5).json()
    except Exception:
        return []
        
    candles = []
    for k in resp:
        candles.append({
            'open': float(k[1]), 'high': float(k[2]),
            'low': float(k[3]), 'close': float(k[4]),
            'volume': float(k[5])
        })
    return candles

# ==================== CORE SCAN FUNCTION ====================
def scan_timeframe(symbols_data, interval, label):
    print(f"\n🔍 Scanning {label} candles across {len(symbols_data)} pairs...")
    alerts = []
    now = time.time()
    confirm_tf = CONFIRMATION_TF.get(interval)
    
    for item in symbols_data:
        symbol = item['symbol']
        
        if symbol not in cooldown:
            cooldown[symbol] = {}
        if interval in cooldown[symbol] and (now - cooldown[symbol][interval]) < (COIN_COOLDOWN_MINUTES * 60):
            continue
        
        try:
            candles = fetch_candles(symbol, interval, limit=100)
            if len(candles) < 100: continue
            
            current = candles[-1]
            prev_candles = candles[:-1]
            
            change_pct = ((current['close'] - current['open']) / current['open']) * 100
            avg_vol = sum(c['volume'] for c in prev_candles[-10:]) / 10
            if avg_vol == 0: continue
            vol_ratio = current['volume'] / avg_vol
            
            volatility_pct = ((current['high'] - current['low']) / current['open']) * 100
            avg_vol_3 = sum(c['volume'] for c in prev_candles[-3:]) / 3
            vol_3_ratio = current['volume'] / avg_vol_3 if avg_vol_3 > 0 else 0
            
            closes = [c['close'] for c in candles[-7:]]
            rsi6 = calculate_rsi(closes, period=6)
            
            all_closes = [c['close'] for c in candles]
            ema7 = calculate_ema(all_closes, 7)
            ema25 = calculate_ema(all_closes, 25)
            ema99 = calculate_ema(all_closes, 99)
            ema7_prev = calculate_ema(all_closes[:-1], 7)
            ema25_prev = calculate_ema(all_closes[:-1], 25)
            
            # --- PRIMARY TIMEFRAME LOGIC ---
            is_bullish_cross = (ema7_prev <= ema25_prev) and (ema7 > ema25)
            is_bearish_cross = (ema7_prev >= ema25_prev) and (ema7 < ema25)
            
            ema_distance = abs(ema7 - ema25) / ema25
            is_intersecting_close = ema_distance <= EMA_CROSS_TOLERANCE
            
            ema_zone_high = max(ema7, ema25)
            ema_zone_low = min(ema7, ema25)
            buffer = ema_zone_high * WICK_TOLERANCE
            wick_touched_ema = (current['low'] <= (ema_zone_high + buffer)) and (current['high'] >= (ema_zone_low - buffer)) and (ema_distance <= EMA_NEAR_TOLERANCE)
            
            is_cross = is_bullish_cross or is_bearish_cross or is_intersecting_close or wick_touched_ema
            trigger_note = ""
            
            if is_bullish_cross:
                trigger_note = "🎯 Trigger: Bullish EMA Crossover.\n"
            elif is_bearish_cross:
                trigger_note = "🎯 Trigger: Bearish EMA Crossover.\n"
            elif is_intersecting_close:
                trigger_note = "📝 Trigger: EMA Intersection at close (EMAs touching).\n"
            elif wick_touched_ema:
                trigger_note = "🪄 Trigger: Wick Slippage Catcher (Price wicked into EMA zone).\n"
            
            # --- LOWER TIMEFRAME CONFIRMATION LOGIC ---
            if not is_cross and confirm_tf and ema_distance <= EMA_NEAR_TOLERANCE:
                lower_candles = fetch_candles(symbol, confirm_tf, limit=100)
                if len(lower_candles) >= 100:
                    lower_closes = [c['close'] for c in lower_candles]
                    l_ema7 = calculate_ema(lower_closes, 7)
                    l_ema25 = calculate_ema(lower_closes, 25)
                    l_ema7_prev = calculate_ema(lower_closes[:-1], 7)
                    l_ema25_prev = calculate_ema(lower_closes[:-1], 25)
                    
                    l_bullish = (l_ema7_prev <= l_ema25_prev) and (l_ema7 > l_ema25)
                    l_bearish = (l_ema7_prev >= l_ema25_prev) and (l_ema7 < l_ema25)
                    l_distance = abs(l_ema7 - l_ema25) / l_ema25
                    l_intersecting = l_distance <= EMA_CROSS_TOLERANCE
                    
                    if l_bullish or l_bearish or l_intersecting:
                        is_cross = True
                        trigger_note = f"🚀 Trigger: Lower Timeframe Confirmation ({confirm_tf} is crossing).\n"
            
            # --- FINAL FILTERS ---
            # Determine signal direction based on close vs EMA25
            if current['close'] > ema25:
                signal_direction = "LONG"
            else:
                signal_direction = "SHORT"
            
            # RSI Filter: >45 for Long, <55 for Short
            rsi_valid = (rsi6 >= EMA_CROSS_RSI) if signal_direction == "LONG" else (rsi6 <= 100 - EMA_CROSS_RSI)
            
            if (is_cross and rsi_valid 
                and vol_ratio >= EMA_CROSS_VOL):
                
                # Trend Filter: Price > EMA99 for Long, Price < EMA99 for Short
                trend_valid = (current['close'] > ema99) if signal_direction == "LONG" else (current['close'] < ema99)
                
                if trend_valid:
                    smart_ratio = 1.0
                    if USE_SMART_SIGNAL:
                        smart_ratio = fetch_smart_signal(symbol, interval)
                    
                    if signal_direction == "LONG":
                        if smart_ratio >= MIN_LONG_SHORT_RATIO:
                            signal_type = "🟢 WHALE ALIGNED LONG"
                            advice = "Whales are long. Good for a long entry on the pullback."
                        else:
                            signal_type = "🔴 WHALE ALIGNED SHORT (Bull Trap Warning)"
                            advice = "Whales are short. This EMA cross is likely a trap. Consider shorting the rejection."
                    else:
                        if smart_ratio <= 1.0:
                            signal_type = "🔴 WHALE ALIGNED SHORT"
                            advice = "Whales are short. Good for a short entry on the bounce."
                        else:
                            signal_type = "🟢 WHALE ALIGNED LONG (Bear Trap Warning)"
                            advice = "Whales are long. This bearish EMA cross is likely a trap. Consider longing the bounce."
                    
                    vol_note = ""
                    if volatility_pct > 5.0:
                        vol_note += "⚠️ Volatility &gt; 5%: Very choppy. Be careful. Wait for a deeper pullback.\n"
                    elif volatility_pct < 2.0:
                        vol_note += "✅ Volatility &lt; 2%: Clean, tight candle. High probability setup.\n"
                    
                    if vol_3_ratio >= 3.0:
                        vol_note += "🚀 Vol vs 3 = 3x+: Massive fresh volume. Highly bullish.\n"
                    elif vol_3_ratio < 1.0:
                        vol_note += "📉 Vol vs 3 &lt; 1x: Breakout lacks conviction. Proceed with caution.\n"
                    
                    if not vol_note:
                        vol_note = "➖ Volatility & Volume are within normal ranges.\n"
                    
                    # Exact Intersection Note
                    if ema_distance <= 0.001:
                        vol_note += "🎯 Note: EXACT INTERSECTION detected (EMAs are practically equal).\n"
                    
                    if is_bullish_cross:
                        vol_note += "🎯 Trigger: Strict Bullish EMA Crossover.\n"
                    elif is_bearish_cross:
                        vol_note += "🎯 Trigger: Strict Bearish EMA Crossover.\n"
                    elif is_intersecting_close:
                        vol_note += "📝 Trigger: EMA Intersection at close (EMAs touching).\n"
                    elif wick_touched_ema:
                        vol_note += "🪄 Trigger: Wick Slippage Catcher (Price wicked into EMA zone).\n"
                    
                    alerts.append({
                        'symbol': symbol,
                        'price': current['close'],
                        'change': change_pct,
                        'vol_ratio': vol_ratio,
                        'vol_3_ratio': vol_3_ratio,
                        'volatility_pct': volatility_pct,
                        'rsi': rsi6,
                        'ema7': ema7,
                        'ema25': ema25,
                        'ema99': ema99,
                        'usd_volume': item['volume'],
                        'label': label,
                        'smart_ratio': smart_ratio,
                        'signal_type': signal_type,
                        'advice': advice,
                        'vol_note': vol_note
                    })
        except Exception:
            pass
    
    alerts.sort(key=lambda x: x['usd_volume'], reverse=True)
    alerts = alerts[:MAX_ALERTS_PER_SCAN]
    
    if alerts:
        print(f"🚨 Found {len(alerts)} {label} setups!")
        for a in alerts:
            entry_low = min(a['ema7'], a['ema25'])
            entry_high = max(a['ema7'], a['ema25'])
            
            msg = (
                f"⚡️ <b>{a['label']} EMA CROSS/INTERSECTION</b> {a['symbol']}\n"
                f"Type: <b>{a['signal_type']}</b>\n"
                f"Price: {a['price']:.5f}\n"
                f"Change: {a['change']:+.2f}%\n"
                f"📊 <b>Volatility: {a['volatility_pct']:.2f}%</b>\n"
                f"📊 <b>Vol vs 3: {a['vol_3_ratio']:.1f}x</b>\n"
                f"RSI(6): {a['rsi']:.1f}\n"
                f"24h Vol: ${a['usd_volume']/1_000_000:.1f}M\n"
                f"🐋 Smart Ratio: {a['smart_ratio']:.2f}\n"
                f"--- Trend ---\n"
                f"EMA7: {a['ema7']:.5f}\n"
                f"EMA25: {a['ema25']:.5f}\n"
                f"EMA99: {a['ema99']:.5f}\n"
                f"⚠️ <b>Entry Zone: {entry_low:.5f} - {entry_high:.5f}</b>\n"
                f"💡 <i>{a['advice']}</i>\n\n"
                f"📝 <b>Rules of Thumb:</b>\n"
                f"{a['vol_note']}"
                f"⏰ {time.strftime('%Y-%m-%d %H:%M:%S')} UTC"
            )
            print(msg)
            send_telegram(msg)
            cooldown[a['symbol']][interval] = now
    else:
        print(f"😴 No {label} setups this candle.")

# ==================== SMART SCHEDULER ====================
def main():
    send_telegram("✅ Master EMA Sniper is LIVE! (Dual Direction, Exact Intersection)")
    print("🚀 Scanner started. Initializing...")
    
    symbols_data = fetch_all_symbols()
    if not symbols_data:
        print("Failed to load symbols. Check your internet connection.")
        return
        
    print(f"✅ Loaded {len(symbols_data)} USDT Perp pairs for 30m scan (Startup).")
    scan_timeframe(symbols_data, "30m", "30M (Startup)")
    
    last_checked_minute = -1
    
    while True:
        now = time.localtime()
        current_minute = now.tm_min
        current_second = now.tm_sec
        current_hour = now.tm_hour
        
        if current_minute != last_checked_minute:
            print(f"⏰ {time.strftime('%H:%M:%S')} - Waiting for next candle close...")
            last_checked_minute = current_minute
        
        if current_second < 15:
            if current_minute == 0:
                symbols_data = fetch_all_symbols()
                if symbols_data:
                    print(f"✅ Loaded {len(symbols_data)} USDT Perp pairs for 30m scan.")
                    scan_timeframe(symbols_data, "30m", "30M")
                    
                    print(f"✅ Loaded {len(symbols_data)} USDT Perp pairs for 1h scan.")
                    scan_timeframe(symbols_data, "1h", "1H")
                    
                    if current_hour == 0:
                        print(f"✅ Loaded {len(symbols_data)} USDT Perp pairs for 1d scan.")
                        scan_timeframe(symbols_data, "1d", "1D")
                
                time.sleep(16)
            
            elif current_minute == 30:
                symbols_data = fetch_all_symbols()
                if symbols_data:
                    print(f"✅ Loaded {len(symbols_data)} USDT Perp pairs for 30m scan.")
                    scan_timeframe(symbols_data, "30m", "30M")
                time.sleep(16)
        
        time.sleep(5)

if __name__ == "__main__":
    main()
