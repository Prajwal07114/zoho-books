# Ledgerly — Multi-Tenant SaaS Accounting Application

**Ledgerly** is a high-integrity, multi-tenant SaaS accounting application designed for small and growing businesses, functionally comparable to Zoho Books. It is engineered with a strict double-entry ledger, transaction-local Row-Level Security (RLS), immutable posted journals, and snapshot-preserved invoicing.

---

## 1. Phase 0: Repository Bootstrap and RLS Validation

This repository is currently at **Phase 0**. It establishes the core project structure, FastAPI application skeleton, database migration pipeline via Alembic, and an empirical PostgreSQL Row-Level Security (RLS) validation spike.

### Technology Stack
- **Runtime:** Python 3.11+
- **Framework:** FastAPI (ASGI)
- **Database Engine:** SQLAlchemy 2.0 (Async) + `asyncpg`
- **Validation:** Pydantic v2 + Pydantic Settings
- **Database:** PostgreSQL 15+ (local or Docker Compose)
- **Migrations:** Alembic (Async)
- **Testing:** Pytest, pytest-asyncio, HTTPX

---

## 2. Directory Structure

```
.
├── 01_PRD (1).md                  # Source Product Requirements Document (Draft v0.1)
├── 02_Workflows (1).md            # Source Workflow Specifications (Draft v0.1)
├── 03_Architecture (1).md         # Source Architecture Design (Draft v0.1)
├── 04_Database_Design (1).md      # Source Database Design Specification (Draft v0.1)
├── docs/
│   └── IMPLEMENTATION_READINESS_REPORT.md  # Comprehensive Pre-Implementation Readiness Report
├── backend/
│   ├── alembic/                   # Database migration environment
│   │   ├── versions/
│   │   │   └── 0001_initial_bootstrap.py  # Schemas (identity, app, audit, platform) & RLS helpers
│   │   ├── env.py
│   │   └── script.py.mako
│   ├── app/
│   │   ├── api/
│   │   │   ├── endpoints/
│   │   │   │   └── health.py      # Liveness and Database connectivity checks
│   │   │   └── router.py          # Central API router
│   │   ├── core/
│   │   │   ├── config.py          # Pydantic BaseSettings loading from .env
│   │   │   └── database.py        # AsyncEngine, AsyncSession, and tenant context helpers
│   │   └── main.py                # FastAPI application entrypoint with CORS and lifespan
│   ├── tests/
│   │   ├── spike/
│   │   │   └── test_rls_spike.py  # Standalone PostgreSQL RLS validation test suite (9 tests)
│   │   ├── conftest.py            # Async test fixtures and database engines
│   │   └── test_health.py         # Health endpoint tests
│   ├── alembic.ini
│   ├── Dockerfile
│   ├── pyproject.toml
│   └── requirements.txt
├── frontend/
│   └── README.md                  # Frontend scaffold placeholder (React + Vite + TypeScript)
├── .env.example                   # Environment configuration template
├── docker-compose.yml             # Local Docker services (PostgreSQL 16 + Backend)
├── .gitignore
└── README.md
```

---

## 3. Local Development Setup

### Prerequisites
1. **Python 3.11+** installed (`py --version` or `python3 --version`).
2. **PostgreSQL 15+** running locally on port 5432 (or launched via `docker compose up -d postgres`).

### Step-by-Step Setup

#### 1. Clone & Initialize Environment
```bash
# In the workspace root
py -3.11 -m venv backend/.venv

# Activate virtual environment (Windows PowerShell)
.\backend\.venv\Scripts\Activate.ps1
# (Linux/macOS)
# source backend/.venv/bin/activate

# Install dependencies
pip install -r backend/requirements.txt
```

#### 2. Configure Environment Variables
Copy `.env.example` to `backend/.env`:
```bash
cp .env.example backend/.env
```
Ensure your database credentials in `backend/.env` match your local PostgreSQL server:
- `ADMIN_DATABASE_URL`: Connection URL with permissions to execute DDL (e.g., `postgres` superuser).
- `DATABASE_URL`: Connection URL for application runtime (`ledgerly_app` role with `NOBYPASSRLS`).

#### 3. Database Initialization
Create the development and test databases and the non-superuser application role in PostgreSQL:
```sql
CREATE DATABASE ledgerly_dev;
CREATE DATABASE ledgerly_test;

CREATE ROLE ledgerly_app WITH LOGIN PASSWORD 'ledgerly_secret' NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
GRANT CONNECT ON DATABASE ledgerly_dev TO ledgerly_app;
GRANT CONNECT ON DATABASE ledgerly_test TO ledgerly_app;
```

#### 4. Run Migrations
Apply the initial bootstrap migration (creates schemas `identity`, `app`, `audit`, `platform` and RLS helper functions):
```bash
cd backend
alembic upgrade head
```

#### 5. Start the Development Server
```bash
cd backend
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```
- API Base: `http://127.0.0.1:8000/api/v1`
- Swagger UI Documentation: `http://127.0.0.1:8000/docs`
- Health Endpoint: `http://127.0.0.1:8000/health`
- Database Readiness Endpoint: `http://127.0.0.1:8000/health/db`

---

## 4. Standalone RLS Validation Spike

The RLS spike verifies that multi-tenant isolation is enforced at the database kernel level, preventing any cross-tenant data exposure even if an application bug omits a `WHERE organization_id = :org_id` clause.

### Architectural Invariants Proven:
1. **Multi-Tenant Partitioning:** Data created in Organization A's transaction context is completely invisible to Organization B.
2. **Fail-Closed Behavior:** Queries executed without tenant context (`current_setting('app.current_org_id', true)` is empty/null) return **0 rows** on reads and **raise an RLS policy violation** on writes.
3. **Cross-Tenant Attack Resistance:**
   - Attempting to query another tenant's record by UUID returns 0 rows (looks like 404 Not Found).
   - Attempting to update or delete another tenant's record affects 0 rows.
   - Attempting to insert a row with another tenant's `organization_id` fails with an RLS policy check violation.
4. **Connection Pool Safety:**
   - Setting context via transaction-local `set_config('app.current_org_id', :org_id, true)` (`is_local=true`) resets cleanly on `COMMIT`, `ROLLBACK`, or `EXCEPTION`.
   - Subsequent transactions reusing the exact same connection start with zero context and cannot read any tenant's data.
5. **Concurrent Pool Contention:** Concurrent asynchronous worker tasks contending for connections in a small pool process isolated queries without cross-talk or leakage.

### Running the RLS & Health Test Suite
From the `backend/` directory:
```bash
.\.venv\Scripts\pytest.exe -v
```

### Verified Test Results (Executed on PostgreSQL 16.14):
```
tests/spike/test_rls_spike.py::test_rls_multi_tenant_isolation PASSED            [  8%]
tests/spike/test_rls_spike.py::test_rls_fails_closed_without_context PASSED     [ 16%]
tests/spike/test_rls_spike.py::test_rls_cross_tenant_read_prevented PASSED     [ 25%]
tests/spike/test_rls_spike.py::test_rls_cross_tenant_update_and_delete_prevented PASSED [ 33%]
tests/spike/test_rls_spike.py::test_rls_cross_tenant_forged_insert_rejected PASSED [ 41%]
tests/spike/test_rls_spike.py::test_rls_connection_reuse_after_commit PASSED     [ 50%]
tests/spike/test_rls_spike.py::test_rls_connection_reuse_after_rollback PASSED   [ 58%]
tests/spike/test_rls_spike.py::test_rls_connection_reuse_after_exception PASSED  [ 66%]
tests/spike/test_rls_spike.py::test_rls_concurrent_pool_contention PASSED       [ 75%]
tests/test_health.py::test_health_check_endpoint PASSED                          [ 83%]
tests/test_health.py::test_api_v1_health_check_endpoint PASSED                   [ 91%]
tests/test_health.py::test_database_health_check_endpoint PASSED                 [100%]

============================= 12 passed in 1.22s ==============================
```

---

## 5. Connection Pooling & PgBouncer Assumptions

- **Transaction-Local Setting:** All tenant context binding is performed using:
  ```sql
  SELECT set_config('app.current_org_id', :org_id, true);
  ```
  The third parameter (`is_local = true`) is critical. In PostgreSQL, this sets the parameter for the duration of the current transaction only.
- **PgBouncer Transaction Pooling Mode:** In production with PgBouncer operating in `pool_mode = transaction`, connection assignments to clients change on every transaction boundary. Because `set_config(..., true)` is automatically cleared by the PostgreSQL server upon transaction completion, no session state persists on the underlying server connection, making this design 100% safe for PgBouncer transaction pooling.
- **Application Role Safeguard:** The application connects under `ledgerly_app`, which has `NOBYPASSRLS`. Tables in schema `app` have `FORCE ROW LEVEL SECURITY`, preventing policy bypass even if the table owner queries them.
