"""`webapp` subcommand — run the FastAPI server in the foreground."""

from __future__ import annotations

# Standard library imports
import argparse
import logging

from .base import BaseCommand

logger = logging.getLogger(__name__)


class WebappCommand(BaseCommand):
    @classmethod
    def add_parser(cls, subparsers: argparse._SubParsersAction) -> None:
        p = subparsers.add_parser(
            "webapp",
            help="Run the FastAPI webapp in the foreground (no tray)",
        )
        p.add_argument("--host", default=None, help="Override host")
        p.add_argument("--port", type=int, default=None, help="Override port")

    def execute(self, args: argparse.Namespace) -> int:
        # Lazy imports so a plain `webapp` subcommand doesn't drag pystray in.
        from app.webapp.event_loop import selector_loop_factory
        from app.webapp.manager import cert_paths, load_config
        from app.webapp.serve import run

        # Bind address/port have one home — config/config.json's `webapp`
        # section, the same one the tray reads (app/tray/tray.py) — so a
        # port change is one edit instead of two config files disagreeing.
        mgr_cfg = load_config(self.config.webapp)
        host = args.host or mgr_cfg.host
        port = args.port or mgr_cfg.port

        certs = cert_paths()
        kwargs = {
            "log_level": "info",
            "server": mgr_cfg.server,
            "loop_factory": selector_loop_factory,
        }
        if certs is not None:
            cert, key = certs
            kwargs["certfile"] = str(cert)
            kwargs["keyfile"] = str(key)
            scheme = "https"
        else:
            scheme = "http"

        logger.info(
            f"🚀 photo-ocr webapp on {scheme}://{host if host != '0.0.0.0' else '127.0.0.1'}:{port}"
        )
        run(host, port, **kwargs)
        return 0
