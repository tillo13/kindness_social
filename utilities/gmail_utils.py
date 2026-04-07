"""Gmail send utility — sends as kumoridotai@gmail.com via Gmail API + OAuth.

Swapped from SMTP app-password (which Gmail flags as spam at volume) to the
Gmail API using a refresh token stored in Secret Manager
(`KUMORI_GMAIL_OAUTH_REFRESH_TOKEN` in project kumori-404602). Same sender
identity, much higher deliverability.
"""

import base64
import json
import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

logger = logging.getLogger(__name__)

PROJECT_ID = "kumori-404602"
OAUTH_SECRET_ID = "KUMORI_GMAIL_OAUTH_REFRESH_TOKEN"

_cached_service = None
_cached_creds = None
_sm_client = None


def _get_gmail_service():
    """Build (and cache) a Gmail API client authed as kumoridotai@gmail.com."""
    global _cached_service, _cached_creds, _sm_client
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build

    if _cached_creds is None:
        from google.cloud import secretmanager
        if _sm_client is None:
            _sm_client = secretmanager.SecretManagerServiceClient()
        name = f"projects/{PROJECT_ID}/secrets/{OAUTH_SECRET_ID}/versions/latest"
        payload = json.loads(
            _sm_client.access_secret_version(request={"name": name}).payload.data.decode("UTF-8")
        )
        _cached_creds = Credentials(
            token=None,
            refresh_token=payload["refresh_token"],
            client_id=payload["client_id"],
            client_secret=payload["client_secret"],
            token_uri=payload["token_uri"],
            scopes=payload.get("scopes"),
        )

    if not _cached_creds.valid:
        _cached_creds.refresh(Request())
        _cached_service = None  # rebuild service after refresh

    if _cached_service is None:
        _cached_service = build("gmail", "v1", credentials=_cached_creds, cache_discovery=False)
    return _cached_service


def send_email(subject, body, to_emails, is_html=True, from_name="Kindness Social"):
    try:
        recipients = to_emails if isinstance(to_emails, list) else [to_emails]
        msg = MIMEMultipart()
        msg['From'] = f'{from_name} <kumoridotai@gmail.com>'
        msg['To'] = ', '.join(recipients)
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'html' if is_html else 'plain'))

        svc = _get_gmail_service()
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        svc.users().messages().send(userId='me', body={'raw': raw}).execute()

        logger.info(f"Email sent: {subject} -> {recipients}")
        return True
    except Exception as e:
        logger.error(f"Email failed: {e}")
        return False
