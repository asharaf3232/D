import uvicorn
import asyncio
import logging
import os
import ccxt.async_support as ccxt
from fastapi import FastAPI, Request, Depends, HTTPException, WebSocket, Body, Header
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Dict, Any, List, Optional
from uuid import UUID
from contextlib import asynccontextmanager

# --- استيراد الوحدات الجديدة ---
import db_utils
from db_utils import UserKeys, TradingVariables

# --- إعداد FastAPI ---
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger("FastAPIServer_V2")

app = FastAPI(title="Trading Bot SaaS Platform (V2)")

# --- (تنفيذ المطلب: التخزين المؤقت للاتصالات) ---
USER_CCXT_CACHE: Dict[UUID, ccxt.Exchange] = {}
CCXT_CACHE_LOCK = asyncio.Lock()

@asynccontextmanager
async def get_ccxt_connection(user_id: UUID) -> ccxt.Exchange:
    """
    يدير اتصالات CCXT المخبأة لجلب الأرصدة بسرعة.
    [cite: 570] (يحاكي CCXTConnectionManager)
    """
    async with CCXT_CACHE_LOCK:
        if user_id in USER_CCXT_CACHE:
            logger.info(f"API: Using cached CCXT connection for user {user_id}")
            yield USER_CCXT_CACHE[user_id]
            return
    
    logger.info(f"API: Creating new CCXT connection for user {user_id}...")
    keys = await db_utils.get_user_api_keys(user_id)
    if not keys:
        raise HTTPException(status_code=404, detail="User API keys not found or invalid. Please set them first.")
        
    exchange = None
    try:
        exchange = ccxt.binance({
            'apiKey': keys.api_key,
            'secret': keys.api_secret,
            'enableRateLimit': True,
            'options': {'defaultType': 'spot'}
        })
        await exchange.load_markets()
        
        async with CCXT_CACHE_LOCK:
            USER_CCXT_CACHE[user_id] = exchange
            
        yield exchange
    
    except Exception as e:
        logger.error(f"API: Failed to create CCXT connection for {user_id}: {e}")
        # حذف المفتاح من الذاكرة المؤقتة إذا فشل
        async with CCXT_CACHE_LOCK:
            if user_id in USER_CCXT_CACHE:
                del USER_CCXT_CACHE[user_id]
        raise HTTPException(status_code=500, detail=f"Failed to initialize exchange connection: {str(e)}")
    finally:
        # (لا نغلق الاتصال هنا، سيبقى في الذاكرة المؤقتة)
        pass

async def close_all_cached_connections():
    async with CCXT_CACHE_LOCK:
        logger.info("API: Closing all cached CCXT connections...")
        for exchange in USER_CCXT_CACHE.values():
            await exchange.close()
        USER_CCXT_CACHE.clear()

# --- المصادقة (مطابقة لـ api.ts) ---

async def get_current_user(authorization: str = Header(None)) -> UUID:
    """
    (يحاكي api.ts)
    يتحقق من رأس 'Authorization: Bearer <token>'
    في هذه البنية، الـ token هو نفسه الـ user_id من Supabase.
    """
    if authorization is None:
        raise HTTPException(status_code=401, detail="Authorization header missing.")
    
    try:
        token_type, token = authorization.split(" ")
        if token_type.lower() != "bearer":
            raise ValueError("Invalid token type")
        
        # الـ Token هو user_id
        user_uuid = UUID(token)
        return user_uuid
    except (ValueError, TypeError) as e:
        logger.warning(f"Auth Error: Invalid token format. {e}")
        raise HTTPException(status_code=401, detail="Invalid authorization token.")

# =======================================================================================
# --- واجهات برمجة التطبيقات (API Endpoints) ---
# (مطابقة تماماً لـ api.ts)
# =======================================================================================

# --- 1. Bot Control  ---

@app.post("/bot/start", tags=["Bot Control"])
async def start_bot(user_id: UUID = Depends(get_current_user)):
    """(ينفذ /bot/start)  - يشغل البوت للمستخدم."""
    logger.info(f"API: User {user_id} requested START")
    settings = await db_utils.set_bot_status(user_id, True)
    return {"status": "starting", "is_running": settings.is_running}

@app.post("/bot/stop", tags=["Bot Control"])
async def stop_bot(user_id: UUID = Depends(get_current_user)):
    """(ينفذ /bot/stop)  - يوقف البوت للمستخدم."""
    logger.info(f"API: User {user_id} requested STOP")
    settings = await db_utils.set_bot_status(user_id, False)
    return {"status": "stopping", "is_running": settings.is_running}

@app.get("/bot/status", tags=["Bot Control"])
async def get_bot_status(user_id: UUID = Depends(get_current_user)):
    """(ينفذ /bot/status)  - يجلب حالة البوت الحالية."""
    logger.debug(f"API: User {user_id} requested status")
    settings = await db_utils.get_bot_status(user_id)
    return {"status": "running" if settings.is_running else "offline", "is_running": settings.is_running}

# --- 2. Balance & Keys [cite: 62-64] ---

@app.get("/bot/balance", tags=["Balance & Keys"])
async def get_balance(user_id: UUID = Depends(get_current_user)):
    """(ينفذ /bot/balance)  - يجلب الرصيد الفعلي من Binance."""
    try:
        async with get_ccxt_connection(user_id) as exchange:
            balance = await exchange.fetch_balance()
            usdt_balance = balance.get('USDT', {})
            return {
                "total_balance": usdt_balance.get('total', 0),
                "available_balance": usdt_balance.get('free', 0),
                "currency": "USDT"
            }
    except HTTPException as e:
        raise e # إعادة إرسال أخطاء 404 أو 500
    except Exception as e:
        logger.error(f"API /balance error for user {user_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

class KeysPayload(BaseModel):
    api_key: str
    secret_key: str
    passphrase: Optional[str] = None

@app.post("/bot/test-keys", tags=["Balance & Keys"])
async def test_binance_keys(payload: KeysPayload, user_id: UUID = Depends(get_current_user)):
    """(ينفذ /bot/test-keys)  - يختبر المفاتيح قبل الحفظ."""
    logger.info(f"API: User {user_id} testing keys...")
    try:
        # إنشاء اتصال مؤقت للاختبار فقط
        test_exchange = ccxt.binance({
            'apiKey': payload.api_key,
            'secret': payload.secret_key,
            'enableRateLimit': True,
        })
        await test_exchange.fetch_balance()
        await test_exchange.close()
        
        # إذا نجح الاختبار، قم بحفظ المفاتيح
        await db_utils.save_api_keys(user_id, payload.api_key, payload.secret_key, payload.passphrase)
        await db_utils.set_api_keys_valid(user_id, True)
        
        return {"status": "success", "message": "تم اختبار وحفظ المفاتيح بنجاح."}
    except Exception as e:
        logger.error(f"API /test-keys error for user {user_id}: {e}")
        await db_utils.set_api_keys_valid(user_id, False) # وضع علامة "غير صالح"
        raise HTTPException(status_code=400, detail=f"فشل اختبار المفاتيح: {str(e)}")

@app.post("/keys", tags=["Balance & Keys"])
async def save_binance_keys(payload: KeysPayload, user_id: UUID = Depends(get_current_user)):
    """(ينفذ /keys) - يحفظ المفاتيح (يستخدمه /bot/test-keys)."""
    logger.info(f"API: User {user_id} saving keys...")
    success = await db_utils.save_api_keys(user_id, payload.api_key, payload.secret_key, payload.passphrase)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to save keys to database.")
    return {"status": "success", "message": "تم حفظ المفاتيح (في انتظار الاختبار)."}

# --- 3. Trades  ---

@app.get("/trades/active", tags=["Trades"])
async def get_active_trades(user_id: UUID = Depends(get_current_user)):
    """(ينفذ /trades/active) [cite: 65] - يجلب الصفقات النشطة."""
    trades = await db_utils.get_active_trades(user_id)
    return trades

class CloseTradePayload(BaseModel):
    trade_id: int

@app.post("/trades/close", tags=["Trades"])
async def close_trade(payload: CloseTradePayload, user_id: UUID = Depends(get_current_user)):
    """(ينفذ /trades/close) [cite: 65] - يطلب إغلاق صفقة."""
    logger.info(f"API: User {user_id} requested manual close for trade #{payload.trade_id}.")
    success = await db_utils.flag_trade_for_closure(user_id, payload.trade_id)
    if not success:
        raise HTTPException(status_code=404, detail="Trade not found or not active.")
    return {"status": "closing", "message": "تم إرسال أمر الإغلاق إلى العامل."}

@app.get("/trades/history", tags=["Trades"])
async def get_trades_history(limit: int = 50, user_id: UUID = Depends(get_current_user)):
    """(ينفذ /trades/history) [cite: 65] - يجلب سجل الصفقات."""
    history = await db_utils.get_trades_history(user_id, limit)
    return history

@app.get("/trades/stats", tags=["Trades"])
async def get_trades_stats(user_id: UUID = Depends(get_current_user)):
    """(ينفذ /trades/stats) [cite: 66] - يجلب الإحصائيات."""
    stats = await db_utils.get_trades_stats(user_id)
    return stats

# --- 4. Strategies (Scanners) [cite: 67, 70] ---
# هذه المسارات تتعامل مع جدول "strategies" الذي تتحكم به الواجهة

@app.get("/strategies", tags=["Strategies & Scanners"])
async def get_strategies(user_id: UUID = Depends(get_current_user)):
    """(ينفذ /strategies) [cite: 67] - يجلب قائمة الاستراتيجيات من Supabase."""
    # (هذا مطابق لـ Scanners.tsx)
    async with db_utils.db_connection() as conn:
        records = await conn.fetch("SELECT * FROM strategies WHERE user_id = $1", user_id)
    return [dict(r) for r in records]

@app.post("/strategies/{strategy_name}/toggle", tags=["Strategies & Scanners"])
async def toggle_strategy(strategy_name: str, enabled_payload: dict = Body(...), user_id: UUID = Depends(get_current_user)):
    """(ينفذ /strategies/{name}/toggle) [cite: 67] - يفعل/يعطل استراتيجية."""
    is_enabled = enabled_payload.get('enabled', False)
    logger.info(f"API: User {user_id} setting strategy {strategy_name} to {is_enabled}")
    async with db_utils.db_connection() as conn:
        await conn.execute(
            "UPDATE strategies SET is_enabled = $1 WHERE user_id = $2 AND strategy_name = $3",
            is_enabled, user_id, strategy_name
        )
    return {"status": "success", "strategy_name": strategy_name, "is_enabled": is_enabled}

# (المسارات /scanners هي نفسها /strategies في هذا العقد)
@app.get("/scanners", tags=["Strategies & Scanners"])
async def get_scanners(user_id: UUID = Depends(get_current_user)):
    """(ينفذ /scanners) [cite: 70]"""
    return await get_strategies(user_id)

@app.post("/scanners/{scanner_name}/toggle", tags=["Strategies & Scanners"])
async def toggle_scanner(scanner_name: str, enabled_payload: dict = Body(...), user_id: UUID = Depends(get_current_user)):
    """(ينفذ /scanners/{name}/toggle) [cite: 70]"""
    return await toggle_strategy(scanner_name, enabled_payload, user_id)

# --- 5. Settings & Presets [cite: 68-69] ---

@app.get("/settings", tags=["Settings & Presets"])
async def get_bot_settings(user_id: UUID = Depends(get_current_user)):
    """(ينفذ GET /settings)  - يجلب الإعدادات المتقدمة."""
    settings = await db_utils.get_api_settings(user_id)
    if not settings:
        raise HTTPException(status_code=404, detail="Advanced variables not found for this user.")
    return settings

@app.post("/settings", tags=["Settings & Presets"])
async def update_bot_settings(settings: Dict[str, Any], user_id: UUID = Depends(get_current_user)):
    """(ينفذ POST /settings)  - يحدّث الإعدادات المتقدمة."""
    logger.info(f"API: User {user_id} updating advanced settings...")
    # (إزالة الحقول التي لا يجب تحديثها)
    settings.pop('id', None)
    settings.pop('user_id', None)
    settings.pop('updated_at', None)
    
    success = await db_utils.update_api_settings(user_id, settings)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to update settings.")
    return {"status": "success", "message": "تم تحديث الإعدادات بنجاح."}

class PresetPayload(BaseModel):
    preset_name: str

@app.post("/settings/preset", tags=["Settings & Presets"])
async def change_preset(payload: PresetPayload, user_id: UUID = Depends(get_current_user)):
    """(ينفذ /settings/preset)  - يطبق نمط جاهز."""
    logger.info(f"API: User {user_id} applying preset '{payload.preset_name}'")
    
    # جلب تعريفات الأنماط (التي في Presets.tsx)
    # 
    preset_definitions = {
        'strict': {"real_trade_size_usdt": 50, "max_concurrent_trades": 2, "risk_reward_ratio": 3.0, "max_daily_loss_pct": 2.0},
        'professional': {"real_trade_size_usdt": 100, "max_concurrent_trades": 3, "risk_reward_ratio": 2.5, "max_daily_loss_pct": 3.0},
        'lenient': {"real_trade_size_usdt": 150, "max_concurrent_trades": 5, "risk_reward_ratio": 2.0, "max_daily_loss_pct": 5.0},
        'very_lenient': {"real_trade_size_usdt": 200, "max_concurrent_trades": 7, "risk_reward_ratio": 1.5, "max_daily_loss_pct": 7.0},
        'bold_heart': {"real_trade_size_usdt": 300, "max_concurrent_trades": 10, "risk_reward_ratio": 1.2, "max_daily_loss_pct": 10.0}
    }
    
    settings_to_apply = preset_definitions.get(payload.preset_name)
    if not settings_to_apply:
        raise HTTPException(status_code=404, detail="Preset not found.")
    
    # (هذه مجرد عينة، يجب نسخ كل الإعدادات من Presets.tsx)
    # (يجب أيضاً تحديث `enabled_strategies` في جدول `strategies`)
    
    success = await db_utils.apply_preset_settings(user_id, payload.preset_name, settings_to_apply)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to apply preset.")
        
    return {"status": "success", "message": f"تم تطبيق نمط '{payload.preset_name}' بنجاح."}

# --- 6. Notifications & Health [cite: 69-70] ---

@app.get("/notifications", tags=["Notifications & Health"])
async def get_notifications(limit: int = 50, unread_only: bool = False, user_id: UUID = Depends(get_current_user)):
    """(ينفذ /notifications) - يجلب الإشعارات."""
    notifications = await db_utils.get_notifications(user_id, limit, unread_only)
    return notifications

@app.post("/notifications/{notification_id}/read", tags=["Notifications & Health"])
async def mark_notification_read(notification_id: int, user_id: UUID = Depends(get_current_user)):
    """(ينفذ /notifications/{id}/read) [cite: 13-14] - يضع علامة "مقروء"."""
    success = await db_utils.mark_notification_read(user_id, notification_id)
    if not success:
        raise HTTPException(status_code=404, detail="Notification not found.")
    return {"status": "success"}

@app.get("/health", tags=["Notifications & Health"])
async def health_check():
    """(ينفذ /health) [cite: 70] - فحص صحة الخادم."""
    return {"status": "ok", "timestamp": datetime.now().isoformat()}

# =======================================================================================
# --- خدمة واجهة الويب (Web UI) ---
# [cite: 10-12, 18]
# =======================================================================================

# 1. تحديد مسار مجلد 'dist'
UI_BUILD_DIR = os.path.join(os.path.dirname(__file__), "dist")

# 2. خدمة ملفات assets (JS/CSS)
app.mount("/assets", StaticFiles(directory=os.path.join(UI_BUILD_DIR, "assets")), name="assets")

# 3. خدمة الملف الرئيسي index.html لجميع المسارات الأخرى
@app.get("/{full_path:path}", response_class=HTMLResponse)
async def serve_react_app(request: Request, full_path: str):
    index_path = os.path.join(UI_BUILD_DIR, "index.html")
    if not os.path.exists(index_path):
        logger.warning(f"index.html not found at {index_path}")
        return HTMLResponse("<h1>Frontend build files (dist) not found.</h1>", status_code=404)
    
    return FileResponse(index_path)

# =======================================================================================
# --- أحداث بدء وإيقاف التشغيل ---
# =======================================================================================

@app.on_event("startup")
async def on_startup():
    await db_utils.get_db_pool() # تهيئة مجموعة الاتصالات
    await PUBLIC_EXCHANGE.load_markets() # تحميل الأسواق العامة
    logger.info("--- 🚀 FastAPI Server Started (V2 - Matched to UI) ---")

@app.on_event("shutdown")
async def on_shutdown():
    await close_all_cached_connections()
    await PUBLIC_EXCHANGE.close()
    if db_utils.POOL:
        await db_utils.POOL.close()
    logger.info("--- 🛑 FastAPI Server Shutdown ---")

if __name__ == "__main__":
    # هذا التشغيل للتطوير فقط
    # في الإنتاج، استخدم Gunicorn
    port = int(os.getenv("PORT", 8000)) # الواجهة تتوقع 8000
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)
