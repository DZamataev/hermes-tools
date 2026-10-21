#!/usr/bin/env python3
"""Backend checks: settings, argv, and every failure class of an ssh run, with ssh faked.

Run via ``tests/run.sh``.
"""
from __future__ import annotations

import importlib.util
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
HERMES = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "/Users/frenzy/.hermes/hermes-agent")
sys.path.insert(0, str(HERMES))

spec = importlib.util.spec_from_file_location("vc_api", ROOT / "dashboard" / "plugin_api.py")
vc = importlib.util.module_from_spec(spec)
sys.modules["vc_api"] = vc
spec.loader.exec_module(vc)

failures: list[str] = []
checks = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global checks
    checks += 1
    if not condition:
        failures.append(f"{name}{f' — {detail}' if detail else ''}")


class FakeSsh:
    """subprocess.run stand-in: records the call, returns a canned result or raises."""

    def __init__(self, rc=0, stdout="", stderr="", raises=None):
        self.rc, self.stdout, self.stderr, self.raises = rc, stdout, stderr, raises
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        if self.raises:
            raise self.raises
        return subprocess.CompletedProcess(argv, self.rc, self.stdout, self.stderr)


CONF = {"ssh_host": "ct125-voice", "tmux_session": "voice"}

# --- the ssh call itself ----------------------------------------------------
ssh = FakeSsh(stdout="listening=true\nwake_enabled=true\n")
out = vc.run_remote("status", CONF, runner=ssh)
argv, kwargs = ssh.calls[0]
check("argv-batchmode-and-host-after-double-dash",
      argv[:6] == ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "--"] and argv[6] == "ct125-voice",
      repr(argv))
check("argv-remote-command", argv[7:] == ["bash", "-s", "--", "status", "voice"], repr(argv))
check("script-goes-over-stdin", kwargs.get("input") == (ROOT / "dashboard" / "remote.sh").read_text())
check("timeout-is-bounded", kwargs.get("timeout") == vc.TIMEOUT_S)
check("status-listening", out["listening"] is True and out["wake_enabled"] is True and out["error"] is None, repr(out))
check("payload-names-host-and-session", out["host"] == "ct125-voice" and out["session"] == "voice")

out = vc.run_remote("off", CONF, runner=FakeSsh(stdout="listening=false\nwake_enabled=false\n"))
check("off-reported", out["listening"] is False and out["wake_enabled"] is False and out["error"] is None, repr(out))

# --- failure classes: each one a labelled payload, never an exception ---------
out = vc.run_remote("status", CONF, runner=FakeSsh(rc=255, stderr="ssh: connect to host x: Connection refused\n"))
check("ssh-failure-is-unreachable", out["error"] == "unreachable" and out["listening"] is None, repr(out))
check("ssh-failure-keeps-stderr", "Connection refused" in out["detail"])

out = vc.run_remote("on", CONF, runner=FakeSsh(raises=subprocess.TimeoutExpired("ssh", 60)))
check("timeout-labelled", out["error"] == "timeout" and out["listening"] is None, repr(out))

out = vc.run_remote("status", CONF, runner=FakeSsh(raises=FileNotFoundError("ssh")))
check("missing-ssh-binary-is-unreachable", out["error"] == "unreachable", repr(out))

out = vc.run_remote("status", CONF, runner=FakeSsh(stdout="error=no_session\nlistening=false\nwake_enabled=true\n"))
check("no-session-passes-through", out["error"] == "no_session" and out["listening"] is False, repr(out))

out = vc.run_remote("on", CONF, runner=FakeSsh(stdout="error=busy\nlistening=true\nwake_enabled=true\n"))
check("busy-passes-through-with-state", out["error"] == "busy" and out["listening"] is True, repr(out))

out = vc.run_remote("status", CONF, runner=FakeSsh(rc=0, stdout="bash: tmux: command not found\n", stderr="oops"))
check("garbage-is-bad-reply", out["error"] == "bad_reply" and out["listening"] is None, repr(out))

# --- music: the same ssh transport, a JSON line instead of key=value ----------
STATUS = ('ничего не играет\n{"state": "stopped", "kind": null, "title": "", "artist": "", "station": "", '
          '"icy_title": "", "pos": 0, "count": 0, "volume": 15}\n')

ssh = FakeSsh(stdout='{"state": "playing", "kind": "jellyfin", "artist": "Кино", "title": "Группа крови", '
                     '"station": "", "icy_title": "", "pos": 2, "count": 9, "volume": 40}\n')
out = vc.run_music("music-status", CONF, runner=ssh)
argv, kwargs = ssh.calls[0]
check("music-argv", argv[7:] == ["bash", "-s", "--", "music-status", "voice"], repr(argv))
check("music-timeout-is-short", kwargs.get("timeout") == vc.MUSIC_TIMEOUT_S and vc.MUSIC_TIMEOUT_S <= 20,
      repr(kwargs.get("timeout")))
check("music-status-parsed", out["state"] == "playing" and out["artist"] == "Кино" and out["volume"] == 40
      and out["error"] is None, repr(out))

# A control command prints the player's own line, then the fresh status: the last line wins.
ssh = FakeSsh(stdout=STATUS)
out = vc.run_music("music", CONF, runner=ssh, extra=("volume", "+10"))
check("music-command-argv", ssh.calls[0][0][7:] == ["bash", "-s", "--", "music", "voice", "volume", "+10"],
      repr(ssh.calls[0][0]))
check("music-command-returns-fresh-status", out["state"] == "stopped" and out["volume"] == 15 and out["error"] is None,
      repr(out))

out = vc.run_music("music-status", CONF, runner=FakeSsh(rc=255, stderr="ssh: connect to host x: Connection refused\n"))
check("music-ssh-failure-is-unreachable", out["error"] == "unreachable" and out["state"] is None, repr(out))
out = vc.run_music("music-status", CONF, runner=FakeSsh(raises=subprocess.TimeoutExpired("ssh", 15)))
check("music-timeout-labelled", out["error"] == "timeout" and out["state"] is None, repr(out))
out = vc.run_music("music", CONF, runner=FakeSsh(rc=1, stdout="error=bad_music\n"), extra=("hold",))
check("music-refusal-is-labelled", out["error"] == "music_failed" and out["detail"] == "error=bad_music", repr(out))
out = vc.run_music("music-status", CONF, runner=FakeSsh(rc=0, stdout="bash: line 1: music: command not found\n", stderr=""))
check("music-garbage-is-bad-reply", out["error"] == "bad_reply" and out["state"] is None, repr(out))
out = vc.run_music("music-status", {"ssh_host": "host; rm -rf ~", "tmux_session": "voice"}, runner=FakeSsh())
check("music-bad-settings", out["error"] == "bad_settings" and out["state"] is None, repr(out))

# --- the routes: a request body never reaches argv unchanged -------------------
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

app = FastAPI()
app.include_router(vc.router)
client = TestClient(app)

calls: list = []
real_run_music = vc.run_music


def fake_run_music(action, conf, runner=subprocess.run, extra=()):
    calls.append((action, extra))
    return {"state": "playing", "kind": "jellyfin", "volume": 40, "error": None}


vc.run_music = fake_run_music  # type: ignore[attr-defined]  # the route looks it up by name
try:
    for bad in [{"cmd": "play"}, {"cmd": "hold"}, {"cmd": "release"}, {"cmd": "volume"}, {"cmd": "volume", "arg": "loud"},
                {"cmd": "volume", "arg": "10; reboot"}, {"cmd": "pause", "arg": "+10"}, {"cmd": None}, {}]:
        calls.clear()
        r = client.post("/music", json=bad)
        check(f"music-route-rejects:{bad!r}", r.status_code == 422 and not calls, r.text)
    check("music-route-volume", client.post("/music", json={"cmd": "volume", "arg": "+10"}).status_code == 200
          and calls[-1] == ("music", ("volume", "+10")), repr(calls))
    check("music-route-volume-int", client.post("/music", json={"cmd": "volume", "arg": 15}).status_code == 200
          and calls[-1] == ("music", ("volume", "15")), repr(calls))
    check("music-route-pause", client.post("/music", json={"cmd": "pause"}).status_code == 200
          and calls[-1] == ("music", ("pause",)), repr(calls))
    check("music-route-status", client.get("/music").status_code == 200 and calls[-1][0] == "music-status", repr(calls))

    # The command must reach remote.sh as $3: a fake run_music cannot show that, ssh can.
    ssh = FakeSsh(stdout='{"state": "playing"}\n')
    vc.run_music = lambda action, conf, extra=(): real_run_music(action, conf, runner=ssh, extra=extra)  # type: ignore[attr-defined]
    check("music-route-toggle-reaches-argv", client.post("/music", json={"cmd": "toggle"}).status_code == 200
          and ssh.calls[0][0][7:] == ["bash", "-s", "--", "music", "voice", "toggle"], repr(ssh.calls))
finally:
    vc.run_music = real_run_music  # type: ignore[attr-defined]

# --- settings are a trust boundary: they reach argv ------------------------
for host in ["-oProxyCommand=touch /tmp/pwned", "host; rm -rf ~", "a b", "", "$(id)"]:
    ssh = FakeSsh(stdout="listening=true\n")
    out = vc.run_remote("status", {"ssh_host": host, "tmux_session": "voice"}, runner=ssh)
    check(f"bad-host-rejected:{host!r}", out["error"] == "bad_settings" and not ssh.calls, repr(out))
ssh = FakeSsh(stdout="listening=true\n")
out = vc.run_remote("status", {"ssh_host": "ct125-voice", "tmux_session": "voice; reboot"}, runner=ssh)
check("bad-session-rejected", out["error"] == "bad_settings" and not ssh.calls, repr(out))
for host in ["ct125-voice", "hermes@10.50.50.140", "box.lan", "user@host:2222"]:
    ssh = FakeSsh(stdout="listening=true\n")
    vc.run_remote("status", {"ssh_host": host, "tmux_session": "voice"}, runner=ssh)
    check(f"good-host-accepted:{host}", len(ssh.calls) == 1)

# --- settings read from config.yaml, defaults for blanks ---------------------
import hermes_cli.config as hc  # noqa: E402

real = hc.load_config_readonly
try:
    hc.load_config_readonly = lambda: {"plugins": {"entries": {"voice-control": {"settings": {"ssh_host": "kitchen"}}}}}
    check("setting-overrides-default", vc.settings() == {"ssh_host": "kitchen", "tmux_session": "voice"}, repr(vc.settings()))
    hc.load_config_readonly = lambda: {"plugins": {"entries": {"voice-control": {"settings": {"ssh_host": "  "}}}}}
    check("blank-setting-falls-back", vc.settings()["ssh_host"] == "ct125-voice", repr(vc.settings()))
    hc.load_config_readonly = lambda: {}
    check("no-entry-uses-defaults", vc.settings() == vc.DEFAULTS)

    def broken():
        raise OSError("unreadable")
    hc.load_config_readonly = broken
    check("unreadable-config-uses-defaults", vc.settings() == vc.DEFAULTS)
finally:
    hc.load_config_readonly = real

# --- the manifest declares the same settings the backend reads ----------------
import yaml  # noqa: E402

schema = yaml.safe_load((ROOT / "plugin.yaml").read_text())["config_schema"]
check("schema-matches-defaults", {k: v["default"] for k, v in schema.items()} == vc.DEFAULTS, repr(schema))

if failures:
    print(f"backend: {len(failures)} of {checks} checks FAILED")
    for f in failures:
        print("  ✗", f)
    sys.exit(1)
print(f"backend: {checks} checks passed")
