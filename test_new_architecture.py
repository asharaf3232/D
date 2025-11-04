# test_new_architecture.py
# هذا الملف يحل محل test_bot_logic.py
# إنه يوضح كيفية اختبار البنية الجديدة المنفصلة

import pytest
import pytest_asyncio
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import pandas as pd
import pandas_ta as ta
from fastapi.testclient import TestClient
from uuid import uuid4

# --- استيراد الوحدات الجديدة ---
# (نفترض أنها في نفس المجلد أو في PYTHONPATH)

# 1. استيراد الخادم لاختبار الـ API
from main import app as fast_api_app

# 2. استيراد المنطق النقي لاختبار الاستراتيجيات
import core_logic

# 3. استيراد العامل لاختبار وظائفه (اختياري ومتقدم)
# import bot_worker

# 4. استيراد أدوات قاعدة البيانات (سنقوم بمحاكاتها 'mocking' في الغالب)
import db_utils
from db_utils import UserSettings


# =======================================================================================
# --- إعدادات الاختبار (Fixtures) ---
# =======================================================================================

@pytest.fixture(scope="module")
def client():
    """يوفر عميل اختبار لخادم FastAPI."""
    with TestClient(fast_api_app) as c:
        yield c

@pytest.fixture
def mock_db_utils(mocker):
    """محاكاة (Mock) كاملة لـ db_utils لمنع أي اتصال حقيقي بقاعدة البيانات."""
    mocker.patch('db_utils.get_user_by_telegram_id', new_callable=AsyncMock)
    mocker.patch('db_utils.get_user_settings', new_callable=AsyncMock)
    mocker.patch('db_utils.update_user_settings', new_callable=AsyncMock)
    mocker.patch('db_utils.get_dashboard_trades_for_user', new_callable=AsyncMock)
    mocker.patch('db_utils.get_trade_details_for_user', new_callable=AsyncMock)
    mocker.patch('db_utils.set_trade_status', new_callable=AsyncMock)
    mocker.patch('db_utils.get_user_api_keys', new_callable=AsyncMock)
    
    # محاكاة لـ Connection Manager في main.py
    mocker.patch('main.ccxt_manager.get_connection', new_callable=AsyncMock)

@pytest.fixture
def sample_user_id():
    """معرف مستخدم وهمي للاختبار."""
    return uuid4()

@pytest.fixture
def sample_settings(sample_user_id):
    """إعدادات مستخدم وهمية للاختبار."""
    # (هذا هيكل مبسط، يجب أن يطابق UserSettings الحقيقي)
    return UserSettings(
        user_id=sample_user_id,
        is_trading_enabled=True,
        active_preset_name="professional",
        real_trade_size_usdt=15.0,
        max_concurrent_trades=5,
        atr_sl_multiplier=2.5,
        risk_reward_ratio=2.0,
        trailing_sl_enabled=True,
        trailing_sl_activation_percent=2.0,
        trailing_sl_callback_percent=1.5,
        top_n_symbols_by_volume=300,
        active_scanners=["momentum_breakout"],
        asset_blacklist=["USDC"],
        market_mood_filter_enabled=True,
        fear_and_greed_threshold=30,
        adx_filter_enabled=True,
        adx_filter_level=25,
        btc_trend_filter_enabled=True,
        news_filter_enabled=True,
        adaptive_intelligence_enabled=True,
        dynamic_trade_sizing_enabled=True,
        strategy_proposal_enabled=True,
        wise_man_auto_close=True,
        wise_guardian_enabled=True
    )

# =======================================================================================
# --- 1. اختبار المنطق النقي (core_logic.py) ---
# =======================================================================================

@pytest.mark.asyncio
async def test_analyze_momentum_breakout():
    """
    اختبار استراتيجية momentum_breakout
    هذا يوضح اختبار "المنطق النقي"
    """
    # 1. إنشاء بيانات وهمية (DataFrame)
    data = {
        'timestamp': pd.to_datetime(pd.date_range(start='1/1/2024', periods=100, freq='15min')),
        'open': [100] * 100,
        'high': [105] * 100,
        'low': [95] * 100,
        'close': [100] * 100,
        'volume': [10] * 100,
        'VWAP_D': [99] * 100
    }
    df = pd.DataFrame(data)
    
    # 2. تعديل آخر سطرين لمحاكاة اختراق
    df.loc[df.index[-3], 'close'] = 100 # Prev
    df.loc[df.index[-2], 'close'] = 105 # Last
    
    # (نحتاج إلى تعديل بيانات MACD و BBands يدويًا لتكون أكثر دقة)
    # (للبساطة، سنفترض أن ta يعمل)
    df.ta.bbands(length=20, append=True)
    df.ta.macd(append=True)
    df.ta.rsi(append=True)
    
    # تعديل القيم المحسوبة لمحاكاة الإشارة
    macd_col = core_logic.find_col(df.columns, "MACD_")
    macds_col = core_logic.find_col(df.columns, "MACDs_")
    bbu_col = core_logic.find_col(df.columns, "BBU_")
    
    df.loc[df.index[-3], macd_col] = 0.5
    df.loc[df.index[-3], macds_col] = 0.6 # MACD below Signal (prev)
    
    df.loc[df.index[-2], macd_col] = 0.7
    df.loc[df.index[-2], macds_col] = 0.6 # MACD above Signal (last)
    df.loc[df.index[-2], bbu_col] = 104   # Close (105) above BBU (104)
    
    # 3. تشغيل الدالة
    result = core_logic.analyze_momentum_breakout(df, {}, 0, 0)
    
    # 4. التحقق من النتيجة
    assert result is not None
    assert result['reason'] == "momentum_breakout"

# =======================================================================================
# --- 2. اختبار خادم الـ API (main.py) ---
# =======================================================================================

@pytest.mark.asyncio
async def test_api_get_active_trades(client, mock_db_utils, sample_user_id):
    """
    اختبار نقطة نهاية (endpoint) الصفقات النشطة.
    هذا يوضح اختبار "الـ API".
    """
    # 1. إعداد المحاكاة (Mock Setup)
    chat_id = 12345
    test_headers = {"X-Telegram-Chat-Id": str(chat_id)}
    
    # إعداد ما ستعيده دوال DB الوهمية
    db_utils.get_user_by_telegram_id.return_value = sample_user_id
    db_utils.get_dashboard_trades_for_user.return_value = [
        {"id": 1, "symbol": "BTC/USDT", "status": "active"},
        {"id": 2, "symbol": "ETH/USDT", "status": "pending"}
    ]
    
    # 2. استدعاء الـ API
    response = client.get("/api/dashboard/active_trades", headers=test_headers)
    
    # 3. التحقق من النتيجة
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    assert data[0]['symbol'] == "BTC/USDT"
    
    # 4. التحقق من أن دوال DB قد تم استدعاؤها
    db_utils.get_user_by_telegram_id.assert_called_once_with(chat_id)
    db_utils.get_dashboard_trades_for_user.assert_called_once_with(sample_user_id)

@pytest.mark.asyncio
async def test_api_toggle_kill_switch(client, mock_db_utils, sample_user_id, sample_settings):
    """اختبار نقطة نهاية مفتاح الإيقاف."""
    # 1. إعداد المحاكاة
    chat_id = 12345
    test_headers = {"X-Telegram-Chat-Id": str(chat_id)}
    
    # لنفترض أن البوت كان يعمل
    sample_settings.is_trading_enabled = True
    db_utils.get_user_by_telegram_id.return_value = sample_user_id
    db_utils.get_user_settings.return_value = sample_settings
    db_utils.update_user_settings.return_value = True
    
    # 2. استدعاء الـ API
    response = client.post("/api/actions/toggle_kill_switch", headers=test_headers)
    
    # 3. التحقق من النتيجة
    assert response.status_code == 200
    assert response.json() == {"new_status": False} # تم عكس الحالة
    
    # 4. التحقق من أن الإعدادات تم تحديثها بالقيمة الجديدة
    db_utils.update_user_settings.assert_called_once_with(sample_user_id, {"is_trading_enabled": False})

@pytest.mark.asyncio
async def test_api_update_settings(client, mock_db_utils, sample_user_id):
    """اختبار نقطة نهاية تحديث الإعدادات."""
    # 1. إعداد المحاكاة
    chat_id = 12345
    test_headers = {"X-Telegram-Chat-Id": str(chat_id)}
    db_utils.get_user_by_telegram_id.return_value = sample_user_id
    db_utils.update_user_settings.return_value = True

    payload = {
        "updates": {
            "real_trade_size_usdt": 50.0,
            "max_concurrent_trades": 10
        }
    }
    
    # 2. استدعاء الـ API
    response = client.post("/api/settings", headers=test_headers, json=payload)
    
    # 3. التحقق من النتيجة
    assert response.status_code == 200
    assert response.json()['message'] == "Settings updated successfully."
    
    # 4. التحقق من أن دالة DB استدعيت بالبيانات الصحيحة
    db_utils.update_user_settings.assert_called_once_with(sample_user_id, payload['updates'])

# =======================================================================================
# --- 3. اختبار العامل (bot_worker.py) ---
# (هذا مثال مبسط، اختبار العامل معقد)
# =======================================================================================

@pytest.mark.asyncio
@patch('bot_worker.get_user_exchange', new_callable=AsyncMock)
@patch('bot_worker.db_utils', new_callable=MagicMock)
@patch('bot_worker.core_logic', new_callable=MagicMock)
@patch('bot_worker.PUBLIC_EXCHANGE', new_callable=AsyncMock)
async def test_worker_scan_for_user_finds_signal(
    mock_public_exchange, 
    mock_core_logic, 
    mock_db_utils, 
    mock_get_user_exchange, 
    sample_settings, 
    sample_user_id
):
    """
    اختبار دالة الفحص لمستخدم واحد.
    هذا يوضح اختبار "العامل".
    """
    from bot_worker import scan_for_user, _execute_buy # استيراد الدالة المحددة

    # 1. إعداد المحاكاة
    # إعدادات المستخدم
    sample_settings.active_scanners = ["momentum_breakout"]
    
    # بيانات السوق
    mock_tickers = {
        "BTC/USDT": {"symbol": "BTC/USDT", "quoteVolume": 2000000, "active": True},
        "ETH/USDT": {"symbol": "ETH/USDT", "quoteVolume": 1500000, "active": True}
    }
    mock_ohlcv = [
        [pd.Timestamp('2024-01-01 12:00').value // 1000, 100, 105, 95, 100, 10]
    ] * 100 # بيانات وهمية
    
    # إعداد دوال DB
    mock_db_utils.get_active_trade_count_for_user.return_value = 0
    mock_db_utils.check_if_symbol_active_for_user.return_value = False
    
    # إعداد دوال CCXT العامة
    mock_public_exchange.fetch_ohlcv.return_value = mock_ohlcv
    
    # إعداد دوال المنطق الأساسي (لإرجاع إشارة)
    mock_core_logic.SCANNERS_MAP.get.return_value = MagicMock(return_value={"reason": "momentum_breakout"})
    
    # إعداد دوال CCXT الخاصة بالمستخدم
    mock_user_exchange = AsyncMock()
    mock_get_user_exchange.return_value = mock_user_exchange
    mock_user_exchange.create_market_buy_order.return_value = {"id": "ORDER-123"}
    
    # إعداد دالة _execute_buy (سنقوم بمحاكاتها لتجنب التعقيد)
    with patch('bot_worker._execute_buy', new_callable=AsyncMock, return_value=True) as mock_execute_buy:
        
        # 2. تشغيل الدالة
        await scan_for_user(sample_settings, mock_tickers)
        
        # 3. التحقق من النتائج
        # التحقق من أننا حاولنا فتح صفقة
        assert mock_execute_buy.call_count == 2 # (مرة لـ BTC ومرة لـ ETH)
        
        # التحقق من أننا استدعينا الماسح
        assert mock_core_logic.SCANNERS_MAP.get.called
        
        # التحقق من أننا طلبنا بيانات OHLCV
        assert mock_public_exchange.fetch_ohlcv.call_count == 2
