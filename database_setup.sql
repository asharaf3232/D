-- تفعيل ملحقات UUID إذا لم تكن مفعلة (مهم لـ Supabase)
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- 1. جدول المستخدمين (يرتبط بمستخدمي Supabase Auth أو أي نظام مصادقة)
CREATE TABLE IF NOT EXISTS user_profiles (
    user_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email TEXT UNIQUE NOT NULL,
    telegram_chat_id BIGINT UNIQUE, -- لربط مستخدم تليجرام بحسابه
    created_at TIMESTAMPTZ DEFAULT now()
);

-- 2. جدول مفاتيح API (مشفرة)
-- يخزن مفاتيح المستخدمين بشكل آمن
CREATE TABLE IF NOT EXISTS user_api_keys (
    id BIGSERIAL PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES user_profiles(user_id) ON DELETE CASCADE,
    exchange TEXT NOT NULL DEFAULT 'binance',
    api_key_encrypted TEXT NOT NULL,  -- يجب تشفيرها قبل التخزين
    api_secret_encrypted TEXT NOT NULL, -- يجب تشفيرها قبل التخزين
    is_valid BOOLEAN DEFAULT true,
    UNIQUE(user_id, exchange)
);

-- 3. جدول الإعدادات (بديل ملف settings.json الفردي)
-- كل مستخدم له صف الإعدادات الخاص به
CREATE TABLE IF NOT EXISTS user_settings (
    user_id UUID PRIMARY KEY NOT NULL REFERENCES user_profiles(user_id) ON DELETE CASCADE,
    
    -- مفاتيح التشغيل
    is_trading_enabled BOOLEAN NOT NULL DEFAULT false, -- مفتاح التشغيل/الإيقاف الرئيسي
    active_preset_name TEXT DEFAULT 'مخصص',

    -- إعدادات المخاطر
    real_trade_size_usdt REAL NOT NULL DEFAULT 15.0,
    max_concurrent_trades INT NOT NULL DEFAULT 5,
    atr_sl_multiplier REAL NOT NULL DEFAULT 2.5,
    risk_reward_ratio REAL NOT NULL DEFAULT 2.0,

    -- إعدادات الوقف المتحرك
    trailing_sl_enabled BOOLEAN NOT NULL DEFAULT true,
    trailing_sl_activation_percent REAL NOT NULL DEFAULT 2.0,
    trailing_sl_callback_percent REAL NOT NULL DEFAULT 1.5,
    
    -- إعدادات الماسح والفلاتر
    top_n_symbols_by_volume INT NOT NULL DEFAULT 300,
    worker_threads INT NOT NULL DEFAULT 10, -- (قد يتم تجاهله في بنية العامل الجديدة)
    active_scanners JSONB NOT NULL DEFAULT '["momentum_breakout", "breakout_squeeze_pro"]',
    asset_blacklist JSONB NOT NULL DEFAULT '["USDC", "USDT", "BTC", "ETH"]',
    
    -- فلاتر السوق
    market_mood_filter_enabled BOOLEAN NOT NULL DEFAULT true,
    fear_and_greed_threshold INT NOT NULL DEFAULT 30,
    adx_filter_enabled BOOLEAN NOT NULL DEFAULT true,
    adx_filter_level INT NOT NULL DEFAULT 25,
    btc_trend_filter_enabled BOOLEAN NOT NULL DEFAULT true,
    news_filter_enabled BOOLEAN NOT NULL DEFAULT true,
    
    -- (يمكن إضافة جميع الإعدادات المتقدمة من DEFAULT_SETTINGS هنا)
    
    -- إعدادات الذكاء التكيفي
    adaptive_intelligence_enabled BOOLEAN NOT NULL DEFAULT true,
    dynamic_trade_sizing_enabled BOOLEAN NOT NULL DEFAULT true,
    strategy_proposal_enabled BOOLEAN NOT NULL DEFAULT true,
    wise_man_auto_close BOOLEAN NOT NULL DEFAULT true,
    wise_guardian_enabled BOOLEAN NOT NULL DEFAULT true,

    updated_at TIMESTAMPTZ DEFAULT now()
);

-- 4. جدول الصفقات (الأساسي)
-- تم تعديله ليحتوي على user_id لعزل بيانات كل مستخدم
CREATE TABLE IF NOT EXISTS trades (
    id BIGSERIAL PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES user_profiles(user_id) ON DELETE CASCADE,
    
    -- بيانات الصفقة الأساسية
    timestamp TIMESTAMPTZ DEFAULT now(),
    symbol TEXT NOT NULL,
    entry_price REAL,
    take_profit REAL,
    stop_loss REAL,
    quantity REAL,
    status TEXT NOT NULL, -- 'pending', 'active', 'closing', 'closed (TP)', 'closed (SL)', 'closed (Manual)', 'incubated'
    reason TEXT,
    order_id TEXT,
    
    -- بيانات التتبع والإدارة
    highest_price REAL DEFAULT 0,
    trailing_sl_active BOOLEAN DEFAULT false,
    last_profit_notification_price REAL DEFAULT 0,
    
    -- بيانات الإغلاق
    close_price REAL,
    pnl_usdt REAL,
    
    -- بيانات التحليل
    signal_strength INTEGER DEFAULT 1,
    trade_weight REAL DEFAULT 1.0,
    
    UNIQUE(user_id, symbol, status) -- لمنع فتح صفقتين نشطتين لنفس العملة (اختياري لكن موصى به)
        WHERE status IN ('active', 'pending')
);

-- 5. جدول سجل التحليل (من Smart Engine)
-- منفصل لتخزين بيانات التحليل العميقة
CREATE TABLE IF NOT EXISTS trade_journal (
    id BIGSERIAL PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES user_profiles(user_id) ON DELETE CASCADE,
    trade_id BIGINT NOT NULL REFERENCES trades(id) ON DELETE CASCADE,
    
    -- بيانات الدخول
    entry_strategy TEXT,
    entry_indicators_snapshot JSONB,
    
    -- بيانات الخروج
    exit_reason TEXT,
    exit_quality_score INT,
    post_exit_performance JSONB,
    notes TEXT,

    UNIQUE(user_id, trade_id)
);

-- إنشاء فهارس (Indexes) لتسريع الاستعلامات الشائعة
CREATE INDEX IF NOT EXISTS idx_trades_user_status ON trades (user_id, status);
CREATE INDEX IF NOT EXISTS idx_trades_symbol_status ON trades (symbol, status);
CREATE INDEX IF NOT EXISTS idx_journal_trade_id ON trade_journal (trade_id);
