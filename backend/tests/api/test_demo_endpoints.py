"""Tests for Demo helper endpoints and Journal inspection API."""

import pytest
from httpx import ASGITransport, AsyncClient
from app.main import app


@pytest.mark.asyncio
async def test_demo_seed_and_journal_inspection():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Trigger demo seed
        seed_resp = await client.post("/api/v1/demo/seed")
        assert seed_resp.status_code == 200
        seed_data = seed_resp.json()
        assert seed_data["status"] == "success"
        org_id = seed_data["organization_id"]
        user_id = seed_data["user_id"]

        # 2. Get demo context
        ctx_resp = await client.get("/api/v1/demo/context")
        assert ctx_resp.status_code == 200
        ctx_data = ctx_resp.json()
        assert ctx_data["seeded"] is True
        assert ctx_data["organization_id"] == org_id

        # 3. Inspect Journals via API
        headers = {
            "X-Organization-ID": org_id,
            "X-User-ID": user_id,
        }
        j_resp = await client.get("/api/v1/journals", headers=headers)
        assert j_resp.status_code == 200
        journals = j_resp.json()
        assert len(journals) >= 2  # Invoices (JP-01) and Payment (JP-03)

        # Verify balanced lines in first journal
        journal_1 = journals[0]
        assert "lines" in journal_1
        assert len(journal_1["lines"]) >= 2
        debit_sum = sum(float(line["debit"]) for line in journal_1["lines"])
        credit_sum = sum(float(line["credit"]) for line in journal_1["lines"])
        assert round(debit_sum, 4) == round(credit_sum, 4)

        # 4. Run isolation spike via API
        spike_resp = await client.get("/api/v1/demo/isolation-spike")
        assert spike_resp.status_code == 200
        spike_data = spike_resp.json()
        assert spike_data["cross_tenant_probe"]["isolated"] is True
        assert spike_data["cross_tenant_probe"]["leaked_records_count"] == 0
