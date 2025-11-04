import asyncpg
import os
import json
import logging
from contextlib import asynccontextmanager
from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional
from uuid import UUID
from datetime import datetime

# --- إعدادات الاتصال ---
# تأكد من ضبط هذا المتغير في بيئة التشغيل لديك
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://user:pass@host:port/dbname")
POOL = None
logger = logging.getLogger(__name__)

# --- نماذج Pydantic (لضمان سلامة الأنواع) ---
# هذا النموذج يمثل صف الإعدادات الكامل للمستخدم
class UserSettings(BaseModel):
    user_id: UUID
    is_trading_enabled: bool
    active_preset_name: str
    real_trade_size_usdt: float
    max_concurrent_trades: int
    atr_sl_multiplier: float
    risk_reward_ratio: float
    trailing_sl_enabled: bool
    trailing_sl_activation_percent: float
    trailing_sl_callback_percent: float
    top_n_symbols_by_volume: int
    active_scanners: List[str]
    asset_blacklist: List[str]
    market_mood_filter_enabled: bool
    fear_and_greed_threshold: int
    adx_filter_enabled: bool
    adx_filter_level: int
    btc_trend_filter_enabled: bool
    news_filter_enabled: bool
    adaptive_intelligence_enabled: bool
    dynamic_trade_sizing_enabled: bool
    strategy_proposal_enabled: bool
    wise_man_auto_close: bool
    wise_guardian_enabled: bool

class UserKeys(BaseModel):
    api_key: str
    api_secret: str

class ActiveTradeMonitor(BaseModel):
    id: int
    user_id: UUID
    symbol: str
    entry_price: float
    take_profit: float
    stop_loss: float
    status: str
    quantity: float
    highest_price: float
    trailing_sl_active: bool
    last_profit_notification_price: float
    timestamp: datetime # لوقت بدء الصفقة

# --- إدارة مجمع الاتصالات (Pool Management) ---

async def get_db_pool():
    """ينشئ أو يعيد مجمع اتصالات PostgreSQL."""
    global POOL
    if POOL is None:
        try:
            POOL = await asyncpg.create_pool(DATABASE_URL, min_size=5, max_size=20)
            logger.info("Database connection pool created successfully.")
        except Exception as e:
            logger.critical(f"Failed to create database pool: {e}")
            raise
    return POOL

@asynccontextmanager
async def db_connection():
    """يوفر اتصالاً من المجمع."""
    pool = await get_db_pool()
    if pool is None:
        raise ConnectionError("Database pool is not initialized.")
    async with pool.acquire() as connection:
        yield connection

# =Functions for API Server (main.py)
# =================================================

async def get_user_by_telegram_id(chat_id: int) -> Optional[UUID]:
    """يجلب user_id (UUID) المرتبط بحساب تليجرام."""
    async with db_connection() as conn:
        user_id = await conn.fetchval(
            "SELECT user_id FROM user_profiles WHERE telegram_chat_id = $1",
            chat_id
        )
        return user_id

async def get_user_settings(user_id: UUID) -> Optional[UserSettings]:
    """يجلب كائن الإعدادات الكامل لمستخدم معين."""
    async with db_connection() as conn:
        record = await conn.fetchrow("SELECT * FROM user_settings WHERE user_id = $1", user_id)
        if record:
            # تحويل حقول JSONB إلى List[str]
            record_dict = dict(record)
            record_dict["active_scanners"] = json.loads(record_dict["active_scanners"])
            record_dict["asset_blacklist"] = json.loads(record_dict["asset_blacklist"])
            return UserSettings(**record_dict)
        return None

async def update_user_settings(user_id: UUID, settings_data: Dict[str, Any]) -> bool:
    """
    يحدّث إعدادات المستخدم. مرن بما يكفي لتحديث مفتاح واحد أو عدة مفاتيح.
    """
    # تحويل القوائم إلى JSON strings لتخزينها في JSONB
    if 'active_scanners' in settings_data:
        settings_data['active_scanners'] = json.dumps(settings_data['active_scanners'])
    if 'asset_blacklist' in settings_data:
        settings_data['asset_blacklist'] = json.dumps(settings_data['asset_blacklist'])

    set_clause = ", ".join([f"{key} = ${i+2}" for i, key in enumerate(settings_data.keys())])
    query = f"UPDATE user_settings SET {set_clause}, updated_at = now() WHERE user_id = $1"
    
    try:
        async with db_connection() as conn:
            await conn.execute(query, user_id, *settings_data.values())
        return True
    except Exception as e:
        logger.error(f"Failed to update settings for user {user_id}: {e}")
        return False

async def get_dashboard_trades_for_user(user_id: UUID) -> List[Dict]:
    """يجلب الصفقات النشطة والمعلقة لواجهة المستخدم (التليجرام والويب)."""
    async with db_connection() as conn:
        records = await conn.fetch(
            "SELECT id, symbol, status, entry_price, highest_price, reason, timestamp FROM trades "
            "WHERE user_id = $1 AND status IN ('active', 'pending') ORDER BY id DESC",
            user_id
        )
        return [dict(r) for r in records]

async def get_trade_details_for_user(user_id: UUID, trade_id: int) -> Optional[Dict]:
    """يجلب تفاصيل صفقة واحدة لمستخدم."""
    async with db_connection() as conn:
        record = await conn.fetchrow(
            "SELECT * FROM trades WHERE user_id = $1 AND id = $2",
            user_id, trade_id
        )
        return dict(record) if record else None

async def get_trade_history_for_user(user_id: UUID, limit: int = 10) -> List[Dict]:
    """يجلب آخر 10 صفقات مغلقة للمستخدم."""
    async with db_connection() as conn:
        records = await conn.fetch(
            "SELECT symbol, pnl_usdt, status FROM trades "
            "WHERE user_id = $1 AND status NOT IN ('active', 'pending') "
            "ORDER BY id DESC LIMIT $2",
            user_id, limit
        )
        return [dict(r) for r in records]

async def clear_user_trades(user_id: UUID) -> bool:
    """يحذف جميع سجلات الصفقات والسجل التحليلي لمستخدم معين."""
    try:
        async with db_connection() as conn:
            async with conn.transaction():
                # يجب الحذف من السجل التحليلي أولاً بسبب المفتاح الأجنبي
                await conn.execute("DELETE FROM trade_journal WHERE user_id = $1", user_id)
                await conn.execute("DELETE FROM trades WHERE user_id = $1", user_id)
        return True
    except Exception as e:
        logger.error(f"Failed to clear trades for user {user_id}: {e}")
        return False

# =Functions for Bot Worker (bot_worker.py)
# ===================================================

async def get_all_active_users_with_settings() -> List[UserSettings]:
    """(لـ "الماسح") يجلب جميع المستخدمين الذين قاموا بتفعيل التداول."""
    async with db_connection() as conn:
        records = await conn.fetch(
            "SELECT * FROM user_settings WHERE is_trading_enabled = true"
        )
        users = []
        for r in records:
            record_dict = dict(r)
            record_dict["active_scanners"] = json.loads(record_dict["active_scanners"])
            record_dict["asset_blacklist"] = json.loads(record_dict["asset_blacklist"])
            users.append(UserSettings(**record_dict))
        return users

async def get_all_active_trades_for_monitoring() -> List[ActiveTradeMonitor]:
    """(لـ "العيون") يجلب كل الصفقات النشطة من كل المستخدمين."""
    async with db_connection() as conn:
        records = await conn.fetch(
            "SELECT id, user_id, symbol, entry_price, take_profit, stop_loss, status, "
            "quantity, highest_price, trailing_sl_active, last_profit_notification_price, timestamp "
            "FROM trades WHERE status = 'active' OR status = 'retry_exit' OR status = 'force_exit'"
        )
        return [ActiveTradeMonitor(**dict(r)) for r in records]

async def get_user_api_keys(user_id: UUID) -> Optional[UserKeys]:
    """يجلب مفاتيح المستخدم لإنشاء اتصال CCXT."""
    async with db_connection() as conn:
        record = await conn.fetchrow(
            "SELECT api_key_encrypted, api_secret_encrypted FROM user_api_keys WHERE user_id = $1 AND is_valid = true",
            user_id
        )
        if not record:
            logger.warning(f"No valid API keys found for user {user_id}.")
            return None
        
        # !!! في الإنتاج، يجب فك تشفير هذه المفاتيح هنا !!!
        # سأقوم بمحاكاة فك التشفير
        def decrypt_key(key):
            # placeholder: return Fernet(ENCRYPTION_KEY).decrypt(key).decode()
            return key.replace("_encrypted", "") # محاكاة بسيطة

        return UserKeys(
            api_key=decrypt_key(record['api_key_encrypted']),
            api_secret=decrypt_key(record['api_secret_encrypted'])
        )

async def get_active_trade_count_for_user(user_id: UUID) -> int:
    """للتحقق من الحد الأقصى لعدد الصفقات للمستخدم."""
    async with db_connection() as conn:
        count = await conn.fetchval(
            "SELECT COUNT(*) FROM trades WHERE user_id = $1 AND status IN ('active', 'pending')",
            user_id
        )
        return count

async def check_if_symbol_active_for_user(user_id: UUID, symbol: str) -> bool:
    """يتحقق مما إذا كان المستخدم لديه صفقة نشطة أو معلقة لهذه العملة."""
    async with db_connection() as conn:
        return await conn.fetchval(
            "SELECT 1 FROM trades WHERE user_id = $1 AND symbol = $2 AND status IN ('active', 'pending') LIMIT 1",
            user_id, symbol
        ) is not None

async def create_pending_trade(user_id: UUID, signal: dict, buy_order: dict) -> bool:
    """تسجيل الصفقة كـ 'pending' بعد إرسال أمر الشراء."""
    try:
        async with db_connection() as conn:
            await conn.execute("""
                INSERT INTO trades (user_id, timestamp, symbol, reason, order_id, status, entry_price, take_profit, stop_loss, signal_strength, trade_weight)
                VALUES ($1, $2, $3, $4, $5, 'pending', $6, $7, $8, $9, $10)
            """, (
                user_id, datetime.now(datetime.timezone.utc), signal['symbol'], signal['reason'], buy_order['id'],
                signal['entry_price'], signal['take_profit'], signal['stop_loss'],
                signal.get('strength', 1), signal.get('weight', 1.0)
            ))
        return True
    except asyncpg.exceptions.UniqueViolationError:
        logger.warning(f"User {user_id} already has an active/pending trade for {signal['symbol']}. Ignoring duplicate.")
        return False
    except Exception as e:
        logger.error(f"Failed to log pending trade for {user_id} on {signal['symbol']}: {e}")
        return False

async def activate_trade(order_id: str, symbol: str, filled_price: float, filled_quantity: float, risk_reward_ratio: float) -> Optional[Dict]:
    """(لـ UserDataStream) ينشط صفقة معلقة ويعدل TP/SL بناءً على سعر التنفيذ الفعلي."""
    async with db_connection() as conn:
        async with conn.transaction():
            # 1. ابحث عن الصفقة المعلقة
            trade = await conn.fetchrow(
                "SELECT * FROM trades WHERE order_id = $1 AND symbol = $2 AND status = 'pending'",
                order_id, symbol
            )
            if not trade:
                logger.warning(f"Activation ignored: No pending trade found for order {order_id} on {symbol}.")
                return None

            # 2. أعد حساب TP/SL بناءً على سعر التنفيذ الفعلي
            original_sl = trade['stop_loss']
            original_entry = trade['entry_price']
            # حساب المخاطرة الأصلية
            risk_per_unit = original_entry - original_sl
            
            # تطبيق المخاطرة على سعر التنفيذ الجديد
            new_stop_loss = filled_price - risk_per_unit
            new_take_profit = filled_price + (risk_per_unit * risk_reward_ratio)

            # 3. قم بتنشيط الصفقة
            await conn.execute(
                """
                UPDATE trades SET 
                    status = 'active', 
                    entry_price = $1, 
                    quantity = $2, 
                    take_profit = $3, 
                    stop_loss = $4,
                    last_profit_notification_price = $1, -- البدء من سعر الدخول
                    highest_price = $1 -- البدء من سعر الدخول
                WHERE id = $5
                """,
                filled_price, filled_quantity, new_take_profit, new_stop_loss, trade['id']
            )
            
            # 4. أعد جلب الصفقة المحدثة لإرسالها كإشعار
            activated_trade = await conn.fetchrow("SELECT * FROM trades WHERE id = $1", trade['id'])
            return dict(activated_trade) if activated_trade else None

async def close_trade_record(trade_id: int, reason: str, close_price: float, pnl: float) -> Optional[Dict]:
    """(لـ "الأيدي") يغلق الصفقة في قاعدة البيانات ويسجل النتائج."""
    async with db_connection() as conn:
        # استخدام RETURNING * لجلب بيانات الصفقة المغلقة لإرسالها لـ Smart Engine
        closed_trade = await conn.fetchrow(
            """
            UPDATE trades 
            SET status = $1, close_price = $2, pnl_usdt = $3
            WHERE id = $4 AND status != $1
            RETURNING *
            """,
            reason, close_price, pnl, trade_id
        )
        return dict(closed_trade) if closed_trade else None

async def set_trade_status(trade_id: int, status: str):
    """(لـ "الأيدي" / "الحارس") يغير حالة الصفقة (مثل 'incubated' أو 'retry_exit')."""
    async with db_connection() as conn:
        await conn.execute("UPDATE trades SET status = $1 WHERE id = $2", status, trade_id)

async def set_trade_force_exit(trade_id: int, user_id: UUID):
    """(لـ WiseMan) يرفع علم الإغلاق الفوري."""
    async with db_connection() as conn:
        await conn.execute(
            "UPDATE trades SET status = 'force_exit' WHERE id = $1 AND user_id = $2 AND status = 'active'",
            trade_id, user_id
        )

async def update_trade_highest_price(trade_id: int, new_highest_price: float):
    """(لـ "العيون") يحدّث أعلى سعر وصلت له الصفقة."""
    async with db_connection() as conn:
        await conn.execute(
            "UPDATE trades SET highest_price = $1 WHERE id = $2",
            new_highest_price, trade_id
        )

async def update_trade_after_tsl_activation(trade_id: int, new_stop_loss: float):
    """(لـ "العيون") يفعل الوقف المتحرك ويرفع الـ SL لنقطة الدخول."""
    async with db_connection() as conn:
        await conn.execute(
            "UPDATE trades SET trailing_sl_active = true, stop_loss = $1 WHERE id = $2",
            new_stop_loss, trade_id
        )

async def update_trade_tsl(trade_id: int, new_stop_loss: float):
    """(لـ "العيون") يحدّث الـ SL المتحرك."""
    async with db_connection() as conn:
        await conn.execute(
            "UPDATE trades SET stop_loss = $1 WHERE id = $2",
            new_stop_loss, trade_id
        )

async def update_trade_profit_notification(trade_id: int, current_price: float):
    """(لـ "العيون") يحدّث آخر سعر تم إرسال إشعار ربح عنه."""
    async with db_connection() as conn:
        await conn.execute(
            "UPDATE trades SET last_profit_notification_price = $1 WHERE id = $2",
            current_price, trade_id
        )

async def update_trade_take_profit(trade_id: int, new_take_profit: float):
    """(لـ WiseMan) يمدد هدف الربح."""
    async with db_connection() as conn:
        await conn.execute(
            "UPDATE trades SET take_profit = $1 WHERE id = $2",
            new_take_profit, trade_id
        )

async def find_stuck_pending_trades(user_id: UUID, minutes_stuck: int = 2) -> List[Dict]:
    """(لـ "المشرف") يبحث عن الصفقات المعلقة العالقة لمستخدم معين."""
    async with db_connection() as conn:
        records = await conn.fetch(
            """
            SELECT id, order_id, symbol FROM trades 
            WHERE user_id = $1 AND status = 'pending' AND timestamp < (NOW() - INTERVAL '$2 minutes')
            """,
            user_id, minutes_stuck
        )
        return [dict(r) for r in records]

async def find_incubated_trades(user_id: UUID) -> List[int]:
    """(لـ "المشرف") يبحث عن صفقات الحضانة لمستخدم معين لإعادة محاولة إغلاقها."""
    async with db_connection() as conn:
        records = await conn.fetch(
            "SELECT id FROM trades WHERE user_id = $1 AND status = 'incubated'",
            user_id
        )
        return [r['id'] for r in records]
        
async def delete_trade(trade_id: int):
    """(لـ "المشرف") يحذف صفقة (عادةً 'pending' التي فشلت)."""
    async with db_connection() as conn:
        await conn.execute("DELETE FROM trades WHERE id = $1", trade_id)

# =Functions for Smart Engine (bot_worker.py)
# ===================================================

async def log_trade_journal_entry(user_id: UUID, trade_id: int, reason: str, snapshot: dict):
    """(لـ "العقل الذكي") يسجل صفقة جديدة في السجل التحليلي."""
    async with db_connection() as conn:
        await conn.execute(
            """
            INSERT INTO trade_journal (user_id, trade_id, entry_strategy, entry_indicators_snapshot, exit_reason)
            VALUES ($1, $2, $3, $4, 'N/A')
            ON CONFLICT (user_id, trade_id) DO NOTHING
            """,
            user_id, trade_id, reason, json.dumps(snapshot)
        )

async def update_trade_journal_exit(trade_id: int, exit_reason: str, score: int, post_performance: dict, notes: str):
    """(لـ "العقل الذكي") يحدّث السجل التحليلي بتحليل "ماذا لو؟"."""
    async with db_connection() as conn:
        await conn.execute(
            """
            UPDATE trade_journal 
            SET exit_reason = $1, exit_quality_score = $2, post_exit_performance = $3, notes = $4
            WHERE trade_id = $5
            """,
            exit_reason, score, json.dumps(post_performance), notes, trade_id
        )

async def get_journal_and_trades_data(user_id: UUID) -> List[Dict]:
    """(لـ "العقل الذكي") يجلب بيانات السجل والصفقات لتقرير الأنماط."""
    async with db_connection() as conn:
        records = await conn.fetch(
            """
            SELECT tj.*, t.pnl_usdt, t.symbol
            FROM trade_journal tj
            JOIN trades t ON tj.trade_id = t.id
            WHERE tj.user_id = $1 AND tj.notes IS NOT NULL
            """,
            user_id
        )
        return [dict(r) for r in records]

# =Functions for Statistics (main.py)
# ===================================================

async def get_user_overall_stats(user_id: UUID) -> Dict:
    """(لـ API) يحسب الإحصائيات العامة (مثل show_stats_command)."""
    async with db_connection() as conn:
        records = await conn.fetch(
            "SELECT pnl_usdt, status FROM trades WHERE user_id = $1 AND status NOT IN ('active', 'pending')",
            user_id
        )
        if not records:
            return {"total_trades": 0}

        total_trades = len(records)
        total_pnl = sum(r['pnl_usdt'] for r in records if r['pnl_usdt'] is not None)
        wins_data = [r['pnl_usdt'] for r in records if ('(TP)' in r['status'] or '(TSL)' in r['status'] or '(Manual)' in r['status'] and r['pnl_usdt'] > 0) and r['pnl_usdt'] is not None]
        losses_data = [r['pnl_usdt'] for r in records if ('(SL)' in r['status'] or '(Manual)' in r['status'] and r['pnl_usdt'] <= 0) and r['pnl_usdt'] is not None]
        
        win_count = len(wins_data)
        loss_count = len(losses_data)
        win_rate = (win_count / total_trades * 100) if total_trades > 0 else 0
        avg_win = sum(wins_data) / win_count if win_count > 0 else 0
        avg_loss = sum(losses_data) / loss_count if loss_count > 0 else 0
        profit_factor = sum(wins_data) / abs(sum(losses_data)) if sum(losses_data) != 0 else float('inf')

        return {
            "total_pnl": total_pnl,
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "profit_factor": profit_factor,
            "win_rate": win_rate,
            "total_trades": total_trades
        }

async def get_user_strategy_performance(user_id: UUID, limit: int = 100) -> Dict:
    """(لـ API) يحسب أداء الاستراتيجيات (مثل update_strategy_performance)."""
    async with db_connection() as conn:
        records = await conn.fetch(
            "SELECT reason, status, pnl_usdt FROM trades "
            "WHERE user_id = $1 AND status NOT IN ('active', 'pending') "
            "ORDER BY id DESC LIMIT $2",
            user_id, limit
        )
    
    if not records:
        return {}

    stats = {}
    for r in records:
        reason_str, status, pnl = r['reason'], r['status'], r['pnl_usdt']
        if not reason_str or pnl is None: continue
        
        clean_reason = reason_str.split(' (')[0]
        reasons = clean_reason.split(' + ')
        
        for reason in set(reasons):
            if reason not in stats:
                stats[reason] = {'wins': 0, 'losses': 0, 'total_pnl': 0.0, 'win_pnl': 0.0, 'loss_pnl': 0.0}
            
            is_win = '(TP)' in status or '(TSL)' in status or ('(Manual)' in status and pnl > 0)
            if is_win:
                stats[reason]['wins'] += 1
                stats[reason]['win_pnl'] += pnl
            else:
                stats[reason]['losses'] += 1
                stats[reason]['loss_pnl'] += pnl
            stats[reason]['total_pnl'] += pnl

    performance_data = {}
    for r, s in stats.items():
        total = s['wins'] + s['losses']
        win_rate = (s['wins'] / total * 100) if total > 0 else 0
        profit_factor = s['win_pnl'] / abs(s['loss_pnl']) if s['loss_pnl'] != 0 else float('inf')
        performance_data[r] = {
            "win_rate": round(win_rate, 2),
            "profit_factor": round(profit_factor, 2),
            "total_trades": total
        }
    return performance_data

async def get_user_daily_report(user_id: UUID) -> Dict:
    """(لـ API) يجلب إحصائيات التقرير اليومي."""
    async with db_connection() as conn:
        records = await conn.fetch(
            "SELECT * FROM trades "
            "WHERE user_id = $1 AND status NOT IN ('active', 'pending') "
            "AND DATE(timestamp) = CURRENT_DATE",
            user_id
        )
    
    if not records:
        return {"total_trades": 0, "message": "لم يتم إغلاق أي صفقات اليوم."}

    wins = [r for r in records if '(TP)' in r['status'] or '(TSL)' in r['status'] or ('(Manual)' in r['status'] and r['pnl_usdt'] > 0)]
    losses = [r for r in records if '(SL)' in r['status'] or ('(Manual)' in r['status'] and r['pnl_usdt'] <= 0)]
    total_pnl = sum(r['pnl_usdt'] for r in records if r['pnl_usdt'] is not None)
    win_rate = (len(wins) / len(records) * 100) if records else 0
    avg_win_pnl = sum(w['pnl_usdt'] for w in wins if w['pnl_usdt'] is not None) / len(wins) if wins else 0
    avg_loss_pnl = sum(l['pnl_usdt'] for l in losses if l['pnl_usdt'] is not None) / len(losses) if losses else 0
    best_trade = max(records, key=lambda t: t.get('pnl_usdt', -float('inf')), default=None)
    worst_trade = min(records, key=lambda t: t.get('pnl_usdt', float('inf')), default=None)

    return {
        "total_trades": len(records),
        "total_pnl": total_pnl,
        "win_rate": win_rate,
        "avg_win_pnl": avg_win_pnl,
        "avg_loss_pnl": avg_loss_pnl,
        "best_trade_symbol": best_trade['symbol'] if best_trade else "N/A",
        "best_trade_pnl": best_trade['pnl_usdt'] if best_trade else 0,
        "worst_trade_symbol": worst_trade['symbol'] if worst_trade else "N/A",
        "worst_trade_pnl": worst_trade['pnl_usdt'] if worst_trade else 0,
    }
