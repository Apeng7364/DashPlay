"""The port the CarPlay viewer (PlayPort's web server) listens on.

18443 unless the owner set one. The settings page (OneCloud/N1) keeps it in state/options.json
"viewer_port"; the OpenWrt LuCI keeps dashplay.main.viewer_port in /etc/config/dashplay. An install that
only edited state/receiver/config.json keeps that value too: run-receiver.py used to write 18443 back on
every start and an update's restart dropped the owner's choice (2026-10-23).
"""
import json
from pathlib import Path

DEFAULT = 18443
# The box settings page and its plain HTTP, and AirPlay; a viewer on one of them would collide.
RESERVED = (80, 443, 7000)


def valid(port):
    try:
        port = int(port)
    except (TypeError, ValueError):
        return False
    return 1024 <= port <= 65535 and port not in RESERVED


def resolve(state):
    """The settings page's choice, else the receiver's saved httpPort, else 18443."""
    state = Path(state)
    try:
        options = json.loads((state / "options.json").read_text())
    except (OSError, ValueError):
        options = {}
    if isinstance(options, dict) and valid(options.get("viewer_port")):
        return int(options["viewer_port"])
    try:
        saved = json.loads((state / "receiver" / "config.json").read_text())
    except (OSError, ValueError):
        saved = {}
    if isinstance(saved, dict) and valid(saved.get("httpPort")):
        return int(saved["httpPort"])
    return DEFAULT
