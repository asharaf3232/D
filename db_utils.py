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
# هذا يجب أن يكون رابط PostgreSQL الخاص بـ Supabase
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:your-password@db.xyz.supabase.co:5432/postgres")
POOL = None
logger = logging.getLogger(__name__)

# --- نماذج Pydantic (لضمان سلامة الأنواع) ---

# نموذج لإعدادات البوت الحية (من جدول user_settings)
class BotSettings(BaseModel):
    user_id: UUID
    is_running: bool
    current_preset_name: str

# نموذج لمتغيرات التداول المتقدمة (من جدول advanced_variables)
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

# نموذج للاستراتيجيات النشطة (من جدول strategies)
class ActiveStrategy(BaseModel):
    strategy_name: str
    parameters: Dict[str, Any]

class UserKeys(BaseModel):
    api_key: str
    api_secret: str
    passphrase: Optional[str] = None


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

# =================================================================
# --- دوال للعامل (Bot Worker) ---
# =================================================================

async def get_all_active_users() -> List[BotSettings]:
    """(للعامل) يجلب جميع المستخدمين الذين قاموا بتفعيل البوت."""
    async with db_connection() as conn:
        records = await conn.fetch(
            "SELECT user_id, is_running, current_preset_name FROM user_settings WHERE is_running = true"
        )
        return [BotSettings(**dict(r)) for r in records]

async def get_user_api_keys(user_id: UUID) -> Optional[UserKeys]:
    """(للعامل والخادم) يجلب مفاتيح المستخدم لإنشاء اتصال CCXT."""
    async with db_connection() as conn:
        record = await conn.fetchrow(
            "SELECT api_key_encrypted, api_secret_encrypted, passphrase_encrypted FROM user_api_keys WHERE user_id = $1 AND is_valid = true",
            user_id
        )
        if not record:
            logger.warning(f"No valid API keys found for user {user_id}.")
            return None
        
        # !!! في الإنتاج، يجب فك تشفير هذه المفاتيح هنا !!!
        def decrypt_key(key):
            # placeholder: return Fernet(ENCRYPTION_KEY).decrypt(key).decode()
            return key.replace("_encrypted", "") # محاكاة بسيطة

        return UserKeys(
            api_key=decrypt_key(record['api_key_encrypted']),
            api_secret=decrypt_key(record['api_secret_encrypted']),
            passphrase=decrypt_key(record['passphrase_encrypted']) if record['passphrase_encrypted'] else None
        )

async def get_user_trading_variables(user_id: UUID) -> Optional[TradingVariables]:
    """(للعامل) يجلب إعدادات التداول المتقدمة للمستخدم."""
    async with db_connection() as conn:
        record = await conn.fetchrow(
            "SELECT * FROM advanced_variables WHERE user_id = $1", user_id
        )
        if record:
            record_dict = dict(record)
            record_dict["market_sessions"] = json.loads(record_dict["market_sessions"])
            # (إزالة الحقول غير المتوقعة إذا لزم الأمر)
            record_dict.pop('id', None)
            record_dict.pop('updated_at', None)
            return TradingVariables(**record_dict)
        return None

async def get_user_enabled_strategies(user_id: UUID) -> List[ActiveStrategy]:
    """(للعامل) يجلب الاستراتيجيات المفعلة فقط للمستخدم."""
    async with db_connection() as conn:
        records = await conn.fetch(
            "SELECT strategy_name, parameters FROM strategies WHERE user_id = $1 AND is_enabled = true",
            user_id
        )
        return [ActiveStrategy(strategy_name=r['strategy_name'], parameters=json.loads(r['parameters'])) for r in records]

async def create_trade(user_id: UUID, symbol: str, reason: str, entry_price: float, qty: float, tp: float, sl: float, order_id: str) -> Optional[Dict]:
    """(للعامل) يسجل صفقة جديدة في قاعدة البيانات."""
    try:
        async with db_connection() as conn:
            new_trade = await conn.fetchrow(
                """
                INSERT INTO trades (user_id, symbol, status, reason, entry_price, quantity, take_profit, stop_loss, order_id, opened_at)
                VALUES ($1, $2, 'active', $3, $4, $5, $6, $7, $8, NOW())
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
                WHERE id = $3 AND status = 'active'
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
# --- دوال للخادم (API Server) ---
# =================================================================

async def set_bot_status(user_id: UUID, is_running: bool) -> Optional[BotSettings]:
    """(لـ /bot/start و /bot/stop) [cite: 8] يحدّث حالة البوت."""
    async with db_connection() as conn:
        # استخدام ON CONFLICT لضمان وجود صف دائماً
        record = await conn.fetchrow(
            """
            INSERT INTO user_settings (user_id, is_running)
            VALUES ($1, $2)
            ON CONFLICT (user_id) DO UPDATE
            SET is_running = $2, updated_at = NOW()
            RETURNING user_id, is_running, current_preset_name
            """,
            user_id, is_running
        )
        return BotSettings(**dict(record)) if record else None

async def get_bot_status(user_id: UUID) -> Optional[BotSettings]:
    """(لـ /bot/status) [cite: 8-9] يقرأ حالة البوت."""
    async with db_connection() as conn:
        record = await conn.fetchrow(
            "SELECT user_id, is_running, current_preset_name FROM user_settings WHERE user_id = $1", user_id
        )
        if record:
            return BotSettings(**dict(record))
        # إذا لم يكن هناك صف، قم بإنشاء واحد بحالة "متوقف"
        return await set_bot_status(user_id, False)

async def save_api_keys(user_id: UUID, api_key: str, secret_key: str, passphrase: Optional[str]) -> bool:
    """(لـ /keys) [cite: 10] يحفظ المفاتيح المشفرة."""
    
    # !!! في الإنتاج، يجب تشفير هذه المفاتيح هنا !!!
    def encrypt_key(key):
        # placeholder: return Fernet(ENCRYPTION_KEY).encrypt(key.encode()).decode()
        return key + "_encrypted" # محاكاة بسيطة

    try:
        async with db_connection() as conn:
            await conn.execute(
                """
                INSERT INTO user_api_keys (user_id, api_key_encrypted, api_secret_encrypted, passphrase_encrypted, is_valid)
                VALUES ($1, $2, $3, $4, false) -- ابدأ كـ "غير صالح" حتى يتم اختباره
                ON CONFLICT (user_id, exchange) DO UPDATE
                SET api_key_encrypted = $2, 
                    api_secret_encrypted = $3, 
                    passphrase_encrypted = $4, 
                    is_valid = false,
                    created_at = NOW()
                """,
                user_id, encrypt_key(api_key), encrypt_key(secret_key), encrypt_key(passphrase) if passphrase else None
            )
        return True
    except Exception as e:
        logger.error(f"Failed to save keys for user {user_id}: {e}")
        return False

async def set_api_keys_valid(user_id: UUID, is_valid: bool):
    """(لـ /bot/test-keys) [cite: 9] يحدّث حالة صلاحية المفاتيح."""
    async with db_connection() as conn:
        await conn.execute(
            "UPDATE user_api_keys SET is_valid = $1 WHERE user_id = $2",
            is_valid, user_id
        )

async def get_active_trades(user_id: UUID) -> List[Dict]:
    """(لـ /trades/active) [cite: 11] يجلب الصفقات النشطة."""
    async with db_connection() as conn:
        records = await conn.fetch(
            "SELECT * FROM trades WHERE user_id = $1 AND status = 'active' ORDER BY opened_at DESC",
            user_id
        )
        return [dict(r) for r in records]

async def flag_trade_for_closure(user_id: UUID, trade_id: int) -> bool:
    """(لـ /trades/close) [cite: 11] يرفع علم للعامل لإغلاق الصفقة."""
    async with db_connection() as conn:
        # العامل (Worker) يجب أن يبحث عن حالة 'closing_manual'
        result = await conn.execute(
            "UPDATE trades SET status = 'closing_manual' WHERE id = $1 AND user_id = $2 AND status = 'active'",
            trade_id, user_id
        )
        return result == "UPDATE 1"

async def get_trades_history(user_id: UUID, limit: int) -> List[Dict]:
    """(لـ /trades/history) [cite: 11] يجلب الصفقات المغلقة."""
    async with db_connection() as conn:
        records = await conn.fetch(
            "SELECT * FROM trades WHERE user_id = $1 AND status = 'closed' ORDER BY closed_at DESC LIMIT $2",
            user_id, limit
        )
        return [dict(r) for r in records]

async def get_trades_stats(user_id: UUID) -> Dict:
    """(لـ /trades/stats) [cite: 11] يحسب الإحصائيات."""
    async with db_connection() as conn:
        stats = await conn.fetchrow(
            """
            SELECT
                COUNT(*) AS total_trades,
                SUM(CASE WHEN pnl_usdt > 0 THEN 1 ELSE 0 END) AS winning_trades,
                SUM(CASE WHEN pnl_usdt <= 0 THEN 1 ELSE 0 END) AS losing_trades,
                SUM(pnl_usdt) AS total_pnl_usdt,
                AVG(pnl_usdt) AS avg_pnl_usdt,
                SUM(CASE WHEN pnl_usdt > 0 THEN pnl_usdt ELSE 0 END) AS total_profit,
                SUM(CASE WHEN pnl_usdt <= 0 THEN pnl_usdt ELSE 0 END) AS total_loss
            FROM trades
            WHERE user_id = $1 AND status = 'closed'
            """,
            user_id
        )
        if not stats or stats['total_trades'] == 0:
            return {"total_trades": 0, "win_rate": 0, "total_pnl_usdt": 0, "avg_pnl_usdt": 0, "profit_factor": 0}

        win_rate = (stats['winning_trades'] / stats['total_trades']) * 100 if stats['total_trades'] > 0 else 0
        profit_factor = stats['total_profit'] / abs(stats['total_loss']) if stats['total_loss'] != 0 else float('inf')

        return {
            "total_trades": stats['total_trades'],
            "winning_trades": stats['winning_trades'],
            "losing_trades": stats['losing_trades'],
            "win_rate": win_rate,
            "total_pnl_usdt": stats['total_pnl_usdt'],
            "avg_pnl_usdt": stats['avg_pnl_usdt'],
            "profit_factor": profit_factor
        }

async def get_api_settings(user_id: UUID) -> Optional[Dict]:
    """(لـ GET /settings) [cite: 12] يقرأ الإعدادات المتقدمة."""
    async with db_connection() as conn:
        record = await conn.fetchrow(
            "SELECT * FROM advanced_variables WHERE user_id = $1", user_id
        )
        if not record:
            return None
        # تحويل JSONB
        record_dict = dict(record)
        record_dict["market_sessions"] = json.loads(record_dict["market_sessions"])
        return record_dict

async def update_api_settings(user_id: UUID, settings: Dict[str, Any]) -> bool:
    """(لـ POST /settings) [cite: 12] يحدّث الإعدادات المتقدمة."""
    # (هذا مطابق لما تتوقعه الواجهة عند الضغط على "تطبيق فوري" [cite: 403])
    # هذه الدالة يجب أن تقوم بالتحقق من صحة الحقول قبل التحديث
    try:
        # تحويل القوائم إلى JSONB
        if 'market_sessions' in settings:
            settings['market_sessions'] = json.dumps(settings['market_sessions'])

        # بناء جملة SET
        set_clause = ", ".join([f"{key} = ${i+2}" for i, key in enumerate(settings.keys())])
        query = f"UPDATE advanced_variables SET {set_clause}, updated_at = NOW() WHERE user_id = $1"

        async with db_connection() as conn:
            await conn.execute(query, user_id, *settings.values())
        return True
    except Exception as e:
        logger.error(f"Failed to update settings for user {user_id}: {e}")
        return False

async def apply_preset_settings(user_id: UUID, preset_name: str, settings: Dict[str, Any]) -> bool:
    """(لـ /settings/preset) [cite: 12-13] يطبق النمط الجاهز ويحدّث اسم النمط."""
    try:
        # 1. تحديث الإعدادات المتقدمة
        await update_api_settings(user_id, settings)
        
        # 2. تحديث اسم النمط في user_settings
        async with db_connection() as conn:
            await conn.execute(
                "UPDATE user_settings SET current_preset_name = $1 WHERE user_id = $2",
                preset_name, user_id
            )
        return True
    except Exception as e:
        logger.error(f"Failed to apply preset for user {user_id}: {e}")
        return False

async def get_notifications(user_id: UUID, limit: int, unread_only: bool) -> List[Dict]:
    """(لـ /notifications) [cite: 13] يجلب الإشعارات."""
    query = "SELECT * FROM notifications WHERE user_id = $1"
    params = [user_id]
    
    if unread_only:
        query += " AND is_read = false"
    
    query += " ORDER BY timestamp DESC LIMIT $2"
    params.append(limit)
    
    async with db_connection() as conn:
        records = await conn.fetch(query, *params)
        return [dict(r) for r in records]

async def mark_notification_read(user_id: UUID, notification_id: int) -> bool:
    """(لـ /notifications/{id}/read) [cite: 13-14] يضع علامة "مقروء"."""
    async with db_connection() as conn:
        result = await conn.execute(
            "UPDATE notifications SET is_read = true WHERE id = $1 AND user_id = $2",
            notification_id, user_id
        )
        return result == "UPDATE 1"
