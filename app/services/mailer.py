"""Email sending using SMTP settings stored in SiteSettings.

Settings are persisted to the site_settings table so an admin can change the
Gmail SMTP credentials from the admin UI at any time. The Gmail app password
is encrypted at rest with a Fernet key derived from SECRET_KEY.
"""
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr
from flask import current_app
from app.helpers import get_site_setting, decrypt_secret


MAIL_KEYS = [
    'mail_server',
    'mail_port',
    'mail_use_tls',
    'mail_username',
    'mail_password_encrypted',
    'mail_from_name',
    'mail_from_email',
]


def get_mail_config():
    """Return the effective SMTP config, preferring DB values over env."""
    cfg = current_app.config
    server = get_site_setting('mail_server') or cfg.get('MAIL_SERVER') or 'smtp.gmail.com'
    port_str = get_site_setting('mail_port') or str(cfg.get('MAIL_PORT') or 587)
    try:
        port = int(port_str)
    except (TypeError, ValueError):
        port = 587
    use_tls_raw = get_site_setting('mail_use_tls', None)
    if use_tls_raw is None:
        use_tls = bool(cfg.get('MAIL_USE_TLS', True))
    else:
        use_tls = use_tls_raw == '1'
    username = get_site_setting('mail_username') or cfg.get('MAIL_USERNAME') or ''
    encrypted = get_site_setting('mail_password_encrypted', '')
    password = decrypt_secret(encrypted) if encrypted else (cfg.get('MAIL_PASSWORD') or '')
    from_email = get_site_setting('mail_from_email') or username
    from_name = get_site_setting('mail_from_name') or ''
    return {
        'server': server,
        'port': port,
        'use_tls': use_tls,
        'username': username,
        'password': password,
        'from_email': from_email,
        'from_name': from_name,
    }


def is_mail_configured():
    cfg = get_mail_config()
    return bool(cfg['server'] and cfg['username'] and cfg['password'] and cfg['from_email'])


def send_email(to_email, subject, html_body, text_body=None):
    """Send a single email using the configured SMTP credentials.

    Raises smtplib.SMTPException / OSError on failure; callers are expected
    to wrap this in try/except and surface a user-facing message.
    """
    cfg = get_mail_config()
    if not (cfg['server'] and cfg['username'] and cfg['password']):
        raise RuntimeError('Email is not configured. Add SMTP settings in Admin → Email.')

    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject
    sender = formataddr((cfg['from_name'], cfg['from_email'])) if cfg['from_name'] else cfg['from_email']
    msg['From'] = sender
    msg['To'] = to_email
    if text_body:
        msg.attach(MIMEText(text_body, 'plain'))
    msg.attach(MIMEText(html_body, 'html'))

    with smtplib.SMTP(cfg['server'], cfg['port'], timeout=20) as smtp:
        smtp.ehlo()
        if cfg['use_tls']:
            smtp.starttls()
            smtp.ehlo()
        smtp.login(cfg['username'], cfg['password'])
        smtp.sendmail(cfg['from_email'], [to_email], msg.as_string())
