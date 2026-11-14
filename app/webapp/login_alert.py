"""The login lock-out and its owner alert, built once per app."""

from __future__ import annotations

# Standard library imports
from typing import Callable, Optional

# Local imports
from src.login_alert import build_login_alert
from src.login_lockout import LockoutAlert, LoginLockout
from src.notify import Notifier, TelegramConfig, build_notifier
from src.webapp_config import WebappConfig

APP_NAME = "photo-ocr"


def make_alert_owner(
    notifier: Optional[Notifier],
) -> Callable[[LockoutAlert], None]:
    """An ``on_alert`` callback sending the guessing alert through the vendored login alert."""
    return build_login_alert(APP_NAME, lambda: notifier)


def build_login_lockout(cfg: WebappConfig) -> LoginLockout:
    """The app's lock-out, alerting through the Telegram notifier when both of
    its credentials are configured and logging the alert as unconfigured otherwise."""
    configured = bool(cfg.telegram_bot_token and cfg.telegram_alert_chat_id)
    notifier = build_notifier(
        "telegram" if configured else "none",
        TelegramConfig(cfg.telegram_bot_token, cfg.telegram_alert_chat_id),
    )
    return LoginLockout(on_alert=make_alert_owner(notifier))
