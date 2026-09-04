import os
import json
import httpx
from datetime import date
from decimal import Decimal
from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

app = FastAPI(title="mangolab-fx-tool")

# Basit in-memory cache sözlüğü
_cache = {}

# 1. ÖZEL HATA YÖNETİMİ
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    errors = exc.errors()
    error_msg = f"Parameter error on '{errors[0]['loc'][-1]}': {errors[0]['msg']}" if errors else "Invalid parameters"
    return JSONResponse(
        status_code=400,
        content={"error": "validation_error", "message": error_msg}
    )

class FXException(Exception):
    def __init__(self, error_code: str, message: str, status_code: int = 400):
        self.error_code = error_code
        self.message = message
        self.status_code = status_code

@app.exception_handler(FXException)
async def fx_exception_handler(request: Request, exc: FXException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.error_code, "message": exc.message}
    )

# 2. ENDPOINT SÖZLEŞMESİ
@app.get("/tools/convert")
async def convert(
    amount: Decimal = Query(..., description="Amount to convert"),
    from_currency: str = Query(..., alias="from", pattern="^[A-Za-z]{3}$"),
    to_currency: str = Query(..., alias="to", pattern="^[A-Za-z]{3}$"),
    target_date: date = Query(..., alias="date")
):
    # --- GİRDİ KONTROLLERİ ---
    if amount <= 0:
        raise FXException("invalid_amount", "Amount must be strictly greater than zero.")
    if abs(amount.as_tuple().exponent) >= 10:
        raise FXException("invalid_amount", "Amount cannot have 10 or more decimal places.")
        
    base = from_currency.upper()
    target = to_currency.upper()
    if base == target:
        raise FXException("identical_currencies", "Source and target currencies must be different.")
    if target_date > date.today():
        raise FXException("future_date", "Cannot convert currencies for a future date.")

    # --- CACHE (ÖNBELLEK) KONTROLÜ ---
    cache_key = f"{base}-{target}-{target_date}"
    
    if cache_key in _cache:
        cached_data = _cache[cache_key]
        rate = cached_data["rate"]
        rate_date = cached_data["rate_date"]
        result = round(float(amount) * rate, 2)
        
        return {
            "amount": float(amount),
            "from": base,
            "to": target,
            "rate": rate,
            "result": result,
            "rate_date": rate_date,
            "asked_date": str(target_date),
            "source": "ECB via frankfurter.dev"
        }

    # --- DIŞ SERVİS (UPSTREAM) ÇAĞRISI (CACHE MISS) ---
    upstream_base = os.getenv("FX_UPSTREAM_BASE", "https://api.frankfurter.dev")
    url = f"{upstream_base}/v1/{target_date}"
    
    try:
        async with httpx.AsyncClient(follow_redirects=True) as client:
            response = await client.get(url, params={"base": base, "symbols": target}, timeout=5.0)
            response.raise_for_status() 
            data = response.json()
            
    except httpx.TimeoutException:
        raise FXException("upstream_timeout", "The upstream API timed out.", 504)
    except httpx.HTTPStatusError as e:
        if 400 <= e.response.status_code < 500:
            raise FXException("rate_not_available", "No exchange rate is available for the requested date and currency pair.", 400)
        else:
            raise FXException("upstream_error", f"The upstream API failed with status {e.response.status_code}.", 502)
    except (json.JSONDecodeError, ValueError):
        raise FXException("upstream_invalid_response", "Upstream returned invalid data (non-JSON).", 502)
    except httpx.RequestError as e:
        raise FXException("upstream_unreachable", "Failed to connect to upstream API.", 502)

    # --- JSON YAPI (STRUCTURE) KONTROLLERİ ---
    if not isinstance(data, dict):
        raise FXException("upstream_invalid_response", "Upstream returned an unexpected structure.", 502)
    if "rates" not in data or target not in data["rates"]:
        raise FXException("rate_not_found", f"Rate for {target} not found in upstream response.", 404)
    if "date" not in data:
        raise FXException("upstream_invalid_response", "Upstream response is missing the 'date' field.", 502)

    # --- BAŞARILI YANITI CACHE'E YAZMA VE TESLİMAT ---
    rate = data["rates"][target]
    actual_rate_date = data["date"]
    
    _cache[cache_key] = {
        "rate": rate,
        "rate_date": actual_rate_date
    }
    
    result = round(float(amount) * rate, 2)

    return {
        "amount": float(amount),
        "from": base,
        "to": target,
        "rate": rate,
        "result": result,
        "rate_date": actual_rate_date,
        "asked_date": str(target_date),
        "source": "ECB via frankfurter.dev"
    }