"""Email Provider Adapter Interface and Implementations (Dev & SMTP)."""

from abc import ABC, abstractmethod
import asyncio
from dataclasses import dataclass, field
from email.message import EmailMessage
import logging
import smtplib
from typing import Any, Dict, List, Optional
import uuid

from app.core.config import settings

logger = logging.getLogger(__name__)


@dataclass
class EmailAttachment:
    filename: str
    content_bytes: bytes
    content_type: str = "application/pdf"


@dataclass
class EmailDeliveryResult:
    success: bool
    provider_message_id: Optional[str] = None
    error_message: Optional[str] = None


class BaseEmailProvider(ABC):
    """Abstract transactional email provider interface."""

    @abstractmethod
    async def send_email(
        self,
        to_email: str,
        subject: str,
        body_html: str,
        body_text: str,
        attachments: Optional[List[EmailAttachment]] = None,
    ) -> EmailDeliveryResult:
        """Send an email message asynchronously."""
        pass


class DevelopmentEmailProvider(BaseEmailProvider):
    """Local in-memory and logging provider for development and testing.
    
    Provides inspection helpers and failure simulation for retry verification.
    """

    def __init__(self):
        self.sent_box: List[Dict[str, Any]] = []
        self._fail_count: int = 0
        self._permanent_fail: bool = False

    def simulate_transient_failures(self, count: int = 1) -> None:
        """Simulate next N send operations failing with transient network error."""
        self._fail_count = count

    def simulate_permanent_failure(self, fail: bool = True) -> None:
        """Simulate persistent provider rejection."""
        self._permanent_fail = fail

    def clear(self) -> None:
        """Clear the in-memory mailbox and failure simulations."""
        self.sent_box.clear()
        self._fail_count = 0
        self._permanent_fail = False

    def get_sent_emails(self) -> List[Dict[str, Any]]:
        return list(self.sent_box)

    async def send_email(
        self,
        to_email: str,
        subject: str,
        body_html: str,
        body_text: str,
        attachments: Optional[List[EmailAttachment]] = None,
    ) -> EmailDeliveryResult:
        # Check simulated failures
        if self._permanent_fail:
            return EmailDeliveryResult(
                success=False,
                error_message="Simulated permanent provider rejection (e.g. invalid recipient domain)",
            )

        if self._fail_count > 0:
            self._fail_count -= 1
            return EmailDeliveryResult(
                success=False,
                error_message="Simulated transient network timeout (e.g. SMTP connection timeout)",
            )

        msg_id = f"dev-msg-{uuid.uuid4()}"
        record = {
            "to": to_email,
            "subject": subject,
            "body_html": body_html,
            "body_text": body_text,
            "attachments_count": len(attachments) if attachments else 0,
            "attachments": [
                {
                    "filename": a.filename,
                    "size": len(a.content_bytes),
                    "type": a.content_type,
                    "content_bytes": a.content_bytes,
                }
                for a in (attachments or [])
            ],
            "message_id": msg_id,
        }
        self.sent_box.append(record)
        logger.info(f"[DEV EMAIL] Sent to '{to_email}' | Subject: '{subject}' | MsgID: {msg_id}")
        return EmailDeliveryResult(success=True, provider_message_id=msg_id)


class SMTPEmailProvider(BaseEmailProvider):
    """Production standard SMTP email provider utilizing background thread execution."""

    def __init__(
        self,
        host: str = settings.SMTP_HOST,
        port: int = settings.SMTP_PORT,
        user: str = settings.SMTP_USER,
        password: str = settings.SMTP_PASSWORD,
        from_email: str = settings.SMTP_FROM_EMAIL,
        from_name: str = settings.SMTP_FROM_NAME,
        use_tls: bool = settings.SMTP_TLS,
    ):
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.from_email = from_email
        self.from_name = from_name
        self.use_tls = use_tls

    def _sync_send(
        self,
        to_email: str,
        subject: str,
        body_html: str,
        body_text: str,
        attachments: Optional[List[EmailAttachment]] = None,
    ) -> EmailDeliveryResult:
        try:
            msg = EmailMessage()
            msg["Subject"] = subject
            msg["From"] = f"{self.from_name} <{self.from_email}>"
            msg["To"] = to_email
            msg.set_content(body_text)
            msg.add_alternative(body_html, subtype="html")

            if attachments:
                for att in attachments:
                    maintype, subtype = att.content_type.split("/", 1)
                    msg.add_attachment(
                        att.content_bytes,
                        maintype=maintype,
                        subtype=subtype,
                        filename=att.filename,
                    )

            with smtplib.SMTP(self.host, self.port, timeout=15) as server:
                if self.use_tls:
                    server.starttls()
                if self.user and self.password:
                    server.login(self.user, self.password)
                server.send_message(msg)

            return EmailDeliveryResult(
                success=True,
                provider_message_id=f"smtp-{uuid.uuid4()}",
            )
        except Exception as ex:
            # Sanitize error message to ensure credentials are never leaked
            sanitized_err = str(ex).replace(self.password, "******") if self.password else str(ex)
            logger.error(f"SMTP delivery failed to {to_email}: {sanitized_err}")
            return EmailDeliveryResult(
                success=False,
                error_message=f"SMTP Error: {sanitized_err}",
            )

    async def send_email(
        self,
        to_email: str,
        subject: str,
        body_html: str,
        body_text: str,
        attachments: Optional[List[EmailAttachment]] = None,
    ) -> EmailDeliveryResult:
        return await asyncio.to_thread(
            self._sync_send, to_email, subject, body_html, body_text, attachments
        )


# Singleton instances
_dev_provider = DevelopmentEmailProvider()
_smtp_provider = None


def get_email_provider() -> BaseEmailProvider:
    """Return configured email provider instance."""
    global _smtp_provider
    if settings.EMAIL_PROVIDER.lower() == "smtp":
        if _smtp_provider is None:
            _smtp_provider = SMTPEmailProvider()
        return _smtp_provider
    return _dev_provider
