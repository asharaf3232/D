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
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:your-password@db.xyz.supabase.co:5432/postgres")
POOL = None
logger = logging.getLogger(__name__)

# --- (جميع النماذج Pydantic كما هي) ---
class BotSettings(BaseModel):
    user_id: UUID
    is_running: bool
    current_preset_name: str
    subscription_status: str
    subscription_expires_at: datetime
    telegram_chat_id: Optional[int] = None

class TradingVariables(BaseModel):
    user_id: UUID
    risk_per_trade: float
    max_drawdown: float
    stop_loss_percentage: float
    take_profit_percentage: float
    risk_reward_ratio: float
    scanner_period: int
    momentum_threshold: float
    signal_sensitivity: str
    indicator_integration: bool
    maker_commission: float
    taker_commission: float
    funding_fee: float
    spread_adjustment: float
    trading_start_hour: int
    trading_end_hour: int
    market_sessions: List[str]
    cooldown_period: int
    max_concurrent_trades: int
    inflation_adjustment: bool
    base_currency: str
    currency_protection: bool
    min_trade_amount: float
    max_trade_amount: float
    confidence_threshold: float
    pattern_sensitivity: str
    learning_enabled: bool
    data_lookback_period: int

class ActiveStrategy(BaseModel):
    strategy_name: str
    parameters: Dict[str, Any]

class UserKeys(BaseModel):
    api_key: str
    api_secret: str
    passphrase: Optional[str] = None

# --- (إدارة مجمع الاتصالات كما هي) ---
async def get_db_pool():
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
    pool = await get_db_pool()
    if pool is None:
        raise ConnectionError("Database pool is not initialized.")
    async with pool.acquire() as connection:
        yield connection

# =================================================================
# --- دوال للعامل (Bot Worker) ---
# =================================================================

async def get_all_active_users() -> List[BotSettings]:
    """
    [ ⬇️ القفل رقم 2 (V4) ⬇️ ]
    (للعامل) يجلب جميع المستخدمين الذين هم 'قيد التشغيل' واشتراكهم 'ساري'.
    """
    async with db_connection() as conn:
        records = await conn.fetch(
            """
            SELECT user_id, is_running, current_preset_name, subscription_status, subscription_expires_at, telegram_chat_id 
            FROM user_settings 
            WHERE is_running = true 
              AND subscription_status = 'active'
              AND subscription_expires_at > NOW()
            """
        )
        return [BotSettings(**dict(r)) for r in records]

async def get_user_api_keys(user_id: UUID) -> Optional[UserKeys]:
    """(للعامل والخادم) يجلب مفاتيح المستخدم لإنشاء اتصال CCXT."""
    async with db_connection() as conn:
        record = await conn.fetchrow("SELECT api_key_encrypted, api_secret_encrypted, passphrase_encrypted FROM user_api_keys WHERE user_id = $1 AND is_valid = true", user_id)
        if not record:
            logger.warning(f"No valid API keys found for user {user_id}.")
            return None
        def decrypt_key(key): return key.replace("_encrypted", "") # محاكاة
        return UserKeys(
            api_key=decrypt_key(record['api_key_encrypted']),
            api_secret=decrypt_key(record['api_secret_encrypted']),
            passphrase=decrypt_key(record['passphrase_encrypted']) if record['passphrase_encrypted'] else None
        )

async def get_user_trading_variables(user_id: UUID) -> Optional[TradingVariables]:
    """(للعامل) يجلب إعدادات التداول المتقدمة للمستخدم."""
    async with db_connection() as conn:
        record = await conn.fetchrow("SELECT * FROM advanced_variables WHERE user_id = $1", user_id)
        if record:
            record_dict = dict(record)
            record_dict["market_sessions"] = json.loads(record_dict["market_sessions"])
            record_dict.pop('id', None); record_dict.pop('updated_at', None)
            return TradingVariables(**record_dict)
        return None

async def get_user_enabled_strategies(user_id: UUID) -> List[ActiveStrategy]:
    """(للعامل) يجلب الاستراتيجيات المفعلة فقط للمستخدم."""
    async with db_connection() as conn:
        records = await conn.fetch("SELECT strategy_name, parameters FROM strategies WHERE user_id = $1 AND is_enabled = true", user_id)
        return [ActiveStrategy(strategy_name=r['strategy_name'], parameters=json.loads(r['parameters'] or '{}')) for r in records]

async def create_trade(user_id: UUID, symbol: str, reason: str, entry_price: float, qty: float, tp: float, sl: float, order_id: str) -> Optional[Dict]:
    """(للعامل) يسجل صفقة جديدة في قاعدة البيانات (V4 - مع حقول TSL)."""
    try:
        async with db_connection() as conn:
            new_trade = await conn.fetchrow(
                """
                INSERT INTO trades (user_id, symbol, status, reason, entry_price, quantity, take_profit, stop_loss, order_id, opened_at, highest_price, last_profit_notification_price, trailing_sl_active)
                VALUES ($1, $2, 'active', $3, $4, $5, $6, $7, $8, NOW(), $4, $4, false) -- (highest_price = entry_price)
                RETURNING *
                """,
                user_id, symbol, reason, entry_price, qty, tp, sl, order_id
            )
            return dict(new_trade) if new_trade else None
    except Exception as e:
        logger.error(f"Failed to create trade for user {user_id} on {symbol}: {e}")
        return None

async def close_trade(trade_id: int, exit_price: float, pnl: float) -> Optional[Dict]:
    """(للعامل) يغلق صفقة ويسجل النتائج."""
    try:
        async with db_connection() as conn:
            closed_trade = await conn.fetchrow(
                """
                UPDATE trades 
                SET status = 'closed', exit_price = $1, pnl_usdt = $2, closed_at = NOW()
                WHERE id = $3 AND status LIKE 'closing_%'
                RETURNING *
                """,
                exit_price, pnl, trade_id
            )
            return dict(closed_trade) if closed_trade else None
    except Exception as e:
        logger.error(f"Failed to close trade {trade_id}: {e}")
        return None

async def create_notification(user_id: UUID, title: str, message: str, type: str = 'info', trade_id: Optional[int] = None):
    """(ل للعامل) يسجل إشعاراً جديداً للمستخدم ليراه في الواجهة."""
    try:
        async with db_connection() as conn:
            await conn.execute(
                """
                INSERT INTO notifications (user_id, title, message, type, related_trade_id)
                VALUES ($1, $2, $3, $4, $5)
                """,
                user_id, title, message, type, trade_id
            )
    except Exception as e:
        logger.error(f"Failed to create notification for user {user_id}: {e}")

# =================================================================
# --- [تم الإصلاح] - دوال إدارة الصفقات المفقودة (V2.1) ---
# --- (هذه هي دوال "كل فسفوسة") ---
# =================================================================

async def set_trade_status(trade_id: int, status: str):
    """يغير حالة الصفقة (مثل 'closing_tp' أو 'active')."""
    async with db_connection() as conn:
        await conn.execute("UPDATE trades SET status = $1 WHERE id = $2", status, trade_id)

async def update_trade_highest_price(trade_id: int, new_highest_price: float):
    """(لـ "العيون") يحدّث أعلى سعر وصلت له الصفقة."""
    async with db_connection() as conn:
        await conn.execute("UPDATE trades SET highest_price = $1 WHERE id = $2 AND $1 > highest_price", new_highest_price, trade_id)

async def update_trade_after_tsl_activation(trade_id: int, new_stop_loss: float):
    """(لـ "العيون") يفعل الوقف المتحرك ويرفع الـ SL لنقطة الدخول."""
    async with db_connection() as conn:
        await conn.execute("UPDATE trades SET trailing_sl_active = true, stop_loss = $1 WHERE id = $2 AND stop_loss < $1", new_stop_loss, trade_id)

async def update_trade_tsl(trade_id: int, new_stop_loss: float):
    """(لـ "العيون") يحدّث الـ SL المتحرك."""
    async with db_connection() as conn:
        await conn.execute("UPDATE trades SET stop_loss = $1 WHERE id = $2 AND stop_loss < $1", new_stop_loss, trade_id)

async def update_trade_profit_notification(trade_id: int, current_price: float):
    """(لـ "العيون") يحدّث آخر سعر تم إرسال إشعار ربح عنه."""
    async with db_connection() as conn:
        await conn.execute("UPDATE trades SET last_profit_notification_price = $1 WHERE id = $2", current_price, trade_id)

async def update_trade_take_profit(trade_id: int, new_take_profit: float):
    """(لـ WiseMan) يمدد هدف الربح."""
    async with db_connection() as conn:
        await conn.execute("UPDATE trades SET take_profit = $1 WHERE id = $2 AND take_profit < $1", new_take_profit, trade_id)


# =================================================================
# --- دوال للخادم (API Server) ---
# =================================================================

async def get_user_settings_by_id(user_id: UUID) -> Optional[BotSettings]:
    """(جديد V4) يجلب إعدادات المستخدم الكاملة بما في ذلك حالة الاشتراك."""
    async with db_connection() as conn:
        record = await conn.fetchrow(
            "SELECT * FROM user_settings WHERE user_id = $1", user_id
        )
        if record:
            return BotSettings(**dict(record))
        
        # [جديد V4] إنشاء إعدادات افتراضية (تجريبية) إذا لم تكن موجودة
        logger.info(f"Creating default 'trial' settings for new user {user_id}")
        record = await conn.fetchrow(
            """
            INSERT INTO user_settings (user_id, is_running, subscription_status, subscription_expires_at)
            VALUES ($1, false, 'trial', NOW() + INTERVAL '7 days')
            ON CONFLICT (user_id) DO NOTHING
            RETURNING *
            """,
            user_id
        )
        if record:
            return BotSettings(**dict(record))
        # (إذا حدثت حالة سباق، احصل عليها مرة أخرى)
        record = await conn.fetchrow("SELECT * FROM user_settings WHERE user_id = $1", user_id)
        return BotSettings(**dict(record)) if record else None

async def set_bot_status(user_id: UUID, is_running: bool) -> Optional[BotSettings]:
    """(لـ /bot/start و /bot/stop) يحدّث حالة البوت."""
    async with db_connection() as conn:
        record = await conn.fetchrow(
            """
            UPDATE user_settings SET is_running = $1, updated_at = NOW()
            WHERE user_id = $1
            RETURNING *
            """,
            is_running, user_id
        )
        return BotSettings(**dict(record)) if record else None

# (بقية دوال الخادم: save_api_keys, set_api_keys_valid, get_active_trades, ...)
# (تبقى كما هي من V2)

async def save_api_keys(user_id: UUID, api_key: str, secret_key: str, passphrase: Optional[str]) -> bool:
    def encrypt_key(key): return key + "_encrypted" # محاكاة
    try:
        async with db_connection() as conn:
            # [تعديل V4] إضافة is_trial_key
            # (هذا يحتاج منطق إضافي: هل هو في فترة تجريبية؟ هل هذا المفتاح مستخدم من قبل؟)
            await conn.execute(
                """
                INSERT INTO user_api_keys (user_id, api_key_encrypted, api_secret_encrypted, passphrase_encrypted, is_valid, is_trial_key)
                VALUES ($1, $2, $3, $4, false, true) -- (نفترض أنه مفتاح تجريبي مبدئياً)
                ON CONFLICT (user_id, exchange) DO UPDATE
                SET api_key_encrypted = $2, api_secret_encrypted = $3, passphrase_encrypted = $4, is_valid = false, created_at = NOW()
                """,
                user_id, encrypt_key(api_key), encrypt_key(secret_key), encrypt_key(passphrase) if passphrase else None
            )
        return True
    except Exception as e:
        logger.error(f"Failed to save keys for user {user_id}: {e}"); return False

async def set_api_keys_valid(user_id: UUID, is_valid: bool):
    async with db_connection() as conn:
        await conn.execute("UPDATE user_api_keys SET is_valid = $1 WHERE user_id = $2", is_valid, user_id)

async def get_active_trades(user_id: UUID) -> List[Dict]:
    async with db_connection() as conn:
        records = await conn.fetch("SELECT * FROM trades WHERE user_id = $1 AND status = 'active' ORDER BY opened_at DESC", user_id)
        return [dict(r) for r in records]

async def flag_trade_for_closure(user_id: UUID, trade_id: int) -> bool:
    async with db_connection() as conn:
        result = await conn.execute("UPDATE trades SET status = 'closing_manual' WHERE id = $1 AND user_id = $2 AND status = 'active'", trade_id, user_id)
        return result == "UPDATE 1"

async def get_trades_history(user_id: UUID, limit: int) -> List[Dict]:
    async with db_connection() as conn:
        records = await conn.fetch("SELECT * FROM trades WHERE user_id = $1 AND status = 'closed' ORDER BY closed_at DESC LIMIT $2", user_id, limit)
        return [dict(r) for r in records]

async def get_trades_stats(user_id: UUID) -> Dict:
    async with db_connection() as conn:
        stats = await conn.fetchrow(
            """
            SELECT COUNT(*) AS total_trades,
                SUM(CASE WHEN pnl_usdt > 0 THEN 1 ELSE 0 END) AS winning_trades,
                SUM(CASE WHEN pnl_usdt <= 0 THEN 1 ELSE 0 END) AS losing_trades,
                SUM(pnl_usdt) AS total_pnl_usdt, AVG(pnl_usdt) AS avg_pnl_usdt,
                SUM(CASE WHEN pnl_usdt > 0 THEN pnl_usdt ELSE 0 END) AS total_profit,
                SUM(CASE WHEN pnl_usdt <= 0 THEN pnl_usdt ELSE 0 END) AS total_loss
            FROM trades WHERE user_id = $1 AND status = 'closed'
            """, user_id
        )
        if not stats or stats['total_trades'] == 0:
            return {"total_trades": 0, "win_rate": 0, "total_pnl_usdt": 0, "avg_pnl_usdt": 0, "profit_factor": 0}
        win_rate = (stats['winning_trades'] / stats['total_trades']) * 100 if stats['total_trades'] > 0 else 0
        profit_factor = stats['total_profit'] / abs(stats['total_loss']) if stats['total_loss'] != 0 else float('inf')
        return {
            "total_trades": stats['total_trades'], "winning_trades": stats['winning_trades'], "losing_trades": stats['losing_trades'],
            "win_rate": win_rate, "total_pnl_usdt": stats['total_pnl_usdt'], "avg_pnl_usdt": stats['avg_pnl_usdt'], "profit_factor": profit_factor
        }

async def get_api_settings(user_id: UUID) -> Optional[Dict]:
    async with db_connection() as conn:
        record = await conn.fetchrow("SELECT * FROM advanced_variables WHERE user_id = $1", user_id)
        if not record: return None
        record_dict = dict(record); record_dict["market_sessions"] = json.loads(record_dict["market_sessions"]); return record_dict

async def update_api_settings(user_id: UUID, settings: Dict[str, Any]) -> bool:
    try:
        if 'market_sessions' in settings: settings['market_sessions'] = json.dumps(settings['market_sessions'])
        set_clause = ", ".join([f"{key} = ${i+2}" for i, key in enumerate(settings.keys())])
        query = f"UPDATE advanced_variables SET {set_clause}, updated_at = NOW() WHERE user_id = $1"
        async with db_connection() as conn:
            await conn.execute(query, user_id, *settings.values())
        return True
    except Exception as e:
        logger.error(f"Failed to update settings for user {user_id}: {e}"); return False

async def apply_preset_settings(user_id: UUID, preset_name: str, settings: Dict[str, Any]) -> bool:
    try:
        await update_api_settings(user_id, settings)
        async with db_connection() as conn:
            await conn.execute("UPDATE user_settings SET current_preset_name = $1 WHERE user_id = $2", preset_name, user_id)
        return True
    except Exception as e:
        logger.error(f"Failed to apply preset for user {user_id}: {e}"); return False

async def get_notifications(user_id: UUID, limit: int, unread_only: bool) -> List[Dict]:
    query = "SELECT * FROM notifications WHERE user_id = $1"
    params = [user_id]
    if unread_only: query += " AND is_read = false"
    query += " ORDER BY timestamp DESC LIMIT $2"; params.append(limit)
    async with db_connection() as conn:
        records = await conn.fetch(query, *params)
        return [dict(r) for r in records]

async def mark_notification_read(user_id: UUID, notification_id: int) -> bool:
    async with db_connection() as conn:
        result = await conn.execute("UPDATE notifications SET is_read = true WHERE id = $1 AND user_id = $2", notification_id, user_id)
        return result == "UPDATE 1"

# =================================================================
# --- [جديد V4] دوال لربط التليجرام والدفع ---
# =================================================================

async def update_user_telegram_id(user_id: UUID, telegram_chat_id: int) -> bool:
    """(لـ /telegram/link) يربط معرف تليجرام بحساب الويب."""
    try:
        async with db_connection() as conn:
            await conn.execute(
                "UPDATE user_settings SET telegram_chat_id = $1 WHERE user_id = $2",
                telegram_chat_id, user_id
            )
        return True
    except asyncpg.exceptions.UniqueViolationError:
        logger.warning(f"Telegram ID {telegram_chat_id} is already linked to another account.")
        return False # هذا الحساب مستخدم من قبل
    except Exception as e:
        logger.error(f"Failed to link Telegram ID for user {user_id}: {e}")
        return False

async def create_payment_request(user_id: UUID, txt_id: str, plan: str, wallet: str, amount: float) -> bool:
    """(لـ /payment/submit) يسجل طلب الدفع اليدوي للمراجعة."""
    try:
        async with db_connection() as conn:
            await conn.execute(
                """
                INSERT INTO manual_payments (user_id, txt_id, amount_paid, subscription_plan, wallet_address_used, status)
                VALUES ($1, $2, $3, $4, $5, 'pending_review')
                """,
                user_id, txt_id, amount, plan, wallet
            )
            # [جديد] تحديث حالة المستخدم إلى "في انتظار الدفع"
            await conn.execute(
                "UPDATE user_settings SET subscription_status = 'pending_payment' WHERE user_id = $1",
                user_id
            )
        return True
    except asyncpg.exceptions.UniqueViolationError:
        logger.warning(f"Duplicate TXT_ID submitted: {txt_id}")
        return False # تم إرسال هذا المعرف من قبل
    except Exception as e:
        logger.error(f"Failed to create payment request for user {user_id}: {e}")
        return False
