"""Bounded main-bot transport; no automatic retries or exception telemetry."""

import asyncio
import threading

import httpx

from src.config import Settings

_slots = threading.BoundedSemaphore(4)


def prompt_text(prompt: dict[str, str]) -> str:
    return (
        f"Website sign-in: {prompt['site']}\nRequested: {prompt['created_at']}\n"
        f"Expires: {prompt['expires_at']}\nComparison: {prompt['comparison']}\n"
        "Confirm only a request you initiated. Return to the originating browser.\n"
        "Opening Telegram does not sign in this device's browser."
    )


class TelegramSender:
    def __init__(self, config: Settings) -> None:
        self.config = config

    async def _send(self, actor: int, prompt: dict[str, str]) -> tuple[str, int]:
        try:
            async with httpx.AsyncClient(
                timeout=10, verify=True, follow_redirects=False, trust_env=False
            ) as client:
                response = await client.post(
                    f"https://api.telegram.org/bot{self.config.telegram_bot_token}/sendMessage",
                    json={
                        "chat_id": actor,
                        "text": prompt_text(prompt),
                        "reply_markup": {
                            "inline_keyboard": [
                                [
                                    {"text": "Confirm", "callback_data": prompt["confirm_data"]},
                                    {"text": "Decline", "callback_data": prompt["decline_data"]},
                                ]
                            ]
                        },
                    },
                )
                data = response.json()
                if (
                    response.status_code == 200
                    and isinstance(data, dict)
                    and data.get("ok") is True
                ):
                    return "sent", 0
                retry = (
                    data.get("parameters", {}).get("retry_after", 0)
                    if isinstance(data, dict) and isinstance(data.get("parameters", {}), dict)
                    else 0
                )
                return "unavailable", retry if type(retry) is int and retry > 0 else 0
        except httpx.ConnectError:
            return "unavailable", 0
        except (httpx.TimeoutException, httpx.NetworkError):
            return "uncertain", 0
        except Exception:
            return "unavailable", 0

    async def send(self, actor: int, prompt: dict[str, str]) -> tuple[str, int]:
        if not _slots.acquire(blocking=False):
            return "unavailable", 0
        worker = asyncio.create_task(self._send(actor, prompt))
        try:
            return await asyncio.shield(worker)
        except asyncio.CancelledError:
            while not worker.done():
                try:
                    await asyncio.shield(worker)
                except asyncio.CancelledError:
                    continue
            worker.result()
            raise
        finally:
            _slots.release()
