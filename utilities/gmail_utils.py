"""Gmail SMTP utility — sends emails via kumori Gmail credentials."""

import smtplib
import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

logger = logging.getLogger(__name__)

PROJECT_ID = 'kumori-404602'
GMAIL_USERNAME_SECRET_ID = 'KUMORI_GMAIL_USERNAME'
GMAIL_APP_PASSWORD_SECRET_ID = 'KUMORI_GMAIL_APP_PASSWORD'

_secrets_cache = {}
_sm_client = None


def _get_secret(secret_id):
    if secret_id in _secrets_cache:
        return _secrets_cache[secret_id]
    global _sm_client
    if _sm_client is None:
        from google.cloud import secretmanager
        _sm_client = secretmanager.SecretManagerServiceClient()
    name = f"projects/{PROJECT_ID}/secrets/{secret_id}/versions/latest"
    val = _sm_client.access_secret_version(request={"name": name}).payload.data.decode('UTF-8')
    _secrets_cache[secret_id] = val
    return val


def send_email(subject, body, to_emails, is_html=True, from_name="Kindness Social"):
    try:
        gmail_user = _get_secret(GMAIL_USERNAME_SECRET_ID)
        gmail_pass = _get_secret(GMAIL_APP_PASSWORD_SECRET_ID)

        msg = MIMEMultipart()
        msg['From'] = f'{from_name} <{gmail_user}>'
        msg['To'] = ', '.join(to_emails) if isinstance(to_emails, list) else to_emails
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'html' if is_html else 'plain'))

        recipients = to_emails if isinstance(to_emails, list) else [to_emails]
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
            server.login(gmail_user, gmail_pass)
            server.send_message(msg, to_addrs=recipients)

        logger.info(f"Email sent: {subject} -> {recipients}")
        return True
    except Exception as e:
        logger.error(f"Email failed: {e}")
        return False
