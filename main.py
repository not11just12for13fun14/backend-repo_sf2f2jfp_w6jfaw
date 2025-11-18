import os
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
import requests

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ConvertRequest(BaseModel):
    from_currency: str = Field(..., min_length=3, max_length=3)
    to_currency: str = Field(..., min_length=3, max_length=3)
    amount: float = Field(..., ge=0)


@app.get("/")
def read_root():
    return {"message": "Currency Exchange API is running"}


@app.get("/api/hello")
def hello():
    return {"message": "Hello from the backend API!"}


@app.get("/test")
def test_database():
    """Test endpoint to check if database is available and accessible"""
    response = {
        "backend": "✅ Running",
        "database": "❌ Not Available",
        "database_url": None,
        "database_name": None,
        "connection_status": "Not Connected",
        "collections": []
    }

    try:
        from database import db

        if db is not None:
            response["database"] = "✅ Available"
            response["database_url"] = "✅ Configured"
            response["database_name"] = db.name if hasattr(db, 'name') else "✅ Connected"
            response["connection_status"] = "Connected"

            try:
                collections = db.list_collection_names()
                response["collections"] = collections[:10]
                response["database"] = "✅ Connected & Working"
            except Exception as e:
                response["database"] = f"⚠️  Connected but Error: {str(e)[:50]}"
        else:
            response["database"] = "⚠️  Available but not initialized"

    except ImportError:
        response["database"] = "❌ Database module not found (run enable-database first)"
    except Exception as e:
        response["database"] = f"❌ Error: {str(e)[:50]}"

    import os
    response["database_url"] = "✅ Set" if os.getenv("DATABASE_URL") else "❌ Not Set"
    response["database_name"] = "✅ Set" if os.getenv("DATABASE_NAME") else "❌ Not Set"

    return response


# External rates provider (free, no key)
EXTERNAL_RATES_BASE = "https://api.exchangerate.host"


@app.get("/api/rates")
def get_rates(base: str = "USD", symbols: Optional[str] = None) -> Dict[str, Any]:
    """Get latest FX rates for a base currency.

    Query params:
    - base: ISO code like USD, EUR
    - symbols: comma-separated symbols like "EUR,GBP,JPY" (optional)
    """
    base = base.upper()
    params = {"base": base}
    if symbols:
        params["symbols"] = symbols.upper()

    try:
        r = requests.get(f"{EXTERNAL_RATES_BASE}/latest", params=params, timeout=10)
        r.raise_for_status()
        data = r.json()
        if not data.get("success", True):
            raise HTTPException(status_code=502, detail="Failed to fetch rates")
        return {
            "base": data.get("base", base),
            "date": data.get("date"),
            "rates": data.get("rates", {}),
        }
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"Rates provider error: {str(e)}")


@app.post("/api/convert")
def convert_currency(payload: ConvertRequest) -> Dict[str, Any]:
    """Convert amount between currencies and save the transaction if DB is available."""
    from_currency = payload.from_currency.upper()
    to_currency = payload.to_currency.upper()
    amount = payload.amount

    # Fetch conversion rate from external API
    try:
        r = requests.get(
            f"{EXTERNAL_RATES_BASE}/convert",
            params={"from": from_currency, "to": to_currency, "amount": amount},
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()
        if not data.get("success", True):
            raise HTTPException(status_code=502, detail="Failed to convert")
        info = data.get("info", {})
        rate = float(info.get("rate") or 0)
        result = float(data.get("result") or (rate * amount))
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"Conversion provider error: {str(e)}")

    # Build transaction record
    tx = {
        "from_currency": from_currency,
        "to_currency": to_currency,
        "amount": amount,
        "rate": rate,
        "result": result,
    }

    # Try to persist in DB if available
    try:
        from database import create_document
        _id = create_document("exchange", tx)
        tx["id"] = _id
    except Exception:
        # DB not available; proceed without persistence
        pass

    return tx


@app.get("/api/transactions")
def list_transactions(limit: int = 10) -> Dict[str, Any]:
    """Return recent saved exchange transactions if DB is available."""
    try:
        from database import get_documents
        docs = get_documents("exchange", limit=limit)
        # Normalize ObjectId and datetime for JSON response
        from bson import ObjectId
        from datetime import datetime

        def serialize(doc):
            out = {}
            for k, v in doc.items():
                if k == "_id":
                    out["id"] = str(v)
                elif hasattr(v, "isoformat"):
                    out[k] = v.isoformat()
                elif isinstance(v, ObjectId):
                    out[k] = str(v)
                else:
                    out[k] = v
            return out

        return {"items": [serialize(d) for d in docs]}
    except Exception:
        # DB not available
        return {"items": []}


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
