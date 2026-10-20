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
