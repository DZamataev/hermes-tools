"""voice-control — backend routes, mounted at /api/plugins/voice-control/.

Turns the microphone of a headless voice Hermes (a Hermes CLI that a service keeps in a
tmux session on another host) on and off over ssh, and drives the music player
(``$HOME/.local/bin/music``, mpv) on the same host. The work happens in ``remote.sh``,
sent over ssh's stdin, so nothing is installed on the assistant host.

    GET  /state                     -> where the assistant is and whether it is listening
    POST /state {"listening": bool} -> /voice on + /wake on, or /wake off + /voice off
    GET  /music                     -> ``music status --json``
    POST /music {"cmd": str, "arg": str} -> one control command, then the fresh status
    POST /prompt {"text": str}      -> type the text into the CLI's session; it answers aloud

Settings (Settings -> Plugins -> voice-control, ``plugins.entries.voice-control.settings``):
``ssh_host`` — a ``~/.ssh/config`` alias or ``user@host``; ``tmux_session`` — the CLI's tmux
session. The ssh key must work non-interactively (BatchMode): there is nobody to type a passphrase.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import pathlib
import re
import subprocess
from typing import Any, Dict, Optional, Tuple

from fastapi import APIRouter, Body, HTTPException

log = logging.getLogger(__name__)

router = APIRouter()

PLUGIN_ID = "voice-control"
DEFAULTS = {"ssh_host": "ct125-voice", "tmux_session": "voice"}
REMOTE_SCRIPT = pathlib.Path(__file__).with_name("remote.sh")
# remote.sh waits up to 20 s for a running turn to finish, then ~5 s of keystrokes and checks.
TIMEOUT_S = 60
# The player answers at once; only ssh (ConnectTimeout=10) is slow.
MUSIC_TIMEOUT_S = 15
# The only commands POST /music may run — remote.sh checks the same list again.
MUSIC_CMDS = ("pause", "resume", "toggle", "next", "prev", "stop", "volume")
_VOLUME_RE = re.compile(r"^[+-]?[0-9]{1,3}$")
# A prompt is typed as one line; longer text is not a spoken question any more.
PROMPT_MAX = 2000
_CTRL_RE = re.compile(r"[\x00-\x1f\x7f]")
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
    "music_failed": "the player did not accept the command",
    "bad_prompt": "the prompt was not accepted (one line, no control characters, no leading /)",
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


def _ssh(action: str, conf: Dict[str, str], runner, extra: Tuple[str, ...] = (),
         timeout: int = TIMEOUT_S) -> Dict[str, Any]:
    """One ssh run of remote.sh. ``{"rc","stdout","stderr"}``, or a labelled ``{"error","detail"}``."""
    host, session = conf["ssh_host"], conf["tmux_session"]
    if not _HOST_RE.match(host) or not _SESSION_RE.match(session):
        return {"error": "bad_settings", "detail": ""}
    argv = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "--", host,
            "bash", "-s", "--", action, session, *extra]
    try:
        done = runner(argv, input=REMOTE_SCRIPT.read_text(encoding="utf-8"), capture_output=True,
                      text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"error": "timeout", "detail": ""}
    except OSError as exc:  # no ssh binary
        return {"error": "unreachable", "detail": str(exc)}
    return {"rc": done.returncode, "stdout": done.stdout or "", "stderr": done.stderr or ""}


def run_remote(action: str, conf: Dict[str, str], runner=subprocess.run) -> Dict[str, Any]:
    """Run remote.sh with ``action`` on the configured host. ``runner`` is subprocess.run (tests swap it)."""
    base = {"host": conf["ssh_host"], "session": conf["tmux_session"]}
    got = _ssh(action, conf, runner)
    if "rc" not in got:
        return {**base, "listening": None, "wake_enabled": None, **got}
    return {**base, **state_from(got["rc"], got["stdout"], got["stderr"])}


def music_from(rc: int, stdout: str, stderr: str) -> Dict[str, Any]:
    """Normalise one ssh run of the music half: its last stdout line is the status JSON."""
    if rc == 255:  # ssh's own failure: connect, auth, host key
        return {"state": None, "error": "unreachable", "detail": stderr.strip()[-300:]}
    last = (stdout.strip().splitlines() or [""])[-1]
    try:
        status = json.loads(last)
    except ValueError:
        status = None
    if not isinstance(status, dict) or "state" not in status:
        # A refused command prints one Russian line (its own message or remote.sh's error=...).
        return {"state": None, "error": "music_failed" if rc else "bad_reply",
                "detail": (last or stderr.strip())[-300:]}
    return {**status, "error": None, "detail": ""}


def run_music(action: str, conf: Dict[str, str], runner=subprocess.run,
              extra: Tuple[str, ...] = ()) -> Dict[str, Any]:
    """Run remote.sh's music half: ``music-status``, or ``music`` with ``extra`` = (cmd, [arg])."""
    got = _ssh(action, conf, runner, extra, MUSIC_TIMEOUT_S)
    if "rc" not in got:
        return {"state": None, **got}
    return music_from(got["rc"], got["stdout"], got["stderr"])


def prompt_text(raw: Any) -> Optional[str]:
    """The prompt as it will be typed: one line, no control characters, not a slash command."""
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text or len(text) > PROMPT_MAX:
        return None
    text = re.sub(r"[\r\n\t]", " ", text)  # a newline would submit the line early
    if text.startswith("/") or _CTRL_RE.search(text):
        return None
    return text


def prompt_from(rc: int, stdout: str, stderr: str) -> Dict[str, Any]:
    """Normalise one ssh run of ``prompt``: ``sent=true``, or the error remote.sh printed."""
    if rc == 255:  # ssh's own failure: connect, auth, host key
        return {"sent": False, "error": "unreachable", "detail": stderr.strip()[-300:]}
    pairs = _parse(stdout)
    error = pairs.get("error") or None
    sent = _flag(pairs.get("sent")) is True and error is None
    if not sent and error is None:
        error = "bad_reply"
    return {"sent": sent, "error": error, "detail": stderr.strip()[-300:] if error == "bad_reply" else ""}


def run_prompt(text: str, conf: Dict[str, str], runner=subprocess.run) -> Dict[str, Any]:
    """Type ``text`` into the CLI's session. It travels base64'd: argv is one shell line."""
    encoded = base64.b64encode(text.encode("utf-8")).decode("ascii")
    got = _ssh("prompt", conf, runner, (encoded,))
    if "rc" not in got:
        return {"sent": False, **got}
    return prompt_from(got["rc"], got["stdout"], got["stderr"])


def _with_message(payload: Dict[str, Any]) -> Dict[str, Any]:
    error = payload.get("error") or ""
    # a failed music command carries the player's own Russian line in `detail`: that is the reason
    if error == "music_failed" and payload.get("detail"):
        payload["message"] = payload["detail"]
    else:
        payload["message"] = ERRORS.get(error, error)
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


@router.get("/music")
async def get_music():
    return _with_message(await asyncio.to_thread(run_music, "music-status", settings()))


@router.post("/music")
async def set_music(payload: Dict[str, Any] = Body(...)):
    cmd, arg = payload.get("cmd"), payload.get("arg")
    if cmd not in MUSIC_CMDS:
        raise HTTPException(status_code=422, detail=f"cmd must be one of: {', '.join(MUSIC_CMDS)}")
    if cmd == "volume":
        if not _VOLUME_RE.match(str(arg)):
            raise HTTPException(status_code=422, detail='volume takes {"arg": "N|+N|-N"}, 0-100')
        extra: Tuple[str, ...] = (cmd, str(arg))
    elif arg is None:
        extra = (cmd,)
    else:
        raise HTTPException(status_code=422, detail=f"{cmd} takes no arg")
    return _with_message(await asyncio.to_thread(run_music, "music", settings(), extra=extra))


@router.post("/prompt")
async def set_prompt(payload: Dict[str, Any] = Body(...)):
    text = prompt_text(payload.get("text"))
    if text is None:
        raise HTTPException(status_code=422,
                            detail=f'body must be {{"text": "..."}}: one line, 1-{PROMPT_MAX} characters, '
                                   'no control characters, not starting with "/"')
    return _with_message(await asyncio.to_thread(run_prompt, text, settings()))

