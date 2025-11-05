import pytest
import pytest_asyncio
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import pandas as pd
import pandas_ta as ta
from fastapi.testclient import TestClient
from uuid import uuid4, UUID
from datetime import datetime, timedelta

# --- استيراد الوحدات الجديدة ---

# 1. استيراد الخادم لاختبار الـ API
from main import app as fast_api_app, get_user_from_token, get_active_user

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

# تجاوز المصادقة (الخطوة 1 فقط)
async def override_get_user_from_token() -> UUID:
    """يستبدل دالة جلب التوكن لإرجاع مستخدم وهمي."""
    return SAMPLE_USER_ID

fast_api_app.dependency_overrides[get_user_from_token] = override_get_user_from_token


@pytest.fixture
def mock_db_utils(mocker):
    """محاكاة (Mock) كاملة لـ db_utils لمنع أي اتصال حقيقي بقاعدة البيانات."""
    mocker.patch('db_utils.get_user_settings_by_id', new_callable=AsyncMock)
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
def active_user_settings() -> BotSettings:
    """إعدادات مستخدم "نشط" واشتراكه ساري."""
    return BotSettings(
        user_id=SAMPLE_USER_ID,
        is_running=False,
        current_preset_name="professional",
        subscription_status="active",
        subscription_expires_at=datetime.now(datetime.timezone.utc) + timedelta(days=30),
        telegram_chat_id=12345
    )

@pytest.fixture
def expired_user_settings(active_user_settings) -> BotSettings:
    """إعدادات مستخدم "منتهي" الاشتراك."""
    settings = active_user_settings.copy()
    settings.subscription_status = "expired"
    settings.subscription_expires_at = datetime.now(datetime.timezone.utc) - timedelta(days=1)
    return settings

# =======================================================================================
# --- 1. اختبار المنطق النقي (core_logic.py) ---
# =======================================================================================
# (هذا الاختبار لم يتغير وهو صالح)
@pytest.mark.asyncio
async def test_analyze_momentum_breakout():
    """
    اختبار استراتيجية momentum_breakout
    """
    data = {'timestamp': pd.to_datetime(pd.date_range(start='1/1/2024', periods=100, freq='15min')),
            'open': [100]*100, 'high': [105]*100, 'low': [95]*100,
            'close': [100]*100, 'volume': [10]*100, 'VWAP_D': [99]*100}
    df = pd.DataFrame(data)
    df.loc[df.index[-3], 'close'] = 100; df.loc[df.index[-2], 'close'] = 105
    df.ta.bbands(length=20, append=True); df.ta.macd(append=True); df.ta.rsi(append=True)
    macd_col = core_logic.find_col(df.columns, "MACD_")
    macds_col = core_logic.find_col(df.columns, "MACDs_")
    bbu_col = core_logic.find_col(df.columns, "BBU_")
    df.loc[df.index[-3], macd_col] = 0.5; df.loc[df.index[-3], macds_col] = 0.6
    df.loc[df.index[-2], macd_col] = 0.7; df.loc[df.index[-2], macds_col] = 0.6
    df.loc[df.index[-2], bbu_col] = 104
    
    result = core_logic.analyze_momentum_breakout(df, {}, 0, 0)
    assert result is not None
    assert result['reason'] == "momentum_breakout"

# =======================================================================================
# --- 2. اختبار خادم الـ API (main.py V4) ---
# =======================================================================================

@pytest.mark.asyncio
async def test_api_start_bot_active_user(client, mock_db_utils, active_user_settings):
    """(اختبار القفل V4) - يجب أن ينجح المستخدم النشط."""
    # 1. إعداد المحاكاة
    active_user_settings.is_running = True
    db_utils.get_user_settings_by_id.return_value = active_user_settings
    db_utils.set_bot_status.return_value = active_user_settings
    
    # 2. استدعاء الـ API (سيتم تجاوز get_active_user بنجاح)
    fast_api_app.dependency_overrides[get_active_user] = lambda: SAMPLE_USER_ID
    
    response = client.post("/bot/start", headers={"Authorization": SAMPLE_TOKEN})
    
    # 3. التحقق من النتيجة
    assert response.status_code == 200
    assert response.json()["is_running"] == True
    
    # 4. التحقق من استدعاء DB
    db_utils.set_bot_status.assert_called_once_with(SAMPLE_USER_ID, True)
    
    # تنظيف
    fast_api_app.dependency_overrides = {}
    fast_api_app.dependency_overrides[get_user_from_token] = override_get_user_from_token


@pytest.mark.asyncio
async def test_api_start_bot_expired_user(client, mock_db_utils, expired_user_settings):
    """(اختبار القفل V4) - يجب أن يفشل المستخدم منتهي الاشتراك."""
    # 1. إعداد المحاكاة
    db_utils.get_user_settings_by_id.return_value = expired_user_settings
    
    # 2. استدعاء الـ API (هنا نختبر get_active_user الحقيقي)
    fast_api_app.dependency_overrides = {} # مسح التجاوزات
    fast_api_app.dependency_overrides[get_user_from_token] = override_get_user_from_token
    
    response = client.post("/bot/start", headers={"Authorization": SAMPLE_TOKEN})
    
    # 3. التحقق من النتيجة
    assert response.status_code == 403 # Forbidden
    assert "انتهى اشتراكك" in response.json()["detail"]
    
    # 4. التحقق من أن set_bot_status لم يتم استدعاؤها
    db_utils.set_bot_status.assert_not_called()

@pytest.mark.asyncio
async def test_api_get_status_unsubscribed_user(client, mock_db_utils, expired_user_settings):
    """(اختبار القفل V4) - يجب أن ينجح جلب الحالة (لأنه لا يستخدم get_active_user)."""
    # 1. إعداد المحاكاة
    db_utils.get_user_settings_by_id.return_value = expired_user_settings
    
    # 2. استدعاء الـ API (يستخدم get_user_from_token فقط)
    fast_api_app.dependency_overrides = {}
    fast_api_app.dependency_overrides[get_user_from_token] = override_get_user_from_token
    
    response = client.get("/bot/status", headers={"Authorization": SAMPLE_TOKEN})
    
    # 3. التحقق من النتيجة
    assert response.status_code == 200
    data = response.json()
    assert data["is_running"] == False
    assert data["subscription_status"] == "expired"
    
    # 4. التحقق من استدعاء DB
    db_utils.get_user_settings_by_id.assert_called_once_with(SAMPLE_USER_ID)

@pytest.mark.asyncio
async def test_api_link_telegram_account(client, mock_db_utils):
    """(اختبار V4) - اختبار ربط معرف تليجرام."""
    # 1. إعداد المحاكاة
    db_utils.update_user_telegram_id.return_value = True
    payload = {"telegram_chat_id": 123456789}
    
    # (هذا المسار لا يستخدم "القفل" get_active_user، فقط get_user_from_token)
    fast_api_app.dependency_overrides = {}
    fast_api_app.dependency_overrides[get_user_from_token] = override_get_current_user
    
    # 2. استدعاء الـ API
    response = client.post("/telegram/link-account", headers={"Authorization": SAMPLE_TOKEN}, json=payload)
    
    # 3. التحقق من النتيجة
    assert response.status_code == 200
    assert response.json()["status"] == "success"
    
    # 4. التحقق من استدعاء DB
    db_utils.update_user_telegram_id.assert_called_once_with(SAMPLE_USER_ID, 123456789)
