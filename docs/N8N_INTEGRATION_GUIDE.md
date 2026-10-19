# Ledgerly: n8n Integration Guide (Phase 5)

This guide documents how to integrate **Ledgerly** with **n8n** for business process automation without compromising accounting correctness, database Row-Level Security, or transaction atomicity.

---

## 1. Architectural Principles

1. **Non-Invasive Notifications**: Ledgerly is the single source of truth for all accounting calculations, journal postings, and balances. n8n must never calculate accounting balances or bypass Ledgerly's API.
2. **Authenticated Outbound Webhooks**: Ledgerly signs all outbound HTTP requests using HMAC-SHA256 with a shared secret configured in `WEBHOOK_SHARED_SECRET`.
3. **Idempotency**: Every outbound event includes a unique `event_id` and timestamps. n8n workflows must deduplicate using this `event_id`.
4. **Tenant Isolation**: Events always carry `organization_id`. Any subsequent API calls made by n8n back to Ledgerly must supply `X-Organization-ID` and valid session/bearer credentials.

---

## 2. Event Catalogue

Ledgerly emits the following events when configured:

| Event Type | Trigger | Key Data Fields |
|:---|:---|:---|
| `invoice.issued` | Invoice transition to `issued` (JP-01 posted) | `invoice_id`, `invoice_number`, `total_amount`, `due_date`, `customer_name` |
| `payment.recorded` | Customer payment allocated (JP-03 posted) | `payment_id`, `reference`, `amount`, `payment_date`, `allocations` |
| `invoice.overdue` | Automated reminder scan detects overdue invoice | `invoice_id`, `invoice_number`, `balance_due`, `due_date`, `days_overdue` |
| `notification.failed` | Delivery attempts exceeded `max_attempts` | `job_id`, `recipient_email`, `event_type`, `error_message` |

---

## 3. Webhook Security & Signature Verification

Every outbound webhook request from Ledgerly includes standard security headers:

```http
POST /webhook/ledgerly HTTP/1.1
Host: your-n8n-instance.com
Content-Type: application/json
X-Ledgerly-Event-Type: invoice.issued
X-Ledgerly-Delivery: d4f9443e-c6e3-46fb-a04f-7be683dae911
X-Ledgerly-Signature: sha256=a59f...5d8
```

### Verification in n8n (Code Node or Crypto Node)

In your n8n workflow, place a **Code Node** immediately after the Webhook Trigger to verify the signature:

```javascript
const crypto = require('crypto');

const secret = $env.LEDGERLY_WEBHOOK_SECRET || 'ledgerly-webhook-secret-phase5';
const receivedHeader = $headers['x-ledgerly-signature'] || '';
const rawBody = JSON.stringify($json);

const computedSignature = 'sha256=' + crypto
  .createHmac('sha256', secret)
  .update(rawBody)
  .digest('hex');

if (receivedHeader !== computedSignature) {
  throw new Error('401 Unauthorized: Invalid Ledgerly Webhook Signature');
}

return [{ json: $json }];
```

---

## 4. Sample Webhook Payload

```json
{
  "event_id": "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d",
  "event_type": "invoice.issued",
  "timestamp": "2026-10-19T10:30:00.000000Z",
  "organization_id": "00000000-0000-0000-0000-000000000001",
  "data": {
    "invoice_id": "7c9e6679-7425-40de-944b-e07fc1f90ae7",
    "invoice_number": "INV-0001",
    "customer_name": "Acme Global Corp",
    "total_amount": "1180.0000",
    "due_date": "2026-11-18",
    "currency_code": "USD"
  }
}
```

---

## 5. Callback Pattern (n8n Calling Ledgerly API)

When an n8n workflow needs to inspect an invoice or trigger an action in Ledgerly:
1. Provide the authenticated session header or API credentials.
2. Provide `X-Organization-ID: <organization_id>` matching the event.
3. Make standard REST requests:
   - `GET /api/v1/invoices/{id}` - Fetch authoritative invoice state.
   - `GET /api/v1/notifications/{job_id}` - Check email delivery status.
   - `POST /api/v1/notifications/{job_id}/retry` - Trigger a retry for a dead-letter job.

---

## 6. Sample n8n Workflow Configuration

An n8n workflow template JSON can be structured as:
1. **Webhook Trigger Node**:
   - HTTP Method: `POST`
   - Path: `/ledgerly-events`
   - Response Mode: `On Received` (Returns 200 immediately)
2. **HMAC Signature Check**:
   - Rejects unauthenticated callers.
3. **Switch Node**:
   - Route `event_type == 'invoice.issued'` -> Send Slack notification to sales channel.
   - Route `event_type == 'payment.recorded'` -> Update CRM deal status.
   - Route `event_type == 'invoice.overdue'` -> Alert account manager.
