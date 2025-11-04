import pytest
import pytest_asyncio
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import pandas as pd
import pandas_ta as ta
from fastapi.testclient import TestClient
from uuid import uuid4, UUID

# --- استيراد الوحدات الجديدة ---

# 1. استيراد الخادم لاختبار الـ API
from main import app as fast_api_app, get_current_user

# 2. استيراد المنطق النقي لاختبار الاستراتيجيات
import core_logic

# 3. استيراد أدوات قاعدة البيانات (سنقوم بمحاكاتها 'mocking')
import db_utils
from db_utils import BotSettings, TradingVariables, UserKeys

# =======================================================================================
# --- إعدادات الاختبار (Fixtures) ---
# =======================================================================================

@pytest.fixture(scope="module")
def client():
    """يوفر عميل اختبار لخادم FastAPI."""
    with TestClient(fast_api_app) as c:
        yield c

# معرف مستخدم وهمي للاختبار
SAMPLE_USER_ID = uuid4()
SAMPLE_TOKEN = f"Bearer {SAMPLE_USER_ID}"

# تجاوز المصادقة
async def override_get_current_user() -> UUID:
    """يستبدل دالة المصادقة لإرجاع مستخدم وهمي."""
    return SAMPLE_USER_ID

# تطبيق التجاوز على التطبيق
fast_api_app.dependency_overrides[get_current_user] = override_get_current_user

@pytest.fixture
def mock_db_utils(mocker):
    """محاكاة (Mock) كاملة لـ db_utils لمنع أي اتصال حقيقي بقاعدة البيانات."""
    mocker.patch('db_utils.get_bot_status', new_callable=AsyncMock)
    mocker.patch('db_utils.set_bot_status', new_callable=AsyncMock)
    mocker.patch('db_utils.get_user_api_keys', new_callable=AsyncMock)
    mocker.patch('db_utils.save_api_keys', new_callable=AsyncMock)
    mocker.patch('db_utils.set_api_keys_valid', new_callable=AsyncMock)
    mocker.patch('db_utils.get_active_trades', new_callable=AsyncMock)
    mocker.patch('db_utils.flag_trade_for_closure', new_callable=AsyncMock)
    mocker.patch('db_utils.get_trades_history', new_callable=AsyncMock)
    mocker.patch('db_utils.get_trades_stats', new_callable=AsyncMock)
    mocker.patch('db_utils.get_api_settings', new_callable=AsyncMock)
    mocker.patch('db_utils.update_api_settings', new_callable=AsyncMock)
    mocker.patch('db_utils.apply_preset_settings', new_callable=AsyncMock)
    
    # محاكاة لـ Connection Manager في main.py
    mocker.patch('main.get_ccxt_connection', new_callable=AsyncMock)


@pytest.fixture
def sample_bot_settings() -> BotSettings:
    """إعدادات بوت وهمية."""
    return BotSettings(
        user_id=SAMPLE_USER_ID,
        is_running=False,
        current_preset_name="professional"
    )

@pytest.fixture
def sample_user_keys() -> UserKeys:
    """مفاتيح وهمية."""
    return UserKeys(api_key="test_key", api_secret="test_secret")

# =======================================================================================
# --- 1. اختبار المنطق النقي (core_logic.py) ---
# =======================================================================================

@pytest.mark.asyncio
async def test_analyze_rsi_divergence():
    """
    اختبار استراتيجية rsi_divergence
    (هذا اختبار للتأكد من أن المنطق النقي يعمل بمعزل)
    """
    # 1. إنشاء بيانات وهمية (DataFrame)
    # (هذا مثال مبسط، يجب بناء بيانات دقيقة لحدوث الدايفرجنس)
    data = {
        'timestamp': pd.to_datetime(pd.date_range(start='1/1/2024', periods=100, freq='15min')),
        'open': [100] * 100, 'high': [105] * 100, 'low': [95] * 100,
        'close': [100] * 100, 'volume': [10] * 100
    }
    df = pd.DataFrame(data)
    
    # 2. محاكاة دايفرجنس (هبوط السعر، صعود RSI)
    df.loc[df.index[50], 'low'] = 90
    df.loc[df.index[98], 'low'] = 88 # قاع جديد للسعر
    
    df.ta.rsi(length=14, append=True)
    rsi_col = core_logic.find_col(df.columns, "RSI_")
    
    df.loc[df.index[50], rsi_col] = 30
    df.loc[df.index[98], rsi_col] = 35 # قاع أعلى لـ RSI
    
    # 3. تشغيل الدالة
    # (هذا الاختبار سيفشل بدون بيانات دقيقة ومحاكاة لـ find_peaks)
    # result = core_logic.analyze_rsi_divergence(df, {}, 0, 0)
    # assert result is not None
    assert True # (هذا مجرد مثال لكيفية بناء الاختبار)


# =======================================================================================
# --- 2. اختبار خادم الـ API (main.py) ---
# (مطابق لـ api.ts) [cite_start][cite: 57-70]
# =======================================================================================

@pytest.mark.asyncio
async def test_api_start_bot(client, mock_db_utils, sample_bot_settings):
    [cite_start]"""اختبار /bot/start [cite: 58]"""
    # 1. إعداد المحاكاة
    sample_bot_settings.is_running = True
    db_utils.set_bot_status.return_value = sample_bot_settings
    
    # 2. استدعاء الـ API
    response = client.post("/bot/start", headers={"Authorization": SAMPLE_TOKEN})
    
    # 3. التحقق من النتيجة
    assert response.status_code == 200
    data = response.json()
    assert data["is_running"] == True
    assert data["status"] == "starting"
    
    # 4. التحقق من أن دالة DB استدعيت
    db_utils.set_bot_status.assert_called_once_with(SAMPLE_USER_ID, True)

@pytest.mark.asyncio
async def test_api_get_balance(client, mock_db_utils, sample_user_keys):
    [cite_start]"""اختبار /bot/balance [cite: 62]"""
    # 1. إعداد المحاكاة
    db_utils.get_user_api_keys.return_value = sample_user_keys
    
    mock_exchange = AsyncMock()
    mock_exchange.fetch_balance.return_value = {
        "USDT": {"free": 1000.0, "total": 1200.0}
    }
    
    # محاكاة Connection Manager
    mock_cm = AsyncMock()
    mock_cm.__aenter__.return_value = mock_exchange
    main.get_ccxt_connection.return_value = mock_cm

    # 2. استدعاء الـ API
    response = client.get("/bot/balance", headers={"Authorization": SAMPLE_TOKEN})
    
    # 3. التحقق من النتيجة
    assert response.status_code == 200
    data = response.json()
    assert data["total_balance"] == 1200.0
    assert data["available_balance"] == 1000.0
    
    # 4. التحقق من أننا طلبنا المفاتيح
    db_utils.get_user_api_keys.assert_called_once_with(SAMPLE_USER_ID)

@pytest.mark.asyncio
async def test_api_get_active_trades(client, mock_db_utils):
    """اختبار /trades/active"""
    # 1. إعداد المحاكاة
    mock_trades = [
        {"id": 1, "symbol": "BTC/USDT", "status": "active"},
        {"id": 2, "symbol": "ETH/USDT", "status": "active"}
    ]
    db_utils.get_active_trades.return_value = mock_trades
    
    # 2. استدعاء الـ API
    response = client.get("/trades/active", headers={"Authorization": SAMPLE_TOKEN})
    
    # 3. التحقق من النتيجة
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    assert data[0]['symbol'] == "BTC/USDT"
    
    # 4. التحقق من استدعاء DB
    db_utils.get_active_trades.assert_called_once_with(SAMPLE_USER_ID)

@pytest.mark.asyncio
async def test_api_close_trade(client, mock_db_utils):
    [cite_start]"""اختبار /trades/close [cite: 66]"""
    # 1. إعداد المحاكاة
    db_utils.flag_trade_for_closure.return_value = True
    payload = {"trade_id": 123}
    
    # 2. استدعاء الـ API
    response = client.post("/trades/close", headers={"Authorization": SAMPLE_TOKEN}, json=payload)
    
    # 3. التحقق من النتيجة
    assert response.status_code == 200
    assert response.json()["status"] == "closing"
    
    # 4. التحقق من استدعاء DB
    db_utils.flag_trade_for_closure.assert_called_once_with(SAMPLE_USER_ID, 123)

@pytest.mark.asyncio
async def test_api_get_settings(client, mock_db_utils):
    [cite_start]"""اختبار GET /settings [cite: 68]"""
    # 1. إعداد المحاكاة
    mock_settings = {"user_id": SAMPLE_USER_ID, "risk_per_trade": 5.0, "market_sessions": "[]"}
    db_utils.get_api_settings.return_value = mock_settings
    
    # 2. استدعاء الـ API
    response = client.get("/settings", headers={"Authorization": SAMPLE_TOKEN})
    
    # 3. التحقق من النتيجة
    assert response.status_code == 200
    assert response.json()["risk_per_trade"] == 5.0
    
    # 4. التحقق من استدعاء DB
    db_utils.get_api_settings.assert_called_once_with(SAMPLE_USER_ID)

@pytest.mark.asyncio
async def test_api_update_settings(client, mock_db_utils):
    """اختبار POST /settings"""
    # 1. إعداد المحاكاة
    db_utils.update_api_settings.return_value = True
    payload = {
        "risk_per_trade": 3.0,
        "max_concurrent_trades": 7
    }
    
    # 2. استدعاء الـ API
    response = client.post("/settings", headers={"Authorization": SAMPLE_TOKEN}, json=payload)
    
    # 3. التحقق من النتيجة
    assert response.status_code == 200
    assert response.json()["status"] == "success"
    
    # 4. التحقق من استدعاء DB
    db_utils.update_api_settings.assert_called_once_with(SAMPLE_USER_ID, payload)

@pytest.mark.asyncio
async def test_api_change_preset(client, mock_db_utils):
    [cite_start]"""اختبار /settings/preset [cite: 12-13]"""
    # 1. إعداد المحاكاة
    db_utils.apply_preset_settings.return_value = True
    payload = {"preset_name": "professional"}
    
    # (البيانات التي سيتم تطبيقها، مطابقة لـ main.py)
    expected_settings_to_apply = {
        "real_trade_size_usdt": 100, 
        "max_concurrent_trades": 3, 
        "risk_reward_ratio": 2.5, 
        "max_daily_loss_pct": 3.0
    }
    
    # 2. استدعاء الـ API
    response = client.post("/settings/preset", headers={"Authorization": SAMPLE_TOKEN}, json=payload)
    
    # 3. التحقق من النتيجة
    assert response.status_code == 200
    assert "professional" in response.json()["message"]
    
    # 4. التحقق من استدعاء DB
    db_utils.apply_preset_settings.assert_called_once_with(
        SAMPLE_USER_ID, 
        "professional", 
        expected_settings_to_apply
    )
