import uuid
import hashlib
import time
from datetime import datetime, timedelta
from typing import List, Optional

import jwt  # PyJWT
from fastapi import FastAPI, HTTPException, Depends, Header, Query, Response
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, EmailStr, Field

app = FastAPI(title="Minimal Portfolio API")

# ---------------------------------------------------------------------------
# In‑memory stores
# ---------------------------------------------------------------------------
_USERS = {}                     # email -> {id, email, hashed_pw, created_at}
_REFRESH_TOKENS = {}            # refresh_token -> user_id
_PORTFOLIO = {}                 # user_id -> list of holdings (static for demo)

# ---------------------------------------------------------------------------
# Settings / secrets (hard‑coded for this demo)
# ---------------------------------------------------------------------------
_JWT_SECRET = "super-secret-key-please-change"
_JWT_ALGORITHM = "HS256"
_ACCESS_TOKEN_EXPIRE_SECONDS = 900  # 15 minutes

# ---------------------------------------------------------------------------
# Pydantic models matching the contract exactly
# ---------------------------------------------------------------------------

class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=8)


class RegisterResponse(BaseModel):
    id: str = Field(..., format="uuid")
    email: EmailStr
    created_at: str = Field(..., format="date-time")
    detail: Optional[str] = None


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=8)


class LoginResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = Field(default="bearer", const=True)
    expires_in: int


class Holding(BaseModel):
    asset_id: str = Field(..., format="uuid")
    symbol: str
    quantity: float
    avg_cost_usd: float
    market_price_usd: float
    unrealized_pl_usd: float


class PortfolioSummaryResponse(BaseModel):
    total_value_usd: float
    updated_at: str = Field(..., format="date-time")
    holdings: List[Holding]


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------

def _hash_password(password: str) -> str:
    """Simple SHA256 hash – replace with bcrypt/argon2 in real code."""
    return hashlib.sha256(password.encode()).hexdigest()


def _create_access_token(user_id: str) -> str:
    expire = datetime.utcnow() + timedelta(seconds=_ACCESS_TOKEN_EXPIRE_SECONDS)
    payload = {"sub": user_id, "exp": expire}
    return jwt.encode(payload, _JWT_SECRET, algorithm=_JWT_ALGORITHM)


def _create_refresh_token(user_id: str) -> str:
    token = str(uuid.uuid4())
    _REFRESH_TOKENS[token] = user_id
    return token


def _decode_access_token(token: str) -> str:
    try:
        payload = jwt.decode(token, _JWT_SECRET, algorithms=[_JWT_ALGORITHM])
        return payload["sub"]
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Access token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid access token")


async def get_current_user(authorization: str = Header(...)):
    """FastAPI dependency that extracts and validates the Bearer token."""
    scheme, _, param = authorization.partition(" ")
    if scheme.lower() != "bearer" or not param:
        raise HTTPException(status_code=401, detail="Invalid authorization header")
    user_id = _decode_access_token(param)
    # Find user by id
    for user in _USERS.values():
        if user["id"] == user_id:
            return user
    raise HTTPException(status_code=401, detail="User not found")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.post("/api/v1/auth/register", response_model=RegisterResponse)
def register(payload: RegisterRequest):
    if payload.email in _USERS:
        raise HTTPException(status_code=400, detail="Email already registered")
    user_id = str(uuid.uuid4())
    created_at = datetime.utcnow().isoformat() + "Z"
    _USERS[payload.email] = {
        "id": user_id,
        "email": payload.email,
        "hashed_pw": _hash_password(payload.password),
        "created_at": created_at,
    }
    # Initialise empty portfolio for the new user
    _PORTFOLIO[user_id] = []
    return RegisterResponse(
        id=user_id,
        email=payload.email,
        created_at=created_at,
        detail="Registration accepted, verification email sent."
    )


@app.post("/api/v1/auth/login", response_model=LoginResponse)
def login(payload: LoginRequest):
    user = _USERS.get(payload.email)
    if not user or user["hashed_pw"] != _hash_password(payload.password):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    access_token = _create_access_token(user["id"])
    refresh_token = _create_refresh_token(user["id"])
    return LoginResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="bearer",
        expires_in=_ACCESS_TOKEN_EXPIRE_SECONDS,
    )


@app.get("/api/v1/portfolio/summary", response_model=PortfolioSummaryResponse)
def portfolio_summary(
    format: str = Query("json", enum=["json", "csv"]),
    current_user: dict = Depends(get_current_user),
):
    user_id = current_user["id"]
    holdings = _PORTFOLIO.get(user_id, [])

    # If no holdings, return an empty list with zero total value
    total_value = sum(h["quantity"] * h["market_price_usd"] for h in holdings)

    response_data = {
        "total_value_usd": total_value,
        "updated_at": datetime.utcnow().isoformat() + "Z",
        "holdings": holdings,
    }

    if format == "csv":
        # Stream CSV
        header = "asset_id,symbol,quantity,avg_cost_usd,market_price_usd,unrealized_pl_usd\n"
        rows = [
            f'{h["asset_id"]},{h["symbol"]},{h["quantity"]},{h["avg_cost_usd"]},'
            f'{h["market_price_usd"]},{h["unrealized_pl_usd"]}\n'
            for h in holdings
        ]
        csv_content = header + "".join(rows)
        return StreamingResponse(
            iter([csv_content]),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=portfolio.csv"},
        )
    else:
        return JSONResponse(content=response_data)


@app.get("/health")
def health():
    return {"status": "ok"}


# Also expose the contract‑named health endpoint
@app.get("/api/v1/health")
def health_v1():
    return {"status": "ok"}