"""voice-control — backend routes, mounted at /api/plugins/voice-control/.

Turns the microphone of a headless voice Hermes (a Hermes CLI that a service keeps in a
tmux session on another host) on and off over ssh. The work happens in ``remote.sh``,
sent over ssh's stdin, so nothing is installed on the assistant host.

    GET  /state                     -> where the assistant is and whether it is listening
    POST /state {"listening": bool} -> /voice on + /wake on, or /wake off + /voice off

Settings (Settings -> Plugins -> voice-control, ``plugins.entries.voice-control.settings``):
``ssh_host`` — a ``~/.ssh/config`` alias or ``user@host``; ``tmux_session`` — the CLI's tmux
session. The ssh key must work non-interactively (BatchMode): there is nobody to type a passphrase.
"""
from __future__ import annotations

import asyncio
import logging
import pathlib
import re
import subprocess
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, HTTPException

log = logging.getLogger(__name__)

router = APIRouter()

PLUGIN_ID = "voice-control"
DEFAULTS = {"ssh_host": "ct125-voice", "tmux_session": "voice"}
REMOTE_SCRIPT = pathlib.Path(__file__).with_name("remote.sh")
# remote.sh waits up to 20 s for a running turn to finish, then ~5 s of keystrokes and checks.
TIMEOUT_S = 60
# ssh_host reaches argv: no leading "-" (would be read as an ssh option), no spaces or shell syntax.
_HOST_RE = re.compile(r"^[A-Za-z0-9_.][A-Za-z0-9_.@:%+-]*$")
_SESSION_RE = re.compile(r"^[A-Za-z0-9_.-]+$")

ERRORS = {
    "no_session": "the assistant's tmux session is not running (is hermes-voice.service up?)",
    "busy": "the assistant is answering right now; try again when it finishes",
    "unreachable": "ssh to the assistant host failed",
    "timeout": "the assistant host did not answer in time",
    "bad_settings": "ssh_host / tmux_session in the plugin settings are not valid",
    "bad_reply": "the assistant host answered something unexpected",
}


def settings() -> Dict[str, str]:
    """The plugin's settings over the defaults; blank values fall back to the default."""
    current: Dict[str, Any] = {}
    try:
        from hermes_cli.config import load_config_readonly
        cfg = load_config_readonly() or {}
        entry = ((cfg.get("plugins") or {}).get("entries") or {}).get(PLUGIN_ID) or {}
        current = entry.get("settings") or {}
    except Exception:  # an unreadable config still leaves the defaults usable
        log.debug("voice-control: cannot read the plugin settings", exc_info=True)
    return {key: str(current.get(key) or "").strip() or default for key, default in DEFAULTS.items()}


def _parse(stdout: str) -> Dict[str, str]:
    pairs = {}
    for line in stdout.splitlines():
        key, sep, value = line.partition("=")
        if sep:
            pairs[key.strip()] = value.strip()
    return pairs


def _flag(value: Optional[str]) -> Optional[bool]:
    return {"true": True, "false": False}.get((value or "").lower())


def state_from(rc: int, stdout: str, stderr: str) -> Dict[str, Any]:
    """Normalise one ssh run to the payload the chip draws. ``listening`` is None when unknown."""
    if rc == 255:  # ssh's own failure: connect, auth, host key
        return {"listening": None, "wake_enabled": None, "error": "unreachable", "detail": stderr.strip()[-300:]}
    pairs = _parse(stdout)
    listening = _flag(pairs.get("listening"))
    error = pairs.get("error") or None
    if listening is None and error is None:
        error = "bad_reply"
    return {"listening": listening, "wake_enabled": _flag(pairs.get("wake_enabled")), "error": error,
            "detail": stderr.strip()[-300:] if error == "bad_reply" else ""}


def run_remote(action: str, conf: Dict[str, str], runner=subprocess.run) -> Dict[str, Any]:
    """Run remote.sh with ``action`` on the configured host. ``runner`` is subprocess.run (tests swap it)."""
    host, session = conf["ssh_host"], conf["tmux_session"]
    base = {"host": host, "session": session}
    if not _HOST_RE.match(host) or not _SESSION_RE.match(session):
        return {**base, "listening": None, "wake_enabled": None, "error": "bad_settings", "detail": ""}
    argv = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "--", host,
            "bash", "-s", "--", action, session]
    try:
        done = runner(argv, input=REMOTE_SCRIPT.read_text(encoding="utf-8"), capture_output=True,
                      text=True, timeout=TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return {**base, "listening": None, "wake_enabled": None, "error": "timeout", "detail": ""}
    except OSError as exc:  # no ssh binary
        return {**base, "listening": None, "wake_enabled": None, "error": "unreachable", "detail": str(exc)}
    return {**base, **state_from(done.returncode, done.stdout or "", done.stderr or "")}


def _with_message(payload: Dict[str, Any]) -> Dict[str, Any]:
    payload["message"] = ERRORS.get(payload.get("error") or "", payload.get("error") or "")
    return payload


@router.get("/state")
async def get_state():
    return _with_message(await asyncio.to_thread(run_remote, "status", settings()))


@router.post("/state")
async def set_state(payload: Dict[str, Any] = Body(...)):
    listening = payload.get("listening")
    if not isinstance(listening, bool):
        raise HTTPException(status_code=422, detail='body must be {"listening": true|false}')
    return _with_message(await asyncio.to_thread(run_remote, "on" if listening else "off", settings()))
