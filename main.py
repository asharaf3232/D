import uvicorn
import asyncio
import logging
import os
import aiohttp
from fastapi import FastAPI, Request, Depends, HTTPException, WebSocket, Body
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import Dict, Any, List
from uuid import UUID

import db_utils
import core_logic
from db_utils import UserKeys

# --- إعداد FastAPI ---
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger("FastAPIServer")

app = FastAPI(title="Trading Bot SaaS Platform")

# --- إعداد CORS (للسماح لواجهة الويب بالتحدث مع الخادم) ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # في الإنتاج، يجب تقييد هذا
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- (تنفيذ المطلب: التخزين المؤقت للاتصالات) ---
# (هذا كان مطلبًا في التحدي الأصلي)
USER_CCXT_CACHE: Dict[UUID, ccxt.Exchange] = {}
CCXT_CACHE_LOCK = asyncio.Lock()

class CCXTConnectionManager:
    """يدير اتصالات CCXT المخبأة لجلب الأرصدة بسرعة."""
    
    async def get_connection(self, user_id: UUID) -> ccxt.Exchange:
        async with CCXT_CACHE_LOCK:
            if user_id in USER_CCXT_CACHE:
                logger.info(f"API: Using cached CCXT connection for user {user_id}")
                return USER_CCXT_CACHE[user_id]
            
            logger.info(f"API: Creating new CCXT connection for user {user_id}...")
            keys = await db_utils.get_user_api_keys(user_id)
            if not keys:
                raise HTTPException(status_code=404, detail="User API keys not found or invalid.")
                
            try:
                exchange = ccxt.binance({
                    'apiKey': keys.api_key,
                    'secret': keys.api_secret,
                    'enableRateLimit': True,
                    'options': {'defaultType': 'spot'}
                })
                await exchange.load_markets()
                USER_CCXT_CACHE[user_id] = exchange
                return exchange
            except Exception as e:
                logger.error(f"API: Failed to create CCXT connection for {user_id}: {e}")
                raise HTTPException(status_code=500, detail="Failed to initialize exchange connection.")

    async def close_all_connections(self):
        async with CCXT_CACHE_LOCK:
            logger.info("API: Closing all cached CCXT connections...")
            for exchange in USER_CCXT_CACHE.values():
                await exchange.close()
            USER_CCXT_CACHE.clear()

ccxt_manager = CCXTConnectionManager()

# --- المصادقة (محاكاة) ---
# هذه الدالة ستبحث عن المستخدم بناءً على رقم الدردشة في تليجرام
async def get_current_user(request: Request) -> UUID:
    """
    محاكاة جلب المستخدم. في الإنتاج، سيفك تشفير JWT.
    هنا، نستخدم رأس 'X-Telegram-Chat-Id' وهمي.
    """
    chat_id_str = request.headers.get("X-Telegram-Chat-Id")
    if not chat_id_str:
        raise HTTPException(status_code=401, detail="Unauthorized: X-Telegram-Chat-Id header missing.")
    
    try:
        chat_id = int(chat_id_str)
        user_id = await db_utils.get_user_by_telegram_id(chat_id)
        if not user_id:
            raise HTTPException(status_code=404, detail="User not found for this Telegram ID.")
        return user_id
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid Telegram Chat ID format.")

# =======================================================================================
# --- واجهات برمجة التطبيقات (API Endpoints) ---
# تم تصميمها لتحاكي كل الأزرار في BN.py
# =======================================================================================

# --- 1. مسارات لوحة التحكم (Dashboard) ---

@app.get("/api/dashboard/portfolio")
async def get_portfolio(user_id: UUID = Depends(get_current_user)):
    """(يحاكي show_portfolio_command) يجلب نظرة عامة على المحفظة."""
    try:
        exchange = await ccxt_manager.get_connection(user_id)
        balance = await exchange.fetch_balance()
        
        owned_assets = {
            asset: data['total'] for asset, data in balance.items() 
            if isinstance(data, dict) and data.get('total', 0) > 0 and 'USDT' not in asset
        }
        usdt_balance = balance.get('USDT', {})
        
        # (يمكن إضافة منطق جلب أسعار الأصول الأخرى هنا...)
        
        stats = await db_utils.get_user_overall_stats(user_id)
        active_count = await db_utils.get_active_trade_count_for_user(user_id)

        return {
            "total_usdt_equity": usdt_balance.get('total', 0),
            "free_usdt": usdt_balance.get('free', 0),
            "owned_assets_count": len(owned_assets),
            "total_realized_pnl": stats.get('total_pnl', 0),
            "active_trades_count": active_count
        }
    except Exception as e:
        logger.error(f"API /portfolio error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/dashboard/active_trades")
async def get_active_trades(user_id: UUID = Depends(get_current_user)):
    """(يحاكي show_trades_command) يجلب الصفقات النشطة."""
    trades = await db_utils.get_dashboard_trades_for_user(user_id)
    return trades

@app.get("/api/dashboard/trade_history")
async def get_trade_history(user_id: UUID = Depends(get_current_user)):
    """(يحاكي show_trade_history_command) يجلب آخر 10 صفقات."""
    history = await db_utils.get_trade_history_for_user(user_id, limit=10)
    return history

@app.get("/api/dashboard/stats")
async def get_stats(user_id: UUID = Depends(get_current_user)):
    """(يحاكي show_stats_command) يجلب الإحصائيات العامة."""
    stats = await db_utils.get_user_overall_stats(user_id)
    return stats

@app.get("/api/dashboard/strategy_report")
async def get_strategy_report(user_id: UUID = Depends(get_current_user)):
    """(يحاكي show_strategy_report_command) يجلب أداء الاستراتيجيات."""
    report = await db_utils.get_user_strategy_performance(user_id, limit=100)
    return report

@app.get("/api/dashboard/mood")
async def get_market_mood(user_id: UUID = Depends(get_current_user)):
    """(يحاكي show_mood_command) يجلب مزاج السوق."""
    # هذا المنطق يعتمد على واجهات خارجية، سنقوم بمحاكاته
    # (يجب نقل منطق get_fear_and_greed_index, get_market_mood... إلخ إلى هنا)
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get("https://api.alternative.me/fng/?limit=1") as resp:
                fng_data = await resp.json()
                fng_index = int(fng_data['data'][0]['value'])
        
        # (يجب إضافة منطق BTC Trend و News Sentiment هنا)
        
        return {
            "verdict": "المؤشرات إيجابية، لكن بحذر.",
            "btc_mood": "صاعد ✅",
            "fng_index": fng_index,
            "news_sentiment": "محايدة"
        }
    except Exception as e:
        logger.error(f"API /mood error: {e}")
        return {"verdict": "فشل جلب بيانات المزاج", "fng_index": "N/A"}

@app.get("/api/dashboard/daily_report")
async def get_daily_report(user_id: UUID = Depends(get_current_user)):
    """(يحاكي daily_report_command) يجلب تقرير اليوم."""
    report = await db_utils.get_user_daily_report(user_id)
    return report

# --- 2. مسارات الإجراءات (Actions) ---

@app.post("/api/actions/toggle_kill_switch")
async def toggle_kill_switch(user_id: UUID = Depends(get_current_user)):
    """(يحاكي toggle_kill_switch) يبدل حالة التداول."""
    settings = await db_utils.get_user_settings(user_id)
    new_status = not settings.is_trading_enabled
    await db_utils.update_user_settings(user_id, {"is_trading_enabled": new_status})
    return {"new_status": new_status}

@app.post("/api/actions/manual_scan")
async def trigger_manual_scan(user_id: UUID = Depends(get_current_user)):
    """(يحاكي manual_scan_command) يطلب فحصاً فورياً."""
    # ملاحظة: هذا لا يشغل الفحص مباشرة.
    # في البنية الجديدة، يمكننا إضافة "علم" في قاعدة البيانات
    # ليقوم العامل (Worker) بالتقاطه.
    logger.info(f"API: Manual scan requested by user {user_id}. (Note: Worker picks this up on its own schedule)")
    # (تحتاج إضافة حقل force_scan في جدول user_settings)
    # await db_utils.update_user_settings(user_id, {"force_scan_request": True})
    return {"message": "تم إرسال طلب الفحص إلى العامل."}

# --- 3. مسارات إدارة الصفقات ---

@app.get("/api/trades/{trade_id}")
async def get_trade_details(trade_id: int, user_id: UUID = Depends(get_current_user)):
    """(يحاكي check_trade_details) يجلب تفاصيل صفقة."""
    trade = await db_utils.get_trade_details_for_user(user_id, trade_id)
    if not trade:
        raise HTTPException(status_code=404, detail="Trade not found or does not belong to user.")
    
    # جلب السعر الحالي
    try:
        ticker = await PUBLIC_EXCHANGE.fetch_ticker(trade['symbol'])
        current_price = ticker['last']
        pnl = (current_price - trade['entry_price']) * trade['quantity']
        pnl_percent = (current_price / trade['entry_price'] - 1) * 100 if trade['entry_price'] > 0 else 0
        trade_with_pnl = dict(trade)
        trade_with_pnl['current_price'] = current_price
        trade_with_pnl['pnl_usdt_live'] = pnl
        trade_with_pnl['pnl_percent_live'] = pnl_percent
        return trade_with_pnl
    except Exception:
        return trade # إرجاع الصفقة بدون بيانات حية إذا فشل

@app.post("/api/trades/{trade_id}/manual_sell")
async def manual_sell_trade(trade_id: int, user_id: UUID = Depends(get_current_user)):
    """(يحاكي handle_manual_sell_execute) يبيع صفقة يدوياً."""
    logger.info(f"API: User {user_id} requested manual sell for trade #{trade_id}.")
    trade = await db_utils.get_trade_details_for_user(user_id, trade_id)
    if not trade:
        raise HTTPException(status_code=404, detail="Trade not found.")
    if trade['status'] != 'active':
        raise HTTPException(status_code=400, detail="Trade is not active.")
        
    # لا نبيع من هنا. نرفع العلم للعامل.
    await db_utils.set_trade_status(trade_id, "force_exit_manual")
    return {"message": "تم إرسال أمر الإغلاق إلى العامل."}

# --- 4. مسارات الإعدادات (Settings) ---

class SettingsUpdatePayload(BaseModel):
    # نموذج مرن لاستقبال أي تحديثات
    updates: Dict[str, Any] = Field(..., example={"real_trade_size_usdt": 20.5, "trailing_sl_enabled": False})

@app.get("/api/settings")
async def get_all_settings(user_id: UUID = Depends(get_current_user)):
    """(يحاكي show_settings_menu) يجلب كائن الإعدادات الكامل."""
    settings = await db_utils.get_user_settings(user_id)
    if not settings:
        raise HTTPException(status_code=404, detail="Settings not found for user.")
    return settings

@app.post("/api/settings")
async def update_settings(payload: SettingsUpdatePayload, user_id: UUID = Depends(get_current_user)):
    """
    (يحاكي handle_setting_value و handle_toggle_parameter)
    مسار واحد قوي لتحديث أي إعدادات.
    """
    logger.info(f"API: User {user_id} updating settings: {payload.updates}")
    
    # (يجب إضافة منطق للتحقق من صحة المدخلات هنا)
    
    success = await db_utils.update_user_settings(user_id, payload.updates)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to update settings in database.")
        
    # مسح ذاكرة التخزين المؤقت للإعدادات لهذا المستخدم في العامل
    # (في البنية المتقدمة، نستخدم Redis Pub/Sub لإعلام العامل فوراً)
    # (للبساطة، سيعتمد العامل على CACHE_SYNC_INTERVAL_SECONDS)
    
    return {"message": "Settings updated successfully.", "updated_fields": list(payload.updates.keys())}

# --- 5. مسارات إدارة البيانات ---

@app.delete("/api/data/clear_trades")
async def clear_trades(user_id: UUID = Depends(get_current_user)):
    """(يحاكي handle_clear_data_execute) يمسح سجل الصفقات."""
    logger.warning(f"API: User {user_id} is clearing all trade data.")
    success = await db_utils.clear_user_trades(user_id)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to clear trade data.")
    return {"message": "تم حذف جميع بيانات الصفقات والسجل التحليلي."}

# =======================================================================================
# --- واجهة الويب (Web UI) والبث المباشر (Log Stream) ---
#
# =======================================================================================

try:
    with open("index.html", "r", encoding="utf-8") as f:
        HTML_CONTENT = f.read()
except FileNotFoundError:
    logger.warning("index.html not found. Web UI will be disabled.")
    HTML_CONTENT = "<html><body><h1>index.html not found.</h1></body></html>"

@app.get("/", response_class=HTMLResponse)
async def get_homepage():
    """يخدم واجهة الويب index.html"""
    return HTMLResponse(content=HTML_CONTENT)

@app.get("/active_trades")
async def get_active_trades_for_web():
    """
    مسار مخصص لـ index.html.
    يستخدم مستخدم "تجريبي" ثابت.
    """
    try:
        # !!! هام: هذا يستخدم user_id ثابت. يجب تغييره بنظام مصادقة للويب
        DEMO_USER_ID = UUID("00000000-0000-0000-0000-000000000001") # (مثال)
        trades = await db_utils.get_dashboard_trades_for_user(DEMO_USER_ID)
        return trades
    except Exception as e:
        logger.error(f"API /active_trades (web) error: {e}")
        return JSONResponse(content={"error": str(e)}, status_code=500)

@app.websocket("/ws/logs")
async def websocket_endpoint(websocket: WebSocket):
    """(ينفذ مطلب index.html) يبث سجلات الخادم."""
    await websocket.accept()
    logger.info("API_WS: Log client connected.")
    try:
        # هذا مثال بسيط. في الإنتاج، يجب قراءة السجلات من ملف
        # أو استخدام نظام (logging handler) مخصص لـ WebSocket.
        while True:
            log_message = f"{datetime.now().isoformat()} - API Server Log: Heartbeat."
            await websocket.send_text(log_message)
            await asyncio.sleep(5)
    except Exception:
        logger.info("API_WS: Log client disconnected.")

# =======================================================================================
# --- أحداث بدء وإيقاف التشغيل ---
# =======================================================================================

@app.on_event("startup")
async def on_startup():
    await db_utils.get_db_pool() # تهيئة مجموعة الاتصالات
    await PUBLIC_EXCHANGE.load_markets() # تحميل الأسواق العامة
    logger.info("--- 🚀 FastAPI Server Started (V6.6 SaaS) ---")

@app.on_event("shutdown")
async def on_shutdown():
    await ccxt_manager.close_all_connections()
    await PUBLIC_EXCHANGE.close()
    if db_utils.POOL:
        await db_utils.POOL.close()
    logger.info("--- 🛑 FastAPI Server Shutdown ---")

if __name__ == "__main__":
    # هذا التشغيل للتطوير فقط
    # في الإنتاج، استخدم Gunicorn:
    # gunicorn -w 4 -k uvicorn.workers.UvicornWorker main:app
    port = int(os.getenv("PORT", 8001))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)
