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
            # جلب الإشعارات غير المقروءة مع ربطها بجدول المستخدمين لجلب chat_id
            notifications = await conn.fetch(
                """
                SELECT 
                    n.id, 
                    n.user_id, 
                    n.title, 
                    n.message, 
                    n.type,
                    p.telegram_chat_id
                FROM 
                    notifications AS n
                JOIN 
                    user_profiles AS p ON n.user_id = p.user_id
                WHERE 
                    n.is_read = false
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
                    logger.warning(f"Notifier: Skipping notification {record['id']} for user {record['user_id']} (no chat_id linked).")
                    sent_ids.append(record['id']) # (نعتبرها "مرسلة" لتجنب تكرارها)
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
    
    logger.info("--- 🚀 Telegram Notifier Service (V3.1) Started ---")
    
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
