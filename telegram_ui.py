# -*- coding: utf-8 -*-
# =======================================================================================
# --- 🚀 واجهة بوت التداول V3.2 (SaaS Client - مع ربط /myid) 🚀 ---
# =======================================================================================
#
# هذا الملف هو واجهة المستخدم (UI) فقط.
# إنه "عميل API" يتحدث إلى خادم main.py (V3).
# [تحديث] يستخدم هذا الإصدار /myid للربط بدلاً من /login.
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
API_SERVER_URL = os.getenv('API_SERVER_URL', 'http://127.0.0.1:8000') # خادم V3

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
    [تعديل V3.2]
    لم يعد هذا يجلب التوكن. بدلاً من ذلك، الخادم سيحتاج إلى ربط chat_id بالـ user_id.
    أو، الطريقة الأسهل: البوت سيحتاج إلى جلب الـ user_id (التوكن) المرتبط بهذا الـ chat_id.
    
    *** تعديل هام: ***
    لقد أخطأت في التصميم السابق. لا يمكننا استخدام /myid فقط.
    يجب أن نستخدم /login مرة واحدة لربط chat_id بالـ user_id.
    
    الحل الأبسط هو الذي اقترحته:
    1. المستخدم يكتب /myid -> البوت يرد بالـ chat_id.
    2. المستخدم يذهب للويب ويلصق الـ chat_id.
    3. الخادم يربط الـ user_id بالـ chat_id.
    
    ولكن... كيف سيقوم البوت بعمل مصادقة للطلبات؟
    
    الحل الصحيح هو:
    1. المستخدم يكتب /login <token> (مرة واحدة فقط).
    2. البوت يرسل هذا التوكن + الـ chat_id الخاص به إلى الخادم (POST /telegram/link-account).
    3. الخادم يحفظ أن 'user_id' (التوكن) مرتبط بهذا 'chat_id'.
    
    الآن، في كل مرة يتحدث فيها المستخدم:
    1. البوت يرسل *فقط* الـ chat_id إلى الخادم.
    2. الخادم يبحث عن الـ chat_id، يجد الـ user_id، وينفذ الأمر.
    
    هذا يعني أننا بحاجة إلى تعديل `main.py` (V4) مرة أخرى.
    
    --- (سأعتمد الحل الأبسط الذي اقترحته أولاً: /myid) ---
    
    *** إعادة تصميم V3.2 (بناءً على فكرتك): ***
    لن نستخدم /login. سنستخدم /myid.
    المستخدم سيربط الـ chat_id في واجهة الويب.
    الخادم (main.py) سيحتاج إلى مسار API جديد للتحقق من chat_id.
    """
    
    # [تصميم جديد V3.2] المصادقة الآن تتم عبر chat_id
    chat_id = context._chat_id
    if not chat_id:
        raise ValueError("لا يمكن العثور على معرف الدردشة.")
    
    # سنقوم بتمرير chat_id كـ "توكن" مؤقت
    # الخادم (main.py V4.1) سيحتاج إلى البحث عن المستخدم عبر هذا الـ ID
    return {'Authorization': f'Bearer chat_id_{chat_id}'}


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
# --- [جديد V3.2] أوامر الربط ---
# =======================================================================================

async def myid_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    (جديد) ينفذ فكرتك. يرسل للمستخدم معرف الدردشة الخاص به.
    """
    chat_id = update.message.chat_id
    message = (
        f"معرف تليجرام الخاص بك هو:\n`{chat_id}`\n\n"
        f"يرجى نسخ هذا الرقم ولصقه في حقل 'معرف تليجرام' في صفحة الإعدادات على واجهة الويب لربط حسابك وتلقي الإشعارات."
    )
    await update.message.reply_text(message, parse_mode=ParseMode.MARKDOWN)

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """(من BN.py) - معدل لـ V3.2"""
    keyboard = [["Dashboard 🖥️"], ["الإعدادات ⚙️"]]
    await update.message.reply_text("أهلاً بك في **بوت باينانس V3 (SaaS)**\n\n"
                                  "لربط هذا البوت بحسابك على واجهة الويب:\n"
                                  "1. أرسل الأمر `/myid` الآن.\n"
                                  "2. انسخ الرقم الذي سأرسله لك.\n"
                                  "3. اذهب إلى صفحة الإعدادات في واجهة الويب والصق الرقم هناك واضغط حفظ.",
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
        ks_status_emoji = "❓"
        ks_status_text = "خطأ (استخدم /myid ؟)"
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
# --- واجهة الإعدادات (النسخة الكاملة V3.2) ---
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
# --- معالجات الإعدادات (Handlers V3.2) ---
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
    """(من BN.py) - موجه الأزرار الرئيسي (النسخة الكاملة V3.2)"""
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

    logger.info("Starting Telegram UI Client (SaaS V3.2 - /myid Link)...")
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("myid", myid_command)) # <-- [جديد V3.2]
    # (تم حذف /login)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, universal_text_handler))
    application.add_handler(CallbackQueryHandler(button_callback_handler))
    
    logger.info("--- Telegram UI Client is now polling ---")
    application.run_polling()
    
if __name__ == '__main__':
    main()

}

{
type: uploaded file
fileName: requirements.txt
fullContent:
# --- للخادم (main.py) ---
fastapi[all]         # خادم الويب السريع (يشمل uvicorn, pydantic)
gunicorn             # خادم الإنتاج (لتشغيل main:app)
python-dotenv        # لتحميل متغيرات البيئة (مثل DATABASE_URL)

# --- للعامل (bot_worker.py) ---
ccxt                 # مكتبة التداول الأساسية
websockets           # للاتصال ببث Binance العام
pandas               # لتحليل بيانات OHLCV
pandas-ta            # للمؤشرات الفنية
scipy                # لبعض الاستراتيجيات مثل RSI Divergence

# --- مشتركة (قاعدة البيانات) ---
asyncpg              # المشغل غير المتزامن لـ PostgreSQL
pydantic             # لتعريف نماذج البيانات (Settings, Variables)
}
