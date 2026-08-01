# Architecture Specification

## Core Stack
- **Backend**: FastAPI (async) + SQLAlchemy (async) + Alembic migrations
- **Database**: PostgreSQL 16
- **Cache / Pub‑Sub**: Redis 7
- **Frontend**: React + Vite + TypeScript + TailwindCSS
- **Containerization**: Docker, orchestrated by Kubernetes (stateless pods, HPA)

## Services
- **API Gateway** (FastAPI) – routes all HTTP & WebSocket traffic, JWT auth middleware
- **Auth Service** – registration, email verification, OAuth (Google/GitHub), 2FA, JWT issuance, refresh token store
- **Market Data Service** – Redis‑backed WebSocket client to Binance, normalizes ticker & OHLCV, publishes to `market:*` channels
- **Matching Engine** – in‑process order book simulation, slippage model, immediate mock fills; writes orders/trades to DB
- **Portfolio Service** – virtual cash, positions, P&L calculations; reads market prices from Redis cache
- **Admin Service** – user CRUD, role mgmt, market feed config, audit log viewer
- **API Key Service** – encrypted API key storage, rate‑limit enforcement (Redis token bucket)
- **Background Workers** (FastAPI background tasks) – periodic candle persistence, backup jobs, GDPR deletion

## Database Schema (inline)

- `users: id(UUID PK), email(VARCHAR UNIQUE), password_hash(VARCHAR), is_active(BOOLEAN), created_at(TIMESTAMP), updated_at(TIMESTAMP)`
- `user_roles: user_id(UUID FK), role(VARCHAR), PRIMARY KEY(user_id, role)`
- `email_verifications: id(UUID PK), user_id(UUID FK), token(VARCHAR), expires_at(TIMESTAMP)`
- `refresh_tokens: id(UUID PK), user_id(UUID FK), token(VARCHAR), expires_at(TIMESTAMP), revoked(BOOLEAN)`
- `api_keys: id(UUID PK), user_id(UUID FK), key_encrypted(BYTEA), created_at(TIMESTAMP), expires_at(TIMESTAMP), rate_limit(INT)`
- `audit_logs: id(UUID PK), actor_id(UUID FK), action(VARCHAR), target_id(UUID), details(JSONB), ts(TIMESTAMP)`
- `crypto_pairs: id(SERIAL PK), base(VARCHAR), quote(VARCHAR), symbol(VARCHAR UNIQUE)`
- `candles: id(SERIAL PK), pair_id(INT FK), timeframe(VARCHAR), open_time(TIMESTAMP), open(NUMERIC(28,8)), high(NUMERIC(28,8)), low(NUMERIC(28,8)), close(NUMERIC(28,8)), volume(NUMERIC(28,8))`
- `portfolios: id(UUID PK), user_id(UUID FK), cash_balance(NUMERIC(28,8)), created_at(TIMESTAMP)`
- `positions: id(UUID PK), portfolio_id(UUID FK), pair_id(INT FK), amount(NUMERIC(28,8)), avg_price(NUMERIC(28,8)), created_at(TIMESTAMP)`
- `orders: id(UUID PK), portfolio_id(UUID FK), pair_id(INT FK), side(VARCHAR), type(VARCHAR), price(NUMERIC(28,8) NULL), qty(NUMERIC(28,8)), status(VARCHAR), placed_at(TIMESTAMP), filled_at(TIMESTAMP NULL)`
- `trades: id(UUID PK), order_id(UUID FK), pair_id(INT FK), side(VARCHAR), price(NUMERIC(28,8)), qty(NUMERIC(28,8)), fee(NUMERIC(28,8)), ts(TIMESTAMP)`

## Redis Keys
- `market:ticker:{symbol}` → latest price JSON
- `market:candle:{symbol}:{tf}` → latest candle JSON
- `rate_limit:{api_key}` → token bucket counters
- `pubsub:orders` → new order events for matching engine
- `pubsub:trades` → filled trade events for portfolio updates

## API Endpoints (HTTP)

- `POST /auth/register` — create account, send verification email
- `GET  /auth/verify?token=` — activate account
- `POST /auth/login` — issue JWT & refresh token
- `POST /auth/refresh` — rotate JWT
- `POST /auth/2fa/enable` — register TOTP secret
- `POST /auth/password-reset/request` — send reset link
- `POST /auth/password-reset/confirm` — set new password
- `GET  /profile/me` — current user profile
- `GET  /portfolio` — summary cash & positions
- `GET  /portfolio/positions` — list positions
- `GET  /portfolio/trades` — trade history (filterable)
- `POST /orders` — place market/limit/stop‑limit order
- `GET  /orders/{id}` — order status
- `GET  /market/ticker/{symbol}` — latest price (fallback if WS down)
- `GET  /market/candles/{symbol}` — historical OHLCV (query tf, range)
- `GET  /charts/{symbol}` — config endpoint for frontend chart lib
- `GET  /admin/users` — list users (admin)
- `POST /admin/users` — create user (admin)
- `PATCH /admin/users/{id}` — update (activate/deactivate, roles)
- `DELETE /admin/users/{id}` — soft delete
- `GET  /admin/audit-logs` — view audit entries
- `POST /admin/market-feed` — configure external feed URL
- `POST /api-keys` — generate encrypted API key
- `GET  /api-keys` — list user's keys
- `DELETE /api-keys/{id}` — revoke key
- `POST /gdpr/delete` — request personal data erasure
- `GET  /healthz` — liveness probe
- `GET  /readyz` — readiness probe

## WebSocket Endpoints

- `WS /ws/market/{symbol}` — push live ticker & candle updates
- `WS /ws/notifications` — user‑specific alerts (order fill, admin messages)

## Security Controls
- JWT signed RS256, 15 min access, 7 day refresh stored hashed in DB
- Passwords hashed with bcrypt (cost 12)
- 2FA via TOTP (RFC 6238)
- All DB connections TLS; Redis password protected
- Rate limiting per API key (100 req/min) via Redis token bucket
- OWASP Top 10 mitigations (input validation, CSP, X‑Content‑Type‑Options, etc.)

## Scalability & Reliability
- Stateless FastAPI pods behind Ingress; session data in Redis
- Market Data Service runs multiple replicas, shares Redis cache
- Matching Engine runs as a singleton pod with leader election (optional)
- PostgreSQL primary‑replica, automated PITR backups every 15 min
- Kubernetes CronJob for GDPR deletion job
- Prometheus metrics + Grafana dashboards; alerts on latency >200 ms, WS disconnects, DB replication lag

## Deployment Pipeline
- GitHub → GitHub Actions CI (lint, unit tests, type check)
- Docker multi‑stage build (backend, frontend)
- Helm chart deploys to K8s (namespace per environment)
- Alembic migrations run on pod startup (`alembic upgrade head`)

## Compliance Notes
- No real funds; all balances stored as `NUMERIC(28,8)` simulation values
- GDPR endpoint (`POST /gdpr/delete`) triggers async purge of user rows & related audit logs
- Audit logs immutable (append‑only, encrypted at rest)