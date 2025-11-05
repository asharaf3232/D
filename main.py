import uvicorn
import asyncio
import logging
import os
import aiohttp
import ccxt.async_support as ccxt
from fastapi import FastAPI, Request, Depends, HTTPException, WebSocket, Body, Header
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Dict, Any, List, Optional
from uuid import UUID
from contextlib import asynccontextmanager
from datetime import datetime

# --- استيراد الوحدات الجديدة ---
import db_utils
from db_utils import UserKeys, TradingVariables, BotSettings

# --- إعداد FastAPI ---
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger("FastAPIServer_V4_Paywall")

app = FastAPI(title="Trading Bot SaaS Platform (V4 - Paywall Enabled)")

# --- (التخزين المؤقت للاتصالات) ---
USER_CCXT_CACHE: Dict[UUID, ccxt.Exchange] = {}
CCXT_CACHE_LOCK = asyncio.Lock()

@asynccontextmanager
async def get_ccxt_connection(user_id: UUID) -> ccxt.Exchange:
    """يدير اتصالات CCXT المخبأة لجلب الأرصدة بسرعة."""
    async with CCXT_CACHE_LOCK:
        if user_id in USER_CCXT_CACHE:
            logger.info(f"API: Using cached CCXT connection for user {user_id}")
            yield USER_CCXT_CACHE[user_id]
            return
    
    logger.info(f"API: Creating new CCXT connection for user {user_id}...")
    keys = await db_utils.get_user_api_keys(user_id)
    if not keys:
        raise HTTPException(status_code=404, detail="User API keys not found or invalid.")
        
    exchange = None
    try:
        exchange = ccxt.binance({
            'apiKey': keys.api_key, 'secret': keys.api_secret,
            'enableRateLimit': True, 'options': {'defaultType': 'spot'}
        })
        await exchange.load_markets()
        async with CCXT_CACHE_LOCK:
            USER_CCXT_CACHE[user_id] = exchange
        yield exchange
    except Exception as e:
        logger.error(f"API: Failed to create CCXT connection for {user_id}: {e}")
        async with CCXT_CACHE_LOCK:
            if user_id in USER_CCXT_CACHE: del USER_CCXT_CACHE[user_id]
        raise HTTPException(status_code=500, detail=f"Failed to initialize exchange connection: {str(e)}")
    finally:
        pass # يبقى الاتصال في الذاكرة المؤقتة

async def close_all_cached_connections():
    async with CCXT_CACHE_LOCK:
        logger.info("API: Closing all cached CCXT connections...")
        for exchange in USER_CCXT_CACHE.values():
            await exchange.close()
        USER_CCXT_CACHE.clear()

# =======================================================================================
# --- [ ⬇️ القفل رقم 1 (V4) ⬇️ ] ---
# --- المصادقة + التحقق من الاشتراك (Paywall) ---
# =======================================================================================

async def get_user_from_token(authorization: str = Header(None)) -> UUID:
    """(الخطوة 1) يتحقق من التوكن ويرجع الـ User ID."""
    if authorization is None:
        raise HTTPException(status_code=401, detail="Authorization header missing.")
    try:
        token_type, token = authorization.split(" ")
        if token_type.lower() != "bearer": raise ValueError("Invalid token type")
        user_uuid = UUID(token)
        return user_uuid
    except (ValueError, TypeError) as e:
        logger.warning(f"Auth Error: Invalid token format. {e}")
        raise HTTPException(status_code=401, detail="Invalid authorization token.")

async def get_active_user(user_id: UUID = Depends(get_user_from_token)) -> UUID:
    """
    (الخطوة 2: "حارس البوابة")
    يتحقق مما إذا كان المستخدم لديه اشتراك ساري.
    هذا هو "القفل" الذي يمنع الاستخدام غير المصرح به.
    """
    try:
        settings = await db_utils.get_user_settings_by_id(user_id)
        if not settings:
            logger.warning(f"Auth: No settings found for user {user_id}. Denying access.")
            raise HTTPException(status_code=403, detail="User profile not found. Please contact support.")

        status = settings.subscription_status
        expires_at = settings.subscription_expires_at

        if status in ('active', 'trial') and expires_at > datetime.now(datetime.timezone.utc):
            # ✅ المستخدم مصرح له
            return user_id
        elif status == 'pending_payment':
            logger.info(f"Auth: Access denied for {user_id}. Status: pending_payment.")
            raise HTTPException(status_code=403, detail="اشتراكك قيد المراجعة. يرجى الانتظار.")
        elif status == 'expired':
            logger.info(f"Auth: Access denied for {user_id}. Status: expired.")
            raise HTTPException(status_code=403, detail="انتهى اشتراكك. الرجاء التجديد.")
        else:
            logger.info(f"Auth: Access denied for {user_id}. Status: {status}.")
            raise HTTPException(status_code=403, detail="حسابك غير نشط. يرجى الاتصال بالدعم.")

    except HTTPException as e:
        raise e # إعادة إرسال أخطاء 401/403
    except Exception as e:
        logger.error(f"Auth: Error checking subscription for user {user_id}: {e}")
        raise HTTPException(status_code=500, detail="Internal server error while checking subscription.")


# =======================================================================================
# --- واجهات برمجة التطبيقات (API Endpoints) ---
# (الآن تستخدم `get_active_user` كـ "حارس بوابة")
# =======================================================================================

# --- 1. Bot Control ---
@app.post("/bot/start", tags=["Bot Control"])
async def start_bot(user_id: UUID = Depends(get_active_user)):
    """(ينفذ /bot/start) - يشغل البوت للمستخدم (يتطلب اشتراك ساري)."""
    logger.info(f"API: User {user_id} requested START")
    settings = await db_utils.set_bot_status(user_id, True)
    return {"status": "starting", "is_running": settings.is_running}

@app.post("/bot/stop", tags=["Bot Control"])
async def stop_bot(user_id: UUID = Depends(get_active_user)):
    """(ينفذ /bot/stop) - يوقف البوت للمستخدم (يتطلب اشتراك ساري)."""
    logger.info(f"API: User {user_id} requested STOP")
    settings = await db_utils.set_bot_status(user_id, False)
    return {"status": "stopping", "is_running": settings.is_running}

@app.get("/bot/status", tags=["Bot Control"])
async def get_bot_status(user_id: UUID = Depends(get_user_from_token)):
    """(ينفذ /bot/status) - يجلب حالة البوت (لا يتطلب اشتراك ساري)."""
    settings = await db_utils.get_user_settings_by_id(user_id)
    if not settings:
         raise HTTPException(status_code=404, detail="User settings not found.")
    return {
        "status": "running" if settings.is_running else "offline", 
        "is_running": settings.is_running, 
        "current_preset_name": settings.current_preset_name,
        # [جديد V4] إرسال حالة الاشتراك للواجهة
        "subscription_status": settings.subscription_status,
        "subscription_expires_at": settings.subscription_expires_at
    }

# --- 2. Balance & Keys ---
@app.get("/bot/balance", tags=["Balance & Keys"])
async def get_balance(user_id: UUID = Depends(get_active_user)):
    """(ينفذ /bot/balance) - يجلب الرصيد (يتطلب اشتراك ساري)."""
    try:
        async with get_ccxt_connection(user_id) as exchange:
            balance = await exchange.fetch_balance()
            usdt_balance = balance.get('USDT', {})
            return {"total_balance": usdt_balance.get('total', 0), "available_balance": usdt_balance.get('free', 0), "currency": "USDT"}
    except HTTPException as e: raise e
    except Exception as e:
        logger.error(f"API /balance error for user {user_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

class KeysPayload(BaseModel):
    api_key: str; secret_key: str; passphrase: Optional[str] = None

@app.post("/bot/test-keys", tags=["Balance & Keys"])
async def test_binance_keys(payload: KeysPayload, user_id: UUID = Depends(get_user_from_token)):
    """(ينفذ /bot/test-keys) - يختبر ويحفظ المفاتيح (لا يتطلب اشتراك ساري)."""
    logger.info(f"API: User {user_id} testing keys...")
    try:
        test_exchange = ccxt.binance({'apiKey': payload.api_key, 'secret': payload.secret_key, 'enableRateLimit': True})
        await test_exchange.fetch_balance()
        await test_exchange.close()
        await db_utils.save_api_keys(user_id, payload.api_key, payload.secret_key, payload.passphrase)
        await db_utils.set_api_keys_valid(user_id, True)
        return {"status": "success", "message": "تم اختبار وحفظ المفاتيح بنجاح."}
    except Exception as e:
        logger.error(f"API /test-keys error for user {user_id}: {e}")
        await db_utils.save_api_keys(user_id, payload.api_key, payload.secret_key, payload.passphrase) # حفظها كغير صالحة
        await db_utils.set_api_keys_valid(user_id, False)
        raise HTTPException(status_code=400, detail=f"فشل اختبار المفاتيح: {str(e)}")

# (هذا المسار أصبح غير ضروري لأن /bot/test-keys يقوم بالحفظ)
# @app.post("/keys", tags=["Balance & Keys"]) ...

# --- 3. Trades ---
@app.get("/trades/active", tags=["Trades"])
async def get_active_trades(user_id: UUID = Depends(get_active_user)):
    return await db_utils.get_active_trades(user_id)

class CloseTradePayload(BaseModel):
    trade_id: int

@app.post("/trades/close", tags=["Trades"])
async def close_trade(payload: CloseTradePayload, user_id: UUID = Depends(get_active_user)):
    success = await db_utils.flag_trade_for_closure(user_id, payload.trade_id)
    if not success: raise HTTPException(status_code=404, detail="Trade not found or not active.")
    return {"status": "closing", "message": "تم إرسال أمر الإغلاق إلى العامل."}

@app.get("/trades/history", tags=["Trades"])
async def get_trades_history(limit: int = 50, user_id: UUID = Depends(get_active_user)):
    return await db_utils.get_trades_history(user_id, limit)

@app.get("/trades/stats", tags=["Trades"])
async def get_trades_stats(user_id: UUID = Depends(get_active_user)):
    return await db_utils.get_trades_stats(user_id)

# --- 4. Strategies (Scanners) ---
@app.get("/strategies", tags=["Strategies & Scanners"])
async def get_strategies(user_id: UUID = Depends(get_active_user)):
    async with db_utils.db_connection() as conn:
        records = await conn.fetch("SELECT * FROM strategies WHERE user_id = $1", user_id)
    return [dict(r) for r in records]

@app.post("/strategies/{strategy_name}/toggle", tags=["Strategies & Scanners"])
async def toggle_strategy(strategy_name: str, enabled_payload: dict = Body(...), user_id: UUID = Depends(get_active_user)):
    is_enabled = enabled_payload.get('enabled', False)
    async with db_utils.db_connection() as conn:
        await conn.execute("UPDATE strategies SET is_enabled = $1 WHERE user_id = $2 AND strategy_name = $3", is_enabled, user_id, strategy_name)
    return {"status": "success", "strategy_name": strategy_name, "is_enabled": is_enabled}

@app.get("/scanners", tags=["Strategies & Scanners"])
async def get_scanners(user_id: UUID = Depends(get_active_user)):
    return await get_strategies(user_id)

@app.post("/scanners/{scanner_name}/toggle", tags=["Strategies & Scanners"])
async def toggle_scanner(scanner_name: str, enabled_payload: dict = Body(...), user_id: UUID = Depends(get_active_user)):
    return await toggle_strategy(scanner_name, enabled_payload, user_id)

# --- 5. Settings & Presets ---
@app.get("/settings", tags=["Settings & Presets"])
async def get_bot_settings(user_id: UUID = Depends(get_user_from_token)):
    """(يجلب الإعدادات المتقدمة - لا يتطلب اشتراك ساري)"""
    settings = await db_utils.get_api_settings(user_id)
    if not settings: 
        # (إذا لم تكن موجودة، قم بإنشاء إعدادات افتراضية)
        logger.info(f"Creating default advanced_variables for user {user_id}")
        async with db_utils.db_connection() as conn:
            await conn.execute("INSERT INTO advanced_variables (user_id) VALUES ($1) ON CONFLICT (user_id) DO NOTHING", user_id)
        settings = await db_utils.get_api_settings(user_id)
    return settings

@app.post("/settings", tags=["Settings & Presets"])
async def update_bot_settings(settings: Dict[str, Any], user_id: UUID = Depends(get_active_user)):
    """(يحدّث الإعدادات المتقدمة - يتطلب اشتراك ساري)"""
    settings.pop('id', None); settings.pop('user_id', None); settings.pop('updated_at', None)
    success = await db_utils.update_api_settings(user_id, settings)
    if not success: raise HTTPException(status_code=500, detail="Failed to update settings.")
    return {"status": "success", "message": "تم تحديث الإعدادات بنجاح."}

class PresetPayload(BaseModel):
    preset_name: str

@app.post("/settings/preset", tags=["Settings & Presets"])
async def change_preset(payload: PresetPayload, user_id: UUID = Depends(get_active_user)):
    logger.info(f"API: User {user_id} applying preset '{payload.preset_name}'")
    preset_definitions = {
        'strict': {"risk_reward_ratio": 3.0, "max_concurrent_trades": 2, "max_daily_loss_pct": 2.0},
        'professional': {"risk_reward_ratio": 2.5, "max_concurrent_trades": 3, "max_daily_loss_pct": 3.0},
        'lenient': {"risk_reward_ratio": 2.0, "max_concurrent_trades": 5, "max_daily_loss_pct": 5.0},
        'very_lenient': {"risk_reward_ratio": 1.5, "max_concurrent_trades": 7, "max_daily_loss_pct": 7.0},
        'bold_heart': {"risk_reward_ratio": 1.2, "max_concurrent_trades": 10, "max_daily_loss_pct": 10.0}
    }
    settings_to_apply = preset_definitions.get(payload.preset_name)
    if not settings_to_apply: raise HTTPException(status_code=404, detail="Preset not found.")
    
    success = await db_utils.apply_preset_settings(user_id, payload.preset_name, settings_to_apply)
    if not success: raise HTTPException(status_code=500, detail="Failed to apply preset.")
    return {"status": "success", "message": f"تم تطبيق نمط '{payload.preset_name}' بنجاح."}

# --- 6. Notifications & Health ---
@app.get("/notifications", tags=["Notifications & Health"])
async def get_notifications(limit: int = 50, unread_only: bool = False, user_id: UUID = Depends(get_user_from_token)):
    """(يجلب الإشعارات - لا يتطلب اشتراك ساري)"""
    return await db_utils.get_notifications(user_id, limit, unread_only)

@app.post("/notifications/{notification_id}/read", tags=["Notifications & Health"])
async def mark_notification_read(notification_id: int, user_id: UUID = Depends(get_user_from_token)):
    success = await db_utils.mark_notification_read(user_id, notification_id)
    if not success: raise HTTPException(status_code=404, detail="Notification not found.")
    return {"status": "success"}

@app.get("/health", tags=["Notifications & Health"])
async def health_check():
    return {"status": "ok", "timestamp": datetime.now().isoformat()}

# =======================================================================================
# --- [ ⬇️ جديد V4 ⬇️ ] واجهات برمجة التطبيقات الخاصة بالتليجرام والاشتراكات ---
# =======================================================================================

class TelegramLinkPayload(BaseModel):
    telegram_chat_id: int

@app.post("/telegram/link-account", tags=["V4 - User Setup"])
async def link_telegram_account(payload: TelegramLinkPayload, user_id: UUID = Depends(get_user_from_token)):
    """(لتنفيذ فكرتك) يربط معرف تليجرام بحساب الويب."""
    logger.info(f"API: User {user_id} linking to Telegram ID {payload.telegram_chat_id}")
    success = await db_utils.update_user_telegram_id(user_id, payload.telegram_chat_id)
    if not success:
        raise HTTPException(status_code=400, detail="This Telegram account is already linked to another user.")
    return {"status": "success", "message": "تم ربط حساب تليجرام بنجاح."}

class PaymentPayload(BaseModel):
    txt_id: str
    subscription_plan: str
    wallet_address_used: str
    amount_paid: float

@app.post("/payment/submit-txtid", tags=["V4 - User Setup"])
async def submit_payment_txtid(payload: PaymentPayload, user_id: UUID = Depends(get_user_from_token)):
    """(لتنفيذ فكرتك) يسجل طلب الدفع اليدوي للمراجعة."""
    logger.info(f"API: User {user_id} submitting payment TXT_ID {payload.txt_id}")
    success = await db_utils.create_payment_request(
        user_id, payload.txt_id, payload.subscription_plan, 
        payload.wallet_address_used, payload.amount_paid
    )
    if not success:
        raise HTTPException(status_code=400, detail="تم إرسال معرف المعاملة هذا من قبل.")
    return {"status": "success", "message": "تم استلام طلب الدفع، جاري المراجعة."}

# =======================================================================================
# --- واجهات برمجة التطبيقات الخاصة بـ Telegram (القديمة من V3) ---
# --- (الآن تستخدم "حارس البوابة" `get_active_user`) ---
# =======================================================================================

@app.get("/telegram/mood", tags=["Telegram API"])
async def get_telegram_mood(user_id: UUID = Depends(get_active_user)):
    """(يحاكي show_mood_command) يجلب مزاج السوق."""
    try:
        fng_index = 50
        async with aiohttp.ClientSession() as session:
            async with session.get("https://api.alternative.me/fng/?limit=1") as resp:
                if resp.status == 200: fng_index = int((await resp.json())['data'][0]['value'])
        
        btc_mood = "غير معروف"
        async with get_ccxt_connection(user_id) as exchange:
            ohlcv = await exchange.fetch_ohlcv('BTC/USDT', '4h', limit=50)
            if ohlcv and len(closes := [c[4] for c in ohlcv]) > 40:
                sma_40 = sum(closes[-40:]) / 40
                btc_mood = "صاعد ✅" if closes[-1] > sma_40 else "هابط ❌"
        
        return {"verdict": "المؤشرات إيجابية، لكن بحذر.", "btc_mood": btc_mood, "fng_index": fng_index, "news_sentiment": "محايدة"}
    except Exception as e:
        logger.error(f"API /telegram/mood error: {e}")
        raise HTTPException(status_code=500, detail="فشل جلب بيانات المزاج")

@app.get("/telegram/diagnostics", tags=["Telegram API"])
async def get_telegram_diagnostics(user_id: UUID = Depends(get_active_user)):
    """(يحاكي show_diagnostics_command) يجلب تقرير التشخيص."""
    try:
        settings = await db_utils.get_api_settings(user_id)
        stats = await db_utils.get_trades_stats(user_id)
        scanners_list = []
        async with db_utils.db_connection() as conn:
            records = await conn.fetch("SELECT display_name, is_enabled FROM strategies WHERE user_id = $1", user_id)
            for r in records: scanners_list.append(f"  - {r['display_name']}: {'✅' if r['is_enabled'] else '❌'}")
        
        bot_status = await db_utils.get_user_settings_by_id(user_id)
        
        return {
            "timestamp": datetime.now().isoformat(), "api_status": "ناجح ✅", "db_status": "ناجح ✅",
            "active_preset_name": bot_status.current_preset_name,
            "subscription_status": bot_status.subscription_status,
            "subscription_expires_at": bot_status.subscription_expires_at.isoformat(),
            "active_scanners_report": "\n".join(scanners_list),
            "total_closed_trades": stats.get('total_trades', 0)
        }
    except Exception as e:
        logger.error(f"API /telegram/diagnostics error: {e}")
        raise HTTPException(status_code=500, detail="فشل جلب بيانات التشخيص")

# =======================================================================================
# --- خدمة واجهة الويب (Web UI) ---
[cite_start]# [cite: 10-12, 18]
# =======================================================================================

UI_BUILD_DIR = os.path.join(os.path.dirname(__file__), "dist")
if not os.path.exists(UI_BUILD_DIR):
    logger.warning("="*50); logger.warning("UI build directory 'dist' not found."); logger.warning(f"Expected at: {UI_BUILD_DIR}"); logger.warning("Web UI will not be served."); logger.warning("="*50)
else:
    app.mount("/assets", StaticFiles(directory=os.path.join(UI_BUILD_DIR, "assets")), name="assets")
    @app.get("/{full_path:path}", response_class=HTMLResponse, include_in_schema=False)
    async def serve_react_app(request: Request, full_path: str):
        index_path = os.path.join(UI_BUILD_DIR, "index.html")
        if not os.path.exists(index_path):
            return HTMLResponse("<h1>Frontend build files (dist/index.html) not found.</h1>", status_code=404)
        return FileResponse(index_path)

# =======================================================================================
# --- أحداث بدء وإيقاف التشغيل ---
# =======================================================================================

@app.on_event("startup")
async def on_startup():
    await db_utils.get_db_pool()
    try: await PUBLIC_EXCHANGE.load_markets()
    except Exception as e: logger.error(f"Failed to load PUBLIC_EXCHANGE markets: {e}")
    logger.info("--- 🚀 FastAPI Server Started (V4 - Paywall Enabled) ---")

@app.on_event("shutdown")
async def on_shutdown():
    await close_all_cached_connections()
    await PUBLIC_EXCHANGE.close()
    if db_utils.POOL:
        await db_utils.POOL.close()
    logger.info("--- 🛑 FastAPI Server Shutdown ---")

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)
