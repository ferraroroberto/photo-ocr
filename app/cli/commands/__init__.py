"""CLI subcommand registry."""

from .base import BaseCommand
from .extract_cmd import ExtractCommand
from .tray_cmd import TrayCommand
from .webapp_cmd import WebappCommand

COMMANDS = {
    "tray": TrayCommand,
    "webapp": WebappCommand,
    "extract": ExtractCommand,
}


def get_command(name: str):
    return COMMANDS.get(name)


__all__ = ["BaseCommand", "COMMANDS", "get_command"]
