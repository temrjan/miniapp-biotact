import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from src.config import settings

logger = logging.getLogger(__name__)


class EmailService:
    def __init__(self) -> None:
        self.smtp_host = settings.smtp_host
        self.smtp_port = settings.smtp_port
        self.smtp_user = settings.smtp_user
        self.smtp_password = settings.smtp_password.get_secret_value()
        self.from_email = settings.smtp_from_email or settings.smtp_user
        self.from_name = settings.smtp_from_name

    def _send_email(self, to_email: str, subject: str, html_content: str) -> bool:
        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = f"{self.from_name} <{self.from_email}>"
            msg["To"] = to_email

            html_part = MIMEText(html_content, "html", "utf-8")
            msg.attach(html_part)

            with smtplib.SMTP(self.smtp_host, self.smtp_port) as server:
                server.starttls()
                server.login(self.smtp_user, self.smtp_password)
                server.sendmail(self.from_email, to_email, msg.as_string())

            logger.info(f"Email sent to {to_email}")
            return True
        except Exception as e:
            logger.error(f"Failed to send email to {to_email}: {e}")
            return False

    def send_verification_email(
        self, to_email: str, token: str, first_name: str | None = None
    ) -> bool:
        name = first_name or "пользователь"
        verify_url = f"{settings.admin_url}/verify-email?token={token}"

        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <style>
                body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
                .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
                .button {{ display: inline-block; padding: 12px 24px; background-color: #4CAF50; color: white; text-decoration: none; border-radius: 4px; }}
                .footer {{ margin-top: 30px; font-size: 12px; color: #666; }}
            </style>
        </head>
        <body>
            <div class="container">
                <h2>Подтверждение email</h2>
                <p>Здравствуйте, {name}!</p>
                <p>Для завершения регистрации в админ-панели Biotact, пожалуйста, подтвердите ваш email:</p>
                <p><a href="{verify_url}" class="button">Подтвердить email</a></p>
                <p>Или скопируйте ссылку: {verify_url}</p>
                <p>Ссылка действительна 24 часа.</p>
                <div class="footer">
                    <p>Если вы не регистрировались в системе, просто проигнорируйте это письмо.</p>
                </div>
            </div>
        </body>
        </html>
        """

        return self._send_email(to_email, "Подтверждение email - Biotact Admin", html_content)

    def send_password_reset_email(
        self, to_email: str, token: str, first_name: str | None = None
    ) -> bool:
        name = first_name or "пользователь"
        reset_url = f"{settings.admin_url}/reset-password?token={token}"

        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <style>
                body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
                .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
                .button {{ display: inline-block; padding: 12px 24px; background-color: #2196F3; color: white; text-decoration: none; border-radius: 4px; }}
                .footer {{ margin-top: 30px; font-size: 12px; color: #666; }}
            </style>
        </head>
        <body>
            <div class="container">
                <h2>Сброс пароля</h2>
                <p>Здравствуйте, {name}!</p>
                <p>Вы запросили сброс пароля для админ-панели Biotact:</p>
                <p><a href="{reset_url}" class="button">Сбросить пароль</a></p>
                <p>Или скопируйте ссылку: {reset_url}</p>
                <p>Ссылка действительна 1 час.</p>
                <div class="footer">
                    <p>Если вы не запрашивали сброс пароля, просто проигнорируйте это письмо.</p>
                </div>
            </div>
        </body>
        </html>
        """

        return self._send_email(to_email, "Сброс пароля - Biotact Admin", html_content)


email_service = EmailService()
