import logging
import pandas as pd
import pandas_ta as ta
import ccxt.async_support as ccxt
import asyncio
import json
import db_utils # لاستيراد نماذج Pydantic
from db_utils import UserSettings, ActiveTradeMonitor
from typing import Dict, Any, Optional

try:
    from scipy.signal import find_peaks
    SCIPY_AVAILABLE = True
except ImportError:
    SCIPY_AVAILABLE = False
    logging.warning("Library 'scipy' not found. RSI Divergence strategy will be disabled.")

logger = logging.getLogger(__name__)

# =======================================================================================
# --- دوال مساعدة (من BN.py) ---
# =======================================================================================

def find_col(df_columns, prefix):
    """
   
    """
    try: 
        return next(col for col in df_columns if col.startswith(prefix))
    except StopIteration: 
        return None

# =======================================================================================
# --- دوال الماسح (من BN.py) ---
#
# =======================================================================================

def analyze_momentum_breakout(df: pd.DataFrame, params: dict, rvol: float, adx_value: float) -> Optional[Dict]:
    df.ta.vwap(append=True)
    df.ta.bbands(length=20, append=True)
    df.ta.macd(append=True)
    df.ta.rsi(append=True)
    
    last, prev = df.iloc[-2], df.iloc[-3]
    macd_col = find_col(df.columns, "MACD_")
    macds_col = find_col(df.columns, "MACDs_")
    bbu_col = find_col(df.columns, "BBU_")
    rsi_col = find_col(df.columns, "RSI_")
    
    if not all([macd_col, macds_col, bbu_col, rsi_col, "VWAP_D" in df.columns]): 
        return None
        
    if (prev[macd_col] <= prev[macds_col] and 
        last[macd_col] > last[macds_col] and 
        last['close'] > last[bbu_col] and 
        last['close'] > last["VWAP_D"] and 
        last[rsi_col] < 68):
        return {"reason": "momentum_breakout"}
    return None

def analyze_breakout_squeeze_pro(df: pd.DataFrame, params: dict, rvol: float, adx_value: float) -> Optional[Dict]:
    df.ta.bbands(length=20, append=True)
    df.ta.kc(length=20, scalar=1.5, append=True)
    df.ta.obv(append=True)
    
    bbu_col = find_col(df.columns, "BBU_")
    bbl_col = find_col(df.columns, "BBL_")
    kcu_col = find_col(df.columns, "KCUe_")
    kcl_col = find_col(df.columns, "KCLEe_")
    
    if not all([bbu_col, bbl_col, kcu_col, kcl_col]): 
        return None
        
    last, prev = df.iloc[-2], df.iloc[-3]
    is_in_squeeze = prev[bbl_col] > prev[kcl_col] and prev[bbu_col] < prev[kcu_col]
    
    if (is_in_squeeze and 
        (last['close'] > last[bbu_col]) and 
        (last['volume'] > df['volume'].rolling(20).mean().iloc[-2] * 1.5) and 
        (df['OBV'].iloc[-2] > df['OBV'].iloc[-3])):
        return {"reason": "breakout_squeeze_pro"}
    return None

async def analyze_support_rebound(df: pd.DataFrame, params: dict, rvol: float, adx_value: float, exchange: ccxt.Exchange, symbol: str) -> Optional[Dict]:
    try:
        ohlcv_1h = await exchange.fetch_ohlcv(symbol, '1h', limit=100)
        if len(ohlcv_1h) < 50: 
            return None
            
        df_1h = pd.DataFrame(ohlcv_1h, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        current_price = df_1h['close'].iloc[-1]
        recent_lows = df_1h['low'].rolling(window=10, center=True).min()
        supports = recent_lows[recent_lows.notna()]
        closest_support = max([s for s in supports if s < current_price], default=None)
        
        if not closest_support or ((current_price - closest_support) / closest_support * 100 > 1.0): 
            return None
            
        last_candle_15m = df.iloc[-2]
        if (last_candle_15m['close'] > last_candle_15m['open'] and 
            last_candle_15m['volume'] > df['volume'].rolling(window=20).mean().iloc[-2] * 1.5):
            return {"reason": "support_rebound"}
    except Exception: 
        return None
    return None

def analyze_sniper_pro(df: pd.DataFrame, params: dict, rvol: float, adx_value: float) -> Optional[Dict]:
    try:
        compression_candles = 24
        if len(df) < compression_candles + 2: 
            return None
            
        compression_df = df.iloc[-compression_candles-1:-1]
        highest_high, lowest_low = compression_df['high'].max(), compression_df['low'].min()
        if lowest_low <= 0: 
            return None
            
        volatility = (highest_high - lowest_low) / lowest_low * 100
        if volatility < 12.0:
            last_candle = df.iloc[-2]
            if (last_candle['close'] > highest_high and 
                last_candle['volume'] > compression_df['volume'].mean() * 2):
                return {"reason": "sniper_pro"}
    except Exception: 
        return None
    return None

async def analyze_whale_radar(df: pd.DataFrame, params: dict, rvol: float, adx_value: float, exchange: ccxt.Exchange, symbol: str) -> Optional[Dict]:
    try:
        ob = await exchange.fetch_order_book(symbol, limit=20)
        if not ob or not ob.get('bids'): 
            return None
        if sum(float(price) * float(qty) for price, qty in ob['bids'][:10]) > 30000:
            return {"reason": "whale_radar"}
    except Exception: 
        return None
    return None

def analyze_rsi_divergence(df: pd.DataFrame, params: dict, rvol: float, adx_value: float) -> Optional[Dict]:
    if not SCIPY_AVAILABLE: 
        return None
        
    rsi_period = params.get('rsi_period', 14)
    lookback = params.get('lookback_period', 35)
    peak_lookback = params.get('peak_trough_lookback', 5)

    df.ta.rsi(length=rsi_period, append=True)
    rsi_col = find_col(df.columns, f"RSI_{rsi_period}")
    if not rsi_col or df[rsi_col].isnull().all(): 
        return None
        
    subset = df.iloc[-lookback:].copy()
    price_troughs_idx, _ = find_peaks(-subset['low'], distance=peak_lookback)
    rsi_troughs_idx, _ = find_peaks(-subset[rsi_col], distance=peak_lookback)
    
    if len(price_troughs_idx) >= 2 and len(rsi_troughs_idx) >= 2:
        p_low1_idx, p_low2_idx = price_troughs_idx[-2], price_troughs_idx[-1]
        r_low1_idx, r_low2_idx = rsi_troughs_idx[-2], rsi_troughs_idx[-1]
        
        is_divergence = (subset.iloc[p_low2_idx]['low'] < subset.iloc[p_low1_idx]['low'] and 
                         subset.iloc[r_low2_idx][rsi_col] > subset.iloc[r_low1_idx][rsi_col])
        
        if is_divergence:
            rsi_exits_oversold = (subset.iloc[r_low1_idx][rsi_col] < 35 and subset.iloc[-2][rsi_col] > 40)
            confirmation_price = subset.iloc[p_low2_idx:]['high'].max()
            price_confirmed = df.iloc[-2]['close'] > confirmation_price
            
            if (not params.get('confirm_with_rsi_exit', True) or rsi_exits_oversold) and price_confirmed:
                return {"reason": "rsi_divergence"}
    return None

def analyze_supertrend_pullback(df: pd.DataFrame, params: dict, rvol: float, adx_value: float) -> Optional[Dict]:
    atr_period = params.get('atr_period', 10)
    atr_mult = params.get('atr_multiplier', 3.0)
    swing_lookback = params.get('swing_high_lookback', 10)

    df.ta.supertrend(length=atr_period, multiplier=atr_mult, append=True)
    st_dir_col = find_col(df.columns, f"SUPERTd_{atr_period}_")
    if not st_dir_col: 
        return None
        
    last, prev = df.iloc[-2], df.iloc[-3]
    if prev[st_dir_col] == -1 and last[st_dir_col] == 1:
        recent_swing_high = df['high'].iloc[-swing_lookback:-2].max()
        if last['close'] > recent_swing_high:
            return {"reason": "supertrend_pullback"}
    return None

# قاموس الماسحات (من BN.py)
SCANNERS_MAP = {
    "momentum_breakout": analyze_momentum_breakout, 
    "breakout_squeeze_pro": analyze_breakout_squeeze_pro,
    "support_rebound": analyze_support_rebound, 
    "sniper_pro": analyze_sniper_pro, 
    "whale_radar": analyze_whale_radar,
    "rsi_divergence": analyze_rsi_divergence, 
    "supertrend_pullback": analyze_supertrend_pullback
}

# =======================================================================================
# --- دوال الرجل الحكيم (من wise_man.py) ---
#
# =======================================================================================

async def wise_man_deep_analysis(trade: ActiveTradeMonitor, settings: UserSettings, exchange: ccxt.Exchange) -> Optional[str]:
    """
    [لقطع الخسائر] تحلل صفقة واحدة بعمق.
    تعيد "force_exit" إذا كان الإغلاق مطلوباً، أو "notify_weak" إذا كان الإغلاق معطلاً.
    """
    symbol = trade.symbol
    logger.info(f"🧠 Wise Man summoned for deep analysis of trade #{trade.id} [{symbol}]...")

    try:
        ohlcv_task = exchange.fetch_ohlcv(symbol, '15m', limit=100)
        btc_ohlcv_task = exchange.fetch_ohlcv('BTC/USDT', '1h', limit=100)
        ohlcv, btc_ohlcv = await asyncio.gather(ohlcv_task, btc_ohlcv_task)

        if not ohlcv:
            logger.warning(f"Wise Man Analysis Canceled: Could not fetch OHLCV for {symbol}.")
            return None

        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['ema_fast'] = ta.ema(df['close'], length=10)
        df['ema_slow'] = ta.ema(df['close'], length=30)
        is_weak = df['close'].iloc[-1] < df['ema_fast'].iloc[-1] and df['close'].iloc[-1] < df['ema_slow'].iloc[-1]

        btc_is_bearish = False
        if btc_ohlcv:
            btc_df = pd.DataFrame(btc_ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            btc_df['btc_momentum'] = ta.mom(btc_df['close'], length=10)
            if not btc_df.empty:
                btc_is_bearish = btc_df['btc_momentum'].iloc[-1] < 0
        
        logger.info(f"Analysis for {symbol}: is_weak={is_weak}, btc_is_bearish={btc_is_bearish}")

        if is_weak and btc_is_bearish:
            if settings.wise_man_auto_close:
                logger.info(f"Wise Man: Recommending FORCE_EXIT for trade #{trade.id}.")
                return "force_exit" # إشارة للعامل بالإغلاق
            else:
                logger.info(f"Wise Man: Recommending NOTIFY_WEAK for trade #{trade.id}.")
                return "notify_weak" # إشارة للعامل بإرسال إشعار فقط
        else:
            logger.info(f"Wise Man Deep Analysis Concluded: No critical weakness found for {symbol}.")
            return None

    except Exception as e:
        logger.error(f"Wise Man: Deep analysis failed for trade #{trade.id}: {e}", exc_info=True)
        return None

async def wise_man_check_momentum(trade: ActiveTradeMonitor, settings: UserSettings, exchange: ccxt.Exchange) -> Optional[float]:
    """
    [لتمديد الأرباح] تتحقق من الزخم.
    تعيد new_tp (float) إذا كان التمديد مطلوباً.
    """
    symbol = trade.symbol
    trade_id = trade.id
    logger.info(f"🧠 Wise Man checking strong momentum for trade #{trade_id} [{symbol}]...")

    try:
        ohlcv = await exchange.fetch_ohlcv(symbol, '15m', limit=50)
        if not ohlcv: 
            return None

        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        adx_data = df.ta.adx()

        if adx_data is None or adx_data.empty: 
            return None
        current_adx = adx_data.iloc[-1]['ADX_14']
        
        # هذه الإعدادات مخصصة، يجب إضافتها إلى user_settings في db_setup
        strong_adx_level = 30 # settings.get('wise_man_strong_adx_level', 30)

        if current_adx > strong_adx_level:
            current_price = df['close'].iloc[-1]
            df.ta.atr(append=True, length=14)
            atr_col = find_col(df.columns, 'ATRr_')
            if not atr_col: 
                return None
            
            atr = df.iloc[-1].get(atr_col, 0)
            new_tp = current_price + (atr * settings.risk_reward_ratio)
            
            if new_tp > trade.take_profit:
                logger.info(f"Wise Man: Recommending TP extension for trade #{trade_id} to {new_tp}.")
                return new_tp # إشارة للعامل بتحديث الهدف
        
        return None

    except Exception as e:
        logger.error(f"Wise Man: Strong momentum check failed for trade #{trade_id}: {e}", exc_info=True)
        return None

# =======================================================================================
# --- دوال العقل الذكي (من smart_engine.py) ---
#
# =======================================================================================

async def smart_engine_capture_snapshot(exchange: ccxt.Exchange, symbol: str) -> dict:
    """يلتقط صورة لحالة المؤشرات الفنية للسوق في لحظة معينة."""
    try:
        ohlcv = await exchange.fetch_ohlcv(symbol, '15m', limit=100)
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        rsi = ta.rsi(df['close'], length=14).iloc[-1]
        adx_data = ta.adx(df['high'], df['low'], df['close'])
        adx = adx_data['ADX_14'].iloc[-1] if adx_data is not None else None
        return {"rsi": round(rsi, 2), "adx": round(adx, 2) if adx is not None else None}
    except Exception as e:
        logger.error(f"Smart Engine: Could not capture market snapshot for {symbol}: {e}")
        return {}

async def smart_engine_what_if_analysis(
    exchange: ccxt.Exchange, 
    closed_trade: Dict[str, Any], 
    settings: UserSettings
) -> Optional[Dict]:
    """
    يحلل سلوك العملة بعد الخروج.
    يعيد قاموساً (dict) يحتوي على النتائج ليتم تسجيلها في قاعدة البيانات.
    """
    trade_id = closed_trade.get('id')
    symbol = closed_trade.get('symbol')
    exit_reason = closed_trade.get('status', '')
    original_tp = closed_trade.get('take_profit')
    risk_reward_ratio = settings.risk_reward_ratio
    analysis_period_candles = 24 #

    logger.info(f"🔬 Smart Engine: Performing 'What-If' analysis for closed trade #{trade_id} ({symbol})...")
    try:
        # الانتظار قليلاً لضمان مرور بعض الوقت قبل التحليل
        await asyncio.sleep(60) 
        
        future_ohlcv = await exchange.fetch_ohlcv(symbol, '15m', limit=analysis_period_candles)
        df_future = pd.DataFrame(future_ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        highest_price_after = df_future['high'].max()
        lowest_price_after = df_future['low'].min()
        
        score = 0
        notes = ""
        
        if '(SL)' in exit_reason or '(Manual)' in exit_reason and closed_trade.get('pnl_usdt', 0) < 0:
            if highest_price_after >= original_tp:
                score = -10
                notes = f"Stop Loss Regret: Price recovered and hit original TP ({original_tp})."
            else:
                score = 10
                notes = f"Good Save: Price continued to drop to {lowest_price_after} after SL."
        
        elif '(TP)' in exit_reason or '(TSL)' in exit_reason:
            missed_profit_pct = ((highest_price_after / original_tp) - 1) * 100 if original_tp > 0 else 0
            if missed_profit_pct > (risk_reward_ratio * 100):
                score = -5
                notes = f"Missed Opportunity: Price rallied an additional {missed_profit_pct:.2f}% after TP."
            elif missed_profit_pct > 1.0:
                score = 5
                notes = f"Good Exit: Price rallied a little more."
            else:
                score = 10
                notes = f"Perfect Exit: Price dropped or stalled after TP."
        
        post_performance_data = {
            "highest_price_after": highest_price_after,
            "lowest_price_after": lowest_price_after,
            "analysis_period_hours": (analysis_period_candles * 15) / 60
        }
        
        logger.info(f"🔬 Analysis complete for trade #{trade_id}. Exit Quality Score: {score}. Notes: {notes}")
        
        return {
            "exit_reason": exit_reason,
            "score": score,
            "post_performance": post_performance_data,
            "notes": notes
        }

    except Exception as e:
        logger.error(f"Smart Engine: 'What-If' analysis failed for trade #{trade_id}: {e}", exc_info=True)
        return None
