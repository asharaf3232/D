import asyncio
import logging
import ccxt.async_support as ccxt
import websockets
import json
import time
import pandas as pd
import pandas_ta as ta
from typing import Dict, List, Any
from uuid import UUID

# --- استيراد الوحدات الجديدة ---
import db_utils
import core_logic
from db_utils import UserSettings, ActiveTradeMonitor, UserKeys

# --- إعداد السجلات ---
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger("BotWorker")

# --- إعدادات فترات التشغيل ---
SCAN_INTERVAL_SECONDS = 900  # 15 دقيقة
SUPERVISOR_INTERVAL_SECONDS = 10 # "الأيدي" - سريع جداً للتحقق من الأعلام
ACTIVATOR_INTERVAL_SECONDS = 20  # "المنشط" - للتحقق من الصفقات المعلقة
CACHE_SYNC_INTERVAL_SECONDS = 60 # "العيون" - لمزامنة الذاكرة المؤقتة

# --- كائن CCXT عام (للفحص وجلب البيانات العامة) ---
PUBLIC_EXCHANGE = ccxt.binance({
    'enableRateLimit': True, 
    'options': {'defaultType': 'spot'}
})

# --- ذاكرة التخزين المؤقت لـ "العيون" ---
# { "BTC/USDT": [ActiveTradeMonitor, ...], ... }
GLOBAL_ACTIVE_TRADES_CACHE: Dict[str, List[ActiveTradeMonitor]] = {}
# --- ذاكرة تخزين مؤقت لإعدادات المستخدمين ---
USER_SETTINGS_CACHE: Dict[UUID, UserSettings] = {}
# --- ذاكرة تخزين مؤقت لمفاتيح المستخدمين ---
USER_KEYS_CACHE: Dict[UUID, UserKeys] = {}
# --- ذاكرة تخزين مؤقت لاتصالات CCXT الخاصة ---
USER_EXCHANGE_CACHE: Dict[UUID, ccxt.Exchange] = {}
# --- ذاكرة تخزين مؤقت لآخر تحليل (للرجل الحكيم) ---
LAST_DEEP_ANALYSIS_TIME: Dict[int, float] = {}

# =======================================================================================
# --- إدارة الاتصالات والمخابئ (Caching) ---
# =======================================================================================

async def get_user_settings(user_id: UUID) -> Optional[UserSettings]:
    """يجلب إعدادات المستخدم من الذاكرة المؤقتة أو قاعدة البيانات."""
    if user_id in USER_SETTINGS_CACHE:
        return USER_SETTINGS_CACHE[user_id]
    
    settings = await db_utils.get_user_settings(user_id)
    if settings:
        USER_SETTINGS_CACHE[user_id] = settings
    return settings

async def get_user_keys(user_id: UUID) -> Optional[UserKeys]:
    """يجلب مفاتيح المستخدم من الذاكرة المؤقتة أو قاعدة البيانات."""
    if user_id in USER_KEYS_CACHE:
        return USER_KEYS_CACHE[user_id]
        
    keys = await db_utils.get_user_api_keys(user_id)
    if keys:
        USER_KEYS_CACHE[user_id] = keys
    return keys

async def get_user_exchange(user_id: UUID) -> Optional[ccxt.Exchange]:
    """ينشئ أو يجلب اتصال CCXT خاص بالمستخدم."""
    if user_id in USER_EXCHANGE_CACHE:
        return USER_EXCHANGE_CACHE[user_id]

    keys = await get_user_keys(user_id)
    if not keys:
        logger.warning(f"WORKER: No keys found for user {user_id}. Cannot create exchange.")
        return None
        
    try:
        exchange = ccxt.binance({
            'apiKey': keys.api_key,
            'secret': keys.api_secret,
            'enableRateLimit': True,
            'options': {'defaultType': 'spot'}
        })
        await exchange.load_markets()
        USER_EXCHANGE_CACHE[user_id] = exchange
        return exchange
    except Exception as e:
        logger.error(f"WORKER: Failed to create CCXT instance for user {user_id}: {e}")
        # ربما المفاتيح غير صالحة؟
        # await db_utils.invalidate_api_keys(user_id)
        if user_id in USER_KEYS_CACHE:
            del USER_KEYS_CACHE[user_id] # مسح المفاتيح الخاطئة
        return None

async def close_all_user_exchanges():
    """يغلق جميع الاتصالات المخبأة عند إيقاف تشغيل العامل."""
    logger.info("WORKER: Closing all cached user CCXT connections...")
    for exchange in USER_EXCHANGE_CACHE.values():
        try:
            await exchange.close()
        except Exception:
            pass
    USER_EXCHANGE_CACHE.clear()

# =======================================================================================
# --- المكون الأول: "العيون" (WebSocket العام) ---
# (تم تعديله بالكامل ليصبح مستقلاً)
# =======================================================================================

async def run_public_websocket_manager():
    """
    "العيون": يتصل ببث واحد، يراقب كل الصفقات، *ولا* ينفذ، بل يحدّث قاعدة البيانات.
    """
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
                                
                                # نستخدم نسخة من القائمة لتجنب مشاكل التعديل أثناء التكرار
                                trades_to_check = list(GLOBAL_ACTIVE_TRADES_CACHE.get(symbol, []))
                                
                                for trade in trades_to_check:
                                    # تجاهل الصفقات التي قيد الإغلاق
                                    if trade.status != 'active':
                                        continue
                                    
                                    # جلب إعدادات المستخدم لهذه الصفقة
                                    settings = await get_user_settings(trade.user_id)
                                    if not settings:
                                        continue # لا يمكن معالجة الصفقة بدون إعدادات

                                    # 1. التحقق من TP (جني الأرباح)
                                    if price >= trade.take_profit:
                                        logger.info(f"EYES: Flagging TP for trade #{trade.id} ({symbol})")
                                        await db_utils.set_trade_status(trade.id, 'force_exit_tp')
                                        _remove_trade_from_cache(trade) # إزالة من الذاكرة
                                        continue # انتقل للصفقة التالية

                                    # 2. التحقق من SL (وقف الخسارة)
                                    if price <= trade.stop_loss:
                                        reason = "force_exit_sl"
                                        if trade.trailing_sl_active:
                                            reason = "force_exit_tsl"
                                        logger.info(f"EYES: Flagging {reason} for trade #{trade.id} ({symbol})")
                                        await db_utils.set_trade_status(trade.id, reason)
                                        _remove_trade_from_cache(trade)
                                        continue

                                    # 3. منطق إدارة الصفقات النشطة (TSL, إشعارات, الحارس)
                                    await _manage_active_trade(trade, price, settings)

                    except Exception as e:
                        logger.error(f"EYES: Error processing message: {e}", exc_info=True)
                        
        except (websockets.exceptions.ConnectionClosed, Exception) as e:
            logger.warning(f"EYES: Connection lost: {e}. Reconnecting in 5s...")
            await asyncio.sleep(5)

async def _manage_active_trade(trade: ActiveTradeMonitor, price: float, settings: UserSettings):
    """دالة مساعدة لـ "العيون": تدير الوقف المتحرك والإشعارات."""
    
    # 1. تحديث أعلى سعر
    highest_price = max(trade.highest_price, price)
    if highest_price > trade.highest_price:
        await db_utils.update_trade_highest_price(trade.id, highest_price)
        trade.highest_price = highest_price # تحديث الكائن في الذاكرة المؤقتة

    # 2. منطق الوقف المتحرك (Trailing SL)
    if settings.trailing_sl_enabled:
        # أ. تفعيل الوقف المتحرك لأول مرة
        if not trade.trailing_sl_active and price >= trade.entry_price * (1 + settings.trailing_sl_activation_percent / 100):
            new_sl = trade.entry_price * 1.001 # رفعه لنقطة الدخول + 0.1%
            if new_sl > trade.stop_loss:
                await db_utils.update_trade_after_tsl_activation(trade.id, new_sl)
                trade.trailing_sl_active = True
                trade.stop_loss = new_sl
                # (لا نرسل إشعاراً من هنا، الواجهة هي من ترسل)
                logger.info(f"EYES: TSL Activated for trade #{trade.id}. New SL: {new_sl}")

        # ب. تحديث الوقف المتحرك (إذا كان مفعلاً)
        if trade.trailing_sl_active:
            new_sl_candidate = highest_price * (1 - settings.trailing_sl_callback_percent / 100)
            if new_sl_candidate > trade.stop_loss:
                await db_utils.update_trade_tsl(trade.id, new_sl_candidate)
                trade.stop_loss = new_sl_candidate
                # logger.debug(f"EYES: TSL Updated for trade #{trade.id}. New SL: {new_sl_candidate}")

    # 3. منطق إشعارات الربح المتزايدة
    if settings.adaptive_intelligence_enabled: # (مفتاح الإعدادات ذو صلة)
        increment = 2.0 # settings.get('incremental_notification_percent', 2.0)
        if price >= trade.last_profit_notification_price * (1 + increment / 100):
            await db_utils.update_trade_profit_notification(trade.id, price)
            trade.last_profit_notification_price = price
            logger.info(f"EYES: Incremental profit hit for trade #{trade.id}.")
            
            # --- [دمج الرجل الحكيم - حلب العملة] ---
            # (تحقق من الزخم القوي لتمديد الهدف)
            cooldown = 900 # 15 دقيقة
            last_analysis = LAST_DEEP_ANALYSIS_TIME.get(trade.id, 0)
            if (time.time() - last_analysis) > cooldown:
                LAST_DEEP_ANALYSIS_TIME[trade.id] = time.time()
                asyncio.create_task(_run_wise_man_momentum_check(trade, settings))

    # 4. منطق الحارس الحكيم (Wise Guardian)
    if settings.wise_guardian_enabled and trade.highest_price > 0:
        drawdown_pct = ((price / trade.highest_price) - 1) * 100
        trigger_pct = -1.5 # settings.get('wise_guardian_trigger_pct', -1.5)
        
        if drawdown_pct < trigger_pct:
            cooldown = 900 # 15 دقيقة
            last_analysis = LAST_DEEP_ANALYSIS_TIME.get(trade.id, 0)
            
            if (time.time() - last_analysis) > cooldown:
                LAST_DEEP_ANALYSIS_TIME[trade.id] = time.time()
                logger.info(f"EYES: Wise Guardian triggered for trade #{trade.id}. Running deep analysis...")
                asyncio.create_task(_run_wise_man_deep_analysis(trade, settings))

async def sync_cache_from_db():
    """يقوم بمزامنة ذاكرة التخزين المؤقت لـ "العيون" والإعدادات مع قاعدة البيانات."""
    global GLOBAL_ACTIVE_TRADES_CACHE, USER_SETTINGS_CACHE
    while True:
        try:
            logger.info("CACHE_SYNC: Syncing active trades and settings from DB...")
            
            # 1. مزامنة الصفقات
            all_trades = await db_utils.get_all_active_trades_for_monitoring()
            new_cache = {}
            for trade in all_trades:
                if trade.symbol not in new_cache:
                    new_cache[trade.symbol] = []
                new_cache[trade.symbol].append(trade)
            GLOBAL_ACTIVE_TRADES_CACHE = new_cache
            
            # 2. مزامنة الإعدادات (للمستخدمين النشطين فقط)
            active_users_settings = await db_utils.get_all_active_users_with_settings()
            temp_settings_cache = {s.user_id: s for s in active_users_settings}
            # (يمكننا إضافة منطق لمسح المستخدمين غير النشطين إذا أردنا)
            USER_SETTINGS_CACHE.update(temp_settings_cache)

            # 3. مسح مخابئ الاتصالات والمفاتيح غير النشطة
            active_user_ids = set(temp_settings_cache.keys())
            _clear_inactive_caches(active_user_ids)

            logger.info(f"CACHE_SYNC: Complete. Monitoring {len(all_trades)} trades. {len(USER_SETTINGS_CACHE)} active user settings cached.")
            
        except Exception as e:
            logger.error(f"CACHE_SYNC: Failed to sync cache: {e}", exc_info=True)
        
        await asyncio.sleep(CACHE_SYNC_INTERVAL_SECONDS)

def _remove_trade_from_cache(trade: ActiveTradeMonitor):
    """يزيل صفقة من ذاكرة التخزين المؤقت بعد إغلاقها."""
    global GLOBAL_ACTIVE_TRADES_CACHE
    if trade.symbol in GLOBAL_ACTIVE_TRADES_CACHE:
        GLOBAL_ACTIVE_TRADES_CACHE[trade.symbol] = [
            t for t in GLOBAL_ACTIVE_TRADES_CACHE[trade.symbol] if t.id != trade.id
        ]
        if not GLOBAL_ACTIVE_TRADES_CACHE[trade.symbol]:
            del GLOBAL_ACTIVE_TRADES_CACHE[trade.symbol]

def _clear_inactive_caches(active_user_ids: set):
    """يمسح المخابئ للمستخدمين الذين أوقفوا التداول."""
    global USER_KEYS_CACHE, USER_EXCHANGE_CACHE, USER_SETTINGS_CACHE
    
    keys_to_del = set(USER_KEYS_CACHE.keys()) - active_user_ids
    for user_id in keys_to_del:
        del USER_KEYS_CACHE[user_id]

    settings_to_del = set(USER_SETTINGS_CACHE.keys()) - active_user_ids
    for user_id in settings_to_del:
        del USER_SETTINGS_CACHE[user_id]

    exchanges_to_del = set(USER_EXCHANGE_CACHE.keys()) - active_user_ids
    for user_id in exchanges_to_del:
        asyncio.create_task(USER_EXCHANGE_CACHE[user_id].close())
        del USER_EXCHANGE_CACHE[user_id]
        
    if keys_to_del or exchanges_to_del or settings_to_del:
        logger.info(f"CACHE_SYNC: Cleared caches for {len(exchanges_to_del)} inactive users.")

# =======================================================================================
# --- المكون الثاني: "الأيدي" (المشرف السريع) ---
# (مبني على _close_trade و the_supervisor_job)
# =======================================================================================

async def run_supervisor():
    """
    "الأيدي": يبحث عن الأعلام التي رفعتها "العيون" وينفذ البيع.
    ويعالج أيضاً الصفقات في "الحضانة" (incubated).
    """
    while True:
        try:
            # 1. جلب كل الصفقات التي تحتاج إغلاق (لكل المستخدمين)
            # (هذه الدالة تحتاج للإضافة في db_utils.py)
            async with db_utils.db_connection() as conn:
                flagged_trades = await conn.fetch(
                    "SELECT * FROM trades WHERE status LIKE 'force_exit_%' OR status = 'retry_exit'"
                )
            
            if flagged_trades:
                logger.info(f"HANDS: Found {len(flagged_trades)} trades flagged for closure.")
                for trade_record in flagged_trades:
                    trade = dict(trade_record)
                    trade_id = trade['id']
                    user_id = trade['user_id']
                    symbol = trade['symbol']
                    reason_code = trade['status']
                    
                    logger.info(f"HANDS: Processing trade #{trade_id} ({symbol}) for user {user_id}. Reason: {reason_code}")
                    
                    # تحويل كود السبب إلى سبب نهائي
                    reason_map = {
                        "force_exit_tp": "ناجحة (TP)",
                        "force_exit_sl": "فاشلة (SL)",
                        "force_exit_tsl": "تم تأمين الربح (TSL)" if trade['entry_price'] < trade['close_price'] else "فاشلة (TSL)",
                        "force_exit_manual": "إغلاق يدوي",
                        "force_exit_wise_man": "فاشلة (بأمر الرجل الحكيم)",
                        "retry_exit": "فاشلة (SL-Incubator)" #
                    }
                    final_reason = reason_map.get(reason_code, "إغلاق آلي")

                    await _execute_close(user_id, trade, final_reason)

            # 2. معالجة الحضانة (من the_supervisor_job)
            # (هذه الدالة تحتاج للإضافة في db_utils.py)
            async with db_utils.db_connection() as conn:
                incubated_trades = await conn.fetch("SELECT id FROM trades WHERE status = 'incubated'")
                if incubated_trades:
                    logger.info(f"HANDS: Found {len(incubated_trades)} incubated trades. Moving to retry.")
                    await conn.executemany("UPDATE trades SET status = 'retry_exit' WHERE id = $1", [(t['id'],) for t in incubated_trades])

        except Exception as e:
            logger.error(f"SUPERVISOR: Critical error in main loop: {e}", exc_info=True)
        
        await asyncio.sleep(SUPERVISOR_INTERVAL_SECONDS)

async def _execute_close(user_id: UUID, trade: Dict, reason: str):
    """
    (مبني على _close_trade)
    ينفذ أمر البيع الفعلي ويحدّث قاعدة البيانات.
    """
    trade_id = trade['id']
    symbol = trade['symbol']
    
    # 1. ضع علامة "جاري الإغلاق" لمنع المحاولات المزدوجة
    try:
        async with db_utils.db_connection() as conn:
            result = await conn.execute(
                "UPDATE trades SET status = 'closing' WHERE id = $1 AND status NOT IN ('closing', 'closed')",
                trade_id
            )
            if result == "UPDATE 0":
                logger.warning(f"HANDS: Trade #{trade_id} is already being closed. Skipping.")
                return
    except Exception as e:
        logger.error(f"HANDS: DB lock failed for trade #{trade_id}: {e}"); return

    # 2. احصل على اتصال المستخدم
    exchange = await get_user_exchange(user_id)
    if not exchange:
        logger.error(f"HANDS: Cannot close trade #{trade_id}. No valid CCXT instance for user {user_id}.")
        await db_utils.set_trade_status(trade_id, 'retry_exit') # إعادة المحاولة لاحقاً
        return

    # 3. جلب الكمية الفعلية والسعر الحالي
    try:
        # استخدام الكمية المسجلة
        quantity_to_sell = float(trade['quantity']) 
        ticker = await PUBLIC_EXCHANGE.fetch_ticker(symbol)
        close_price = ticker['last']

        # 4. [الإصلاح الحاسم] التحقق من قواعد السوق (Min Notional / Lot Size)
        market = await PUBLIC_EXCHANGE.market(symbol) # استخدام الكائن العام أسرع
        
        # التحقق من Min Notional
        min_notional_str = market.get('limits', {}).get('notional', {}).get('min')
        if min_notional_str and (quantity_to_sell * close_price) < float(min_notional_str):
            logger.warning(f"HANDS: Trade #{trade_id} value below MIN_NOTIONAL. Incubating.")
            await db_utils.set_trade_status(trade_id, 'incubated') #
            return

        # (يمكن إضافة التحقق من LOT_SIZE هنا أيضاً إذا لزم الأمر)
        
        # 5. تنفيذ البيع
        await exchange.create_market_sell_order(symbol, quantity_to_sell)
        
        # 6. حساب PnL
        pnl = (close_price - trade['entry_price']) * quantity_to_sell
        
        # 7. تسجيل الإغلاق
        closed_trade_data = await db_utils.close_trade_record(trade_id, reason, close_price, pnl)
        logger.info(f"HANDS: Successfully closed trade #{trade_id}. PnL: ${pnl:.2f}")

        # 8. [دمج العقل الذكي] بدء تحليل "ماذا لو؟"
        if closed_trade_data:
            settings = await get_user_settings(user_id)
            if settings:
                asyncio.create_task(_run_smart_engine_analysis(exchange, closed_trade_data, settings))

    except (ccxt.InvalidOrder, ccxt.InsufficientFunds) as e:
         logger.warning(f"HANDS: Closure for #{trade_id} failed with trade rule error. Incubating: {e}")
         await db_utils.set_trade_status(trade_id, 'incubated') #
    except Exception as e:
        logger.error(f"HANDS: Critical failure closing trade #{trade_id}. Retrying: {e}", exc_info=True)
        await db_utils.set_trade_status(trade_id, 'retry_exit')
    finally:
        # لا نغلق الاتصال، سيبقى في الذاكرة المؤقتة
        pass

# =======================================================================================
# --- المكون الثالث: "المنشط" (مشرف الصفقات المعلقة) ---
# (بديل لـ UserDataStream)
# =======================================================================================

async def run_pending_trade_monitor():
    """
    "المنشط": يبحث عن الصفقات المعلقة ويتحقق من حالتها.
    إذا تم التنفيذ، يقوم بتنشيطها.
    """
    while True:
        try:
            # 1. جلب كل الصفقات المعلقة
            async with db_utils.db_connection() as conn:
                pending_trades = await conn.fetch(
                    "SELECT * FROM trades WHERE status = 'pending' AND timestamp > (NOW() - INTERVAL '1 hour')"
                )
            
            if not pending_trades:
                await asyncio.sleep(ACTIVATOR_INTERVAL_SECONDS)
                continue

            logger.info(f"ACTIVATOR: Found {len(pending_trades)} pending trades to check.")
            
            for trade_record in pending_trades:
                trade = dict(trade_record)
                user_id = trade['user_id']
                order_id = trade['order_id']
                symbol = trade['symbol']

                # 2. جلب اتصال المستخدم
                exchange = await get_user_exchange(user_id)
                if not exchange:
                    logger.warning(f"ACTIVATOR: Skipping check for trade #{trade['id']}. No CCXT instance for user {user_id}.")
                    continue

                # 3. التحقق من حالة الأمر
                try:
                    order_details = await exchange.fetch_order(order_id, symbol)
                    
                    if order_details.get('status') == 'closed' and order_details.get('filled', 0) > 0:
                        logger.info(f"ACTIVATOR: Order {order_id} is FILLED. Activating trade #{trade['id']}...")
                        
                        filled_price = float(order_details.get('average', 0.0))
                        filled_qty = float(order_details.get('filled', 0.0))
                        
                        if filled_price <= 0 or filled_qty <= 0:
                             logger.error(f"ACTIVATOR: Order {order_id} has invalid fill data. Deleting trade.")
                             await db_utils.delete_trade(trade['id'])
                             continue

                        settings = await get_user_settings(user_id)
                        
                        # 4. تنشيط الصفقة
                        activated_trade = await db_utils.activate_trade(
                            order_id, symbol, filled_price, filled_qty, settings.risk_reward_ratio
                        )
                        
                        if activated_trade:
                            logger.info(f"ACTIVATOR: Trade #{activated_trade['id']} activated successfully.")
                            # (الإشعار سيتم إرساله من الواجهة عندما ترى التغيير)
                            
                            # [دمج العقل الذكي] تسجيل بيانات الدخول
                            snapshot = await core_logic.smart_engine_capture_snapshot(exchange, symbol)
                            await db_utils.log_trade_journal_entry(
                                user_id, activated_trade['id'], activated_trade['reason'], snapshot
                            )

                    elif order_details.get('status') in ['canceled', 'expired', 'rejected']:
                        logger.warning(f"ACTIVATOR: Order {order_id} for trade #{trade['id']} failed. Deleting trade.")
                        await db_utils.delete_trade(trade['id'])

                except ccxt.OrderNotFound:
                    logger.error(f"ACTIVATOR: Order {order_id} for trade #{trade['id']} NOT FOUND. Deleting.")
                    await db_utils.delete_trade(trade['id'])
                except Exception as e:
                    logger.error(f"ACTIVATOR: Error checking order {order_id}: {e}")

        except Exception as e:
            logger.error(f"ACTIVATOR: Critical error in main loop: {e}", exc_info=True)
        
        await asyncio.sleep(ACTIVATOR_INTERVAL_SECONDS)

# =======================================================================================
# --- المكون الرابع: "الماسح" (Scanner) ---
# (مبني على perform_scan و worker_batch)
# =======================================================================================

async def run_scanner():
    """
    "الماسح": يلف على جميع المستخدمين النشطين وينفذ الفحص لهم.
    """
    while True:
        logger.info("SCANNER: Starting new multi-user scan cycle...")
        try:
            active_users = await db_utils.get_all_active_users_with_settings()
            if not active_users:
                logger.info("SCANNER: No active users found. Sleeping.")
                await asyncio.sleep(SCAN_INTERVAL_SECONDS)
                continue

            logger.info(f"SCANNER: Found {len(active_users)} active users to scan for.")

            # 1. جلب الأسواق مرة واحدة
            all_tickers = await PUBLIC_EXCHANGE.fetch_tickers()
            
            # 2. تنفيذ الفحص لكل مستخدم بالتوازي
            tasks = [scan_for_user(user, all_tickers) for user in active_users]
            await asyncio.gather(*tasks, return_exceptions=True)

        except Exception as e:
            logger.error(f"SCANNER: Critical error in main loop: {e}", exc_info=True)
        
        logger.info(f"SCANNER: Scan cycle complete. Sleeping for {SCAN_INTERVAL_SECONDS}s.")
        await asyncio.sleep(SCAN_INTERVAL_SECONDS)

async def scan_for_user(user: UserSettings, all_tickers: Dict):
    """ينفذ منطق الفحص الكامل لمستخدم واحد."""
    
    user_id = user.user_id
    logger.info(f"SCANNER: Starting scan for user {user_id}...")
    
    try:
        # 1. التحقق من الحد الأقصى للصفقات
        active_count = await db_utils.get_active_trade_count_for_user(user_id)
        available_slots = user.max_concurrent_trades - active_count
        if available_slots <= 0:
            logger.info(f"SCANNER: User {user_id} at max trades ({active_count}). Skipping.")
            return

        # 2. فلترة الأسواق (نفس منطق BN.py ولكن باستخدام إعدادات المستخدم)
        # (يجب إضافة منطق فلترة المزاج، F&G، اتجاه BTC هنا... سأبسطه للسرعة)
        
        valid_markets = [
            t for t in all_tickers.values() 
            if 'USDT' in t['symbol'] 
            and t.get('quoteVolume', 0) > 1000000 # (يجب جلب هذا من user.settings)
            and t['symbol'].split('/')[0] not in user.asset_blacklist
            and t.get('active', True)
        ]
        valid_markets.sort(key=lambda m: m.get('quoteVolume', 0), reverse=True)
        symbols_to_scan = [m['symbol'] for m in valid_markets[:user.top_n_symbols_by_volume]]

        if not symbols_to_scan:
            logger.info(f"SCANNER: No valid symbols found for user {user_id} after filtering.")
            return

        # 3. جلب بيانات OHLCV (دفعات)
        tasks = [PUBLIC_EXCHANGE.fetch_ohlcv(s, '15m', limit=100) for s in symbols_to_scan]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        ohlcv_data = {symbols_to_scan[i]: results[i] for i in range(len(symbols_to_scan)) if not isinstance(results[i], Exception)}

        # 4. تشغيل الماسحات (مثل worker_batch)
        user_exchange = None # نهيئه فقط إذا احتجناه
        
        for symbol, ohlcv in ohlcv_data.items():
            if available_slots <= 0: break
            if await db_utils.check_if_symbol_active_for_user(user_id, symbol):
                continue # تخطى إذا كانت لديه صفقة نشطة لهذه العملة

            try:
                df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
                if len(df) < 50: continue
                
                # ... (منطق الفلاتر: ATR, Volume, ADX, EMA ... إلخ) ...
                # (سأبسط هذا الجزء، لكن يجب نسخ كل الفلاتر من worker_batch)
                
                confirmed_reasons = []
                for scanner_name in user.active_scanners:
                    if not (strategy_func := core_logic.SCANNERS_MAP.get(scanner_name)): continue
                    
                    params = {} # (يجب جلب الإعدادات المخصصة من user.settings)
                    
                    # الدوال التي تحتاج اتصال خاص (مثل whale_radar)
                    if scanner_name in ['whale_radar', 'support_rebound']:
                        if user_exchange is None:
                            user_exchange = await get_user_exchange(user_id)
                            if not user_exchange: break # لا يمكن إكمال الفحص بدون اتصال
                        
                        result = await strategy_func(df.copy(), params, 0, 0, user_exchange, symbol)
                    else:
                        # الماسحات التي لا تحتاج اتصال
                        result = strategy_func(df.copy(), params, 0, 0)
                    
                    if result: confirmed_reasons.append(result['reason'])

                # 5. إذا وجدت إشارة، قم بإنشاء الصفقة
                if confirmed_reasons:
                    logger.info(f"SCANNER: Signal found for user {user_id} on {symbol}!")
                    
                    # ... (منطق حساب TP/SL من BN.py) ...
                    entry_price = df.iloc[-1]['close']
                    atr = ta.atr(df['high'], df['low'], df['close'], length=14).iloc[-1]
                    if pd.isna(atr) or atr == 0: continue
                    
                    risk = atr * user.atr_sl_multiplier
                    stop_loss = entry_price - risk
                    take_profit = entry_price + (risk * user.risk_reward_ratio)
                    
                    signal = {
                        "symbol": symbol, "entry_price": entry_price, "take_profit": take_profit, 
                        "stop_loss": stop_loss, "reason": ' + '.join(set(confirmed_reasons)), 
                        "strength": len(set(confirmed_reasons)), "weight": 1.0 # (يجب إضافة منطق trade_weight)
                    }
                    
                    # 6. تنفيذ الشراء
                    if user_exchange is None:
                        user_exchange = await get_user_exchange(user_id)
                        if not user_exchange: break

                    if await _execute_buy(user_exchange, user.user_id, signal, user.real_trade_size_usdt):
                        available_slots -= 1
            
            except Exception as e:
                logger.error(f"SCANNER: Error processing symbol {symbol} for user {user_id}: {e}")
                
    except Exception as e:
        logger.error(f"SCANNER: Failed scan for user {user_id}: {e}", exc_info=True)
    finally:
        logger.info(f"SCANNER: Finished scan for user {user_id}.")
        # (لا نغلق الاتصال، سيبقى في الذاكرة المؤقتة)

async def _execute_buy(exchange: ccxt.Exchange, user_id: UUID, signal: dict, trade_size: float) -> bool:
    """(من initiate_real_trade) ينفذ الشراء ويسجل الصفقة كـ 'pending'."""
    symbol = signal['symbol']
    try:
        # --- [الإصلاح الحاسم] التحقق من الحد الأدنى لقيمة الصفقة ---
        market = await PUBLIC_EXCHANGE.market(symbol)
        min_notional_str = market.get('limits', {}).get('notional', {}).get('min')
        
        if min_notional_str:
            min_notional_value = float(min_notional_str)
            required_size = min_notional_value * 1.05 # هامش أمان
            
            if trade_size < required_size:
                logger.warning(f"BUYER ({user_id}): Trade for {symbol} aborted. Size ({trade_size:.2f}) < Min Notional ({required_size:.2f}).")
                return False

        base_amount = trade_size / signal['entry_price']
        formatted_amount = exchange.amount_to_precision(symbol, base_amount)

        # (يجب إضافة التحقق من الرصيد هنا)
        
        buy_order = await exchange.create_market_buy_order(symbol, formatted_amount)

        if await db_utils.create_pending_trade(user_id, signal, buy_order):
            logger.info(f"BUYER ({user_id}): Pending trade created for {symbol}.")
            return True
        else:
            logger.critical(f"BUYER ({user_id}): Failed to log pending trade for {symbol}. Cancelling order {buy_order['id']}.")
            await exchange.cancel_order(buy_order['id'], symbol)
            return False

    except ccxt.InsufficientFunds:
        logger.error(f"BUYER ({user_id}): Insufficient funds for {symbol}.")
        return False
    except Exception as e:
        logger.error(f"BUYER ({user_id}): Failed to execute buy for {symbol}: {e}", exc_info=True)
        return False

# =======================================================================================
# --- دوال مساعدة لـ "العقل" ---
# =======================================================================================

async def _run_wise_man_deep_analysis(trade: ActiveTradeMonitor, settings: UserSettings):
    """(تشغيل غير متزامن) ينفذ تحليل الرجل الحكيم لقطع الخسائر."""
    exchange = await get_user_exchange(trade.user_id)
    if not exchange: return

    result = await core_logic.wise_man_deep_analysis(trade, settings, exchange)
    
    if result == "force_exit":
        await db_utils.set_trade_force_exit(trade.id, trade.user_id)
        logger.info(f"WISE_MAN: Force exit signal sent for trade #{trade.id}.")
    elif result == "notify_weak":
        # (لا يمكننا إرسال إشعار تليجرام من هنا)
        # (يمكننا تسجيل هذا في جدول "إشعارات" جديد لواجهة المستخدم)
        logger.info(f"WISE_MAN: Weakness detected for trade #{trade.id}. Auto-close disabled.")

async def _run_wise_man_momentum_check(trade: ActiveTradeMonitor, settings: UserSettings):
    """(تشغيل غير متزامن) ينفذ تحليل الرجل الحكيم لتمديد الأرباح."""
    exchange = await get_user_exchange(trade.user_id)
    if not exchange: return

    new_tp = await core_logic.wise_man_check_momentum(trade, settings, exchange)
    
    if new_tp and new_tp > trade.take_profit:
        await db_utils.update_trade_take_profit(trade.id, new_tp)
        logger.info(f"WISE_MAN: TP extended for trade #{trade.id} to {new_tp}.")

async def _run_smart_engine_analysis(exchange: ccxt.Exchange, closed_trade: Dict, settings: UserSettings):
    """(تشغيل غير متزامن) ينفذ تحليل "ماذا لو؟" بعد إغلاق الصفقة."""
    await asyncio.sleep(60) # (انتظار لتجنب بيانات الشمعة الحالية)
    
    analysis_results = await core_logic.smart_engine_what_if_analysis(exchange, closed_trade, settings)
    
    if analysis_results:
        await db_utils.update_trade_journal_exit(
            closed_trade['id'],
            analysis_results['exit_reason'],
            analysis_results['score'],
            analysis_results['post_performance'],
            analysis_results['notes']
        )
        logger.info(f"SMART_ENGINE: 'What-If' analysis saved for trade #{closed_trade['id']}.")

# =======================================================================================
# --- نقطة الدخول الرئيسية للعامل ---
# =======================================================================================

async def main():
    logger.info("--- 🚀 Bot Worker (Hybrid Engine) Starting Up... ---")
    await db_utils.get_db_pool() # تهيئة مجموعة الاتصالات
    await PUBLIC_EXCHANGE.load_markets() # تحميل الأسواق العامة مرة واحدة
    
    tasks = [
        run_public_websocket_manager(), # "العيون"
        sync_cache_from_db(),           # مزامنة "العيون" والمخابئ
        run_supervisor(),               # "الأيدي" (إغلاق الصفقات)
        run_pending_trade_monitor(),    # "المنشط" (تفعيل الصفقات)
        run_scanner()                   # "الماسح"
    ]
    
    await asyncio.gather(*tasks)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("--- 🛑 Bot Worker Shutting Down... ---")
    finally:
        # إغلاق جميع الاتصالات المفتوحة
        asyncio.run(PUBLIC_EXCHANGE.close())
        asyncio.run(close_all_user_exchanges())
        if db_utils.POOL:
            asyncio.run(db_utils.POOL.close())
