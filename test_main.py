import pytest
from fastapi.testclient import TestClient
import httpx
from main import app, _cache

# FastAPI uygulamasını test etmek için senkron client
client = TestClient(app)

# --- MOCK & IZOLASYON YARDIMCILARI ---

class CallTracker:
    """Upstream'e kaç kez istek atıldığını sayar (Cache testi için)."""
    count = 0

def mock_upstream(monkeypatch, status_code=200, json_data=None, raise_json_error=False, exception=None):
    """Gerçek httpx.AsyncClient'i ezip yerine sahte (fake) bir asenkron client koyar."""
    CallTracker.count = 0

    class MockResponse:
        def __init__(self):
            self.status_code = status_code

        def json(self):
            if raise_json_error:
                # main.py json.JSONDecodeError ve ValueError yakalıyor.
                raise ValueError("invalid JSON")
            return json_data

        def raise_for_status(self):
            if self.status_code >= 400:
                req = httpx.Request("GET", "http://fake")
                raise httpx.HTTPStatusError("mock error", request=req, response=self)

    class MockAsyncClient:
        def __init__(self, *args, **kwargs):
            pass
        
        async def __aenter__(self):
            return self
            
        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass
            
        async def get(self, url, params=None, timeout=None):
            CallTracker.count += 1
            if exception:
                raise exception
            return MockResponse()

    # main.py içindeki httpx.AsyncClient çağrısını bizim sahte sınıfımızla değiştiriyoruz
    monkeypatch.setattr(httpx, "AsyncClient", MockAsyncClient)

@pytest.fixture(autouse=True)
def setup_teardown(monkeypatch):
    """HER TESTTEN ÖNCE: Cache'i temizler ve ağı tamamen kapatır."""
    _cache.clear()
    monkeypatch.setenv("FX_UPSTREAM_BASE", "http://127.0.0.1:9") # Kapalı port


# --- 1. SUCCESS / NORMAL CONVERSION ---
def test_success_normal_conversion(monkeypatch):
    fake_data = {"date": "2026-08-28", "rates": {"TRY": 47.1234}}
    mock_upstream(monkeypatch, json_data=fake_data)

    res = client.get("/tools/convert?amount=250&from=EUR&to=TRY&date=2026-08-28")
    
    assert res.status_code == 200
    data = res.json()
    assert data["amount"] == 250.0
    assert data["from"] == "EUR"
    assert data["to"] == "TRY"
    assert data["rate"] == 47.1234
    assert data["result"] == 11780.85
    assert data["rate_date"] == "2026-08-28"
    assert data["asked_date"] == "2026-08-28"
    assert data["source"] == "ECB via frankfurter.dev"

# --- 2. WEEKEND / RATE DATE TRANSPARENCY ---
def test_weekend_rate_date_transparency(monkeypatch):
    fake_data = {"date": "2026-07-31", "rates": {"TRY": 47.1234}}
    mock_upstream(monkeypatch, json_data=fake_data)

    res = client.get("/tools/convert?amount=250&from=EUR&to=TRY&date=2026-08-01")
    
    assert res.status_code == 200
    data = res.json()
    assert data["asked_date"] == "2026-08-01"
    assert data["rate_date"] == "2026-07-31"

# --- 3. VALIDATION TESTLERİ ---
def test_validation_amount_missing():
    res = client.get("/tools/convert?from=EUR&to=TRY&date=2026-08-28")
    assert res.status_code == 400
    assert res.json()["error"] == "validation_error"

def test_validation_amount_zero():
    res = client.get("/tools/convert?amount=0&from=EUR&to=TRY&date=2026-08-28")
    assert res.status_code == 400
    assert res.json()["error"] == "invalid_amount"

def test_validation_amount_negative():
    res = client.get("/tools/convert?amount=-1&from=EUR&to=TRY&date=2026-08-28")
    assert res.status_code == 400
    assert res.json()["error"] == "invalid_amount"

def test_validation_amount_decimals():
    res = client.get("/tools/convert?amount=1.1234567890&from=EUR&to=TRY&date=2026-08-28")
    assert res.status_code == 400
    assert res.json()["error"] == "invalid_amount"

def test_validation_identical_currencies():
    res = client.get("/tools/convert?amount=100&from=EUR&to=EUR&date=2026-08-28")
    assert res.status_code == 400
    assert res.json()["error"] == "identical_currencies"

def test_validation_invalid_currency_format():
    res = client.get("/tools/convert?amount=100&from=123&to=TRY&date=2026-08-28")
    assert res.status_code == 400
    assert res.json()["error"] == "validation_error"

def test_validation_invalid_date_format():
    res = client.get("/tools/convert?amount=100&from=EUR&to=TRY&date=not-a-date")
    assert res.status_code == 400
    assert res.json()["error"] == "validation_error"

def test_validation_future_date():
    res = client.get("/tools/convert?amount=100&from=EUR&to=TRY&date=2099-01-01")
    assert res.status_code == 400
    assert res.json()["error"] == "future_date"

# --- 4. UPSTREAM 4xx ---
def test_upstream_4xx(monkeypatch):
    mock_upstream(monkeypatch, status_code=400)
    res = client.get("/tools/convert?amount=100&from=ZZZ&to=TRY&date=2026-08-28")
    assert res.status_code == 400
    assert res.json()["error"] == "rate_not_available"

# --- 5. UPSTREAM 500 ---
def test_upstream_500(monkeypatch):
    mock_upstream(monkeypatch, status_code=500)
    res = client.get("/tools/convert?amount=100&from=EUR&to=TRY&date=2026-08-28")
    assert res.status_code == 502
    assert res.json()["error"] == "upstream_error"

# --- 6. TIMEOUT ---
def test_upstream_timeout(monkeypatch):
    mock_upstream(monkeypatch, exception=httpx.TimeoutException("timeout"))
    res = client.get("/tools/convert?amount=100&from=EUR&to=TRY&date=2026-08-28")
    assert res.status_code == 504
    assert res.json()["error"] == "upstream_timeout"

# --- 7. NON-JSON ---
def test_upstream_non_json(monkeypatch):
    mock_upstream(monkeypatch, raise_json_error=True)
    res = client.get("/tools/convert?amount=100&from=EUR&to=TRY&date=2026-08-28")
    assert res.status_code == 502
    assert res.json()["error"] == "upstream_invalid_response"

# --- 8. MALFORMED JSON - DATE YOK ---
def test_upstream_malformed_no_date(monkeypatch):
    fake_data = {"rates": {"TRY": 47.1234}}
    mock_upstream(monkeypatch, json_data=fake_data)
    res = client.get("/tools/convert?amount=100&from=EUR&to=TRY&date=2026-08-28")
    assert res.status_code == 502
    assert res.json()["error"] == "upstream_invalid_response"

# --- 9. MALFORMED JSON - RATE YOK ---
def test_upstream_malformed_no_rate(monkeypatch):
    fake_data = {"date": "2026-08-28", "rates": {}}
    mock_upstream(monkeypatch, json_data=fake_data)
    res = client.get("/tools/convert?amount=100&from=EUR&to=TRY&date=2026-08-28")
    assert res.status_code == 404
    assert res.json()["error"] == "rate_not_found"

# --- 10. CACHE TESTİ (KRİTİK) ---
def test_cache_logic(monkeypatch):
    fake_data = {"date": "2026-07-31", "rates": {"TRY": 47.0}}
    mock_upstream(monkeypatch, json_data=fake_data)

    # 1. İstek (Cache Miss -> Upstream'e gidecek)
    res1 = client.get("/tools/convert?amount=100&from=EUR&to=TRY&date=2026-08-01")
    assert res1.status_code == 200
    assert res1.json()["result"] == 4700.0
    assert CallTracker.count == 1  # Upstream tam olarak 1 kere çağrıldı.

    # 2. İstek (Aynı kur, aynı tarih, FARKLI miktar -> Cache Hit)
    res2 = client.get("/tools/convert?amount=200&from=EUR&to=TRY&date=2026-08-01")
    assert res2.status_code == 200
    assert res2.json()["result"] == 9400.0
    assert res2.json()["rate_date"] == "2026-07-31"  # Gerçek rate_date korundu
    
    # KANIT: Upstream 2. kez ÇAĞRILMADI. Sayaç hala 1.
    assert CallTracker.count == 1