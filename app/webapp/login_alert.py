"""The login lock-out and its owner alert, built once per app."""

from __future__ import annotations

# Standard library imports
import logging
import threading
from typing import Callable, Optional

# Local imports
from src.login_lockout import LockoutAlert, LoginLockout
from src.notify import Notifier, NotifierError, TelegramConfig, build_notifier
from src.webapp_config import WebappConfig

logger = logging.getLogger(__name__)

APP_NAME = "photo-ocr"


def make_alert_owner(
    notifier: Optional[Notifier],
) -> Callable[[LockoutAlert], None]:
    """An ``on_alert`` callback that sends the guessing alert from a thread.

    The lock-out calls it inside the login request, so the send never runs
    there. The text names the app, the client and the count, never a password
    or the token.
    """

    def alert_owner(alert: LockoutAlert) -> None:
        if notifier is None:
            logger.info("ℹ️  login alert raised but no notifier is configured")
            return
        held = f" (+{alert.suppressed} more held back)" if alert.suppressed else ""
        text = (
            f"🚨 {APP_NAME}: {alert.failures} wrong passwords in a row "
            f"from {alert.key}{held}"
        )

        def send() -> None:
            try:
                notifier.send_text(text)
            except NotifierError as exc:
                logger.warning(f"⚠️  login alert not delivered: {type(exc).__name__}")

        threading.Thread(target=send, name="login-alert", daemon=True).start()

    return alert_owner


def build_login_lockout(cfg: WebappConfig) -> LoginLockout:
    """The app's lock-out, alerting through the Telegram notifier when both of
    its credentials are configured and logging only otherwise."""
    configured = bool(cfg.telegram_bot_token and cfg.telegram_alert_chat_id)
    notifier = build_notifier(
        "telegram" if configured else "none",
        TelegramConfig(cfg.telegram_bot_token, cfg.telegram_alert_chat_id),
    )
    return LoginLockout(on_alert=make_alert_owner(notifier))
