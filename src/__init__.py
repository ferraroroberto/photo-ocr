"""Logic layer — image handling, OCR client, config, archive, diagnostics.

UI surfaces (`app/cli/`, `app/webapp/`, `app/tray/`) consume this package;
nothing in here imports any UI framework. See `CLAUDE.md` for the
`src/` ↔ `app/` split convention shared across the monorepo.
"""

from .app_config import (
    AppConfig,
    load_app_config,
)
from .diagnostics import (
    RingLogHandler,
    app_log_handler,
    attach_app_log_handler,
    port_owner,
)

__all__ = [
    "AppConfig",
    "RingLogHandler",
    "app_log_handler",
    "attach_app_log_handler",
    "load_app_config",
    "port_owner",
]
