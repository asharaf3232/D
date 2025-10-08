# -*- coding: utf-8 -*-
# =======================================================================================
# --- 🧠 Maestro V9.2 (Strategic Protocol Assigner) 🧠 ---
# =======================================================================================
#
# --- سجل التغييرات للإصدار 9.2 (التطوير المعماري) ---
#   ✅ [هيكلة] **تحويل إلى المايسترو:** تم تغيير دور `WiseMan` بشكل جذري. لم يعد يقدم توصيات تكتيكية للصفقات المفتوحة.
#   ✅ [هيكلة] **نظام البروتوكولات:** يقوم الآن بتحليل كل فرصة تداول "قبل" فتحها، ويحدد لها "بروتوكول إدارة" مخصص (1, 2, أو 3).
#   ✅ [تكامل] **تسليم السلطة الكامل:** يتم تسجيل البروتوكول مع الصفقة، ويصبح `TradeGuardian` هو المنفذ الوحيد المسؤول عن تطبيق قواعد هذا البروتوكول لحظيًا.
#
# =======================================================================================

import logging
import aiosqlite
import pandas as pd
import pandas_ta as ta
import ccxt.async_support as ccxt
from telegram.ext import Application
from collections import defaultdict, deque
import asyncio
import time
from datetime import datetime, timezone, timedelta
import os

# --- [تعديل V2.0] إضافة مكتبات جديدة ---
import numpy as np
from smtplib import SMTP
from email.mime.text import MIMEText

try:
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler
    SKLEARN_AVAILABLE = True
except ImportError:
    SKLEARN_AVAILABLE = False
    logging.warning("Scikit-learn not found. ML features will be disabled.")


# --- إعدادات أساسية ---
logger = logging.getLogger(__name__)

# --- قواعد إدارة مخاطر المحفظة ---
PORTFOLIO_RISK_RULES = {
    "max_asset_concentration_pct": 30.0,
    "max_sector_concentration_pct": 50.0,
}
SECTOR_MAP = {
    'RNDR': 'AI', 'FET': 'AI', 'AGIX': 'AI', 'WLD': 'AI', 'OCEAN': 'AI', 'TAO': 'AI',
    'SAND': 'Gaming', 'MANA': 'Gaming', 'GALA': 'Gaming', 'AXS': 'Gaming', 'IMX': 'Gaming', 'APE': 'Gaming',
    'UNI': 'DeFi', 'AAVE': 'DeFi', 'LDO': 'DeFi', 'MKR': 'DeFi', 'CRV': 'DeFi', 'COMP': 'DeFi',
    'SOL': 'Layer 1', 'ETH': 'Layer 1', 'AVAX': 'Layer 1', 'ADA': 'Layer 1', 'NEAR': 'Layer 1', 'SUI': 'Layer 1',
    'MATIC': 'Layer 2', 'ARB': 'Layer 2', 'OP': 'Layer 2', 'STRK': 'Layer 2',
    'ONDO': 'RWA', 'POLYX': 'RWA', 'OM': 'RWA',
    'DOGE': 'Memecoin', 'PEPE': 'Memecoin', 'SHIB': 'Memecoin', 'WIF': 'Memecoin', 'BONK': 'Memecoin',
}

class WiseMan:
    def __init__(self, exchange: ccxt.Exchange, application: Application, bot_data_ref: object, db_file: str):
        self.exchange = exchange
        self.application = application
        self.bot_data = bot_data_ref
        self.db_file = db_file
        self.telegram_chat_id = application.bot_data.get('TELEGRAM_CHAT_ID')
        
        self.ml_model = LogisticRegression() if SKLEARN_AVAILABLE else None
        self.scaler = StandardScaler() if SKLEARN_AVAILABLE else None
        self.model_trained = False
        self.historical_features = deque(maxlen=200) 
        
        self.correlation_cache = {}
        self.request_semaphore = asyncio.Semaphore(3)
        self.entry_event = asyncio.Event()
        
        logger.info("🧠 Wise Man module upgraded to V9.2 'Maestro' model.")

    # ==============================================================================
    # --- 🧠 محرك تعلم الآلة (يعمل أسبوعيًا) 🧠 ---
    # ==============================================================================
    async def train_ml_model(self, context: object = None):
        """تدريب نموذج تعلم الآلة على بيانات الصفقات التاريخية للتنبؤ بالنجاح."""
        if not SKLEARN_AVAILABLE:
            logger.warning("Maestro: Cannot train ML model, scikit-learn is not installed.")
            return

        logger.info("🧠 Maestro: Starting weekly ML model training...")
        features = []
        labels = []
        try:
            async with aiosqlite.connect(self.db_file) as conn:
                conn.row_factory = aiosqlite.Row
                closed_trades = await (await conn.execute("SELECT * FROM trades WHERE status LIKE '%(%' LIMIT 500")).fetchall()

            if len(closed_trades) < 20:
                logger.warning(f"Maestro: Not enough historical data to train ML model (found {len(closed_trades)} trades).")
                return
            
            # Fetch BTC data for trend analysis
            btc_ohlcv = await self.exchange.fetch_ohlcv('BTC/USDT', '1h', limit=1000)
            btc_df = pd.DataFrame(btc_ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            btc_df['timestamp'] = pd.to_datetime(btc_df['timestamp'], unit='ms', utc=True)
            btc_df.set_index('timestamp', inplace=True)
            btc_df['btc_ema_50'] = ta.ema(btc_df['close'], length=50)

            for trade in closed_trades:
                try:
                    trade_time = datetime.fromisoformat(trade['timestamp']).replace(tzinfo=None)
                    ohlcv = await self.exchange.fetch_ohlcv(trade['symbol'], '15m', since=int((trade_time - timedelta(hours=20)).timestamp() * 1000), limit=80)
                    df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
                    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
                    
                    # Find data point closest to trade entry time
                    entry_df = df.iloc[(df['timestamp'] - trade_time).abs().argsort()[:1]]
                    if entry_df.empty: continue

                    adx_data = ta.adx(df['high'], df['low'], df['close'])
                    rsi = ta.rsi(df['close']).iloc[-1]
                    adx = adx_data['ADX_14'].iloc[-1] if adx_data is not None and not adx_data.empty else 25

                    btc_row = btc_df.iloc[btc_df.index.get_loc(pd.to_datetime(trade['timestamp']), method='nearest')]
                    btc_trend = 1 if btc_row['close'] > btc_row['btc_ema_50'] else 0

                    features.append([rsi, adx, btc_trend])
                    is_win = 1 if 'ناجحة' in trade['status'] or 'تأمين' in trade['status'] else 0
                    labels.append(is_win)
                except Exception:
                    continue # Skip trade if data fetching fails
            
            if len(features) < 10:
                logger.warning("Maestro: Could not generate enough features for ML training.")
                return

            X = np.array(features)
            y = np.array(labels)

            X_scaled = self.scaler.fit_transform(X)
            self.ml_model.fit(X_scaled, y)
            self.model_trained = True
            logger.info(f"🧠 Maestro: ML model training complete. Trained on {len(X)} data points.")
        except Exception as e:
            logger.error(f"Maestro: An error occurred during ML model training: {e}", exc_info=True)

    # --- [V9.2] دوال مساعدة لـ "The Maestro" Protocol Assignment ---
    def _determine_market_regime(self, atr_percent: float, adx_value: float) -> str:
        """[V9.2] تحديد نظام السوق بناءً على التقلب والزخم."""
        if atr_percent > 3.0:
            return 'Volatile'
        elif adx_value > 25:
            return 'Trending'
        else:
            return 'Ranging'

    def assign_management_protocol(self, signal_data: dict) -> tuple[int, int]:
        """[V9.2] تعيين البروتوكول الإداري بناءً على نظام النقاط غير القابل للتفاوض."""
        score = 0
        strategy = signal_data.get('strategy', '')
        atr_percent = signal_data.get('atr_percent', 1.0)
        market_regime = self._determine_market_regime(atr_percent, signal_data.get('adx_value', 20))
        win_prob = signal_data.get('win_prob', 0.5)

        # 1. حسب الاستراتيجية
        primary_strategy = strategy.split(' + ')[0]
        if primary_strategy in ['momentum_breakout', 'breakout_squeeze_pro']:
            score += 3
        elif primary_strategy in ['supertrend_pullback', 'rsi_divergence']:
            score += 2
        else:
            score += 1

        # 2. حسب تقلب الأصل (ATR)
        if atr_percent > 3.0:
            score += 3
        elif 1.5 <= atr_percent <= 3.0:
            score += 2
        else:
            score += 1

        # 3. حسب حالة السوق
        if market_regime == 'Volatile':
            score += 3
        elif market_regime == 'Trending':
            score += 2
        else:
            score += 1

        # 4. حسب احتمالية النجاح (ML)
        if win_prob > 0.75:
            score += 3
        elif 0.60 < win_prob <= 0.75:
            score += 2
        else:
            score += 1

        # القرار النهائي
        if score <= 5:
            protocol_id = 1
        elif 6 <= score <= 9:
            protocol_id = 2
        else: # score >= 10
            protocol_id = 3

        return protocol_id, score

    # ==============================================================================
    # --- 🚀 المحرك الرئيسي السريع (يعمل كل 10 ثوانٍ) 🚀 ---
    # ==============================================================================
    async def run_realtime_review(self, context: object = None):
        """المهمة السريعة التي تتخذ قرارات الدخول والخروج اللحظية."""
        await self._review_pending_entries()
        await self._review_pending_exits()

    # ==============================================================================
    # --- 1. منطق "نقطة الدخول الممتازة" (جزء من المحرك السريع) ---
    # ==============================================================================
    async def _review_pending_entries(self):
        """[V9.2] يراجع الفرص، ويعين البروتوكول، ثم يسلمها للتنفيذ."""
        async with aiosqlite.connect(self.db_file) as conn:
            conn.row_factory = aiosqlite.Row
            candidates = await (await conn.execute("SELECT * FROM trade_candidates WHERE status = 'pending'")).fetchall()
            for cand_data in candidates:
                candidate = dict(cand_data)
                symbol = candidate['symbol']
                
                trade_exists = await (await conn.execute("SELECT 1 FROM trades WHERE symbol = ? AND status IN ('active', 'pending')", (symbol,))).fetchone()
                if trade_exists:
                    await conn.execute("UPDATE trade_candidates SET status = 'cancelled_duplicate' WHERE id = ?", (candidate['id'],)); await conn.commit()
                    continue

                try:
                    async with self.request_semaphore:
                        ticker = await self.exchange.fetch_ticker(symbol)
                        ohlcv = await self.exchange.fetch_ohlcv(symbol, '15m', limit=50)
                    
                    current_price = ticker['last']
                    signal_price = candidate['entry_price']
                    if not (0.995 * signal_price <= current_price <= 1.005 * signal_price):
                        if current_price > 1.01 * signal_price:
                            logger.info(f"Maestro cancels {symbol}: Price moved too far.")
                            await conn.execute("UPDATE trade_candidates SET status = 'cancelled_price_moved' WHERE id = ?", (candidate['id'],))
                        elif time.time() - datetime.fromisoformat(candidate['timestamp']).timestamp() > 180:
                            logger.info(f"Maestro cancels {symbol}: Candidate expired.")
                            await conn.execute("UPDATE trade_candidates SET status = 'cancelled_expired' WHERE id = ?", (candidate['id'],))
                        await conn.commit()
                        continue

                    df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
                    atr = ta.atr(df['high'], df['low'], df['close'], length=14).iloc[-1]
                    atr_percent = (atr / current_price) * 100
                    
                    # --- [V9.2] جمع كل البيانات اللازمة لقرار المايسترو ---
                    adx_data = ta.adx(df['high'], df['low'], df['close'])
                    adx_value = adx_data['ADX_14'].iloc[-1] if adx_data is not None and not adx_data.empty else 25

                    correlation = await self._get_correlation(symbol, df)
                    if correlation > 0.8:
                        logger.warning(f"Maestro rejects {symbol}: High correlation with BTC ({correlation:.2f}).")
                        await conn.execute("UPDATE trade_candidates SET status = 'rejected_correlation' WHERE id = ?", (candidate['id'],)); await conn.commit()
                        continue

                    win_prob = 0.5
                    if self.model_trained:
                        btc_ohlcv = await self.exchange.fetch_ohlcv('BTC/USDT', '1h', limit=51)
                        btc_df = pd.DataFrame(btc_ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
                        btc_ema_50 = ta.ema(btc_df['close'], length=50).iloc[-1]
                        btc_trend = 1 if btc_df['close'].iloc[-1] > btc_ema_50 else 0
                        
                        rsi = ta.rsi(df['close']).iloc[-1]
                        
                        current_features = np.array([[rsi, adx_value, btc_trend]])
                        scaled_features = self.scaler.transform(current_features)
                        win_prob = self.ml_model.predict_proba(scaled_features)[0][1]
                        candidate['win_prob'] = win_prob

                        if win_prob < self.bot_data.settings.get('min_win_probability', 0.6):
                            logger.warning(f"Maestro rejects {symbol}: Low ML win probability ({win_prob:.2f}).")
                            await conn.execute("UPDATE trade_candidates SET status = 'rejected_ml_prob' WHERE id = ?", (candidate['id'],)); await conn.commit()
                            continue
                    
                    # --- [V9.2] هنا يتخذ المايسترو قراره الاستراتيجي ---
                    maestro_input = {
                        'strategy': candidate['reason'],
                        'atr_percent': atr_percent,
                        'adx_value': adx_value,
                        'win_prob': win_prob
                    }
                    protocol_id, score = self.assign_management_protocol(maestro_input)
                    candidate['management_protocol'] = protocol_id
                    candidate['protocol_score'] = score
                    logger.info(f"Maestro assigned Protocol {protocol_id} to {symbol} with score {score}.")

                    # 5. Final Confirmation & Handover to Execution
                    logger.info(f"Maestro confirms entry for {symbol}. Handing over to execution engine.")
                    from okx_maestro import initiate_real_trade 
                    if await initiate_real_trade(candidate, self.bot_data.settings, self.exchange, self.application.bot):
                        await conn.execute("UPDATE trade_candidates SET status = 'executed' WHERE id = ?", (candidate['id'],))
                    else:
                        await conn.execute("UPDATE trade_candidates SET status = 'failed_execution' WHERE id = ?", (candidate['id'],))
                    await conn.commit()
                    await asyncio.sleep(1)
                except Exception as e:
                    logger.error(f"Maestro: Error reviewing entry candidate for {symbol}: {e}", exc_info=True)
                    await conn.execute("UPDATE trade_candidates SET status = 'error' WHERE id = ?", (candidate['id'],)); await conn.commit()

    # ==============================================================================
    # --- 2. منطق "نقطة الخروج الرائعة" (جزء من المحرك السريع) ---
    # ==============================================================================
    async def _review_pending_exits(self):
        """يراجع طلبات الخروج مع الأخذ في الاعتبار مشاعر السوق."""
        async with aiosqlite.connect(self.db_file) as conn:
            conn.row_factory = aiosqlite.Row
            trades_to_review = await (await conn.execute("SELECT * FROM trades WHERE status = 'pending_exit_confirmation'")).fetchall()
            if not trades_to_review: return

            # --- [تعديل V2.0] جلب مشاعر السوق مرة واحدة لجميع المراجعات
            from okx_maestro import get_fundamental_market_mood
            mood_result = await get_fundamental_market_mood()
            is_negative_mood = mood_result['mood'] in ["NEGATIVE", "DANGEROUS"]

            for trade_data in trades_to_review:
                trade = dict(trade_data)
                symbol = trade['symbol']
                try:
                    async with self.request_semaphore:
                        ohlcv = await self.exchange.fetch_ohlcv(symbol, '1m', limit=20)
                    df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
                    df['ema_9'] = ta.ema(df['close'], length=9)
                    current_price = df['close'].iloc[-1]
                    last_ema = df['ema_9'].iloc[-1]
                    
                    # --- [تعديل V2.0] تشديد شرط الخروج إذا كانت المشاعر سلبية
                    # نجعل الخروج أكثر حساسية (يحدث أسرع) عندما يكون المزاج سيئًا
                    exit_threshold = last_ema
                    if is_negative_mood:
                        exit_threshold *= 0.998 # A tighter stop: exit even if price is only slightly below EMA
                        logger.info(f"Maestro: Negative market mood detected. Tightening SL for {symbol}.")
                    
                    if current_price < exit_threshold:
                        logger.warning(f"Maestro confirms exit for {symbol}. Momentum is weak. Closing trade #{trade['id']}.")
                        await self.bot_data.trade_guardian._close_trade(trade, "فاشلة (بقرار حكيم)", current_price)
                    else:
                        logger.info(f"Maestro cancels exit for {symbol}. Price recovered. Resetting status to active for trade #{trade['id']}.")
                        from okx_maestro import safe_send_message
                        message = f"✅ **إلغاء الخروج | #{trade['id']} {symbol}**\nقرر الرجل الحكيم إعطاء الصفقة فرصة أخرى بعد تعافي السعر لحظيًا."
                        await safe_send_message(self.application.bot, message)
                        await conn.execute("UPDATE trades SET status = 'active' WHERE id = ?", (trade['id'],))
                        await conn.commit()
                except Exception as e:
                    logger.error(f"Maestro: Error making final exit decision for {symbol}: {e}. Forcing closure.", exc_info=True)
                    await self.bot_data.trade_guardian._close_trade(trade, "فاشلة (خطأ في المراجعة)", trade['stop_loss'])

    # ==============================================================================
    # --- 🎼 المايسترو التكتيكي (يعمل كل 15 دقيقة) 🎼 ---
    # ==============================================================================
    async def review_active_trades_with_tactics(self, context: object = None):
        """
        [V9.2] تم تعديل هذه الدالة لتخدم البروتوكول 2 فقط (الحارس الديناميكي).
        """
        logger.info("🧠 Maestro: Running tactical review for Protocol 2 trades...")
        async with self.bot_data.trade_management_lock:
            async with aiosqlite.connect(self.db_file) as conn:
                conn.row_factory = aiosqlite.Row
                # --- [V9.2] التعديل الحاسم: اختر فقط الصفقات التي تتبع البروتوكول 2 ---
                protocol_2_trades = await (await conn.execute("SELECT * FROM trades WHERE status = 'active' AND management_protocol = 2")).fetchall()

                if not protocol_2_trades:
                    logger.info("Maestro: No active Protocol 2 trades to review.")
                    return

                try:
                    # جلب بيانات البيتكوين مرة واحدة لتقليل طلبات API
                    async with self.request_semaphore:
                        btc_ohlcv = await self.exchange.fetch_ohlcv('BTC/USDT', '1h', limit=20)
                    btc_df = pd.DataFrame(btc_ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
                    btc_momentum_is_negative = ta.mom(btc_df['close'], length=10).iloc[-1] < 0
                except Exception:
                    btc_momentum_is_negative = False

                for trade_data in protocol_2_trades:
                    trade = dict(trade_data)
                    symbol = trade['symbol']
                    try:
                        async with self.request_semaphore:
                            ohlcv = await self.exchange.fetch_ohlcv(symbol, '15m', limit=50)
                        if not ohlcv:
                            continue
                        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
                        current_price = df['close'].iloc[-1]

                        # --- المنطق الأول: الخروج الاستباقي من الصفقات الضعيفة والعالقة ---
                        trade_open_time = datetime.fromisoformat(trade['timestamp'])
                        minutes_since_open = (datetime.now(timezone.utc).astimezone(trade_open_time.tzinfo) - trade_open_time).total_seconds() / 60
                        
                        if minutes_since_open > 45:
                            df['ema_slow'] = ta.ema(df['close'], length=30)
                            if current_price < (df['ema_slow'].iloc[-1] * 0.995) and btc_momentum_is_negative and current_price < trade['entry_price']:
                                logger.warning(f"Maestro proactively detected SUSTAINED weakness in {symbol}. Requesting exit.")
                                await conn.execute("UPDATE trades SET status = 'pending_exit_confirmation' WHERE id = ?", (trade['id'],))
                                await conn.commit()
                                from okx_maestro import safe_send_message
                                await safe_send_message(self.application.bot, f"🧠 **إنذار ضعف! | #{trade['id']} {symbol}**\nرصد الرجل الحكيم ضعفًا مستمرًا، جاري مراجعة الخروج.")
                                continue # ننتقل للصفقة التالية بعد طلب الخروج

                        # --- المنطق الثاني: التمديد والتأمين الذكي المتدرج للصفقات القوية ---
                        settings = self.bot_data.settings
                        if not settings.get('trailing_sl_enabled', True):
                            continue

                        strong_adx_level = settings.get('wise_man_strong_adx_level', 30)
                        
                        # نسبة الاقتراب من الهدف المطلوبة لتفعيل التمديد (يمكن إضافتها للإعدادات)
                        PROXIMITY_PERCENT = 0.98  # يعني عندما يصل السعر إلى 98% من الهدف

                        # الشرط الجديد: هل السعر اقترب من الهدف الحالي؟
                        price_is_near_target = current_price >= (trade['take_profit'] * PROXIMITY_PERCENT)

                        if price_is_near_target:
                            adx_data = ta.adx(df['high'], df['low'], df['close'])
                            current_adx = adx_data['ADX_14'].iloc[-1] if adx_data is not None and not adx_data.empty else 0

                            # شرط الزخم لا يزال مطلوبًا
                            if current_adx > strong_adx_level:
                                # --- المنطق الجديد لتحديد الأهداف والوقف ---
                                previous_tp = trade['take_profit']
                                
                                # 1. الهدف الجديد يكون أعلى من الهدف السابق بنسبة 5% (يمكن تعديلها)
                                new_tp = previous_tp * 1.05
                                
                                # 2. الوقف الجديد هو الهدف السابق (أو تحته بـ 1% للأمان من الانزلاق السعري)
                                new_sl = previous_tp * 0.99

                                # 3. Post recommendation for TradeGuardian
                                self.bot_data.trade_update_recommendations[trade['id']] = {
                                    'new_tp': new_tp,
                                    'new_sl': new_sl,
                                    'entry_price': trade['entry_price']
                                }
                                logger.info(f"Maestro recommended TP extension to {new_tp} and SL to {new_sl} for trade #{trade['id']}")

                        await asyncio.sleep(2) # فاصل بسيط بين معالجة كل صفقة
                    except Exception as e:
                        logger.error(f"Maestro: Error during tactical review for {symbol}: {e}", exc_info=True)
    # ==============================================================================
    # --- ♟️ المدير الاستراتيجي (يعمل كل ساعة) ♟️ ---
    # ==============================================================================
    async def review_portfolio_risk(self, context: object = None):
        """يراجع مخاطر المحفظة (تركيز الأصول، القطاعات، والارتباط) ويرسل تنبيهات."""
        logger.info("🧠 Maestro: Starting portfolio risk review...")
        alerts = []
        try:
            async with self.request_semaphore:
                balance = await self.exchange.fetch_balance()
            assets = {a: d['total'] for a, d in balance.items() if isinstance(d, dict) and d.get('total', 0) > 1e-5 and a != 'USDT'}
            if not assets: return
            
            asset_list = [f"{asset}/USDT" for asset in assets.keys()]
            async with self.request_semaphore:
                tickers = await self.exchange.fetch_tickers(asset_list)
            
            total_portfolio_value = balance.get('USDT', {}).get('total', 0.0)
            asset_values = {}
            for asset, amount in assets.items():
                symbol = f"{asset}/USDT"
                if symbol in tickers and tickers[symbol] and tickers[symbol]['last'] is not None:
                    value_usdt = amount * tickers[symbol]['last']
                    if value_usdt > 1.0:
                        asset_values[asset] = value_usdt
                        total_portfolio_value += value_usdt
            if total_portfolio_value < 1.0: return
            
            # 1. Asset Concentration Check
            for asset, value in asset_values.items():
                concentration = (value / total_portfolio_value) * 100
                if concentration > PORTFOLIO_RISK_RULES['max_asset_concentration_pct']:
                    alerts.append(f"High Asset Concentration: `{asset}` is **{concentration:.1f}%** of portfolio.")
            
            # 2. Sector Concentration Check
            sector_values = defaultdict(float)
            for asset, value in asset_values.items():
                sector_values[SECTOR_MAP.get(asset, 'Other')] += value
            for sector, value in sector_values.items():
                concentration = (value / total_portfolio_value) * 100
                if concentration > PORTFOLIO_RISK_RULES['max_sector_concentration_pct']:
                    alerts.append(f"High Sector Concentration: '{sector}' sector is **{concentration:.1f}%** of portfolio.")
            
            # 3. Correlation Check for major holdings
            major_holdings = sorted(asset_values.items(), key=lambda item: item[1], reverse=True)[:3]
            for asset, value in major_holdings:
                correlation = await self._get_correlation(f"{asset}/USDT")
                if correlation > 0.9:
                    alerts.append(f"High Correlation Warning: `{asset}` has a very high correlation of **{correlation:.2f}** with BTC.")
            
            if alerts:
                from okx_maestro import safe_send_message
                message_body = "\n- ".join(alerts)
                message = f"⚠️ **تنبيه من الرجل الحكيم (إدارة المخاطر):**\n- {message_body}"
                await safe_send_message(self.application.bot, message)
                await self._send_email_alert("Maestro: Portfolio Risk Warning", message.replace('`', '').replace('*', ''))

        except Exception as e:
            logger.error(f"Maestro: Error during portfolio risk review: {e}", exc_info=True)
            
    # ==============================================================================
    # --- 🛠️ دوال مساعدة 🛠️ ---
    # ==============================================================================
    async def _get_correlation(self, symbol: str, df_symbol: pd.DataFrame = None) -> float:
        """يحسب الارتباط بين عملة و BTC، مع استخدام ذاكرة تخزين مؤقتة."""
        now = time.time()
        if symbol in self.correlation_cache and (now - self.correlation_cache[symbol]['timestamp'] < 3600):
            return self.correlation_cache[symbol]['value']
        try:
            async with self.request_semaphore:
                if df_symbol is None:
                    ohlcv_symbol = await self.exchange.fetch_ohlcv(symbol, '1h', limit=100)
                    df_symbol = pd.DataFrame(ohlcv_symbol, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
                
                ohlcv_btc = await self.exchange.fetch_ohlcv('BTC/USDT', '1h', limit=100)
                df_btc = pd.DataFrame(ohlcv_btc, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            
            correlation = df_symbol['close'].corr(df_btc['close'])
            self.correlation_cache[symbol] = {'timestamp': now, 'value': correlation}
            return correlation
        except Exception as e:
            logger.error(f"Maestro: Could not calculate correlation for {symbol}: {e}")
            return 0.5 # Return neutral value on error

    async def _send_email_alert(self, subject: str, body: str):
        """يرسل تنبيهًا عبر البريد الإلكتروني."""
        smtp_user = os.getenv('SMTP_USER')
        smtp_pass = os.getenv('SMTP_PASSWORD')
        smtp_server = os.getenv('SMTP_SERVER')
        smtp_port = os.getenv('SMTP_PORT')
        recipient = os.getenv('RECIPIENT_EMAIL')

        if not all([smtp_user, smtp_pass, smtp_server, smtp_port, recipient]):
            logger.warning("Maestro: Email credentials not fully configured. Skipping email alert.")
            return

        msg = MIMEText(body)
        msg['Subject'] = subject
        msg['From'] = smtp_user
        msg['To'] = recipient

        try:
            with SMTP(smtp_server, int(smtp_port)) as server:
                server.starttls()
                server.login(smtp_user, smtp_pass)
                server.send_message(msg)
            logger.info(f"Maestro: Successfully sent email alert: '{subject}'")
        except Exception as e:
            logger.error(f"Maestro: Failed to send email alert: {e}", exc_info=True)
