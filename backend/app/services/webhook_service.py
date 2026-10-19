"""Authenticated Outbound Webhook Service for Automation & n8n Integration."""

from datetime import datetime
import hashlib
import hmac
import json
import logging
from typing import Any, Dict, List, Optional
import uuid

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

# In-memory buffer of dispatched webhook events for testing and local inspection
_dispatched_webhooks: List[Dict[str, Any]] = []


def get_dispatched_webhooks() -> List[Dict[str, Any]]:
    """Return in-memory history of dispatched webhooks."""
    return list(_dispatched_webhooks)


def clear_dispatched_webhooks() -> None:
    """Clear in-memory webhook history."""
    _dispatched_webhooks.clear()


class WebhookService:
    """Outbound HMAC-SHA256 Signed Webhook Service."""

    @staticmethod
    def compute_signature(payload_bytes: bytes, secret: str) -> str:
        """Compute HMAC-SHA256 hexadecimal digest for webhook payload."""
        mac = hmac.new(secret.encode("utf-8"), payload_bytes, hashlib.sha256)
        return mac.hexdigest()

    @staticmethod
    def verify_signature(payload_bytes: bytes, signature_header: str, secret: str) -> bool:
        """Validate webhook signature in a timing-attack resistant manner."""
        prefix = "sha256="
        if not signature_header.startswith(prefix):
            return False
        received_sig = signature_header[len(prefix):]
        expected_sig = WebhookService.compute_signature(payload_bytes, secret)
        return hmac.compare_digest(received_sig, expected_sig)

    @staticmethod
    async def dispatch_event(
        event_type: str,
        organization_id: uuid.UUID,
        data: Dict[str, Any],
        webhook_url: Optional[str] = None,
        secret: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Format, sign, and dispatch an authenticated outbound event."""
        url = webhook_url or settings.WEBHOOK_OUTBOUND_URL
        shared_secret = secret or settings.WEBHOOK_SHARED_SECRET

        envelope = {
            "event_id": str(uuid.uuid4()),
            "event_type": event_type,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "organization_id": str(organization_id),
            "data": data,
        }

        payload_bytes = json.dumps(envelope, separators=(",", ":"), default=str).encode("utf-8")
        signature = WebhookService.compute_signature(payload_bytes, shared_secret)

        result_record = {
            "event_id": envelope["event_id"],
            "event_type": event_type,
            "organization_id": str(organization_id),
            "timestamp": envelope["timestamp"],
            "url": url,
            "signature": f"sha256={signature}",
            "delivered": False,
            "status_code": None,
            "error": None,
            "payload": envelope,
        }

        if url:
            try:
                headers = {
                    "Content-Type": "application/json",
                    "X-Ledgerly-Event-Type": event_type,
                    "X-Ledgerly-Signature": f"sha256={signature}",
                    "X-Ledgerly-Delivery": envelope["event_id"],
                }
                async with httpx.AsyncClient(timeout=5.0) as client:
                    resp = await client.post(url, content=payload_bytes, headers=headers)
                    result_record["status_code"] = resp.status_code
                    result_record["delivered"] = (200 <= resp.status_code < 300)
            except Exception as ex:
                logger.warning(f"Failed to deliver webhook {event_type} to {url}: {ex}")
                result_record["error"] = str(ex)
        else:
            # Local recording when no remote endpoint configured
            result_record["delivered"] = True
            result_record["status_code"] = 200

        _dispatched_webhooks.append(result_record)
        return result_record
