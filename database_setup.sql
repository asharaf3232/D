-- تفعيل ملحق RLS (Row Level Security) في Supabase
-- CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- 1. جدول المستخدمين (الذي تتوقعه Supabase)
-- (نفترض أن هذا الجدول موجود بالفعل في Supabase Auth)
-- CREATE TABLE IF NOT EXISTS public.users ( ... );

-- 2. جدول مفاتيح API (الذي يتوقعه الخادم)
CREATE TABLE IF NOT EXISTS user_api_keys (
    id BIGSERIAL PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    exchange TEXT NOT NULL DEFAULT 'binance',
    api_key_encrypted TEXT NOT NULL,
    api_secret_encrypted TEXT NOT NULL,
    passphrase_encrypted TEXT,
    is_valid BOOLEAN DEFAULT false,
    created_at TIMESTAMPTZ DEFAULT now(),
    -- [إضافة V4] تحديد أن المفتاح للفترة التجريبية لمنع التلاعب
    is_trial_key BOOLEAN DEFAULT false, 
    UNIQUE(user_id, exchange)
);

-- 3. جدول الإعدادات العامة للبوت (معدل بالكامل V4)
CREATE TABLE IF NOT EXISTS user_settings (
    user_id UUID PRIMARY KEY NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    is_running BOOLEAN NOT NULL DEFAULT false, -- (يتحكم به /bot/start و /bot/stop)
    current_preset_name TEXT DEFAULT 'professional',

    -- [ ⬇️⬇️ الإضافة الجديدة (V4 Paywall + Telegram Link) ⬇️⬇️ ]
    
    -- حالة الاشتراك (يتحكم بها الـ Admin)
    subscription_status TEXT NOT NULL DEFAULT 'trial', -- ( trial | pending_payment | active | expired )
    
    -- تاريخ انتهاء الاشتراك (افتراضي: 7 أيام تجريبية)
    subscription_expires_at TIMESTAMPTZ DEFAULT (NOW() + INTERVAL '7 days'),
    
    -- حقل الربط (الذي اقترحته)
    -- المستخدم سيحصل على هذا من /myid في تليجرام ويلصقه في واجهة الويب
    telegram_chat_id BIGINT UNIQUE, 
    
    -- [ ⬆️⬆️ نهاية الإضافة ⬆️⬆️ ]

    updated_at TIMESTAMPTZ DEFAULT now()
);

-- 4. جدول الاستراتيجيات (كما هو)
CREATE TABLE IF NOT EXISTS strategies (
    id BIGSERIAL PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    strategy_name TEXT NOT NULL, 
    display_name TEXT NOT NULL,
    is_enabled BOOLEAN NOT NULL DEFAULT true,
    parameters JSONB,
    total_signals INT DEFAULT 0,
    successful_signals INT DEFAULT 0,
    last_signal_time TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE(user_id, strategy_name)
);

-- 5. جدول المتغيرات المتقدمة (كما هو)
CREATE TABLE IF NOT EXISTS advanced_variables (
    id BIGSERIAL PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE UNIQUE,
    -- (كل الحقول مثل risk_per_trade, max_drawdown... إلخ كما هي)
    risk_per_trade REAL DEFAULT 2,
    max_drawdown REAL DEFAULT 20,
    stop_loss_percentage REAL DEFAULT 2,
    take_profit_percentage REAL DEFAULT 4,
    risk_reward_ratio REAL DEFAULT 2,
    scanner_period INT DEFAULT 20,
    momentum_threshold REAL DEFAULT 1.5,
    signal_sensitivity TEXT DEFAULT 'medium',
    indicator_integration BOOLEAN DEFAULT true,
    maker_commission REAL DEFAULT 0.02,
    taker_commission REAL DEFAULT 0.04,
    funding_fee REAL DEFAULT 0.03,
    spread_adjustment REAL DEFAULT 0.5,
    trading_start_hour INT DEFAULT 0,
    trading_end_hour INT DEFAULT 23,
    market_sessions JSONB DEFAULT '["Asian", "European", "US"]',
    cooldown_period INT DEFAULT 5,
    max_concurrent_trades INT DEFAULT 3,
    inflation_adjustment BOOLEAN DEFAULT false,
    base_currency TEXT DEFAULT 'USDT',
    currency_protection BOOLEAN DEFAULT true,
    min_trade_amount REAL DEFAULT 10,
    max_trade_amount REAL DEFAULT 1000,
    confidence_threshold REAL DEFAULT 80,
    pattern_sensitivity TEXT DEFAULT 'medium',
    learning_enabled BOOLEAN DEFAULT true,
    data_lookback_period INT DEFAULT 500,
    updated_at TIMESTAMPTZ DEFAULT now()
);

-- 6. جدول الصفقات (كما هو)
CREATE TABLE IF NOT EXISTS trades (
    id BIGSERIAL PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    symbol TEXT NOT NULL,
    status TEXT NOT NULL, -- 'active', 'closing_tp', 'closing_sl', 'closed'
    reason TEXT, 
    entry_price REAL,
    exit_price REAL,
    quantity REAL,
    take_profit REAL,
    stop_loss REAL,
    highest_price REAL, -- (مهم للوقف المتحرك)
    trailing_sl_active BOOLEAN DEFAULT false, -- (مهم للوقف المتحرك)
    last_profit_notification_price REAL, -- (لرسائل الربح المتزايد)
    pnl_usdt REAL,
    order_id TEXT,
    opened_at TIMESTAMPTZ DEFAULT now(),
    closed_at TIMESTAMPTZ
);

-- 7. جدول الإشعارات (كما هو)
CREATE TABLE IF NOT EXISTS notifications (
    id BIGSERIAL PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    timestamp TIMESTAMPTZ DEFAULT now(),
    title TEXT NOT NULL,
    message TEXT NOT NULL,
    type TEXT DEFAULT 'info',
    is_read BOOLEAN DEFAULT false,
    related_trade_id BIGINT REFERENCES trades(id) ON DELETE SET NULL
);

-- 8. [جديد V4] جدول الدفع اليدوي (لتصورك)
-- المستخدم يضع الـ txt_id هنا من واجهة الويب
CREATE TABLE IF NOT EXISTS manual_payments (
    id BIGSERIAL PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    txt_id TEXT NOT NULL UNIQUE, -- معرف المعاملة
    amount_paid REAL,
    subscription_plan TEXT, -- ( '3-months', '1-year' )
    wallet_address_used TEXT, -- (العنوان الذي دفعت له)
    status TEXT DEFAULT 'pending_review', -- ( pending_review | approved | rejected )
    created_at TIMESTAMPTZ DEFAULT now(),
    admin_notes TEXT -- (ملاحظات لك)
);

-- إنشاء فهارس
CREATE INDEX IF NOT EXISTS idx_trades_user_status ON trades (user_id, status);
CREATE INDEX IF NOT EXISTS idx_notifications_user_read ON notifications (user_id, is_read);
CREATE INDEX IF NOT EXISTS idx_payments_user_status ON manual_payments (user_id, status);
CREATE INDEX IF NOT EXISTS idx_user_settings_status ON user_settings (subscription_status, subscription_expires_at);
