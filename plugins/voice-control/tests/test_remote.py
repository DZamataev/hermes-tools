#!/usr/bin/env python3
"""remote.sh itself, run locally against a stub `tmux`: what `prompt` types, what it refuses.

The real tmux is never involved. A temp dir with an executable `tmux` comes first on PATH; it
records every argv and answers has-session / list-panes / capture-pane. ``TMUX_BUSY`` makes the
first N capture-pane calls report a running turn.

Run via ``tests/run.sh``.
"""
from __future__ import annotations

import base64
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "dashboard" / "remote.sh"

STUB = r"""#!/bin/bash
printf '%s\n' "$*" >> "$TMUX_LOG"
case $1 in
  has-session) exit 0 ;;
  list-panes) echo $$ ;;
  capture-pane)
    n=$(cat "$TMUX_COUNT" 2>/dev/null || echo 0); n=$((n + 1)); echo "$n" > "$TMUX_COUNT"
    if [ "$n" -le "${TMUX_BUSY:-0}" ]; then echo '❯ msg=queue'; else echo "${TMUX_LINE:-❯ }"; fi ;;
  send-keys) exit 0 ;;
esac
"""

failures: list[str] = []
checks = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global checks
    checks += 1
    if not condition:
        failures.append(f"{name}{f' — {detail}' if detail else ''}")


def b64(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def run(args: list[str], busy: int = 0, line: str = ""):
    """remote.sh with the stub tmux: (rc, stdout, recorded tmux calls)."""
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="vc-remote-"))
    (tmp / "bin").mkdir()
    stub = tmp / "bin" / "tmux"
    stub.write_text(STUB)
    stub.chmod(0o755)
    log = tmp / "calls"
    log.write_text("")
    env = {**os.environ, "PATH": f"{tmp / 'bin'}:{os.environ['PATH']}", "HOME": str(tmp),
           "TMUX_LOG": str(log), "TMUX_COUNT": str(tmp / "count"), "TMUX_BUSY": str(busy),
           "TMUX_LINE": line}
    try:
        done = subprocess.run(["bash", str(SCRIPT), *args], capture_output=True, text=True, env=env, timeout=90)
        return done.returncode, done.stdout, log.read_text().splitlines()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def typed(calls: list[str]) -> list[str]:
    """The arguments of every `send-keys -l --`: what would be typed into the CLI."""
    out = []
    for call in calls:
        head, sep, text = call.partition(" -l -- ")
        if call.startswith("send-keys") and sep:
            out.append(text)
    return out


TEXT = "скажи привет, как дела? $(reboot) 'quoted'"
rc, out, calls = run(["prompt", "voice", b64(TEXT)])
check("prompt-reports-sent", rc == 0 and out.strip().splitlines()[-1] == "sent=true", repr((rc, out)))
check("prompt-typed-literally", typed(calls) == [TEXT], repr(calls))
sends = [i for i, call in enumerate(calls) if call.startswith("send-keys")]
check("prompt-clears-the-input-first", bool(sends) and calls[sends[0]].endswith("C-e C-u"), repr(calls))
check("prompt-presses-enter-once", sum(1 for call in calls if call.endswith("Enter")) == 1, repr(calls))
check("prompt-checks-the-session", calls[:1] == ["has-session -t voice"], repr(calls))

# A leading "-" reads as a flag for tmux unless "--" ends the options: it must still be typed.
DASH = "- что дальше"
rc, out, calls = run(["prompt", "voice", b64(DASH)])
check("prompt-typed-a-leading-dash", rc == 0 and out.strip().splitlines()[-1] == "sent=true"
      and typed(calls) == [DASH], repr((rc, out, calls)))

# A long prompt wraps, so the first visual line holds only its beginning: the completion
# menu (which ate the first Enter) is spotted from that prefix, not from the whole text.
LONG = "aaaaaaaaaaaaaaaaaaaaaaaa " + "почему " * 40
rc, out, calls = run(["prompt", "voice", b64(LONG)], line="❯ " + LONG[:24])
check("prompt-second-enter-on-a-wrapped-line", rc == 0
      and sum(1 for call in calls if call.endswith("Enter")) == 2, repr(calls))

# A turn in flight: nothing is typed until the prompt shows plain again.
rc, out, calls = run(["prompt", "voice", b64(TEXT)], busy=1)
check("prompt-waits-out-a-running-turn", rc == 0 and out.strip().splitlines()[-1] == "sent=true"
      and typed(calls) == [TEXT], repr((rc, out, calls)))
first_type = next((i for i, call in enumerate(calls) if call.startswith("send-keys")), 0)
check("prompt-typed-after-the-turn", sum(1 for call in calls[:first_type] if call.startswith("capture-pane")) >= 2,
      repr(calls[:first_type]))

# What the backend never sends but a stray caller could: the script refuses to type it.
# (A NUL byte is not here: bash cannot carry one, and the backend answers 422 for it.)
for bad, why in [("/wake off", "a slash command would run and the mic switch owns those"),
                 ("  /wake off", "leading whitespace does not hide a slash command"),
                 ("!ls", "a leading bang runs a shell command in the assistant's CLI"),
                 ("  !ls", "leading whitespace does not hide a bang command"),
                 ("a\nb", "a newline submits the line early"),
                 ("a\rb", "a CR submits the line early"),
                 ("a\x1bb", "an escape sequence moves the cursor")]:
    rc, out, calls = run(["prompt", "voice", b64(bad)])
    check(f"prompt-refused-when:{why}", rc == 2 and "error=bad_prompt" in out
          and not typed(calls) and not any(c.startswith("send-keys") for c in calls),
          repr((rc, out, calls)))

for bad in ["c2th0L3Q7IHJlYm9vdA==;reboot", "not base64!", "--", ""]:
    rc, out, calls = run(["prompt", "voice", bad])
    check(f"prompt-refused-b64:{bad!r}", rc == 2 and "error=bad_prompt" in out
          and not any(c.startswith("send-keys") for c in calls), repr((rc, out, calls)))

if failures:
    print(f"remote: {len(failures)} of {checks} checks FAILED")
    for f in failures:
        print("  ✗", f)
    sys.exit(1)
print(f"remote: {checks} checks passed")
