"""Bounded buyer SMTP delivery; capacity belongs to the underlying running work."""

import asyncio
import smtplib
import ssl
import threading
from email.message import EmailMessage
from email.utils import formataddr

from src.buyer_auth.security import BuyerAuthError, normalize_email
from src.config import Settings

_slots = threading.BoundedSemaphore(4)


class BuyerMailer:
    def __init__(self, config: Settings) -> None:
        self.config = config
        self.in_flight = 0

    def _send(self, recipient: str, code: str) -> None:
        config = self.config
        normalize_email(recipient)
        normalize_email(config.smtp_from_email)
        if any(ord(c) < 32 or ord(c) == 127 for c in config.smtp_from_name):
            raise ValueError("Invalid mail headers")
        message = EmailMessage()
        message["From"] = formataddr((config.smtp_from_name, config.smtp_from_email))
        message["To"] = recipient
        message["Subject"] = "Biotact buyer sign-in code"
        message.set_content(f"Your Biotact buyer sign-in code: {code}\nValid for 10 minutes.\n")
        with smtplib.SMTP(config.smtp_host, config.smtp_port, timeout=10) as smtp:
            smtp.set_debuglevel(0)
            smtp.ehlo()
            smtp.starttls(context=ssl.create_default_context())
            smtp.ehlo()
            smtp.login(config.smtp_user, config.smtp_password.get_secret_value())
            refused = smtp.send_message(
                message, from_addr=config.smtp_from_email, to_addrs=[recipient]
            )
            if refused:
                raise ValueError("Mail refused")

    async def send(self, recipient: str, code: str) -> None:
        if not _slots.acquire(blocking=False):
            raise BuyerAuthError(503, "authentication_unavailable")
        self.in_flight += 1
        worker = asyncio.create_task(asyncio.to_thread(self._send, recipient, code))
        try:
            await asyncio.shield(worker)
        except asyncio.CancelledError:
            # Do not release admission while an uncancellable SMTP thread still owns it.
            while not worker.done():
                try:
                    await asyncio.shield(worker)
                except asyncio.CancelledError:
                    continue
                except Exception:
                    break
            if not worker.cancelled():
                worker.exception()
            raise
        except Exception:
            raise BuyerAuthError(503, "authentication_unavailable") from None
        finally:
            self.in_flight -= 1
            _slots.release()
