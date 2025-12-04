import httpx
from typing import Literal

from app.core.settings import settings


async def send_email(
    to_email: str,
    subject: str,
    template: Literal["confirm", "reset"],
    username: str,
    otp: str,
) -> None:
    headers = {"x-api-token": settings.MAILER_TOKEN}
    payload = {
        "to_email": to_email,
        "subject": subject,
        "template": template,
        "username": username,
        "otp": otp,
    }

    async with httpx.AsyncClient() as client:
        response = await client.post(settings.MAILER_URL, json=payload, headers=headers)
        response.raise_for_status()
