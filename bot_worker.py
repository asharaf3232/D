import asyncio
import logging
import ccxt.async_support as ccxt
import websockets
import json
import time
import pandas as pd
import pandas_ta as ta
from typing import Dict, List, Any, Optional
from uuid import UUID

# --- استيراد الوحدات الجديدة ---
import db_utils
import core_logic
from db_utils import UserSettings, TradingVariables, ActiveStrategy, UserKeys, BotSettings

# --- إعداد السجلات ---
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger("BotWorker_V4_Final")

# --- (الثوابت والمخابئ كما هي) ---
SCAN_INTERVAL_SECONDS = 900
SUPERVISOR_INTERVAL_SECONDS = 10
CACHE_SYNC_INTERVAL_SECONDS = 60

PUBLIC_EXCHANGE = ccxt.binance({'enableRateLimit': True, 'options': {'defaultType': 'spot'}})
GLOBAL_ACTIVE_TRADES_CACHE: Dict[str, List[Dict]] = {}
USER_SETTINGS_CACHE: Dict[UUID, TradingVariables] = {}
USER_STRATEGIES_CACHE: Dict[UUID, List[ActiveStrategy]] = {}
USER_EXCHANGE_CACHE: Dict[UUID, ccxt.Exchange] = {}
LAST_DEEP_ANALYSIS_TIME: Dict[int, float] = {}
SCAN_SKIP_NOTIFICATION_CACHE: Dict[UUID, str] = {}


# =======================================================================================
# --- إدارة الاتصالات والمخابئ (Caching) ---
# =======================================================================================

async def get_user_exchange(user_id: UUID) -> Optional[ccxt.Exchange]:
    if user_id in USER_EXCHANGE_CACHE:
        return USER_EXCHANGE_CACHE[user_id]
    keys = await db_utils.get_user_api_keys(user_id)
    if not keys:
        logger.warning(f"WORKER: No valid keys for user {user_id}.")
        return None
    try:
        exchange = ccxt.binance({'apiKey': keys.api_key, 'secret': keys.api_secret, 'enableRateLimit': True, 'options': {'defaultType': 'spot'}})
        await exchange.load_markets()
        USER_EXCHANGE_CACHE[user_id] = exchange
        return exchange
    except Exception as e:
        logger.error(f"WORKER: Failed to create CCXT instance for user {user_id}: {e}")
        await db_utils.set_api_keys_valid(user_id, False)
        if user_id in USER_EXCHANGE_CACHE: del USER_EXCHANGE_CACHE[user_id]
        return None

async def get_user_settings(user_id: UUID) -> Optional[TradingVariables]:
    if user_id in USER_SETTINGS_CACHE:
        return USER_SETTINGS_CACHE[user_id]
    settings = await db_utils.get_user_trading_variables(user_id)
    if settings: USER_SETTINGS_CACHE[user_id] = settings
    return settings

async def get_user_strategies(user_id: UUID) -> List[ActiveStrategy]:
    if user_id in USER_STRATEGIES_CACHE:
        return USER_STRATEGIES_CACHE[user_id]
    strategies = await db_utils.get_user_enabled_strategies(user_id)
    USER_STRATEGIES_CACHE[user_id] = strategies
    return strategies

async def close_all_user_exchanges():
    logger.info("WORKER: Closing all cached user CCXT connections...")
    for exchange in USER_EXCHANGE_CACHE.values():
        try: await exchange.close()
        except Exception: pass
    USER_EXCHANGE_CACHE.clear()

def _clear_inactive_caches(active_user_ids: set):
    global USER_SETTINGS_CACHE, USER_STRATEGIES_CACHE, USER_EXCHANGE_CACHE, SCAN_SKIP_NOTIFICATION_CACHE
    inactive_users = set(USER_EXCHANGE_CACHE.keys()) - active_user_ids
    if not inactive_users: return
    for user_id in inactive_users:
        if user_id in USER_SETTINGS_CACHE: del USER_SETTINGS_CACHE[user_id]
        if user_id in USER_STRATEGIES_CACHE: del USER_STRATEGIES_CACHE[user_id]
        if user_id in SCAN_SKIP_NOTIFICATION_CACHE: del SCAN_SKIP_NOTIFICATION_CACHE[user_id]
        if user_id in USER_EXCHANGE_CACHE:
            asyncio.create_task(USER_EXCHANGE_CACHE[user_id].close())
            del USER_EXCHANGE_CACHE[user_id]
    logger.info(f"CACHE_SYNC: Cleared caches for {len(inactive_users)} inactive users.")

def _remove_trade_from_cache(trade: Dict):
    global GLOBAL_ACTIVE_TRADES_CACHE
    symbol, trade_id = trade['symbol'], trade['id']
    if symbol in GLOBAL_ACTIVE_TRADES_CACHE:
        GLOBAL_ACTIVE_TRADES_CACHE[symbol] = [t for t in GLOBAL_ACTIVE_TRADES_CACHE[symbol] if t['id'] != trade_id]
        if not GLOBAL_ACTIVE_TRADES_CACHE[symbol]: del GLOBAL_ACTIVE_TRADES_CACHE[symbol]

# =======================================================================================
# --- المكون الأول: "العيون" (WebSocket العام) ---
# =======================================================================================

async def run_public_websocket_manager():
    """ "العيون": يراقب كل الصفقات النشطة (لكل المستخدمين) في بث واحد. """
    global GLOBAL_ACTIVE_TRADES_CACHE
    uri = "wss://stream.binance.com:9443/ws/!miniTicker@arr"
    while True:
        try:
            logger.info(f"EYES: Connecting to Binance Public Ticker Stream...")
            async with websockets.connect(uri, ping_interval=180, ping_timeout=60) as ws:
                logger.info(f"EYES: Connected. Monitoring {len(GLOBAL_ACTIVE_TRADES_CACHE)} symbols.")
                async for message in ws:
                    try:
                        data_list = json.loads(message)
                        for data in data_list:
                            symbol = data['s'].replace('USDT', '/USDT')
                            if symbol in GLOBAL_ACTIVE_TRADES_CACHE:
                                price = float(data['c'])
                                trades_to_check = list(GLOBAL_ACTIVE_TRADES_CACHE.get(symbol, []))
                                for trade in trades_to_check:
                                    if trade['status'] != 'active': continue
                                    # 1. التحقق من TP
                                    if price >= trade['take_profit']:
                                        logger.info(f"EYES: Flagging TP for trade #{trade['id']} ({symbol})")
                                        await db_utils.set_trade_status(trade['id'], 'closing_tp')
                                        _remove_trade_from_cache(trade); continue
                                    # 2. التحقق من SL
                                    if price <= trade['stop_loss']:
                                        reason = "closing_tsl" if trade['trailing_sl_active'] else "closing_sl"
                                        logger.info(f"EYES: Flagging {reason} for trade #{trade['id']}")
                                        await db_utils.set_trade_status(trade['id'], reason)
                                        _remove_trade_from_cache(trade); continue
                                    # 3. منطق إدارة الصفقات النشطة (TSL, إشعارات, الحارس)
                                    await _manage_active_trade(trade, price)
                    except Exception as e:
                        logger.error(f"EYES: Error processing message: {e}", exc_info=True)
        except (websockets.exceptions.ConnectionClosed, Exception) as e:
            logger.warning(f"EYES: Connection lost: {e}. Reconnecting in 5s...")
            await asyncio.sleep(5)

async def _manage_active_trade(trade: Dict, price: float):
    """ (V4) دالة مساعدة لـ "العيون": تدير الوقف المتحرك والإشعارات "التافهة". """
    trade_id, user_id = trade['id'], trade['user_id']
    settings = await get_user_settings(user_id)
    if not settings: return

    # 1. تحديث أعلى سعر
    highest_price = max(trade.get('highest_price', 0), price)
    if highest_price > trade.get('highest_price', 0):
        await db_utils.update_trade_highest_price(trade_id, highest_price)
        trade['highest_price'] = highest_price

    # 2. منطق الوقف المتحرك (Trailing SL)
    if settings.trailing_sl_enabled:
        # أ. تفعيل الوقف المتحرك
        if not trade['trailing_sl_active'] and price >= trade['entry_price'] * (1 + settings.trailing_sl_activation_percent / 100):
            new_sl = trade['entry_price'] * 1.001
            if new_sl > trade['stop_loss']:
                await db_utils.update_trade_after_tsl_activation(trade_id, new_sl)
                trade['trailing_sl_active'] = True
                trade['stop_loss'] = new_sl
                logger.info(f"EYES: TSL Activated for trade #{trade_id}. New SL: {new_sl}")
                await db_utils.create_notification(
                    user_id, f"🚀 تأمين الأرباح! | #{trade_id} {trade['symbol']}",
                    f"تم رفع وقف الخسارة إلى نقطة الدخول: ${new_sl:.4f}", "info", trade_id
                )
        # ب. تحديث الوقف المتحرك
        if trade['trailing_sl_active']:
            new_sl_candidate = highest_price * (1 - settings.trailing_sl_callback_percent / 100)
            if new_sl_candidate > trade['stop_loss']:
                await db_utils.update_trade_tsl(trade_id, new_sl_candidate)
                trade['stop_loss'] = new_sl_candidate

    # 3. منطق إشعارات الربح المتزايدة
    increment_pct = 2.0 # (يجب جلب هذا من الإعدادات)
    last_notified = trade.get('last_profit_notification_price', trade['entry_price'])
    if price >= last_notified * (1 + increment_pct / 100):
        await db_utils.update_trade_profit_notification(trade_id, price)
        trade['last_profit_notification_price'] = price
        profit_percent = ((price / trade['entry_price']) - 1) * 100
        logger.info(f"EYES: Incremental profit hit for trade #{trade_id}.")
        await db_utils.create_notification(
            user_id, f"📈 ربح متزايد! | #{trade_id} {trade['symbol']}",
            f"**الربح الحالي:** `{profit_percent:+.2f}%`", "info", trade_id
        )
        # [دمج الرجل الحكيم - حلب العملة]
        cooldown = 900
        last_analysis = LAST_DEEP_ANALYSIS_TIME.get(trade_id, 0)
        if (time.time() - last_analysis) > cooldown:
            LAST_DEEP_ANALYSIS_TIME[trade_id] = time.time()
            asyncio.create_task(_run_wise_man_momentum_check(trade, settings.model_dump()))

    # 4. منطق الحارس الحكيم (Wise Guardian)
    if settings.wise_guardian_enabled and trade.get('highest_price', 0) > 0:
        drawdown_pct = ((price / trade['highest_price']) - 1) * 100
        trigger_pct = -1.5 # (يجب جلب هذا من الإعدادات)
        if drawdown_pct < trigger_pct:
            cooldown = 900
            last_analysis = LAST_DEEP_ANALYSIS_TIME.get(trade_id, 0)
            if (time.time() - last_analysis) > cooldown:
                LAST_DEEP_ANALYSIS_TIME[trade_id] = time.time()
                logger.info(f"EYES: Wise Guardian triggered for trade #{trade_id}. Running deep analysis...")
                asyncio.create_task(_run_wise_man_deep_analysis(trade, settings.model_dump()))

async def sync_cache_from_db():
    """(V4) يقوم بمزامنة ذاكرة التخزين المؤقت لـ "العيون" والإعدادات مع قاعدة البيانات."""
    global GLOBAL_ACTIVE_TRADES_CACHE, USER_SETTINGS_CACHE, USER_STRATEGIES_CACHE
    while True:
        try:
            logger.info("CACHE_SYNC: Syncing active trades and user settings...")
            # [ ⬇️ القفل رقم 2 (V4) ⬇️ ]
            # جلب المستخدمين الذين يريدون التداول + اشتراكهم ساري
            active_users = await db_utils.get_all_active_users()
            active_user_ids = {u.user_id for u in active_users}
            
            # مزامنة الصفقات (فقط للمستخدمين النشطين)
            new_cache = {}
            all_trades_count = 0
            if active_user_ids:
                async with db_utils.db_connection() as conn:
                    all_trades = await conn.fetch("SELECT * FROM trades WHERE status = 'active' AND user_id = ANY($1)", list(active_user_ids))
                for r in all_trades:
                    trade = dict(r)
                    if trade['symbol'] not in new_cache: new_cache[trade['symbol']] = []
                    new_cache[trade['symbol']].append(trade)
                all_trades_count = len(all_trades)
            GLOBAL_ACTIVE_TRADES_CACHE = new_cache
            
            # مسح المخابئ
            _clear_inactive_caches(active_user_ids)
            for user_id in active_user_ids: # إجبار على إعادة التحميل
                if user_id in USER_SETTINGS_CACHE: del USER_SETTINGS_CACHE[user_id]
                if user_id in USER_STRATEGIES_CACHE: del USER_STRATEGIES_CACHE[user_id]

            logger.info(f"CACHE_SYNC: Complete. Monitoring {all_trades_count} trades across {len(active_user_ids)} active users. Caches cleared.")
        except Exception as e:
            logger.error(f"CACHE_SYNC: Failed to sync cache: {e}", exc_info=True)
        await asyncio.sleep(CACHE_SYNC_INTERVAL_SECONDS)

# =======================================================================================
# --- المكون الثاني: "الأيدي" (المشرف السريع) ---
# =======================================================================================

async def run_supervisor():
    """ "الأيدي": يبحث عن الأعلام التي رفعتها "العيون" أو الواجهة وينفذ البيع. """
    while True:
        try:
            async with db_utils.db_connection() as conn:
                flagged_trades = await conn.fetch("SELECT * FROM trades WHERE status LIKE 'closing_%'")
            
            if flagged_trades:
                logger.info(f"HANDS: Found {len(flagged_trades)} trades flagged for closure.")
                for trade_record in flagged_trades:
                    trade = dict(trade_record)
                    reason_code = trade['status']
                    reason_map = {
                        "closing_tp": "جني الأرباح (TP)", "closing_sl": "وقف الخسارة (SL)",
                        "closing_tsl": "وقف الخسارة المتحرك (TSL)", "closing_manual": "إغلاق يدوي (من الواجهة)",
                        "closing_wise_man": "إغلاق (بأمر الرجل الحكيم)",
                    }
                    final_reason = reason_map.get(reason_code, "إغلاق آلي")
                    await _execute_close(trade['user_id'], trade, final_reason)
        except Exception as e:
            logger.error(f"SUPERVISOR: Critical error in main loop: {e}", exc_info=True)
        await asyncio.sleep(SUPERVISOR_INTERVAL_SECONDS)

async def _execute_close(user_id: UUID, trade: Dict, reason: str):
    """ (V4) ينفذ أمر البيع الفعلي ويحدّث قاعدة البيانات. """
    trade_id, symbol = trade['id'], trade['symbol']
    exchange = await get_user_exchange(user_id)
    if not exchange:
        logger.error(f"HANDS: Cannot close trade #{trade_id}. No valid CCXT instance."); await db_utils.set_trade_status(trade_id, 'active'); return
    try:
        quantity_to_sell = float(trade['quantity']) 
        ticker = await PUBLIC_EXCHANGE.fetch_ticker(symbol)
        close_price = ticker['last']
        
        market = await PUBLIC_EXCHANGE.market(symbol)
        min_notional_str = market.get('limits', {}).get('notional', {}).get('min')
        if min_notional_str and (quantity_to_sell * close_price) < float(min_notional_str):
            logger.warning(f"HANDS: Trade #{trade_id} value below MIN_NOTIONAL. Closing as 'dust'.")
            closed_trade_data = await db_utils.close_trade(trade_id, close_price, 0.0)
            await db_utils.create_notification(user_id, f"⚠️ صفقة غير قابلة للبيع | #{trade_id} {symbol}", "قيمة الصفقة أقل من الحد الأدنى للبيع. تم إغلاقها إدارياً.", "warning", trade_id)
            return

        await exchange.create_market_sell_order(symbol, quantity_to_sell)
        pnl = (close_price - trade['entry_price']) * quantity_to_sell
        pnl_percent = (close_price / trade['entry_price'] - 1) * 100
        
        closed_trade_data = await db_utils.close_trade(trade_id, close_price, pnl)
        logger.info(f"HANDS: Successfully closed trade #{trade_id}. PnL: ${pnl:.2f}")
        
        await db_utils.create_notification(
            user_id, f"✅ تم إغلاق الصفقة | {symbol}",
            f"السبب: {reason}\nالربح/الخسارة: ${pnl:+.2f} ({pnl_percent:+.2f}%)",
            "success" if pnl > 0 else "error", trade_id
        )
        
        if closed_trade_data:
            settings = await get_user_settings(user_id)
            if settings and settings.learning_enabled:
                asyncio.create_task(_run_smart_engine_analysis(exchange, closed_trade_data, settings.model_dump()))
    except (ccxt.InvalidOrder, ccxt.InsufficientFunds) as e:
         logger.warning(f"HANDS: Closure for #{trade_id} failed with known error. Retrying: {e}")
         await db_utils.set_trade_status(trade_id, 'active')
    except Exception as e:
        logger.error(f"HANDS: Critical failure closing trade #{trade_id}. Retrying: {e}", exc_info=True)
        await db_utils.set_trade_status(trade_id, 'active')

# =======================================================================================
# --- المكون الثالث: "الماسح" (Scanner) ---
# =======================================================================================

async def run_scanner():
    """ (V4) "الماسح": يلف على جميع المستخدمين النشطين وينفذ الفحص لهم. """
    while True:
        logger.info("SCANNER: Starting new multi-user scan cycle...")
        try:
            # [ ⬇️ القفل رقم 2 (V4) ⬇️ ]
            active_users = await db_utils.get_all_active_users()
            if not active_users:
                logger.info("SCANNER: No active users with valid subscriptions found. Sleeping.")
                await asyncio.sleep(SCAN_INTERVAL_SECONDS); continue
            
            logger.info(f"SCANNER: Found {len(active_users)} active users to scan for.")
            all_tickers = await PUBLIC_EXCHANGE.fetch_tickers()
            tasks = [scan_for_user(user.user_id, all_tickers) for user in active_users]
            await asyncio.gather(*tasks, return_exceptions=True)
        except Exception as e:
            logger.error(f"SCANNER: Critical error in main loop: {e}", exc_info=True)
        
        logger.info(f"SCANNER: Scan cycle complete. Sleeping for {SCAN_INTERVAL_SECONDS}s.")
        await asyncio.sleep(SCAN_INTERVAL_SECONDS)

async def scan_for_user(user_id: UUID, all_tickers: Dict):
    """ (V5) [إصلاح الكنز] ينفذ الفحص مع التحقق من الرصيد والحد الأقصى قبل كل عملية شراء. """
    
    logger.info(f"SCANNER: Starting scan for user {user_id}...")
    scan_start_time = time.time()
    signals_found_count = 0
    trades_opened_count = 0
    analysis_errors_count = 0
    
    try:
        # 1. جلب الإعدادات والاستراتيجيات
        settings = await get_user_settings(user_id)
        strategies = await get_user_strategies(user_id)
        if not settings or not strategies:
            logger.warning(f"SCANNER: No settings or active strategies for user {user_id}."); return
        
        # 2. جلب الاتصال (مرة واحدة)
        user_exchange = await get_user_exchange(user_id)
        if not user_exchange:
            await _notify_scan_skip(user_id, "فشل الفحص: مفاتيح API غير صالحة أو مفقودة."); return
        
        # [ ⬇️ إصلاح الكنز V5 ⬇️ ]
        # 3. جلب الرصيد والحد الأقصى للصفقات (مرة واحدة في البداية)
        try:
            balance = await user_exchange.fetch_balance()
            usdt_balance = balance.get('USDT', {}).get('free', 0.0)
            if usdt_balance < settings.min_trade_amount:
                await _notify_scan_skip(user_id, f"فحص متوقف: الرصيد غير كافٍ (${usdt_balance:,.2f} < ${settings.min_trade_amount:,.2f})."); return
        except Exception as e:
            await _notify_scan_skip(user_id, f"فشل الفحص: لا يمكن جلب الرصيد ({e})."); return

        async with db_utils.db_connection() as conn:
            active_count = await conn.fetchval("SELECT COUNT(*) FROM trades WHERE user_id = $1 AND status = 'active'", user_id)
        available_slots = settings.max_concurrent_trades - active_count
        if available_slots <= 0:
            await _notify_scan_skip(user_id, f"فحص متوقف: تم الوصول للحد الأقصى للصفقات ({active_count})."); return
        # [ ⬆️ نهاية إصلاح الكنز V5 ⬆️ ]

        # 4. فلترة الأسواق (مزاج السوق، F&G، اتجاه BTC)
        if settings.market_mood_filter_enabled:
            fng = 50 # (يجب جلب القيمة الحقيقية)
            if fng < settings.fear_and_greed_threshold:
                await _notify_scan_skip(user_id, f"فحص متوقف: مزاج السوق سلبي (F&G: {fng})."); return
        
        # 5. فلترة الأسواق
        valid_markets = [
            t for t in all_tickers.values() 
            if 'USDT' in t['symbol'] 
            and t.get('quoteVolume', 0) > 1000000
            and t.get('active', True)
        ]
        valid_markets.sort(key=lambda m: m.get('quoteVolume', 0), reverse=True)
        symbols_to_scan = [m['symbol'] for m in valid_markets[:100]]
        if not symbols_to_scan: return

        # 6. جلب بيانات OHLCV
        tasks = [PUBLIC_EXCHANGE.fetch_ohlcv(s, '15m', limit=100) for s in symbols_to_scan]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        ohlcv_data = {symbols_to_scan[i]: results[i] for i in range(len(symbols_to_scan)) if not isinstance(results[i], Exception)}

        # 7. تشغيل الماسحات
        for symbol, ohlcv in ohlcv_data.items():
            # [ ⬇️ إصلاح الكنز V5 ⬇️ ]
            # التحقق من "فتحات الصفقات" المتاحة داخل الحلقة
            if available_slots <= 0:
                logger.info(f"SCANNER ({user_id}): No more available trade slots. Stopping scan for user.")
                break
            
            async with db_utils.db_connection() as conn:
                if await conn.fetchval("SELECT 1 FROM trades WHERE user_id = $1 AND symbol = $2 AND status = 'active' LIMIT 1", user_id, symbol):
                    continue
            try:
                df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
                if len(df) < 50: continue
                
                confirmed_reasons = []
                for strategy in strategies:
                    scanner_name = strategy.strategy_name
                    if not (strategy_func := core_logic.SCANNERS_MAP.get(scanner_name)): continue
                    params = strategy.parameters
                    
                    if scanner_name in ['whale_radar', 'support_rebound']:
                        result = await strategy_func(df.copy(), params, 0, 0, user_exchange, symbol)
                    else:
                        result = strategy_func(df.copy(), params, 0, 0)
                    if result: confirmed_reasons.append(result['reason'])
                
                if confirmed_reasons:
                    signals_found_count += 1
                    logger.info(f"SCANNER: Signal found for user {user_id} on {symbol}!")
                    
                    entry_price = df.iloc[-1]['close']
                    atr = ta.atr(df['high'], df['low'], df['close'], length=14).iloc[-1]
                    if pd.isna(atr) or atr == 0: continue
                    
                    risk = atr * settings.risk_reward_ratio # (يجب استخدام atr_sl_multiplier)
                    stop_loss = entry_price - risk
                    take_profit = entry_price + (risk * settings.risk_reward_ratio)
                    
                    signal = {"symbol": symbol, "entry_price": entry_price, "take_profit": take_profit, "stop_loss": stop_loss, "reason": ' + '.join(set(confirmed_reasons))}
                    
                    # --- [ ⬇️ إصلاح الكنز V5 ⬇️ ] ---
                    # التحقق من الرصيد الفعلي *قبل* الشراء مباشرة
                    required_size = settings.min_trade_amount
                    if usdt_balance < required_size:
                        logger.warning(f"SCANNER ({user_id}): Signal for {symbol}, but skipping. Insufficient balance ({usdt_balance} < {required_size}).")
                        break # إيقاف البحث عن صفقات لهذا المستخدم
                    
                    # الرصيد كافٍ، قم بالشراء
                    if await _execute_buy(user_exchange, user_id, signal, settings):
                        available_slots -= 1
                        trades_opened_count += 1
                        usdt_balance -= required_size # (تحديث الرصيد الوهمي)
                        await asyncio.sleep(1) # إعطاء فرصة لتحديث الرصيد

                    # --- [ ⬆️ نهاية إصلاح الكنز V5 ⬆️ ] ---
            
            except Exception as e:
                logger.error(f"SCANNER: Error processing symbol {symbol} for user {user_id}: {e}")
                analysis_errors_count += 1
                
        # --- إرسال إشعار نجاح الفحص ---
        scan_duration = time.time() - scan_start_time
        await db_utils.create_notification(
            user_id, "✅ فحص السوق اكتمل",
            f"**المدة:** {int(scan_duration)} ثانية | **العملات المفحوصة:** {len(symbols_to_scan)}\n"
            f"**النتائج:**\n"
            f"  - **إشارات جديدة:** {signals_found_count}\n"
            f"  - **صفقات تم فتحها:** {trades_opened_count} صفقة\n"
            f"  - **مشكلات تحليل:** {analysis_errors_count} عملة",
            "info"
        )
        if user_id in SCAN_SKIP_NOTIFICATION_CACHE:
            del SCAN_SKIP_NOTIFICATION_CACHE[user_id]

    except Exception as e:
        logger.error(f"SCANNER: Failed scan for user {user_id}: {e}", exc_info=True)
        await db_utils.create_notification(user_id, "❌ فشل فحص السوق", f"حدث خطأ فادح: {e}", "error")

async def _notify_scan_skip(user_id: UUID, reason: str):
    """(V2.1) يرسل إشعار تخطي الفحص مرة واحدة فقط."""
    if SCAN_SKIP_NOTIFICATION_CACHE.get(user_id) == reason:
        return
    logger.info(f"SCANNER: {reason} (User: {user_id})")
    await db_utils.create_notification(user_id, "⚠️ تم تخطي الفحص", reason, "warning")
    SCAN_SKIP_NOTIFICATION_CACHE[user_id] = reason

async def _execute_buy(exchange: ccxt.Exchange, user_id: UUID, signal: dict, settings: TradingVariables) -> bool:
    """ (V4) ينفذ الشراء ويسجل الصفقة. """
    symbol = signal['symbol']
    trade_size = settings.min_trade_amount
    try:
        market = await PUBLIC_EXCHANGE.market(symbol)
        min_notional_str = market.get('limits', {}).get('notional', {}).get('min')
        if min_notional_str:
            min_notional_value = float(min_notional_str)
            if trade_size < min_notional_value:
                logger.warning(f"BUYER ({user_id}): Trade for {symbol} aborted. Size ({trade_size:.2f}) < Min Notional ({min_notional_value:.2f}).")
                return False
        
        base_amount = trade_size / signal['entry_price']
        formatted_amount = exchange.amount_to_precision(symbol, base_amount)
        buy_order = await exchange.create_market_buy_order(symbol, formatted_amount)
        
        new_trade = await db_utils.create_trade(
            user_id, symbol, signal['reason'], 
            buy_order.get('average', signal['entry_price']),
            buy_order.get('filled', formatted_amount),
            signal['take_profit'], signal['stop_loss'], buy_order['id']
        )
        if new_trade:
            logger.info(f"BUYER ({user_id}): Active trade #{new_trade['id']} created for {symbol}.")
            await db_utils.create_notification(
                user_id, f"✅ تم فتح صفقة جديدة | {symbol}",
                f"الاستراتيجية: {signal['reason']}\nسعر الدخول: ${new_trade['entry_price']:.4f}\nالهدف: ${new_trade['take_profit']:.4f}",
                "success", new_trade['id']
            )
            return True
        else:
            logger.critical(f"BUYER ({user_id}): Failed to log active trade for {symbol}. Cancelling order {buy_order['id']}.")
            await exchange.cancel_order(buy_order['id'], symbol); return False
    except ccxt.InsufficientFunds:
        logger.error(f"BUYER ({user_id}): Insufficient funds for {symbol}."); return False
    except Exception as e:
        logger.error(f"BUYER ({user_id}): Failed to execute buy for {symbol}: {e}", exc_info=True); return False

# =======================================================================================
# --- دوال مساعدة لـ "العقل" (Wise Man & Smart Engine) ---
# =======================================================================================

async def _run_wise_man_deep_analysis(trade: Dict, settings: dict):
    """(V2.1) (تشغيل غير متزامن) ينفذ تحليل الرجل الحكيم لقطع الخسائر."""
    exchange = await get_user_exchange(trade['user_id'])
    if not exchange: return
    result = await core_logic.wise_man_deep_analysis(trade['id'], trade['symbol'], settings, exchange)
    if result == "force_exit":
        await db_utils.set_trade_status(trade['id'], "closing_wise_man")
        _remove_trade_from_cache(trade)
        logger.info(f"WISE_MAN: Force exit signal sent for trade #{trade['id']}.")
        await db_utils.create_notification(
            trade['user_id'], f"🧠 إغلاق آلي | #{trade['id']} {trade['symbol']}",
            "أظهر التحليل العميق ضعفاً حاداً في السوق والعملة.", "warning", trade['id']
        )
    elif result == "notify_weak":
        logger.info(f"WISE_MAN: Weakness detected for trade #{trade['id']}. Auto-close disabled.")
        await db_utils.create_notification(
            trade['user_id'], f"💡 تحذير تكتيكي | #{trade['id']} {trade['symbol']}",
            "رصد ضعف حاد. يُنصح بالخروج اليدوي.", "warning", trade['id']
        )

async def _run_wise_man_momentum_check(trade: Dict, settings: dict):
    """(V2.1) (تشغيل غير متزامن) ينفذ تحليل الرجل الحكيم لتمديد الأرباح."""
    exchange = await get_user_exchange(trade['user_id'])
    if not exchange: return
    new_tp = await core_logic.wise_man_check_momentum(trade, settings, exchange)
    if new_tp and new_tp > trade['take_profit']:
        await db_utils.update_trade_take_profit(trade['id'], new_tp)
        logger.info(f"WISE_MAN: TP extended for trade #{trade['id']} to {new_tp}.")
        await db_utils.create_notification(
            trade['user_id'], f"🧠 تمديد الهدف! | #{trade['id']} {trade['symbol']}",
            f"تم رصد زخم قوي، تم رفع الهدف إلى ${new_tp:.4f}", "info", trade['id']
        )

async def _run_smart_engine_analysis(exchange: ccxt.Exchange, closed_trade: Dict, settings: dict):
    """(V2.1) (تشغيل غير متزامن) ينفذ تحليل "ماذا لو؟"."""
    await asyncio.sleep(60) 
    analysis_results = await core_logic.smart_engine_what_if_analysis(exchange, closed_trade, settings)
    if analysis_results:
        # (يمكن إضافة هذا الجدول لاحقاً)
        # await db_utils.update_trade_journal_exit(...)
        logger.info(f"SMART_ENGINE: 'What-If' analysis saved for trade #{closed_trade['id']}.")

# =======================================================================================
# --- نقطة الدخول الرئيسية للعامل ---
# =======================================================================================

async def main():
    logger.info("--- 🚀 Bot Worker (SaaS Engine V4.0 - Paywall + Treasure Fix) Starting Up... ---")
    await db_utils.get_db_pool()
    await PUBLIC_EXCHANGE.load_markets()
    tasks = [
        run_public_websocket_manager(), # "العيون"
        sync_cache_from_db(),           # مزامنة "العيون" والمخابئ
        run_supervisor(),               # "الأيدي" (إغلاق الصفقات)
        run_scanner()                   # "الماسح" (فتح الصفقات)
    ]
    await asyncio.gather(*tasks)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("--- 🛑 Bot Worker Shutting Down... ---")
    finally:
        asyncio.run(PUBLIC_EXCHANGE.close())
        asyncio.run(close_all_user_exchanges())
        if db_utils.POOL:
            asyncio.run(db_utils.POOL.close())
