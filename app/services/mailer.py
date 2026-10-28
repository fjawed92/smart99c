"""Email sending using SMTP settings stored in SiteSettings.

Settings are persisted to the site_settings table so an admin can change the
Gmail SMTP credentials from the admin UI at any time. The Gmail app password
is encrypted at rest with a Fernet key derived from SECRET_KEY.
"""
import smtplib
from email.message import EmailMessage
from email.utils import formataddr
from urllib.parse import quote_plus

from flask import current_app, render_template, url_for
from app.helpers import get_site_setting, decrypt_secret
from app.store import STORE


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

    # EmailMessage encodes non-ASCII text (e.g. the ¢ in "99¢") as UTF-8 in
    # both headers and body; the old MIMEText + sendmail path crashed on it.
    msg = EmailMessage()
    msg['Subject'] = subject
    sender = formataddr((cfg['from_name'], cfg['from_email'])) if cfg['from_name'] else cfg['from_email']
    msg['From'] = sender
    msg['To'] = to_email
    msg.set_content(text_body or 'This message requires an HTML-capable email client.')
    msg.add_alternative(html_body, subtype='html')

    with smtplib.SMTP(cfg['server'], cfg['port'], timeout=20) as smtp:
        smtp.ehlo()
        if cfg['use_tls']:
            smtp.starttls()
            smtp.ehlo()
        smtp.login(cfg['username'], cfg['password'])
        smtp.send_message(msg, from_addr=cfg['from_email'], to_addrs=[to_email])


# ─── Branded templates ──────────────────────────────────────────────────────

# Email-safe palette and fonts for the "Clean Receipt" design (matches the
# site's colors; email apps can't load web fonts, so system fonts only).
EMAIL_STYLE = {
    'page': '#F4F1F0', 'white': '#FFFFFF', 'gray': '#F4F1F0', 'hair': '#EAD9D5',
    'text': '#2A1215', 'muted': '#7A5C5F', 'ink': '#5E171C',
    'teal': '#2E6E6E', 'pink': '#F0668E',
    'font': "-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif",
    'mono': "Menlo,Consolas,'Courier New',monospace",
    # pill tones: (background, text)
    'tones': {
        'ok': ('#D8F2E4', '#12824F'),
        'info': ('#E3F0EF', '#2E6E6E'),
        'warn': ('#FFF4D1', '#7A4B00'),
        'muted': ('#F4F1F0', '#7A5C5F'),
    },
}


def _external(endpoint, **values):
    try:
        return url_for(endpoint, _external=True, **values)
    except RuntimeError:  # no request context (e.g. a shell script)
        return ''


def render_email(template, subject, **context):
    """Render a customer email from templates/email/ with the shared layout."""
    head, _, tail = STORE['address'].partition(', ')
    return render_template(
        f'email/{template}',
        subject=subject,
        c=EMAIL_STYLE,
        store=STORE,
        address_lines=[head, tail] if tail else [STORE['address']],
        maps_url='https://maps.google.com/?q=' + quote_plus(STORE['address']),
        site_url=_external('main.index'),
        logo_url=_external('static', filename='img/email-logo.png'),
        **context,
    )
