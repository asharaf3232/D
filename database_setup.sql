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
    passphrase_encrypted TEXT, -- لـ OKX أو غيرها
    is_valid BOOLEAN DEFAULT false,
    created_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE(user_id, exchange)
);

-- 3. جدول الإعدادات العامة للبوت (الذي تتوقعه دالة botAPI.getBotSettings)
-- هذا الجدول سيقوم main.py بتحديثه
CREATE TABLE IF NOT EXISTS user_settings (
    user_id UUID PRIMARY KEY NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    is_running BOOLEAN NOT NULL DEFAULT false, -- (يتحكم به /bot/start و /bot/stop)
    current_preset_name TEXT DEFAULT 'professional',
    -- (يمكن إضافة أي إعدادات عامة أخرى هنا)
    updated_at TIMESTAMPTZ DEFAULT now()
);

-- 4. جدول الاستراتيجيات (الذي تتوقعه صفحة Strategies.tsx )
-- هذا الجدول تتحدث معه الواجهة مباشرة عبر Supabase
CREATE TABLE IF NOT EXISTS strategies (
    id BIGSERIAL PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    strategy_name TEXT NOT NULL, -- (مثل 'momentum_breakout')
    display_name TEXT NOT NULL,  -- (مثل 'ماسح الاختراقات')
    is_enabled BOOLEAN NOT NULL DEFAULT true,
    parameters JSONB,
    -- بيانات الأداء التي يمكن أن يحدّثها العامل
    total_signals INT DEFAULT 0,
    successful_signals INT DEFAULT 0,
    last_signal_time TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE(user_id, strategy_name)
);

-- 5. جدول المتغيرات المتقدمة (الذي تتوقعه صفحة AdvancedVariables.tsx )
-- هذا الجدول أيضاً تتحدث معه الواجهة مباشرة عبر Supabase
CREATE TABLE IF NOT EXISTS advanced_variables (
    id BIGSERIAL PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE UNIQUE,
    
    -- إدارة المخاطر
    risk_per_trade REAL DEFAULT 2,
    max_drawdown REAL DEFAULT 20,
    stop_loss_percentage REAL DEFAULT 2,
    take_profit_percentage REAL DEFAULT 4,
    risk_reward_ratio REAL DEFAULT 2,
    
    -- إعدادات الماسحات
    scanner_period INT DEFAULT 20,
    momentum_threshold REAL DEFAULT 1.5,
    signal_sensitivity TEXT DEFAULT 'medium',
    indicator_integration BOOLEAN DEFAULT true,
    
    -- المحاسبة والرسوم
    maker_commission REAL DEFAULT 0.02,
    taker_commission REAL DEFAULT 0.04,
    funding_fee REAL DEFAULT 0.03,
    spread_adjustment REAL DEFAULT 0.5,
    
    -- إعدادات التوقيت
    trading_start_hour INT DEFAULT 0,
    trading_end_hour INT DEFAULT 23,
    market_sessions JSONB DEFAULT '["Asian", "European", "US"]',
    cooldown_period INT DEFAULT 5,
    max_concurrent_trades INT DEFAULT 3,
    
    -- التضخم والعملات
    inflation_adjustment BOOLEAN DEFAULT false,
    base_currency TEXT DEFAULT 'USDT',
    currency_protection BOOLEAN DEFAULT true,
    min_trade_amount REAL DEFAULT 10,
    max_trade_amount REAL DEFAULT 1000,
    
    -- الذكاء الاصطناعي
    confidence_threshold REAL DEFAULT 80,
    pattern_sensitivity TEXT DEFAULT 'medium',
    learning_enabled BOOLEAN DEFAULT true,
    data_lookback_period INT DEFAULT 500,

    updated_at TIMESTAMPTZ DEFAULT now()
);

-- 6. جدول الصفقات (الذي يتوقعه الخادم Bot Worker)
CREATE TABLE IF NOT EXISTS trades (
    id BIGSERIAL PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    symbol TEXT NOT NULL,
    status TEXT NOT NULL, -- 'pending', 'active', 'closed'
    reason TEXT, -- (اسم الاستراتيجية)
    entry_price REAL,
    exit_price REAL,
    quantity REAL,
    take_profit REAL,
    stop_loss REAL,
    pnl_usdt REAL,
    order_id TEXT,
    opened_at TIMESTAMPTZ DEFAULT now(),
    closed_at TIMESTAMPTZ
);

-- 7. جدول الإشعارات (الذي تتوقعه دالة getNotifications [cite: 13])
CREATE TABLE IF NOT EXISTS notifications (
    id BIGSERIAL PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    timestamp TIMESTAMPTZ DEFAULT now(),
    title TEXT NOT NULL,
    message TEXT NOT NULL,
    type TEXT DEFAULT 'info', -- 'info', 'success', 'warning', 'error'
    is_read BOOLEAN DEFAULT false,
    related_trade_id BIGINT REFERENCES trades(id) ON DELETE SET NULL
);

-- إنشاء فهارس (Indexes) لتسريع الاستعلامات
CREATE INDEX IF NOT EXISTS idx_trades_user_status ON trades (user_id, status);
CREATE INDEX IF NOT EXISTS idx_notifications_user_read ON notifications (user_id, is_read);
