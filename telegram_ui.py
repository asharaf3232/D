# -*- coding: utf-8 -*-
# =======================================================================================
# --- 🚀 واجهة بوت التداول V4.1 (SaaS Client - الربط الآمن) 🚀 ---
# =======================================================================================
#
# هذا الملف هو واجهة المستخدم (UI) فقط.
# [تحديث V4.1] يستخدم هذا الإصدار /login <token> للربط الآمن لمرة واحدة.
#
# =======================================================================================

import os
import logging
import asyncio
import httpx
import json
from datetime import datetime
from uuid import UUID

# --- مكتبات تليجرام ---
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters, CallbackQueryHandler
from telegram.constants import ParseMode
from telegram.error import BadRequest, TimedOut, Forbidden

# --- إعدادات أساسية ---
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# --- متغيرات البيئة ---
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
API_SERVER_URL = os.getenv('API_SERVER_URL', 'http://127.0.0.1:8000') # خادم V4

# --- ثوابت (من BN.py) ---
STRATEGY_NAMES_AR = {
    "momentum_breakout": "زخم اختراقي", "breakout_squeeze_pro": "اختراق انضغاطي",
    "support_rebound": "ارتداد الدعم", "sniper_pro": "القناص المحترف", "whale_radar": "رادار الحيتان",
    "rsi_divergence": "دايفرجنس RSI", "supertrend_pullback": "انعكاس سوبرترند"
}
PRESET_NAMES_AR = {"professional": "احترافي", "strict": "متشدد", "lenient": "متساهل", "very_lenient": "فائق التساهل", "bold_heart": "القلب الجريء"}

# =======================================================================================
# --- دوال مساعدة للاتصال بـ API ---
# =======================================================================================

async def get_api_headers(context: ContextTypes.DEFAULT_TYPE) -> dict:
    """
    [تصميم V4.1 الآمن]
    يستخدم التوكن (user_id) المخزن في ذاكرة الجلسة للمصادقة.
    """
    user_id_token = context.user_data.get('user_id_token')
    if not user_id_token:
        logger.warning("User ID token not found in context.user_data. User must /login.")
        raise ValueError("أنت غير مسجل. الرجاء استخدام أمر /login <token> أولاً.")
        
    return {'Authorization': f'Bearer {user_id_token}'}


async def safe_send_message(bot, chat_id, text, **kwargs):
    """(من BN.py) إرسال رسالة بشكل آمن."""
    try:
        await bot.send_message(chat_id, text, parse_mode=ParseMode.MARKDOWN, **kwargs)
    except Exception as e:
        logger.error(f"Telegram Send Error: {e}")

async def safe_edit_message(query: Update.callback_query, text: str, **kwargs):
    """(من BN.py) تعديل رسالة بشكل آمن."""
    try: 
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, **kwargs)
    except BadRequest as e:
        if "Message is not modified" not in str(e): 
            logger.warning(f"Edit Message Error: {e}")
    except Exception as e: 
        logger.error(f"Edit Message Error: {e}")

async def handle_api_error(query: Update.callback_query, error: httpx.HTTPStatusError):
    """يعالج أخطاء API ويعرض رسالة للمستخدم."""
    error_details = "خطأ غير معروف"
    try:
        error_details = error.response.json().get('detail', error.response.text)
    except json.JSONDecodeError:
        error_details = error.response.text
        
    await safe_edit_message(query, f"❌ حدث خطأ في الخادم:\n`{error.response.status_code}: {error_details}`",
                            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 العودة للوحة التحكم", callback_data="back_to_dashboard")]]))

async def get_settings_from_cache_or_api(context: ContextTypes.DEFAULT_TYPE) -> dict:
    """يجلب الإعدادات المتقدمة (من /settings) من الذاكرة المؤقتة أو يطلبها من الـ API."""
    if 'settings_cache' in context.user_data:
        return context.user_data['settings_cache']
    
    headers = await get_api_headers(context)
    async with httpx.AsyncClient() as client:
        response = await client.get(f"{API_SERVER_URL}/settings", headers=headers) 
        response.raise_for_status()
        settings = response.json()
        context.user_data['settings_cache'] = settings
        return settings

async def clear_settings_cache(context: ContextTypes.DEFAULT_TYPE):
    """يمسح ذاكرة التخزين المؤقت للإعدادات بعد التحديث."""
    if 'settings_cache' in context.user_data:
        del context.user_data['settings_cache']

# =======================================================================================
# --- [جديد V4.1] المصادقة والربط ---
# =======================================================================================

async def login_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    (جديد) الربط الآمن لمرة واحدة.
    1. يحفظ التوكن في الذاكرة للأوامر.
    2. يرسل التوكن + chat_id للخادم لربط الإشعارات.
    """
    if not context.args:
        await update.message.reply_text("الرجاء إدخال الـ User ID (Token) الخاص بك. \nمثال: `/login <your-uuid-token>`\n\n(يمكنك نسخه من صفحة ملفك الشخصي في واجهة الويب)")
        return
        
    user_id_token = context.args[0]
    chat_id = update.message.chat_id
    
    try:
        # 1. التحقق من أن التوكن صالح (UUID)
        UUID(user_id_token, version=4)
        
        # 2. إرسال طلب الربط إلى الخادم (لربط الإشعارات)
        headers = {'Authorization': f'Bearer {user_id_token}'} # استخدام التوكن الجديد للمصادقة
        payload = {'telegram_chat_id': chat_id}
        
        async with httpx.AsyncClient() as client:
            response = await client.post(f"{API_SERVER_URL}/telegram/link-account", json=payload, headers=headers, timeout=10.0)
            response.raise_for_status() # (سيثير خطأ إذا فشل الربط 400/500)
        
        # 3. إذا نجح الربط، احفظ التوكن في الذاكرة (للأوامر المستقبلية)
        context.user_data['user_id_token'] = user_id_token
        await update.message.reply_text(f"✅ تم ربط حسابك بنجاح!\nمعرف الدردشة `{chat_id}` مرتبط الآن بحسابك.\n\nأنت جاهز لاستقبال الإشعارات واستخدام لوحة التحكم.")
    
    except (ValueError, TypeError):
        await update.message.reply_text("❌ الـ Token غير صالح. الرجاء إدخال UUID صحيح.")
    except httpx.HTTPStatusError as e:
        error_msg = e.response.json().get('detail', str(e))
        await update.message.reply_text(f"❌ فشل الربط:\n`{error_msg}`\n\nتأكد أن التوكن صحيح وأن حساب التليجرام هذا غير مرتبط بحساب آخر.")
    except Exception as e:
        await update.message.reply_text(f"❌ حدث خطأ غير متوقع: {e}")


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """(من BN.py) - معدل لـ V4.1"""
    keyboard = [["Dashboard 🖥️"], ["الإعدادات ⚙️"]]
    await update.message.reply_text("أهلاً بك في **بوت باينانس V4 (SaaS)**\n\n"
                                  "لربط هذا البوت بحسابك على واجهة الويب (مرة واحدة فقط):\n"
                                  "1. اذهب إلى واجهة الويب وانسخ `User ID` (التوكن) الخاص بك.\n"
                                  "2. أرسل الأمر التالي هنا:\n`/login <Your-User-ID-Token>`",
                                  reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True), 
                                  parse_mode=ParseMode.MARKDOWN)

# =======================================================================================
# --- واجهة تليجرام (مطابقة لـ V3 API) ---
# =======================================================================================

async def show_dashboard_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """(يحاكي show_dashboard_command) - معدل لـ /bot/status"""
    query = update.callback_query
    target_message = update.message or query.message
    
    ks_status_emoji = "⏳"
    ks_status_text = "جاري التحميل..."
    try:
        headers = await get_api_headers(context)
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{API_SERVER_URL}/bot/status", headers=headers)
            response.raise_for_status()
            status = response.json()
            is_enabled = status.get('is_running', False)
            ks_status_emoji = "✅" if is_enabled else "🚨"
            ks_status_text = "الحالة (طبيعية)" if is_enabled else "مفتاح الإيقاف (مفعل)"

    except (ValueError, httpx.HTTPStatusError) as e:
        logger.error(f"Failed to fetch bot status: {e}")
        error_detail = "غير معروف"
        if isinstance(e, ValueError):
            error_detail = str(e) # (سيعرض "أنت غير مسجل...")
        else:
            error_detail = e.response.json().get('detail', 'خطأ في الاتصال')
        ks_status_emoji = "❓"
        ks_status_text = f"خطأ ({error_detail})"
    except Exception as e:
        logger.error(f"Failed to fetch bot status: {e}")
        ks_status_emoji = "❓"
        ks_status_text = "خطأ في الاتصال"

    # (إعادة الأزرار المفقودة)
    keyboard = [
        [InlineKeyboardButton("💼 نظرة عامة على المحفظة", callback_data="db_portfolio"), InlineKeyboardButton("📈 الصفقات النشطة", callback_data="db_trades")],
        [InlineKeyboardButton("📜 سجل الصفقات المغلقة", callback_data="db_history"), InlineKeyboardButton("📊 الإحصائيات والأداء", callback_data="db_stats")],
        [InlineKeyboardButton("🌡️ تحليل مزاج السوق", callback_data="db_mood")], 
        [InlineKeyboardButton(f"{ks_status_emoji} {ks_status_text}", callback_data="kill_switch_toggle"), InlineKeyboardButton("🕵️‍♂️ تقرير التشخيص", callback_data="db_diagnostics")]
    ]
    message_text = "🖥️ **لوحة تحكم البوت (SaaS)**\n\nاختر نوع التقرير الذي تريد عرضه:"
    if ks_status_emoji == "🚨": message_text += "\n\n**تحذير: تم تفعيل مفتاح الإيقاف.**"

    if query: 
        await safe_edit_message(query, message_text, reply_markup=InlineKeyboardMarkup(keyboard))
    else: 
        await target_message.reply_text(message_text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))

async def toggle_kill_switch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """(يحاكي toggle_kill_switch) - معدل لـ /bot/start و /bot/stop"""
    query = update.callback_query
    await query.answer("جاري إرسال الأمر...")
    try:
        headers = await get_api_headers(context)
        async with httpx.AsyncClient() as client:
            status_res = await client.get(f"{API_SERVER_URL}/bot/status", headers=headers)
            status_res.raise_for_status()
            is_currently_running = status_res.json().get('is_running', False)
            
            endpoint = "/bot/stop" if is_currently_running else "/bot/start"
            response = await client.post(f"{API_SERVER_URL}{endpoint}", headers=headers)
            response.raise_for_status()
            new_status = response.json().get('is_running', False)
        
        if new_status: 
            await query.answer("✅ تم استئناف التداول الطبيعي.")
        else: 
            await query.answer("🚨 تم تفعيل مفتاح الإيقاف!", show_alert=True)
        
        await show_dashboard_command(update, context) # تحديث لوحة التحكم
    except (ValueError, httpx.HTTPStatusError) as e: await handle_api_error(query, e)
    except Exception as e: await safe_edit_message(query, f"❌ خطأ: {e}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 العودة", callback_data="back_to_dashboard")]]))

async def show_portfolio_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """(يحاكي show_portfolio_command) - معدل لـ /bot/balance و /trades/stats"""
    query = update.callback_query; await query.answer("جاري جلب بيانات المحفظة...")
    try:
        headers = await get_api_headers(context)
        async with httpx.AsyncClient() as client:
            balance_res = await client.get(f"{API_SERVER_URL}/bot/balance", headers=headers)
            stats_res = await client.get(f"{API_SERVER_URL}/trades/stats", headers=headers)
            trades_res = await client.get(f"{API_SERVER_URL}/trades/active", headers=headers)
            balance_res.raise_for_status(); stats_res.raise_for_status(); trades_res.raise_for_status()
            portfolio = balance_res.json(); stats = stats_res.json(); active_trades = trades_res.json()
        
        message = (
            f"**💼 نظرة عامة على المحفظة**\n🗓️ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n**💰 إجمالي الرصيد (USDT):** `≈ ${portfolio['total_balance']:,.2f}`\n"
            f"  - **السيولة المتاحة (USDT):** `${portfolio['available_balance']:,.2f}`\n"
            f"━━━━━━━━━━━━━━━━━━━━\n**📈 أداء التداول:**\n"
            f"  - **الربح/الخسارة المحقق:** `${stats.get('total_pnl_usdt', 0):,.2f}`\n"
            f"  - **عدد الصفقات النشطة:** {len(active_trades)}\n"
        )
        keyboard = [[InlineKeyboardButton("🔄 تحديث", callback_data="db_portfolio")], [InlineKeyboardButton("🔙 العودة للوحة التحكم", callback_data="back_to_dashboard")]]
        await safe_edit_message(query, message, reply_markup=InlineKeyboardMarkup(keyboard))
    except (ValueError, httpx.HTTPStatusError) as e: await handle_api_error(query, e)
    except Exception as e: await safe_edit_message(query, f"❌ خطأ: {e}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 العودة", callback_data="back_to_dashboard")]]))

async def show_trades_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """(يحاكي show_trades_command) - معدل لـ /trades/active"""
    query = update.callback_query; await query.answer("جاري جلب الصفقات النشطة...")
    try:
        headers = await get_api_headers(context)
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{API_SERVER_URL}/trades/active", headers=headers)
            response.raise_for_status(); trades = response.json()
        if not trades:
            text = "لا توجد صفقات نشطة حاليًا."
            keyboard = [[InlineKeyboardButton("🔙 العودة للوحة التحكم", callback_data="back_to_dashboard")]]
            await safe_edit_message(query, text, reply_markup=InlineKeyboardMarkup(keyboard)); return
        text = "📈 *الصفقات النشطة*\nاختر صفقة لعرض تفاصيلها:\n"; keyboard = []
        for trade in trades: 
            button_text = f"#{trade['id']} ✅ | {trade['symbol']}"
            keyboard.append([InlineKeyboardButton(button_text, callback_data=f"check_{trade['id']}")])
        keyboard.append([InlineKeyboardButton("🔄 تحديث", callback_data="db_trades")])
        keyboard.append([InlineKeyboardButton("🔙 العودة للوحة التحكم", callback_data="back_to_dashboard")])
        await safe_edit_message(query, text, reply_markup=InlineKeyboardMarkup(keyboard))
    except (ValueError, httpx.HTTPStatusError) as e: await handle_api_error(query, e)
    except Exception as e: await safe_edit_message(query, f"❌ خطأ: {e}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 العودة", callback_data="back_to_dashboard")]]))

async def check_trade_details(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """(يحاكي check_trade_details)"""
    query = update.callback_query; trade_id = int(query.data.split('_')[1]); await query.answer("جاري جلب تفاصيل الصفقة...")
    try:
        headers = await get_api_headers(context)
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{API_SERVER_URL}/trades/active", headers=headers)
            response.raise_for_status(); trades = response.json()
            trade = next((t for t in trades if t['id'] == trade_id), None)
            if not trade:
                 await safe_edit_message(query, "لم يتم العثور على الصفقة (ربما أُغلقت؟)", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 العودة للصفقات", callback_data="db_trades")]])); return
        keyboard = [
            [InlineKeyboardButton("🚨 بيع فوري (بسعر السوق)", callback_data=f"manual_sell_confirm_{trade_id}")],
            [InlineKeyboardButton("🔙 العودة للصفقات", callback_data="db_trades")]
        ]
        message = (
            f"**✅ حالة الصفقة #{trade_id}**\n\n"
            f"- **العملة:** `{trade['symbol']}`\n- **سعر الدخول:** `${trade['entry_price']}`\n- **الكمية:** `{trade['quantity']}`\n"
            f"----------------------------------\n- **الهدف (TP):** `${trade['take_profit']}`\n- **الوقف (SL):** `${trade['stop_loss']}`\n----------------------------------\n"
            f"💰 (الربح/الخسارة الحي متاح في واجهة الويب)"
        )
        await safe_edit_message(query, message, reply_markup=InlineKeyboardMarkup(keyboard))
    except (ValueError, httpx.HTTPStatusError) as e: await handle_api_error(query, e)
    except Exception as e: await safe_edit_message(query, f"❌ خطأ: {e}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 العودة", callback_data="db_trades")]]))

async def show_trade_history_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """(يحاكي show_trade_history_command) - معدل لـ /trades/history"""
    query = update.callback_query; await query.answer("جاري جلب السجل...")
    try:
        headers = await get_api_headers(context)
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{API_SERVER_URL}/trades/history?limit=10", headers=headers)
            response.raise_for_status(); closed_trades = response.json()
        if not closed_trades: text = "لم يتم إغلاق أي صفقات بعد."
        else:
            history_list = ["📜 *آخر 10 صفقات مغلقة*"]
            for trade in closed_trades:
                pnl = trade['pnl_usdt'] or 0.0; emoji = "✅" if pnl > 0 else "🛑"
                history_list.append(f"{emoji} `{trade['symbol']}` | الربح/الخسارة: `${pnl:,.2f}`")
            text = "\n".join(history_list)
        keyboard = [[InlineKeyboardButton("🔙 العودة للوحة التحكم", callback_data="back_to_dashboard")]]
        await safe_edit_message(query, text, reply_markup=InlineKeyboardMarkup(keyboard))
    except (ValueError, httpx.HTTPStatusError) as e: await handle_api_error(query, e)
    except Exception as e: await safe_edit_message(query, f"❌ خطأ: {e}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 العودة", callback_data="back_to_dashboard")]]))

async def show_stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """(يحاكي show_stats_command) - معدل لـ /trades/stats"""
    query = update.callback_query; await query.answer("جاري جلب الإحصائيات...")
    try:
        headers = await get_api_headers(context)
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{API_SERVER_URL}/trades/stats", headers=headers)
            response.raise_for_status(); stats = response.json()
        if stats['total_trades'] == 0:
            await safe_edit_message(query, "لم يتم إغلاق أي صفقات بعد.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 العودة للوحة التحكم", callback_data="back_to_dashboard")]])); return
        message = (
            f"📊 **إحصائيات الأداء التفصيلية**\n━━━━━━━━━━━━━━━━━━\n"
            f"**إجمالي الربح/الخسارة:** `${stats['total_pnl_usdt']:+.2f}`\n**عامل الربح (Profit Factor):** `{stats['profit_factor']:,.2f}`\n"
            f"**معدل النجاح:** {stats['win_rate']:.1f}%\n**إجمالي الصفقات:** {stats['total_trades']}\n"
            f"**صفقات رابحة:** {stats['winning_trades']}\n**صفقات خاسرة:** {stats['losing_trades']}"
        )
        keyboard = [[InlineKeyboardButton("🔙 العودة للوحة التحكم", callback_data="back_to_dashboard")]]
        await safe_edit_message(query, message, reply_markup=InlineKeyboardMarkup(keyboard))
    except (ValueError, httpx.HTTPStatusError) as e: await handle_api_error(query, e)
    except Exception as e: await safe_edit_message(query, f"❌ خطأ: {e}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 العودة", callback_data="back_to_dashboard")]]))

async def show_mood_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """(يحاكي show_mood_command) - معدل لـ /telegram/mood"""
    query = update.callback_query; await query.answer("جاري تحليل مزاج السوق...")
    try:
        headers = await get_api_headers(context)
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.get(f"{API_SERVER_URL}/telegram/mood", headers=headers)
            response.raise_for_status(); mood = response.json()
        message = (
            f"**🌡️ تحليل مزاج السوق الشامل**\n━━━━━━━━━━━━━━━━━━━━\n**⚫️ الخلاصة:** *{mood['verdict']}*\n━━━━━━━━━━━━━━━━━━━━\n**📊 المؤشرات الرئيسية:**\n"
            f"  - **اتجاه BTC العام:** {mood.get('btc_mood', 'N/A')}\n  - **الخوف والطمع:** {mood.get('fng_index', 'N/A')}\n  - **مشاعر الأخبار:** {mood.get('news_sentiment', 'N/A')}\n")
        keyboard = [[InlineKeyboardButton("🔄 تحديث", callback_data="db_mood")], [InlineKeyboardButton("🔙 العودة للوحة التحكم", callback_data="back_to_dashboard")]]
        await safe_edit_message(query, message, reply_markup=InlineKeyboardMarkup(keyboard))
    except (ValueError, httpx.HTTPStatusError) as e: await handle_api_error(query, e)
    except Exception as e: await safe_edit_message(query, f"❌ خطأ: {e}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 العودة", callback_data="back_to_dashboard")]]))

async def show_diagnostics_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """(يحاكي show_diagnostics_command) - معدل لـ /telegram/diagnostics"""
    query = update.callback_query; await query.answer("جاري جلب التشخيصات...")
    try:
        headers = await get_api_headers(context)
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{API_SERVER_URL}/telegram/diagnostics", headers=headers)
            response.raise_for_status(); diag = response.json()
        
        expires_at_dt = datetime.fromisoformat(diag['subscription_expires_at'])
        expires_str = expires_at_dt.strftime('%Y-%m-%d %H:%M')

        report = (
            f"🕵️‍♂️ *تقرير التشخيص (SaaS)*\n\nتم إنشاؤه في: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n----------------------------------\n"
            f"⚙️ **حالة النظام**\n- اتصال الخادم (API): {diag['api_status']}\n- اتصال قاعدة البيانات: {diag['db_status']}\n\n"
            f"💳 **حالة الاشتراك**\n"
            f"- الحالة: `{diag['subscription_status']}`\n"
            f"- ينتهي في: `{expires_str}`\n\n"
            f"🔧 **الإعدادات النشطة**\n- **النمط الحالي: {diag['active_preset_name']}**\n- الماسحات المفعلة:\n{diag['active_scanners_report']}\n----------------------------------\n"
            f"🔩 **إحصائيات قاعدة البيانات**\n  - إجمالي الصفقات المغلقة: {diag['total_closed_trades']}\n")
        await safe_edit_message(query, report, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔄 تحديث", callback_data="db_diagnostics")], [InlineKeyboardButton("🔙 العودة للوحة التحكم", callback_data="back_to_dashboard")]]))
    except (ValueError, httpx.HTTPStatusError) as e: await handle_api_error(query, e)
    except Exception as e: await safe_edit_message(query, f"❌ خطأ: {e}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 العودة", callback_data="back_to_dashboard")]]))

# =======================================================================================
# --- واجهة الإعدادات (النسخة الكاملة V4.1) ---
# =======================================================================================

async def show_settings_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """(من BN.py) - معدل"""
    keyboard = [
        [InlineKeyboardButton("🧠 إعدادات الذكاء التكيفي", callback_data="settings_adaptive")],
        [InlineKeyboardButton("🎛️ تعديل المعايير المتقدمة", callback_data="settings_params")],
        [InlineKeyboardButton("🔭 تفعيل/تعطيل الماسحات", callback_data="settings_scanners")],
        [InlineKeyboardButton("🗂️ أنماط جاهزة", callback_data="settings_presets")],
    ]
    message_text = "⚙️ *الإعدادات الرئيسية*\n\nاختر فئة الإعدادات التي تريد تعديلها."
    target_message = update.message or update.callback_query.message
    if update.callback_query: 
        await safe_edit_message(update.callback_query, message_text, reply_markup=InlineKeyboardMarkup(keyboard))
    else: 
        await target_message.reply_text(message_text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))

async def show_adaptive_intelligence_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """(من BN.py) - معدل لـ API (النسخة الكاملة)"""
    query = update.callback_query; await query.answer("جاري جلب إعدادات الذكاء...")
    try:
        s = await get_settings_from_cache_or_api(context) 
        def bool_format(key, text):
            val = s.get(key, False); emoji = "✅" if val else "❌"
            return f"{text}: {emoji} مفعل"
        keyboard = [
            [InlineKeyboardButton(bool_format('adaptive_intelligence_enabled', 'تفعيل الذكاء التكيفي'), callback_data="param_toggle_adaptive_intelligence_enabled")],
            [InlineKeyboardButton(bool_format('wise_man_auto_close', 'الإغلاق الآلي للرجل الحكيم'), callback_data="param_toggle_wise_man_auto_close")],
            [InlineKeyboardButton(bool_format('wise_guardian_enabled', 'تفعيل الحارس الحكيم (للخسائر)'), callback_data="param_toggle_wise_guardian_enabled")],
            [InlineKeyboardButton(bool_format('dynamic_trade_sizing_enabled', 'الحجم الديناميكي للصفقات'), callback_data="param_toggle_dynamic_trade_sizing_enabled")],
            [InlineKeyboardButton(bool_format('strategy_proposal_enabled', 'اقتراحات الاستراتيجيات (للعامل)'), callback_data="param_toggle_strategy_proposal_enabled")],
            [InlineKeyboardButton("--- معايير الضبط ---", callback_data="noop")],
            [InlineKeyboardButton(f"حد أدنى للتعطيل (WR%): {s.get('strategy_deactivation_threshold_wr', 45.0)}", callback_data="param_set_strategy_deactivation_threshold_wr")],
            [InlineKeyboardButton(f"أقل عدد صفقات للتحليل: {s.get('strategy_analysis_min_trades', 10)}", callback_data="param_set_strategy_analysis_min_trades")],
            [InlineKeyboardButton(f"أقصى زيادة للحجم (%): {s.get('dynamic_sizing_max_increase_pct', 25.0)}", callback_data="param_set_dynamic_sizing_max_increase_pct")],
            [InlineKeyboardButton(f"أقصى تخفيض للحجم (%): {s.get('dynamic_sizing_max_decrease_pct', 50.0)}", callback_data="param_set_dynamic_sizing_max_decrease_pct")],
            [InlineKeyboardButton("🔙 العودة للإعدادات", callback_data="settings_main")]
        ]
        await safe_edit_message(query, "🧠 **إعدادات الذكاء التكيفي**\n\nتحكم في كيفية تعلم البوت وتكيفه:", reply_markup=InlineKeyboardMarkup(keyboard))
    except (ValueError, httpx.HTTPStatusError) as e: await handle_api_error(query, e)
    except Exception as e: await safe_edit_message(query, f"❌ خطأ: {e}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 العودة", callback_data="settings_main")]]))

async def show_parameters_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """(من BN.py) - معدل لـ API (النسخة الكاملة)"""
    query = update.callback_query; await query.answer("جاري جلب المعايير...")
    try:
        s = await get_settings_from_cache_or_api(context) 
        def bool_format(key, text):
            val = s.get(key, False); emoji = "✅" if val else "❌"
            return f"{text}: {emoji} مفعل"
        keyboard = [
            [InlineKeyboardButton("--- إعدادات عامة ---", callback_data="noop")],
            [InlineKeyboardButton(f"عدد العملات للفحص: {s.get('top_n_symbols_by_volume', 300)}", callback_data="param_set_top_n_symbols_by_volume"),
             InlineKeyboardButton(f"أقصى عدد للصفقات: {s.get('max_concurrent_trades', 5)}", callback_data="param_set_max_concurrent_trades")],
            [InlineKeyboardButton("--- إعدادات المخاطر ---", callback_data="noop")],
            [InlineKeyboardButton(f"حجم الصفقة ($): {s.get('real_trade_size_usdt', 15.0)}", callback_data="param_set_real_trade_size_usdt"),
             InlineKeyboardButton(f"مضاعف وقف الخسارة (ATR): {s.get('atr_sl_multiplier', 2.5)}", callback_data="param_set_atr_sl_multiplier")],
            [InlineKeyboardButton(f"نسبة المخاطرة/العائد: {s.get('risk_reward_ratio', 2.0)}", callback_data="param_set_risk_reward_ratio")],
            [InlineKeyboardButton(bool_format('trailing_sl_enabled', 'تفعيل الوقف المتحرك'), callback_data="param_toggle_trailing_sl_enabled")],
            [InlineKeyboardButton(f"تفعيل الوقف المتحرك (%): {s.get('trailing_sl_activation_percent', 2.0)}", callback_data="param_set_trailing_sl_activation_percent")],
            [InlineKeyboardButton(f"مسافة الوقف المتحرك (%): {s.get('trailing_sl_callback_percent', 1.5)}", callback_data="param_set_trailing_sl_callback_percent")],
            [InlineKeyboardButton("--- إعدادات الفلاتر ---", callback_data="noop")],
            [InlineKeyboardButton(bool_format('btc_trend_filter_enabled', 'فلتر اتجاه BTC'), callback_data="param_toggle_btc_trend_filter_enabled")],
            [InlineKeyboardButton(bool_format('market_mood_filter_enabled', 'فلتر الخوف والطمع'), callback_data="param_toggle_market_mood_filter_enabled"),
             InlineKeyboardButton(f"حد مؤشر الخوف: {s.get('fear_and_greed_threshold', 30)}", callback_data="param_set_fear_and_greed_threshold")],
            [InlineKeyboardButton(bool_format('adx_filter_enabled', 'فلتر ADX'), callback_data="param_toggle_adx_filter_enabled"),
             InlineKeyboardButton(f"مستوى فلتر ADX: {s.get('adx_filter_level', 25)}", callback_data="param_set_adx_filter_level")],
            [InlineKeyboardButton(bool_format('news_filter_enabled', 'فلتر الأخبار والبيانات'), callback_data="param_toggle_news_filter_enabled")],
            [InlineKeyboardButton("--- إعدادات الرجل الحكيم (حساسية الزخم) ---", callback_data="noop")],
            [InlineKeyboardButton(f"نسبة الربح للزخم القوي (%): {s.get('wise_man_strong_profit_pct', 3.0)}", callback_data="param_set_wise_man_strong_profit_pct")],
            [InlineKeyboardButton(f"مستوى ADX للزخم القوي: {s.get('wise_man_strong_adx_level', 30)}", callback_data="param_set_wise_man_strong_adx_level")],
            [InlineKeyboardButton("🔙 العودة للإعدادات", callback_data="settings_main")]
        ]
        await safe_edit_message(query, "🎛️ **تعديل المعايير المتقدمة**\n\nاضغط على أي معيار لتعديل قيمته مباشرة:", reply_markup=InlineKeyboardMarkup(keyboard))
    except (ValueError, httpx.HTTPStatusError) as e: await handle_api_error(query, e)
    except Exception as e: await safe_edit_message(query, f"❌ خطأ: {e}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 العودة", callback_data="settings_main")]]))

async def show_scanners_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """(يحاكي Scanners.tsx) - معدل لـ /scanners"""
    query = update.callback_query; await query.answer("جاري جلب الماسحات...")
    try:
        headers = await get_api_headers(context)
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{API_SERVER_URL}/scanners", headers=headers)
            response.raise_for_status(); scanners = response.json()
        keyboard = []
        for scanner in scanners:
            key = scanner['strategy_name']; name = scanner.get('display_name', key)
            status_emoji = "✅" if scanner['is_enabled'] else "❌"
            perf_hint = ""
            if scanner.get('total_signals', 0) > 0:
                win_rate = (scanner.get('successful_signals', 0) / scanner['total_signals']) * 100
                perf_hint = f" ({win_rate:.0f}% WR)"
            keyboard.append([InlineKeyboardButton(f"{status_emoji} {name}{perf_hint}", callback_data=f"scanner_toggle_{key}")])
        keyboard.append([InlineKeyboardButton("🔙 العودة للإعدادات", callback_data="settings_main")])
        await safe_edit_message(query, "🔭 اختر الماسحات لتفعيلها أو تعطيلها:", reply_markup=InlineKeyboardMarkup(keyboard))
    except (ValueError, httpx.HTTPStatusError) as e: await handle_api_error(query, e)
    except Exception as e: await safe_edit_message(query, f"❌ خطأ: {e}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 العودة", callback_data="settings_main")]]))

async def show_presets_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """(يحاكي Presets.tsx) - معدل لـ /settings/preset"""
    query = update.callback_query
    try:
        headers = await get_api_headers(context)
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{API_SERVER_URL}/bot/status", headers=headers)
            response.raise_for_status()
            current_preset = response.json().get('current_preset_name', 'مخصص')
    except Exception: current_preset = "غير معروف"
    keyboard = []
    for key, name in PRESET_NAMES_AR.items():
        emoji = "🔹" if key == current_preset else "▫️"
        keyboard.append([InlineKeyboardButton(f"{emoji} {name}", callback_data=f"preset_set_{key}")])
    keyboard.append([InlineKeyboardButton("🔙 العودة للإعدادات", callback_data="settings_main")])
    await safe_edit_message(query, f"**🗂️ أنماط جاهزة**\n\nالنمط الحالي: **{current_preset}**\nاختر نمط إعدادات جاهز:", reply_markup=InlineKeyboardMarkup(keyboard))

# =======================================================================================
# --- معالجات الإعدادات (Handlers V4.1) ---
# =======================================================================================

async def _update_settings(query: Update.callback_query, context: ContextTypes.DEFAULT_TYPE, updates: dict):
    """دالة مساعدة لإرسال تحديثات الإعدادات (POST /settings)."""
    try:
        headers = await get_api_headers(context)
        async with httpx.AsyncClient() as client:
            response = await client.post(f"{API_SERVER_URL}/settings", json=updates, headers=headers)
            response.raise_for_status()
        await clear_settings_cache(context)
        return True
    except (ValueError, httpx.HTTPStatusError) as e:
        await handle_api_error(query, e); return False
    except Exception as e:
        await safe_edit_message(query, f"❌ خطأ في الاتصال: {e}"); return False

async def handle_toggle_parameter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """(من BN.py) - معدل لـ POST /settings"""
    query = update.callback_query; await query.answer("جاري التبديل..."); param_key = query.data.replace("param_toggle_", "")
    try:
        s = await get_settings_from_cache_or_api(context)
        current_value = s.get(param_key, False)
        updates_payload = {param_key: not current_value, "active_preset_name": "مخصص"}
        
        if await _update_settings(query, context, updates_payload):
            if any(k in param_key for k in ["adaptive", "wise_man", "dynamic", "strategy"]):
                await show_adaptive_intelligence_menu(update, context)
            else:
                await show_parameters_menu(update, context)
    except Exception as e: await safe_edit_message(query, f"❌ خطأ: {e}")

async def handle_scanner_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """(يحاكي Scanners.tsx) - معدل لـ /scanners/{name}/toggle"""
    query = update.callback_query; await query.answer("جاري التبديل..."); scanner_key = query.data.replace("scanner_toggle_", "")
    try:
        headers = await get_api_headers(context)
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{API_SERVER_URL}/scanners", headers=headers)
            response.raise_for_status(); scanners = response.json()
            scanner = next((s for s in scanners if s['strategy_name'] == scanner_key), None)
            if not scanner: await query.answer("الماسح غير موجود!", show_alert=True); return
            new_status = not scanner['is_enabled']
            toggle_res = await client.post(f"{API_SERVER_URL}/scanners/{scanner_key}/toggle", json={"enabled": new_status}, headers=headers)
            toggle_res.raise_for_status()
        
        # [إصلاح] تحديث الإعدادات المسبقة إلى "مخصص"
        await _update_settings(query, context, {"active_preset_name": "مخصص"})
        
        await show_scanners_menu(update, context)
    except (ValueError, httpx.HTTPStatusError) as e: await handle_api_error(query, e)
    except Exception as e: await safe_edit_message(query, f"❌ خطأ: {e}")

async def handle_preset_set(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """(يحاكي Presets.tsx) - معدل لـ /settings/preset"""
    query = update.callback_query; preset_key = query.data.replace("preset_set_", "")
    preset_name_ar = PRESET_NAMES_AR.get(preset_key, "غير معروف")
    await query.answer(f"✅ جاري تفعيل نمط: {preset_name_ar}...")
    try:
        headers = await get_api_headers(context)
        async with httpx.AsyncClient() as client:
            response = await client.post(f"{API_SERVER_URL}/settings/preset", json={"preset_name": preset_key}, headers=headers)
            response.raise_for_status()
        await clear_settings_cache(context)
        await show_presets_menu(update, context)
    except (ValueError, httpx.HTTPStatusError) as e: await handle_api_error(query, e)
    except Exception as e: await safe_edit_message(query, f"❌ خطأ: {e}")

async def handle_parameter_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """(من BN.py) - معدل لـ /settings"""
    query = update.callback_query; param_key = query.data.replace("param_set_", "")
    context.user_data['setting_to_change'] = param_key
    try:
        s = await get_settings_from_cache_or_api(context)
        current_value = s.get(param_key, "غير معرف")
        await query.message.reply_text(f"أرسل القيمة الرقمية الجديدة لـ `{param_key}`:\n(القيمة الحالية: `{current_value}`)", parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
         await query.message.reply_text(f"❌ خطأ في جلب القيمة الحالية: {e}")

async def handle_setting_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """(من BN.py) - معدل لـ POST /settings"""
    user_input = update.message.text.strip(); parent_menu_data = None
    try:
        if 'setting_to_change' in context.user_data:
            setting_key = context.user_data.pop('setting_to_change')
            if any(k in setting_key for k in ["adaptive", "wise_man", "dynamic", "strategy", "deactivation", "analysis", "sizing"]):
                parent_menu_data = "settings_adaptive"
            else:
                parent_menu_data = "settings_params"
            try:
                s = await get_settings_from_cache_or_api(context)
                original_value = s.get(setting_key)
                if isinstance(original_value, int): new_value = int(user_input)
                else: new_value = float(user_input)
            except (ValueError, TypeError):
                await update.message.reply_text("❌ قيمة غير صالحة. الرجاء إرسال رقم."); return
            updates_payload = {setting_key: new_value, "active_preset_name": "مخصص"}
            if await _update_settings(update.callback_query, context, updates_payload):
                await update.message.reply_text(f"✅ تم تحديث `{setting_key}` إلى `{new_value}`.")
            return
    except httpx.HTTPStatusError as e:
        await update.message.reply_text(f"❌ فشل التحديث: {e.response.json().get('detail')}")
    except Exception as e:
        await update.message.reply_text(f"❌ خطأ: {e}")
    finally:
        if 'setting_to_change' in context.user_data: del context.user_data['setting_to_change']
        if parent_menu_data:
            fake_query = type('Query', (), {'message': update.message, 'data': parent_menu_data, 'edit_message_text': (lambda *args, **kwargs: asyncio.sleep(0)), 'answer': (lambda *args, **kwargs: asyncio.sleep(0))})
            if parent_menu_data == "settings_adaptive": await show_adaptive_intelligence_menu(Update(update.update_id, callback_query=fake_query), context)
            else: await show_parameters_menu(Update(update.update_id, callback_query=fake_query), context)

async def handle_manual_sell_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """(من BN.py)"""
    query = update.callback_query; trade_id = int(query.data.split('_')[-1])
    message = f"🛑 **تأكيد البيع الفوري** 🛑\n\nهل أنت متأكد أنك تريد بيع الصفقة رقم `#{trade_id}` بسعر السوق الحالي؟"
    keyboard = [
        [InlineKeyboardButton("✅ نعم، قم بالبيع الآن", callback_data=f"manual_sell_execute_{trade_id}")],
        [InlineKeyboardButton("❌ لا، تراجع", callback_data=f"check_{trade_id}")]
    ]
    await safe_edit_message(query, message, reply_markup=InlineKeyboardMarkup(keyboard))

async def handle_manual_sell_execute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """(يحاكي handle_manual_sell_execute) - معدل لـ /trades/close"""
    query = update.callback_query; trade_id = int(query.data.split('_')[-1])
    await safe_edit_message(query, "⏳ جاري إرسال أمر البيع إلى العامل...", reply_markup=None)
    try:
        headers = await get_api_headers(context)
        async with httpx.AsyncClient() as client:
            response = await client.post(f"{API_SERVER_URL}/trades/close", json={"trade_id": trade_id}, headers=headers)
            response.raise_for_status()
        await query.answer("✅ تم إرسال أمر البيع بنجاح!")
        await safe_edit_message(query, f"✅ {response.json().get('message')}")
        await asyncio.sleep(2)
        await show_dashboard_command(update, context)
    except (ValueError, httpx.HTTPStatusError) as e: await handle_api_error(query, e)
    except Exception as e: await safe_edit_message(query, f"❌ خطأ: {e}")

# =======================================================================================
# --- الموجهات والمعالجات الرئيسية ---
# =======================================================================================

async def universal_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """(من BN.py)"""
    if 'setting_to_change' in context.user_data:
        await handle_setting_value(update, context); return
    text = update.message.text
    if text == "Dashboard 🖥️": await show_dashboard_command(update, context)
    elif text == "الإعدادات ⚙️": 
        await clear_settings_cache(context)
        await show_settings_menu(update, context)

async def button_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """(من BN.py) - موجه الأزرار الرئيسي (النسخة الكاملة V4.1)"""
    query = update.callback_query; await query.answer(); data = query.data
    
    if not data.startswith("param_") and not data.startswith("scanner_") and not data.startswith("preset_") and not data.startswith("settings_"):
        await clear_settings_cache(context)
    
    route_map = {
        "db_stats": show_stats_command, "db_trades": show_trades_command, "db_history": show_trade_history_command,
        "db_mood": show_mood_command, "db_diagnostics": show_diagnostics_command, "back_to_dashboard": show_dashboard_command,
        "db_portfolio": show_portfolio_command,
        "kill_switch_toggle": toggle_kill_switch,
        "settings_main": show_settings_menu, "settings_params": show_parameters_menu, "settings_scanners": show_scanners_menu,
        "settings_presets": show_presets_menu, "settings_adaptive": show_adaptive_intelligence_menu,
        "noop": (lambda u,c: None)
    }
    try:
        if data in route_map: 
            await route_map[data](update, context)
        elif data.startswith("check_"): 
            await check_trade_details(update, context)
        elif data.startswith("manual_sell_confirm_"): 
            await handle_manual_sell_confirmation(update, context)
        elif data.startswith("manual_sell_execute_"): 
            await handle_manual_sell_execute(update, context)
        elif data.startswith("scanner_toggle_"): 
            await handle_scanner_toggle(update, context)
        elif data.startswith("preset_set_"): 
            await handle_preset_set(update, context)
        elif data.startswith("param_set_"): 
            await handle_parameter_selection(update, context)
        elif data.startswith("param_toggle_"): 
            await handle_toggle_parameter(update, context)
    except Exception as e: 
        logger.error(f"Error in button callback handler for data '{data}': {e}", exc_info=True)

# =======================================================================================
# --- التشغيل ---
# =======================================================================================

def main():
    if not TELEGRAM_BOT_TOKEN: logger.critical("TELEGRAM_BOT_TOKEN not set! Exiting."); return
    if not API_SERVER_URL: logger.critical("API_SERVER_URL not set! Exiting."); return

    logger.info("Starting Telegram UI Client (SaaS V4.1 - Secure Link)...")
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("login", login_command)) # <-- [جديد V4.1]
    # (تم حذف /myid)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, universal_text_handler))
    application.add_handler(CallbackQueryHandler(button_callback_handler))
    
    logger.info("--- Telegram UI Client is now polling ---")
    application.run_polling()
    
if __name__ == '__main__':
    main()

}

{
type: uploaded file
fileName: telegram_notifier.py
fullContent:
import asyncio
import logging
import asyncpg
import os
from telegram import Bot
from telegram.constants import ParseMode
from telegram.error import Forbidden, BadRequest

# --- إعدادات أساسية ---
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger("TelegramNotifier")

# --- متغيرات البيئة ---
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:your-password@db.xyz.supabase.co:5432/postgres")
POLL_INTERVAL_SECONDS = 5 # (كل 5 ثوانٍ يبحث عن إشعارات جديدة)

# --- (نحتاج دوال اتصال قاعدة البيانات هنا أيضاً) ---
POOL = None

async def get_db_pool():
    global POOL
    if POOL is None:
        try:
            POOL = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5)
            logger.info("Notifier: Database connection pool created.")
        except Exception as e:
            logger.critical(f"Notifier: Failed to create database pool: {e}")
            raise
    return POOL

@asynccontextmanager
async def db_connection():
    pool = await get_db_pool()
    if pool is None:
        raise ConnectionError("Database pool is not initialized.")
    async with pool.acquire() as connection:
        yield connection

# --- الدالة الرئيسية للمرسل ---

async def fetch_and_send_notifications(bot: Bot):
    """
    يجلب الإشعارات غير المقروءة ويرسلها.
    """
    try:
        async with db_connection() as conn:
            # [تعديل V4] جلب chat_id من user_settings بدلاً من user_profiles
            notifications = await conn.fetch(
                """
                SELECT 
                    n.id, 
                    n.user_id, 
                    n.title, 
                    n.message, 
                    n.type,
                    s.telegram_chat_id
                FROM 
                    notifications AS n
                JOIN 
                    user_settings AS s ON n.user_id = s.user_id
                WHERE 
                    n.is_read = false 
                    AND s.telegram_chat_id IS NOT NULL
                ORDER BY 
                    n.timestamp ASC
                LIMIT 50; -- (إرسال 50 رسالة كحد أقصى في كل دورة)
                """
            )
            
            if not notifications:
                return # لا يوجد شيء لإرساله

            logger.info(f"Notifier: Found {len(notifications)} new notifications to send.")
            
            sent_ids = []
            for record in notifications:
                chat_id = record['telegram_chat_id']
                if not chat_id:
                    # (هذا الشرط يجب ألا يحدث بسبب JOIN)
                    logger.warning(f"Notifier: Skipping notification {record['id']} for user {record['user_id']} (no chat_id linked).")
                    sent_ids.append(record['id']) 
                    continue
                
                # تنسيق الرسالة
                icon_map = {
                    'success': '✅', 'error': '🛑',
                    'warning': '⚠️', 'info': '💡'
                }
                icon = icon_map.get(record['type'], 'ℹ️')
                
                message_text = (
                    f"*{icon} {record['title']}*\n"
                    f"━━━━━━━━━━━━━━━━━━\n"
                    f"{record['message']}"
                )
                
                try:
                    await bot.send_message(
                        chat_id=chat_id,
                        text=message_text,
                        parse_mode=ParseMode.MARKDOWN
                    )
                    sent_ids.append(record['id'])
                except (Forbidden, BadRequest) as e:
                    # إذا تم حظر البوت، سنتجاهل الإشعار
                    logger.error(f"Notifier: Failed to send to chat_id {chat_id} (User blocked?): {e}")
                    sent_ids.append(record['id']) # (نعتبرها "مرسلة")
                except Exception as e:
                    # خطأ مؤقت في الشبكة، لا نضع علامة "مقروء"
                    logger.error(f"Notifier: Temporary failure sending to {chat_id}: {e}")
            
            # وضع علامة "مقروء" على كل الإشعارات التي تمت معالجتها
            if sent_ids:
                await conn.execute(
                    "UPDATE notifications SET is_read = true WHERE id = ANY($1::bigint[])",
                    sent_ids
                )

    except Exception as e:
        logger.error(f"Notifier: Critical error in fetch_and_send loop: {e}", exc_info=True)

async def main_loop():
    """
    الحلقة الرئيسية التي تعمل باستمرار.
    """
    if not TELEGRAM_BOT_TOKEN:
        logger.critical("TELEGRAM_BOT_TOKEN not set! Notifier cannot start.")
        return
        
    await get_db_pool() # تهيئة الاتصال
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    
    logger.info("--- 🚀 Telegram Notifier Service (V4.1) Started ---")
    
    while True:
        await fetch_and_send_notifications(bot)
        await asyncio.sleep(POLL_INTERVAL_SECONDS)

if __name__ == "__main__":
    try:
        asyncio.run(main_loop())
    except KeyboardInterrupt:
        logger.info("--- 🛑 Telegram Notifier Service Shutting Down... ---")
    finally:
        if POOL:
            asyncio.run(POOL.close())

}

{
type: uploaded file
fileName: main (1).py
fullContent:
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
    #
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
        async with get_ccxt_connection(user_id) as exchange: # استخدام اتصال المستخدم
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
#
# =======================================================================================

UI_BUILD_DIR = os.path.join(os.path.dirname(__file__), "dist")

if not os.path.exists(UI_BUILD_DIR):
    logger.warning("="*50)
    logger.warning("UI build directory 'dist' not found.")
    logger.warning(f"Expected at: {UI_BUILD_DIR}")
    logger.warning("Web UI will not be served.")
    logger.warning("="*50)
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

}
