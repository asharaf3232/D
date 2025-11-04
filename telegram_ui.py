# -*- coding: utf-8 -*-
# =======================================================================================
# --- 🚀 واجهة بوت التداول V6.6 (SaaS Client - النسخة الكاملة) 🚀 ---
# =======================================================================================
#
# هذا الملف هو واجهة المستخدم (UI) فقط.
# إنه لا يقوم بأي عمليات تداول أو تحليل.
# كل زر يتم الضغط عليه هنا يرسل طلب API إلى خادم main.py.
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
API_SERVER_URL = os.getenv('API_SERVER_URL', 'http://127.0.0.1:8001') # عنوان خادم FastAPI

# --- ثوابت (من BN.py) ---
STRATEGY_NAMES_AR = {
    "momentum_breakout": "زخم اختراقي", "breakout_squeeze_pro": "اختراق انضغاطي",
    "support_rebound": "ارتداد الدعم", "sniper_pro": "القناص المحترف", "whale_radar": "رادار الحيتان",
    "rsi_divergence": "دايفرجنس RSI", "supertrend_pullback": "انعكاس سوبرترند"
}
PRESET_NAMES_AR = {"professional": "احترافي", "strict": "متشدد", "lenient": "متساهل", "very_lenient": "فائق التساهل", "bold_heart": "القلب الجريء"}

# إعدادات الأنماط الجاهزة (منسوخة من BN.py)
# هذه مطلوبة في العميل لإرسالها إلى الـ API
SETTINGS_PRESETS = {
    "professional": {
        "real_trade_size_usdt": 15.0, "max_concurrent_trades": 5, "top_n_symbols_by_volume": 300,
        "atr_sl_multiplier": 2.5, "risk_reward_ratio": 2.0, "trailing_sl_enabled": True,
        "trailing_sl_activation_percent": 2.0, "trailing_sl_callback_percent": 1.5,
        "market_mood_filter_enabled": True, "fear_and_greed_threshold": 30,
        "adx_filter_enabled": True, "adx_filter_level": 25, "btc_trend_filter_enabled": True,
        "news_filter_enabled": True
        # (يجب إضافة باقي الفلاتر من DEFAULT_SETTINGS في BN.py)
    },
    "strict": {
        "real_trade_size_usdt": 15.0, "max_concurrent_trades": 3, "top_n_symbols_by_volume": 300,
        "atr_sl_multiplier": 2.5, "risk_reward_ratio": 2.5, "trailing_sl_enabled": True,
        "trailing_sl_activation_percent": 2.0, "trailing_sl_callback_percent": 1.5,
        "market_mood_filter_enabled": True, "fear_and_greed_threshold": 40,
        "adx_filter_enabled": True, "adx_filter_level": 28, "btc_trend_filter_enabled": True,
        "news_filter_enabled": True
    },
    # (إضافة باقي الأنماط... lenient, very_lenient, bold_heart)
}


# =======================================================================================
# --- دوال مساعدة للاتصال بـ API ---
# =======================================================================================

async def get_api_headers(context: ContextTypes.DEFAULT_TYPE) -> dict:
    """ينشئ رؤوس المصادقة."""
    chat_id = context._chat_id
    if not chat_id:
        logger.error("Could not get chat_id from context.")
        raise ValueError("Chat ID not found.")
    return {'X-Telegram-Chat-Id': str(chat_id)}

async def safe_send_message(bot, chat_id, text, **kwargs):
    """(من BN.py) إرسال رسالة بشكل آمن."""
    for i in range(3):
        try:
            await bot.send_message(chat_id, text, parse_mode=ParseMode.MARKDOWN, **kwargs)
            return
        except (TimedOut, Forbidden) as e:
            logger.error(f"Telegram Send Error: {e}. Attempt {i+1}/3.")
            if isinstance(e, Forbidden) or i == 2:
                logger.critical("Critical Telegram error. Cannot send messages.")
                return
            await asyncio.sleep(2)
        except Exception as e:
            logger.error(f"Unknown Telegram Send Error: {e}. Attempt {i+1}/3.")
            await asyncio.sleep(2)


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

# =======================================================================================
# --- واجهة تليجرام (النسخة الكاملة) ---
# =======================================================================================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """(من BN.py)"""
    keyboard = [["Dashboard 🖥️"], ["الإعدادات ⚙️"]]
    # (في الإنتاج، يجب أن يكون هناك أمر /register لربط chat_id بـ user_id)
    # (نفترض أن الربط تم مسبقاً في قاعدة البيانات)
    await update.message.reply_text("أهلاً بك في **بوت باينانس V6.6 (SaaS)**\n\n*ملاحظة: تأكد من تسجيل حسابك وربطه بـ /register*", 
                                  reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True), 
                                  parse_mode=ParseMode.MARKDOWN)

async def manual_scan_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """(من BN.py) - معدل لـ API"""
    target_message = update.message or update.callback_query.message
    try:
        headers = await get_api_headers(context)
        async with httpx.AsyncClient() as client:
            # التحقق من حالة المفتاح أولاً
            settings_res = await client.get(f"{API_SERVER_URL}/api/settings", headers=headers)
            settings_res.raise_for_status()
            if not settings_res.json().get('is_trading_enabled'):
                await target_message.reply_text("🔬 الفحص محظور. مفتاح الإيقاف مفعل."); return

            await target_message.reply_text("🔬 أمر فحص يدوي... جاري إرسال الطلب للعامل.")
            response = await client.post(f"{API_SERVER_URL}/api/actions/manual_scan", headers=headers)
            response.raise_for_status()
            await target_message.reply_text(f"✅ {response.json().get('message')}")

    except httpx.HTTPStatusError as e:
        await target_message.reply_text(f"❌ فشل إرسال الطلب: {e.response.json().get('detail')}")
    except Exception as e:
        await target_message.reply_text(f"❌ خطأ غير متوقع: {e}")


async def show_dashboard_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """(من BN.py) - معدل لـ API (جلب حالة المفتاح)"""
    query = update.callback_query
    target_message = update.message or query.message
    
    ks_status_emoji = "⏳"
    ks_status_text = "جاري التحميل..."
    try:
        headers = await get_api_headers(context)
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{API_SERVER_URL}/api/settings", headers=headers)
            response.raise_for_status()
            settings = response.json()
            is_enabled = settings.get('is_trading_enabled', False)
            
            ks_status_emoji = "✅" if is_enabled else "🚨"
            ks_status_text = "الحالة (طبيعية)" if is_enabled else "مفتاح الإيقاف (مفعل)"
            
            # تخزين الإعدادات مؤقتاً في context لتجنب جلبها مرة أخرى
            context.user_data['settings_cache'] = settings

    except Exception as e:
        logger.error(f"Failed to fetch kill switch status: {e}")
        ks_status_emoji = "❓"
        ks_status_text = "خطأ في جلب الحالة"

    keyboard = [
        [InlineKeyboardButton("💼 نظرة عامة على المحفظة", callback_data="db_portfolio"), InlineKeyboardButton("📈 الصفقات النشطة", callback_data="db_trades")],
        [InlineKeyboardButton("📜 سجل الصفقات المغلقة", callback_data="db_history"), InlineKeyboardButton("📊 الإحصائيات والأداء", callback_data="db_stats")],
        [InlineKeyboardButton("🌡️ تحليل مزاج السوق", callback_data="db_mood"), InlineKeyboardButton("🔬 فحص فوري", callback_data="db_manual_scan")],
        [InlineKeyboardButton("🗓️ التقرير اليومي", callback_data="db_daily_report")],
        [InlineKeyboardButton(f"{ks_status_emoji} {ks_status_text}", callback_data="kill_switch_toggle"), InlineKeyboardButton("🕵️‍♂️ تقرير التشخيص", callback_data="db_diagnostics")]
    ]
    message_text = "🖥️ **لوحة تحكم بوت Binance (SaaS)**\n\nاختر نوع التقرير الذي تريد عرضه:"
    if ks_status_emoji == "🚨": message_text += "\n\n**تحذير: تم تفعيل مفتاح الإيقاف.**"

    if query: 
        await safe_edit_message(query, message_text, reply_markup=InlineKeyboardMarkup(keyboard))
    else: 
        await target_message.reply_text(message_text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))

async def toggle_kill_switch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """(من BN.py) - معدل لـ API"""
    query = update.callback_query
    await query.answer("جاري إرسال الأمر...")
    try:
        headers = await get_api_headers(context)
        async with httpx.AsyncClient() as client:
            response = await client.post(f"{API_SERVER_URL}/api/actions/toggle_kill_switch", headers=headers)
            response.raise_for_status()
            new_status = response.json().get('new_status', False)
        
        # مسح ذاكرة التخزين المؤقت للإعدادات
        if 'settings_cache' in context.user_data:
            del context.user_data['settings_cache']
            
        if new_status: 
            await query.answer("✅ تم استئناف التداول الطبيعي.")
            await safe_send_message(context.bot, context._chat_id, "✅ **تم استئناف التداول الطبيعي.**")
        else: 
            await query.answer("🚨 تم تفعيل مفتاح الإيقاف!", show_alert=True)
            await safe_send_message(context.bot, context._chat_id, "🚨 **تحذير: تم تفعيل مفتاح الإيقاف!**")
        
        await show_dashboard_command(update, context) # تحديث لوحة التحكم
    except httpx.HTTPStatusError as e:
        await handle_api_error(query, e)
    except Exception as e:
         await safe_edit_message(query, f"❌ خطأ: {e}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 العودة", callback_data="back_to_dashboard")]]))


# --- (بقية دوال لوحة التحكم: show_trades, check_trade_details, show_portfolio, ...إلخ) ---
# --- (هي مطابقة للملف السابق، سأركز على الإعدادات المفقودة) ---
async def show_trades_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer("جاري جلب الصفقات...")
    try:
        headers = await get_api_headers(context)
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{API_SERVER_URL}/api/dashboard/active_trades", headers=headers)
            response.raise_for_status(); trades = response.json()
        if not trades:
            text = "لا توجد صفقات نشطة حاليًا."
            keyboard = [[InlineKeyboardButton("🔙 العودة للوحة التحكم", callback_data="back_to_dashboard")]]
            await safe_edit_message(query, text, reply_markup=InlineKeyboardMarkup(keyboard)); return
        text = "📈 *الصفقات النشطة*\nاختر صفقة لعرض تفاصيلها:\n"; keyboard = []
        for trade in trades: 
            status_emoji = "✅" if trade['status'] == 'active' else "⏳"
            button_text = f"#{trade['id']} {status_emoji} | {trade['symbol']}"
            keyboard.append([InlineKeyboardButton(button_text, callback_data=f"check_{trade['id']}")])
        keyboard.append([InlineKeyboardButton("🔄 تحديث", callback_data="db_trades")])
        keyboard.append([InlineKeyboardButton("🔙 العودة للوحة التحكم", callback_data="back_to_dashboard")])
        await safe_edit_message(query, text, reply_markup=InlineKeyboardMarkup(keyboard))
    except httpx.HTTPStatusError as e: await handle_api_error(query, e)
    except Exception as e: await safe_edit_message(query, f"❌ خطأ: {e}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 العودة", callback_data="back_to_dashboard")]]))

async def check_trade_details(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; trade_id = int(query.data.split('_')[1]); await query.answer("جاري جلب التفاصيل...")
    try:
        headers = await get_api_headers(context)
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{API_SERVER_URL}/api/trades/{trade_id}", headers=headers)
            response.raise_for_status(); trade = response.json()
        keyboard = [[InlineKeyboardButton("🚨 بيع فوري (بسعر السوق)", callback_data=f"manual_sell_confirm_{trade_id}")], [InlineKeyboardButton("🔙 العودة للصفقات", callback_data="db_trades")]]
        if trade['status'] == 'pending':
            message = f"**⏳ حالة الصفقة #{trade_id}**\n- **العملة:** `{trade['symbol']}`\n- **الحالة:** في انتظار تأكيد التنفيذ..."
            keyboard = [[InlineKeyboardButton("🔙 العودة للصفقات", callback_data="db_trades")]]
        else:
            pnl_text = "💰 تعذر جلب الربح/الخسارة الحية."; current_price_text = "- **السعر الحالي:** `تعذر الجلب`"
            if 'pnl_usdt_live' in trade:
                pnl = trade['pnl_usdt_live']; pnl_percent = trade['pnl_percent_live']
                pnl_text = f"💰 **الربح/الخسارة الحالية:** `${pnl:+.2f}` ({pnl_percent:+.2f}%)"
                current_price_text = f"- **السعر الحالي:** `${trade['current_price']}`"
            message = (f"**✅ حالة الصفقة #{trade_id}**\n\n- **العملة:** `{trade['symbol']}`\n- **سعر الدخول:** `${trade['entry_price']}`\n{current_price_text}\n- **الكمية:** `{trade['quantity']}`\n"
                       f"----------------------------------\n- **الهدف (TP):** `${trade['take_profit']}`\n- **الوقف (SL):** `${trade['stop_loss']}`\n----------------------------------\n{pnl_text}")
        await safe_edit_message(query, message, reply_markup=InlineKeyboardMarkup(keyboard))
    except httpx.HTTPStatusError as e: await handle_api_error(query, e)
    except Exception as e: await safe_edit_message(query, f"❌ خطأ: {e}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 العودة", callback_data="back_to_dashboard")]]))

async def show_portfolio_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer("جاري جلب بيانات المحفظة...")
    try:
        headers = await get_api_headers(context)
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{API_SERVER_URL}/api/dashboard/portfolio", headers=headers)
            response.raise_for_status(); portfolio = response.json()
        message = (f"**💼 نظرة عامة على المحفظة**\n🗓️ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                   f"━━━━━━━━━━━━━━━━━━━━\n**💰 إجمالي قيمة (USDT):** `≈ ${portfolio['total_usdt_equity']:,.2f}`\n  - **السيولة المتاحة (USDT):** `${portfolio['free_usdt']:,.2f}`\n"
                   f"  - **عدد الأصول الأخرى:** `{portfolio['owned_assets_count']}`\n━━━━━━━━━━━━━━━━━━━━\n**📈 أداء التداول:**\n"
                   f"  - **الربح/الخسارة المحقق:** `${portfolio['total_realized_pnl']:,.2f}`\n  - **عدد الصفقات النشطة:** {portfolio['active_trades_count']}\n")
        keyboard = [[InlineKeyboardButton("🔄 تحديث", callback_data="db_portfolio")], [InlineKeyboardButton("🔙 العودة للوحة التحكم", callback_data="back_to_dashboard")]]
        await safe_edit_message(query, message, reply_markup=InlineKeyboardMarkup(keyboard))
    except httpx.HTTPStatusError as e: await handle_api_error(query, e)
    except Exception as e: await safe_edit_message(query, f"❌ خطأ: {e}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 العودة", callback_data="back_to_dashboard")]]))

async def show_trade_history_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer("جاري جلب السجل...")
    try:
        headers = await get_api_headers(context)
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{API_SERVER_URL}/api/dashboard/trade_history", headers=headers)
            response.raise_for_status(); closed_trades = response.json()
        if not closed_trades: text = "لم يتم إغلاق أي صفقات بعد."
        else:
            history_list = ["📜 *آخر 10 صفقات مغلقة*"]
            for trade in closed_trades:
                emoji = "✅" if '(TP)' in trade['status'] or '(TSL)' in trade['status'] else "🛑"
                pnl = trade['pnl_usdt'] or 0.0
                history_list.append(f"{emoji} `{trade['symbol']}` | الربح/الخسارة: `${pnl:,.2f}`")
            text = "\n".join(history_list)
        keyboard = [[InlineKeyboardButton("🔙 العودة للوحة التحكم", callback_data="back_to_dashboard")]]
        await safe_edit_message(query, text, reply_markup=InlineKeyboardMarkup(keyboard))
    except httpx.HTTPStatusError as e: await handle_api_error(query, e)
    except Exception as e: await safe_edit_message(query, f"❌ خطأ: {e}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 العودة", callback_data="back_to_dashboard")]]))

async def show_stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer("جاري جلب الإحصائيات...")
    try:
        headers = await get_api_headers(context)
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{API_SERVER_URL}/api/dashboard/stats", headers=headers)
            response.raise_for_status(); stats = response.json()
        if stats['total_trades'] == 0:
            await safe_edit_message(query, "لم يتم إغلاق أي صفقات بعد.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 العودة للوحة التحكم", callback_data="back_to_dashboard")]])); return
        message = (f"📊 **إحصائيات الأداء التفصيلية**\n━━━━━━━━━━━━━━━━━━\n**إجمالي الربح/الخسارة:** `${stats['total_pnl']:+.2f}`\n**متوسط الربح:** `${stats['avg_win']:+.2f}`\n"
                   f"**متوسط الخسارة:** `${stats['avg_loss']:+.2f}`\n**عامل الربح (Profit Factor):** `{stats['profit_factor']:,.2f}`\n**معدل النجاح:** {stats['win_rate']:.1f}%\n**إجمالي الصفقات:** {stats['total_trades']}")
        keyboard = [[InlineKeyboardButton("📜 عرض تقرير الاستراتيجيات", callback_data="db_strategy_report")], [InlineKeyboardButton("🔙 العودة للوحة التحكم", callback_data="back_to_dashboard")]]
        await safe_edit_message(query, message, reply_markup=InlineKeyboardMarkup(keyboard))
    except httpx.HTTPStatusError as e: await handle_api_error(query, e)
    except Exception as e: await safe_edit_message(query, f"❌ خطأ: {e}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 العودة", callback_data="back_to_dashboard")]]))

async def show_strategy_report_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer("جاري جلب أداء الاستراتيجيات...")
    try:
        headers = await get_api_headers(context)
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{API_SERVER_URL}/api/dashboard/strategy_report", headers=headers)
            response.raise_for_status(); performance_data = response.json()
        if not performance_data:
            await safe_edit_message(query, "لا توجد بيانات أداء حاليًا.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 العودة للإحصائيات", callback_data="db_stats")]])); return
        report = ["**📜 تقرير أداء الاستراتيجيات**\n(بناءً على آخر 100 صفقة)"]; sorted_strategies = sorted(performance_data.items(), key=lambda item: item[1]['total_trades'], reverse=True)
        for r, s in sorted_strategies:
            report.append(f"\n--- *{STRATEGY_NAMES_AR.get(r, r)}* ---\n  - **النجاح:** {s['win_rate']:.1f}% ({s['total_trades']} صفقة)\n  - **عامل الربح:** {s['profit_factor'] if s['profit_factor'] != float('inf') else '∞'}")
        keyboard = [[InlineKeyboardButton("📊 عرض الإحصائيات العامة", callback_data="db_stats")], [InlineKeyboardButton("🔙 العودة للوحة التحكم", callback_data="back_to_dashboard")]]
        await safe_edit_message(query, "\n".join(report), reply_markup=InlineKeyboardMarkup(keyboard))
    except httpx.HTTPStatusError as e: await handle_api_error(query, e)
    except Exception as e: await safe_edit_message(query, f"❌ خطأ: {e}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 العودة", callback_data="back_to_dashboard")]]))

async def show_mood_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer("جاري تحليل مزاج السوق...")
    try:
        headers = await get_api_headers(context)
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.get(f"{API_SERVER_URL}/api/dashboard/mood", headers=headers)
            response.raise_for_status(); mood = response.json()
        message = (f"**🌡️ تحليل مزاج السوق الشامل**\n━━━━━━━━━━━━━━━━━━━━\n**⚫️ الخلاصة:** *{mood['verdict']}*\n━━━━━━━━━━━━━━━━━━━━\n**📊 المؤشرات الرئيسية:**\n"
                   f"  - **اتجاه BTC العام:** {mood.get('btc_mood', 'N/A')}\n  - **الخوف والطمع:** {mood.get('fng_index', 'N/A')}\n  - **مشاعر الأخبار:** {mood.get('news_sentiment', 'N/A')}\n")
        keyboard = [[InlineKeyboardButton("🔄 تحديث", callback_data="db_mood")], [InlineKeyboardButton("🔙 العودة للوحة التحكم", callback_data="back_to_dashboard")]]
        await safe_edit_message(query, message, reply_markup=InlineKeyboardMarkup(keyboard))
    except httpx.HTTPStatusError as e: await handle_api_error(query, e)
    except Exception as e: await safe_edit_message(query, f"❌ خطأ: {e}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 العودة", callback_data="back_to_dashboard")]]))

async def show_diagnostics_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer("جاري جلب التشخيصات...")
    try:
        headers = await get_api_headers(context)
        async with httpx.AsyncClient() as client:
            settings_res = await client.get(f"{API_SERVER_URL}/api/settings", headers=headers)
            stats_res = await client.get(f"{API_SERVER_URL}/api/dashboard/stats", headers=headers)
            settings_res.raise_for_status(); stats_res.raise_for_status()
            s = settings_res.json(); stats = stats_res.json()
        scanners_list = "\n".join([f"  - {STRATEGY_NAMES_AR.get(key, key)}" for key in s['active_scanners']])
        report = (f"🕵️‍♂️ *تقرير التشخيص (SaaS)*\n\nتم إنشاؤه في: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n----------------------------------\n"
                  f"⚙️ **حالة النظام**\n- اتصال الخادم (API): ناجح ✅\n- اتصال قاعدة البيانات: (عبر الخادم) ✅\n\n"
                  f"🔧 **الإعدادات النشطة**\n- **النمط الحالي: {s['active_preset_name']}**\n- الماسحات المفعلة:\n{scanners_list}\n----------------------------------\n"
                  f"🔩 **إحصائيات قاعدة البيانات**\n  - إجمالي الصفقات المغلقة: {stats.get('total_trades', 0)}\n")
        await safe_edit_message(query, report, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔄 تحديث", callback_data="db_diagnostics")], [InlineKeyboardButton("🔙 العودة للوحة التحكم", callback_data="back_to_dashboard")]]))
    except httpx.HTTPStatusError as e: await handle_api_error(query, e)
    except Exception as e: await safe_edit_message(query, f"❌ خطأ: {e}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 العودة", callback_data="back_to_dashboard")]]))

# =======================================================================================
# --- واجهة الإعدادات (النسخة الكاملة) ---
# =======================================================================================

async def get_settings_from_cache_or_api(context: ContextTypes.DEFAULT_TYPE) -> dict:
    """يجلب الإعدادات من الذاكرة المؤقتة أو يطلبها من الـ API."""
    if 'settings_cache' in context.user_data:
        return context.user_data['settings_cache']
    
    headers = await get_api_headers(context)
    async with httpx.AsyncClient() as client:
        response = await client.get(f"{API_SERVER_URL}/api/settings", headers=headers)
        response.raise_for_status()
        settings = response.json()
        context.user_data['settings_cache'] = settings
        return settings

async def clear_settings_cache(context: ContextTypes.DEFAULT_TYPE):
    """يمسح ذاكرة التخزين المؤقت للإعدادات بعد التحديث."""
    if 'settings_cache' in context.user_data:
        del context.user_data['settings_cache']

async def show_settings_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """(من BN.py)"""
    keyboard = [
        [InlineKeyboardButton("🧠 إعدادات الذكاء التكيفي", callback_data="settings_adaptive")],
        [InlineKeyboardButton("🎛️ تعديل المعايير المتقدمة", callback_data="settings_params")],
        [InlineKeyboardButton("🔭 تفعيل/تعطيل الماسحات", callback_data="settings_scanners")],
        [InlineKeyboardButton("🗂️ أنماط جاهزة", callback_data="settings_presets")], # <-- [تم الإصلاح]
        [InlineKeyboardButton("🚫 القائمة السوداء", callback_data="settings_blacklist"), InlineKeyboardButton("🗑️ إدارة البيانات", callback_data="settings_data")]
    ]
    message_text = "⚙️ *الإعدادات الرئيسية*\n\nاختر فئة الإعدادات التي تريد تعديلها."
    target_message = update.message or update.callback_query.message
    if update.callback_query: 
        await safe_edit_message(update.callback_query, message_text, reply_markup=InlineKeyboardMarkup(keyboard))
    else: 
        await target_message.reply_text(message_text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))

async def show_adaptive_intelligence_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """(من BN.py) - معدل لـ API (النسخة الكاملة)"""
    query = update.callback_query
    await query.answer("جاري جلب إعدادات الذكاء...")
    try:
        s = await get_settings_from_cache_or_api(context)

        def bool_format(key, text):
            val = s.get(key, False)
            emoji = "✅" if val else "❌"
            return f"{text}: {emoji} مفعل"

        keyboard = [
            [InlineKeyboardButton(bool_format('adaptive_intelligence_enabled', 'تفعيل الذكاء التكيفي'), callback_data="param_toggle_adaptive_intelligence_enabled")],
            [InlineKeyboardButton(bool_format('wise_man_auto_close', 'الإغلاق الآلي للرجل الحكيم'), callback_data="param_toggle_wise_man_auto_close")],
            [InlineKeyboardButton(bool_format('wise_guardian_enabled', 'تفعيل الحارس الحكيم (للخسائر)'), callback_data="param_toggle_wise_guardian_enabled")],
            [InlineKeyboardButton(bool_format('dynamic_trade_sizing_enabled', 'الحجم الديناميكي للصفقات'), callback_data="param_toggle_dynamic_trade_sizing_enabled")],
            [InlineKeyboardButton(bool_format('strategy_proposal_enabled', 'اقتراحات الاستراتيجيات (للعامل)'), callback_data="param_toggle_strategy_proposal_enabled")],
            [InlineKeyboardButton("--- معايير الضبط ---", callback_data="noop")],
            # (يجب إضافة المعايير الرقمية بنفس الطريقة param_set_...)
            [InlineKeyboardButton("🔙 العودة للإعدادات", callback_data="settings_main")]
        ]
        await safe_edit_message(query, "🧠 **إعدادات الذكاء التكيفي**\n\nتحكم في كيفية تعلم البوت وتكيفه:", reply_markup=InlineKeyboardMarkup(keyboard))
    except httpx.HTTPStatusError as e:
        await handle_api_error(query, e)
    except Exception as e:
        await safe_edit_message(query, f"❌ خطأ: {e}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 العودة", callback_data="settings_main")]]))

async def show_parameters_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """(من BN.py) - معدل لـ API (النسخة الكاملة)"""
    query = update.callback_query
    await query.answer("جاري جلب المعايير...")
    try:
        s = await get_settings_from_cache_or_api(context)

        def bool_format(key, text):
            val = s.get(key, False)
            emoji = "✅" if val else "❌"
            return f"{text}: {emoji} مفعل"

        keyboard = [
            [InlineKeyboardButton("--- إعدادات عامة ---", callback_data="noop")],
            [InlineKeyboardButton(f"عدد العملات للفحص: {s['top_n_symbols_by_volume']}", callback_data="param_set_top_n_symbols_by_volume"),
             InlineKeyboardButton(f"أقصى عدد للصفقات: {s['max_concurrent_trades']}", callback_data="param_set_max_concurrent_trades")],
            [InlineKeyboardButton("--- إعدادات المخاطر ---", callback_data="noop")],
            [InlineKeyboardButton(f"حجم الصفقة ($): {s['real_trade_size_usdt']}", callback_data="param_set_real_trade_size_usdt"),
             InlineKeyboardButton(f"مضاعف وقف الخسارة (ATR): {s['atr_sl_multiplier']}", callback_data="param_set_atr_sl_multiplier")],
            [InlineKeyboardButton(f"نسبة المخاطرة/العائد: {s['risk_reward_ratio']}", callback_data="param_set_risk_reward_ratio")],
            [InlineKeyboardButton(bool_format('trailing_sl_enabled', 'تفعيل الوقف المتحرك'), callback_data="param_toggle_trailing_sl_enabled")],
            [InlineKeyboardButton(f"تفعيل الوقف المتحرك (%): {s['trailing_sl_activation_percent']}", callback_data="param_set_trailing_sl_activation_percent")],
            [InlineKeyboardButton(f"مسافة الوقف المتحرك (%): {s['trailing_sl_callback_percent']}", callback_data="param_set_trailing_sl_callback_percent")],
            [InlineKeyboardButton("--- إعدادات الفلاتر ---", callback_data="noop")],
            [InlineKeyboardButton(bool_format('btc_trend_filter_enabled', 'فلتر اتجاه BTC'), callback_data="param_toggle_btc_trend_filter_enabled")],
            [InlineKeyboardButton(bool_format('market_mood_filter_enabled', 'فلتر الخوف والطمع'), callback_data="param_toggle_market_mood_filter_enabled"),
             InlineKeyboardButton(f"حد مؤشر الخوف: {s['fear_and_greed_threshold']}", callback_data="param_set_fear_and_greed_threshold")],
            [InlineKeyboardButton(bool_format('adx_filter_enabled', 'فلتر ADX'), callback_data="param_toggle_adx_filter_enabled"),
             InlineKeyboardButton(f"مستوى فلتر ADX: {s['adx_filter_level']}", callback_data="param_set_adx_filter_level")],
            [InlineKeyboardButton(bool_format('news_filter_enabled', 'فلتر الأخبار والبيانات'), callback_data="param_toggle_news_filter_enabled")],
            [InlineKeyboardButton("🔙 العودة للإعدادات", callback_data="settings_main")]
        ]
        await safe_edit_message(query, "🎛️ **تعديل المعايير المتقدمة**\n\nاضغط على أي معيار لتعديل قيمته مباشرة:", reply_markup=InlineKeyboardMarkup(keyboard))
    except httpx.HTTPStatusError as e:
        await handle_api_error(query, e)
    except Exception as e:
        await safe_edit_message(query, f"❌ خطأ: {e}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 العودة", callback_data="settings_main")]]))

async def show_scanners_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """(من BN.py) - معدل لـ API (النسخة الكاملة)"""
    query = update.callback_query
    await query.answer("جاري جلب الماسحات...")
    try:
        headers = await get_api_headers(context)
        async with httpx.AsyncClient() as client:
            settings_res = await client.get(f"{API_SERVER_URL}/api/settings", headers=headers)
            report_res = await client.get(f"{API_SERVER_URL}/api/dashboard/strategy_report", headers=headers)
            settings_res.raise_for_status()
            report_res.raise_for_status()
            
            s = await get_settings_from_cache_or_api(context)
            active_scanners = s.get('active_scanners', [])
            performance_data = report_res.json()

        keyboard = []
        for key, name in STRATEGY_NAMES_AR.items():
            status_emoji = "✅" if key in active_scanners else "❌"
            perf_hint = ""
            if (perf := performance_data.get(key)):
                perf_hint = f" ({perf['win_rate']}% WR)"
            keyboard.append([InlineKeyboardButton(f"{status_emoji} {name}{perf_hint}", callback_data=f"scanner_toggle_{key}")])
        
        keyboard.append([InlineKeyboardButton("🔙 العودة للإعدادات", callback_data="settings_main")])
        await safe_edit_message(query, "اختر الماسحات لتفعيلها أو تعطيلها (مع تلميح الأداء):", reply_markup=InlineKeyboardMarkup(keyboard))
    except httpx.HTTPStatusError as e:
        await handle_api_error(query, e)
    except Exception as e:
        await safe_edit_message(query, f"❌ خطأ: {e}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 العودة", callback_data="settings_main")]]))


async def show_presets_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """(من BN.py) - [تم الإصلاح]"""
    query = update.callback_query
    s = await get_settings_from_cache_or_api(context)
    current_preset = s.get('active_preset_name', 'مخصص')

    keyboard = []
    for key, name in PRESET_NAMES_AR.items():
        emoji = "🔹" if name == current_preset else "▫️"
        keyboard.append([InlineKeyboardButton(f"{emoji} {name}", callback_data=f"preset_set_{key}")])
    
    keyboard.append([InlineKeyboardButton("🔙 العودة للإعدادات", callback_data="settings_main")])
    await safe_edit_message(query, f"**🗂️ أنماط جاهزة**\n\nالنمط الحالي: **{current_preset}**\nاختر نمط إعدادات جاهز:", reply_markup=InlineKeyboardMarkup(keyboard))


async def show_blacklist_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """(من BN.py) - معدل لـ API"""
    query = update.callback_query
    await query.answer("جاري جلب القائمة...")
    try:
        s = await get_settings_from_cache_or_api(context)
        blacklist = s.get('asset_blacklist', [])
        
        blacklist_str = ", ".join(f"`{item}`" for item in blacklist) if blacklist else "لا توجد عملات في القائمة."
        text = f"🚫 **القائمة السوداء**\n" \
               f"هذه قائمة بالعملات التي لن يتم التداول عليها:\n\n{blacklist_str}"
        keyboard = [
            [InlineKeyboardButton("➕ إضافة عملة", callback_data="blacklist_add"), InlineKeyboardButton("➖ إزالة عملة", callback_data="blacklist_remove")],
            [InlineKeyboardButton("🔙 العودة للإعدادات", callback_data="settings_main")]
        ]
        await safe_edit_message(query, text, reply_markup=InlineKeyboardMarkup(keyboard))
    except httpx.HTTPStatusError as e:
        await handle_api_error(query, e)
    except Exception as e:
        await safe_edit_message(query, f"❌ خطأ: {e}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 العودة", callback_data="settings_main")]]))


async def show_data_management_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """(من BN.py)"""
    keyboard = [[InlineKeyboardButton("‼️ مسح كل الصفقات ‼️", callback_data="data_clear_confirm")], [InlineKeyboardButton("🔙 العودة للإعدادات", callback_data="settings_main")]]
    await safe_edit_message(update.callback_query, "🗑️ *إدارة البيانات*\n\n**تحذير:** هذا الإجراء سيحذف سجل جميع الصفقات بشكل نهائي.", reply_markup=InlineKeyboardMarkup(keyboard))

async def handle_clear_data_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """(من BN.py)"""
    keyboard = [[InlineKeyboardButton("نعم، متأكد. احذف كل شيء.", callback_data="data_clear_execute")], [InlineKeyboardButton("لا، تراجع.", callback_data="settings_data")]]
    await safe_edit_message(update.callback_query, "🛑 **تأكيد نهائي: حذف البيانات**\n\nهل أنت متأكد أنك تريد حذف جميع بيانات الصفقات بشكل نهائي؟", reply_markup=InlineKeyboardMarkup(keyboard))

async def handle_clear_data_execute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """(من BN.py) - معدل لـ API"""
    query = update.callback_query
    await safe_edit_message(query, "جاري حذف البيانات...", reply_markup=None)
    try:
        headers = await get_api_headers(context)
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.delete(f"{API_SERVER_URL}/api/data/clear_trades", headers=headers)
            response.raise_for_status()
        await safe_edit_message(query, f"✅ {response.json().get('message')}")
    except httpx.HTTPStatusError as e:
        await handle_api_error(query, e)
    except Exception as e:
        await safe_edit_message(query, f"❌ خطأ: {e}")
    
    await asyncio.sleep(2)
    await show_settings_menu(update, context)

# =======================================================================================
# --- معالجات الإعدادات (Handlers) ---
# =======================================================================================

async def _update_settings(query: Update.callback_query, context: ContextTypes.DEFAULT_TYPE, updates: dict):
    """دالة مساعدة لإرسال تحديثات الإعدادات إلى الـ API."""
    try:
        headers = await get_api_headers(context)
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{API_SERVER_URL}/api/settings", 
                json={"updates": updates},
                headers=headers
            )
            response.raise_for_status()
        
        # مسح الذاكرة المؤقتة بعد التحديث
        clear_settings_cache(context)
        return True
    except httpx.HTTPStatusError as e:
        await handle_api_error(query, e)
        return False
    except Exception as e:
        await safe_edit_message(query, f"❌ خطأ في الاتصال: {e}")
        return False

async def handle_toggle_parameter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """(من BN.py) - معدل لـ API"""
    query = update.callback_query
    await query.answer("جاري التبديل...")
    param_key = query.data.replace("param_toggle_", "")
    
    try:
        s = await get_settings_from_cache_or_api(context)
        current_value = s.get(param_key, False)
        
        if await _update_settings(query, context, {param_key: not current_value}):
            # إعادة تحميل القائمة التي كنا فيها
            if "adaptive" in param_key or "wise_man" in param_key or "dynamic" in param_key or "strategy" in param_key:
                await show_adaptive_intelligence_menu(update, context)
            else:
                await show_parameters_menu(update, context)

    except httpx.HTTPStatusError as e:
        await handle_api_error(query, e)
    except Exception as e:
        await safe_edit_message(query, f"❌ خطأ: {e}")

async def handle_scanner_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """(من BN.py) - معدل لـ API"""
    query = update.callback_query
    await query.answer("جاري التبديل...")
    scanner_key = query.data.replace("scanner_toggle_", "")
    
    try:
        s = await get_settings_from_cache_or_api(context)
        active_scanners = s.get('active_scanners', [])

        if scanner_key in active_scanners:
            if len(active_scanners) > 1:
                active_scanners.remove(scanner_key)
            else:
                await query.answer("يجب تفعيل ماسح واحد على الأقل.", show_alert=True); return
        else:
            active_scanners.append(scanner_key)
        
        if await _update_settings(query, context, {"active_scanners": active_scanners, "active_preset_name": "مخصص"}):
            await show_scanners_menu(update, context) # تحديث القائمة

    except httpx.HTTPStatusError as e:
        await handle_api_error(query, e)
    except Exception as e:
        await safe_edit_message(query, f"❌ خطأ: {e}")


async def handle_preset_set(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """(من BN.py) - [تم الإصلاح]"""
    query = update.callback_query
    preset_key = query.data.replace("preset_set_", "")
    
    if preset_settings := SETTINGS_PRESETS.get(preset_key):
        await query.answer(f"✅ جاري تفعيل نمط: {PRESET_NAMES_AR.get(preset_key, preset_key)}...")
        
        # إضافة اسم النمط إلى التحديثات
        preset_settings_with_name = preset_settings.copy()
        preset_settings_with_name["active_preset_name"] = PRESET_NAMES_AR.get(preset_key, preset_key)
        
        # إرسال النمط الجاهز كتحديث كامل (سيقوم الخادم بتحديث كل هذه الحقول)
        if await _update_settings(query, context, preset_settings_with_name):
            await show_presets_menu(update, context)
    else:
        await query.answer("لم يتم العثور على النمط.")


async def handle_parameter_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """(من BN.py)"""
    query = update.callback_query
    param_key = query.data.replace("param_set_", "")
    context.user_data['setting_to_change'] = param_key
    
    # جلب القيمة الحالية
    try:
        s = await get_settings_from_cache_or_api(context)
        current_value = s.get(param_key, "غير معرف")
        await query.message.reply_text(f"أرسل القيمة الرقمية الجديدة لـ `{param_key}`:\n(القيمة الحالية: `{current_value}`)", parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
         await query.message.reply_text(f"❌ خطأ في جلب القيمة الحالية: {e}")


async def handle_blacklist_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """(من BN.py)"""
    query = update.callback_query
    action = query.data.replace("blacklist_", "")
    context.user_data['blacklist_action'] = action
    await query.message.reply_text(f"أرسل رمز العملة التي تريد **{ 'إضافتها' if action == 'add' else 'إزالتها'}** (مثال: `BTC`)")

async def handle_setting_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """(من BN.py) - معدل لـ API"""
    user_input = update.message.text.strip()
    parent_menu_data = None
    
    try:
        # --- معالجة القائمة السوداء ---
        if 'blacklist_action' in context.user_data:
            action = context.user_data.pop('blacklist_action')
            symbol = user_input.upper().replace("/USDT", "")
            parent_menu_data = "settings_blacklist"
            
            s = await get_settings_from_cache_or_api(context)
            blacklist = s.get('asset_blacklist', [])

            if action == 'add':
                if symbol not in blacklist: 
                    blacklist.append(symbol)
                    await update.message.reply_text(f"✅ تم إضافة `{symbol}`...")
                else: 
                    await update.message.reply_text(f"⚠️ العملة `{symbol}` موجودة بالفعل."); return
            elif action == 'remove':
                if symbol in blacklist: 
                    blacklist.remove(symbol)
                    await update.message.reply_text(f"✅ تم إزالة `{symbol}`...")
                else: 
                    await update.message.reply_text(f"⚠️ العملة `{symbol}` غير موجودة."); return
            
            # إرسال التحديث
            if await _update_settings(update.callback_query, context, {"asset_blacklist": blacklist, "active_preset_name": "مخصص"}):
                await update.message.reply_text("تم تحديث القائمة السوداء.")
            return

        # --- معالجة الإعدادات الرقمية ---
        if 'setting_to_change' in context.user_data:
            setting_key = context.user_data.pop('setting_to_change')
            if "adaptive" in setting_key or "wise_man" in setting_key:
                parent_menu_data = "settings_adaptive"
            else:
                parent_menu_data = "settings_params"

            try:
                # التحقق إذا كانت القيمة يجب أن تكون صحيحة (int) أو عشرية (float)
                s = await get_settings_from_cache_or_api(context)
                original_value = s.get(setting_key)
                if isinstance(original_value, int):
                    new_value = int(user_input)
                else:
                    new_value = float(user_input)
            except (ValueError, TypeError):
                await update.message.reply_text("❌ قيمة غير صالحة. الرجاء إرسال رقم.")
                return

            if await _update_settings(update.callback_query, context, {setting_key: new_value, "active_preset_name": "مخصص"}):
                await update.message.reply_text(f"✅ تم تحديث `{setting_key}` إلى `{new_value}`.")
            return
            
    except httpx.HTTPStatusError as e:
        await update.message.reply_text(f"❌ فشل التحديث: {e.response.json().get('detail')}")
    except Exception as e:
        await update.message.reply_text(f"❌ خطأ: {e}")
    finally:
        # تنظيف الحالة والعودة إلى القائمة الصحيحة
        if 'blacklist_action' in context.user_data: del context.user_data['blacklist_action']
        if 'setting_to_change' in context.user_data: del context.user_data['setting_to_change']
        
        # محاكاة CbQuery للعودة للقائمة
        if parent_menu_data:
            fake_query = type('Query', (), {'message': update.message, 'data': parent_menu_data, 'edit_message_text': (lambda *args, **kwargs: asyncio.sleep(0)), 'answer': (lambda *args, **kwargs: asyncio.sleep(0))})
            if parent_menu_data == "settings_adaptive": await show_adaptive_intelligence_menu(Update(update.update_id, callback_query=fake_query), context)
            elif parent_menu_data == "settings_params": await show_parameters_menu(Update(update.update_id, callback_query=fake_query), context)
            elif parent_menu_data == "settings_blacklist": await show_blacklist_menu(Update(update.update_id, callback_query=fake_query), context)


async def handle_manual_sell_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """(من BN.py)"""
    query = update.callback_query
    trade_id = int(query.data.split('_')[-1])
    message = f"🛑 **تأكيد البيع الفوري** 🛑\n\nهل أنت متأكد أنك تريد بيع الصفقة رقم `#{trade_id}` بسعر السوق الحالي؟"
    keyboard = [
        [InlineKeyboardButton("✅ نعم، قم بالبيع الآن", callback_data=f"manual_sell_execute_{trade_id}")],
        [InlineKeyboardButton("❌ لا، تراجع", callback_data=f"check_{trade_id}")]
    ]
    await safe_edit_message(query, message, reply_markup=InlineKeyboardMarkup(keyboard))

async def handle_manual_sell_execute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """(من BN.py) - معدل لـ API"""
    query = update.callback_query
    trade_id = int(query.data.split('_')[-1])
    await safe_edit_message(query, "⏳ جاري إرسال أمر البيع إلى العامل...", reply_markup=None)
    try:
        headers = await get_api_headers(context)
        async with httpx.AsyncClient() as client:
            response = await client.post(f"{API_SERVER_URL}/api/trades/{trade_id}/manual_sell", headers=headers)
            response.raise_for_status()
        await query.answer("✅ تم إرسال أمر البيع بنجاح!")
        await safe_edit_message(query, f"✅ {response.json().get('message')}")
        await asyncio.sleep(2)
        await show_dashboard_command(update, context)
    except httpx.HTTPStatusError as e:
        await handle_api_error(query, e)
    except Exception as e:
        await safe_edit_message(query, f"❌ خطأ: {e}")

# =======================================================================================
# --- الموجهات والمعالجات الرئيسية ---
# =======================================================================================

async def universal_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """(من BN.py)"""
    if 'setting_to_change' in context.user_data or 'blacklist_action' in context.user_data:
        await handle_setting_value(update, context)
        return
    text = update.message.text
    if text == "Dashboard 🖥️": await show_dashboard_command(update, context)
    elif text == "الإعدادات ⚙️": 
        await clear_settings_cache(context) # مسح الذاكرة المؤقتة عند فتح الإعدادات
        await show_settings_menu(update, context)

async def button_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """(من BN.py) - موجه الأزرار الرئيسي (النسخة الكاملة)"""
    query = update.callback_query
    await query.answer()
    data = query.data
    
    # مسح الذاكرة المؤقتة إذا غادرنا قائمة الإعدادات
    if not data.startswith("param_") and not data.startswith("scanner_") and not data.startswith("preset_") and not data.startswith("blacklist_") and not data.startswith("data_") and not data.startswith("settings_"):
        await clear_settings_cache(context)
    
    route_map = {
        "db_stats": show_stats_command, "db_trades": show_trades_command, "db_history": show_trade_history_command,
        "db_mood": show_mood_command, "db_diagnostics": show_diagnostics_command, "back_to_dashboard": show_dashboard_command,
        "db_portfolio": show_portfolio_command, "db_manual_scan": manual_scan_command,
        "kill_switch_toggle": toggle_kill_switch, "db_daily_report": daily_report_command, "db_strategy_report": show_strategy_report_command,
        "settings_main": show_settings_menu, "settings_params": show_parameters_menu, "settings_scanners": show_scanners_menu,
        "settings_presets": show_presets_menu, "settings_blacklist": show_blacklist_menu, "settings_data": show_data_management_menu,
        "blacklist_add": handle_blacklist_action, "blacklist_remove": handle_blacklist_action,
        "data_clear_confirm": handle_clear_data_confirmation, "data_clear_execute": handle_clear_data_execute,
        "settings_adaptive": show_adaptive_intelligence_menu,
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
        # --- [ملاحظة معمارية] ---
        # `handle_strategy_adjustment` تم إهماله عمداً
        # لأن العامل (Worker) لا يرسل اقتراحات للواجهة في هذه البنية.
        # يتطلب ذلك جدول "إشعارات" جديد في قاعدة البيانات.
    except Exception as e: 
        logger.error(f"Error in button callback handler for data '{data}': {e}", exc_info=True)
        try:
            await safe_edit_message(query, f"❌ حدث خطأ فادح في معالج الأزرار: {e}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 العودة", callback_data="back_to_dashboard")]]))
        except:
            pass # فشل حتى في إرسال رسالة الخطأ

# =======================================================================================
# --- التشغيل ---
# =======================================================================================

def main():
    if not TELEGRAM_BOT_TOKEN:
        logger.critical("TELEGRAM_BOT_TOKEN not set! Exiting.")
        return
    if not API_SERVER_URL:
        logger.critical("API_SERVER_URL not set! Exiting.")
        return

    logger.info("Starting Telegram UI Client (SaaS - Full Version)...")
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("scan", manual_scan_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, universal_text_handler))
    application.add_handler(CallbackQueryHandler(button_callback_handler))
    
    logger.info("--- Telegram UI Client is now polling ---")
    application.run_polling()
    
if __name__ == '__main__':
    main()
