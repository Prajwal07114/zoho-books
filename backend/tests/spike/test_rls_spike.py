"""PostgreSQL Row-Level Security (RLS) Validation Spike.

This module validates that transaction-local PostgreSQL RLS:
1. Enforces strict multi-tenant isolation.
2. Fails closed when tenant context is missing.
3. Prevents cross-tenant read, update, delete, and insert operations.
4. Leaves no tenant context leaks across connection pool reuse (after commit, rollback, and exceptions).
5. Functions correctly under concurrent load without session contamination.
"""

import asyncio
import uuid
from typing import AsyncGenerator
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import settings
from app.core.database import set_tenant_context

# ---------------------------------------------------------------------------
# Spike Setup Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="module")
async def setup_spike_tables():
    """Create isolated spike table with FORCE ROW LEVEL SECURITY via admin role."""
    admin_engine: AsyncEngine = create_async_engine(
        settings.ADMIN_TEST_DATABASE_URL,
        pool_pre_ping=True,
    )

    async with admin_engine.begin() as conn:
        # Create dedicated spike table in schema 'app'
        await conn.execute(
            text("""
            CREATE TABLE IF NOT EXISTS app.spike_tenant_records (
                organization_id uuid NOT NULL,
                id uuid NOT NULL DEFAULT gen_random_uuid(),
                name text NOT NULL,
                amount numeric(19, 4) NOT NULL DEFAULT 0,
                created_at timestamptz NOT NULL DEFAULT now(),
                PRIMARY KEY (organization_id, id)
            )
        """)
        )

        # Force RLS so even table owners/privileged callers adhere to policies
        await conn.execute(text("ALTER TABLE app.spike_tenant_records ENABLE ROW LEVEL SECURITY"))
        await conn.execute(text("ALTER TABLE app.spike_tenant_records FORCE ROW LEVEL SECURITY"))

        # Drop and re-create strict policy for runtime application role
        await conn.execute(text("DROP POLICY IF EXISTS spike_tenant_policy ON app.spike_tenant_records"))
        await conn.execute(
            text("""
            CREATE POLICY spike_tenant_policy ON app.spike_tenant_records
                AS PERMISSIVE
                FOR ALL
                TO ledgerly_app
                USING (organization_id = app.current_org_id())
                WITH CHECK (organization_id = app.current_org_id())
        """)
        )

        # Grant DML permissions to runtime non-superuser role
        await conn.execute(text("GRANT SELECT, INSERT, UPDATE, DELETE ON app.spike_tenant_records TO ledgerly_app"))

    await admin_engine.dispose()
    yield

    # Teardown
    admin_engine = create_async_engine(settings.ADMIN_TEST_DATABASE_URL)
    async with admin_engine.begin() as conn:
        await conn.execute(text("DROP TABLE IF EXISTS app.spike_tenant_records CASCADE"))
    await admin_engine.dispose()


@pytest_asyncio.fixture
async def app_engine(setup_spike_tables) -> AsyncGenerator[AsyncEngine, None]:
    """Provide async engine connecting as the non-superuser application role."""
    engine = create_async_engine(
        settings.TEST_DATABASE_URL,
        pool_size=5,
        max_overflow=2,
        pool_pre_ping=True,
    )
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def app_session_factory(app_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Provide sessionmaker using non-superuser runtime credentials."""
    return async_sessionmaker(
        bind=app_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autocommit=False,
        autoflush=False,
    )


# ---------------------------------------------------------------------------
# Test Cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rls_multi_tenant_isolation(app_session_factory):
    """Confirm records created by Tenant A and Tenant B are strictly isolated."""
    org_a = uuid.uuid4()
    org_b = uuid.uuid4()

    # 1. Tenant A inserts records
    async with app_session_factory() as session:
        async with session.begin():
            await set_tenant_context(session, org_a)
            await session.execute(
                text("INSERT INTO app.spike_tenant_records (organization_id, name, amount) VALUES (:org, 'Item A1', 100.00)"),
                {"org": str(org_a)},
            )
            await session.execute(
                text("INSERT INTO app.spike_tenant_records (organization_id, name, amount) VALUES (:org, 'Item A2', 200.00)"),
                {"org": str(org_a)},
            )

    # 2. Tenant B inserts records
    async with app_session_factory() as session:
        async with session.begin():
            await set_tenant_context(session, org_b)
            await session.execute(
                text("INSERT INTO app.spike_tenant_records (organization_id, name, amount) VALUES (:org, 'Item B1', 300.00)"),
                {"org": str(org_b)},
            )

    # 3. Query under Tenant A context: Should only see A1 and A2
    async with app_session_factory() as session:
        async with session.begin():
            await set_tenant_context(session, org_a)
            result = await session.execute(text("SELECT name, amount FROM app.spike_tenant_records ORDER BY name"))
            rows = result.fetchall()
            assert len(rows) == 2
            assert rows[0][0] == "Item A1"
            assert rows[1][0] == "Item A2"

    # 4. Query under Tenant B context: Should only see B1
    async with app_session_factory() as session:
        async with session.begin():
            await set_tenant_context(session, org_b)
            result = await session.execute(text("SELECT name, amount FROM app.spike_tenant_records ORDER BY name"))
            rows = result.fetchall()
            assert len(rows) == 1
            assert rows[0][0] == "Item B1"


@pytest.mark.asyncio
async def test_rls_fails_closed_without_context(app_session_factory):
    """Confirm that queries without tenant context return zero rows and reject writes."""
    org_a = uuid.uuid4()

    # Seed record for Org A
    async with app_session_factory() as session:
        async with session.begin():
            await set_tenant_context(session, org_a)
            await session.execute(
                text("INSERT INTO app.spike_tenant_records (organization_id, name, amount) VALUES (:org, 'Secret Item', 500.00)"),
                {"org": str(org_a)},
            )

    # Query with NO tenant context (current_org_id is unset/empty)
    async with app_session_factory() as session:
        async with session.begin():
            # Verify app.current_org_id() evaluates to NULL
            ctx_check = await session.execute(text("SELECT app.current_org_id()"))
            assert ctx_check.scalar() is None

            # SELECT fails closed: returns 0 rows
            result = await session.execute(text("SELECT * FROM app.spike_tenant_records"))
            rows = result.fetchall()
            assert len(rows) == 0

            # INSERT fails closed: raises RLS check violation error
            with pytest.raises(DBAPIError) as exc_info:
                await session.execute(
                    text("INSERT INTO app.spike_tenant_records (organization_id, name, amount) VALUES (:org, 'Hacked Item', 999.00)"),
                    {"org": str(org_a)},
                )
            assert "violates row-level security policy" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_rls_cross_tenant_read_prevented(app_session_factory):
    """Confirm Tenant A cannot read Tenant B's record even if Tenant A knows the exact record UUID."""
    org_a = uuid.uuid4()
    org_b = uuid.uuid4()
    record_b_id = None

    # Tenant B creates a sensitive record
    async with app_session_factory() as session:
        async with session.begin():
            await set_tenant_context(session, org_b)
            res = await session.execute(
                text("INSERT INTO app.spike_tenant_records (organization_id, name, amount) VALUES (:org, 'Confidential B', 10000.00) RETURNING id"),
                {"org": str(org_b)},
            )
            record_b_id = res.scalar()

    assert record_b_id is not None

    # Tenant A attempts to read Tenant B's record directly by ID
    async with app_session_factory() as session:
        async with session.begin():
            await set_tenant_context(session, org_a)
            query = await session.execute(
                text("SELECT * FROM app.spike_tenant_records WHERE id = :id"),
                {"id": record_b_id},
            )
            records = query.fetchall()
            # Returns 0 rows (looks like 404 Not Found)
            assert len(records) == 0


@pytest.mark.asyncio
async def test_rls_cross_tenant_update_and_delete_prevented(app_session_factory):
    """Confirm Tenant A cannot modify or delete Tenant B's records."""
    org_a = uuid.uuid4()
    org_b = uuid.uuid4()
    record_b_id = None

    # Tenant B creates record
    async with app_session_factory() as session:
        async with session.begin():
            await set_tenant_context(session, org_b)
            res = await session.execute(
                text("INSERT INTO app.spike_tenant_records (organization_id, name, amount) VALUES (:org, 'Immutable B', 777.00) RETURNING id"),
                {"org": str(org_b)},
            )
            record_b_id = res.scalar()

    # Tenant A attempts to UPDATE Tenant B's record
    async with app_session_factory() as session:
        async with session.begin():
            await set_tenant_context(session, org_a)
            update_res = await session.execute(
                text("UPDATE app.spike_tenant_records SET amount = 0.00 WHERE id = :id"),
                {"id": record_b_id},
            )
            # 0 rows affected
            assert update_res.rowcount == 0

    # Tenant A attempts to DELETE Tenant B's record
    async with app_session_factory() as session:
        async with session.begin():
            await set_tenant_context(session, org_a)
            del_res = await session.execute(
                text("DELETE FROM app.spike_tenant_records WHERE id = :id"),
                {"id": record_b_id},
            )
            # 0 rows affected
            assert del_res.rowcount == 0

    # Verify Tenant B's record is completely intact
    async with app_session_factory() as session:
        async with session.begin():
            await set_tenant_context(session, org_b)
            check_res = await session.execute(
                text("SELECT amount FROM app.spike_tenant_records WHERE id = :id"),
                {"id": record_b_id},
            )
            amount = check_res.scalar()
            assert amount == 777.00


@pytest.mark.asyncio
async def test_rls_cross_tenant_forged_insert_rejected(app_session_factory):
    """Confirm Tenant A cannot forge an insert containing Tenant B's organization_id."""
    org_a = uuid.uuid4()
    org_b = uuid.uuid4()

    async with app_session_factory() as session:
        async with session.begin():
            # Tenant context set to Org A
            await set_tenant_context(session, org_a)

            # Attempt to insert a row tagged with Org B's ID
            with pytest.raises(DBAPIError) as exc_info:
                await session.execute(
                    text("INSERT INTO app.spike_tenant_records (organization_id, name, amount) VALUES (:org, 'Spoofed', 1.00)"),
                    {"org": str(org_b)},
                )
            assert "violates row-level security policy" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_rls_connection_reuse_after_commit(app_engine: AsyncEngine):
    """Confirm transaction-local context resets on COMMIT and does not leak to next pooled use."""
    org_a = uuid.uuid4()

    # Checkout a single raw pooled connection to test connection reuse directly
    async with app_engine.connect() as conn:
        # Transaction 1: Set context to Org A and commit
        async with conn.begin():
            await conn.execute(
                text("SELECT set_config('app.current_org_id', :org_id, true)"),
                {"org_id": str(org_a)},
            )
            res1 = await conn.execute(text("SELECT app.current_org_id()"))
            assert res1.scalar() == org_a

        # Transaction 2 on the SAME pooled connection: Context must be cleanly reset
        async with conn.begin():
            res2 = await conn.execute(text("SELECT app.current_org_id()"))
            assert res2.scalar() is None, "Tenant context leaked across COMMIT on pooled connection!"


@pytest.mark.asyncio
async def test_rls_connection_reuse_after_rollback(app_engine: AsyncEngine):
    """Confirm transaction-local context resets on ROLLBACK and does not leak to next pooled use."""
    org_a = uuid.uuid4()

    async with app_engine.connect() as conn:
        # Transaction 1: Set context to Org A and explicitly rollback
        trans1 = await conn.begin()
        await conn.execute(
            text("SELECT set_config('app.current_org_id', :org_id, true)"),
            {"org_id": str(org_a)},
        )
        res1 = await conn.execute(text("SELECT app.current_org_id()"))
        assert res1.scalar() == org_a
        await trans1.rollback()

        # Transaction 2 on the SAME connection: Context must be cleanly reset
        async with conn.begin():
            res2 = await conn.execute(text("SELECT app.current_org_id()"))
            assert res2.scalar() is None, "Tenant context leaked across ROLLBACK on pooled connection!"


@pytest.mark.asyncio
async def test_rls_connection_reuse_after_exception(app_engine: AsyncEngine):
    """Confirm transaction-local context resets after unhandled SQL exception and rollback."""
    org_a = uuid.uuid4()

    async with app_engine.connect() as conn:
        # Transaction 1: Set context and trigger SQL error
        try:
            async with conn.begin():
                await conn.execute(
                    text("SELECT set_config('app.current_org_id', :org_id, true)"),
                    {"org_id": str(org_a)},
                )
                # Cause division by zero
                await conn.execute(text("SELECT 1 / 0"))
        except DBAPIError:
            pass  # Expected exception

        # Transaction 2 on the SAME connection: Context must be clean
        async with conn.begin():
            res2 = await conn.execute(text("SELECT app.current_org_id()"))
            assert res2.scalar() is None, "Tenant context leaked across EXCEPTION on pooled connection!"


@pytest.mark.asyncio
async def test_rls_concurrent_pool_contention(app_session_factory):
    """Confirm concurrent asynchronous workers sharing a small connection pool never cross-contaminate."""
    org_a = uuid.uuid4()
    org_b = uuid.uuid4()

    # Pre-seed records for both tenants
    async with app_session_factory() as session:
        async with session.begin():
            await set_tenant_context(session, org_a)
            await session.execute(
                text("INSERT INTO app.spike_tenant_records (organization_id, name, amount) VALUES (:org, 'Worker Item A', 10.00)"),
                {"org": str(org_a)},
            )
            await set_tenant_context(session, org_b)
            await session.execute(
                text("INSERT INTO app.spike_tenant_records (organization_id, name, amount) VALUES (:org, 'Worker Item B', 20.00)"),
                {"org": str(org_b)},
            )

    async def worker_task(target_org: uuid.UUID, expected_name: str, iterations: int):
        for _ in range(iterations):
            async with app_session_factory() as session:
                async with session.begin():
                    await set_tenant_context(session, target_org)
                    # Small async delay to interleave execution across workers
                    await asyncio.sleep(0.005)
                    res = await session.execute(text("SELECT name FROM app.spike_tenant_records"))
                    rows = res.fetchall()
                    assert len(rows) == 1
                    assert rows[0][0] == expected_name

    # Run 10 concurrent workers (5 for Org A, 5 for Org B) contending on pool_size=5
    tasks = []
    for i in range(5):
        tasks.append(worker_task(org_a, "Worker Item A", iterations=5))
        tasks.append(worker_task(org_b, "Worker Item B", iterations=5))

    await asyncio.gather(*tasks)
