## Standing constraints for a research card (read first)

- Workspace: `{{WORKDIR}}`. You **write one new file** (named in the task) and
  nothing else. No commits, no edits to existing files, no dependency
  installs.
- Do the work yourself with read, search, terminal and web tools. Do **not**
  launch codex, claude or any other agent CLI.
- Every claim cites a file path with line, a command with its output, or a
  URL. A claim you could not verify is written as `UNVERIFIED: <why>`, never as
  fact.
- Never call an endpoint with a real-world side effect (sending an SMS, a
  message to a person, a payment) unless the task says so. Never write a
  token, password, phone number or one-time code into the output.
- Then append a `## Answer` section to the ticket file named in the task (a
  short gist plus the path of your file) and set its `Status:` line to
  `resolved`. That ticket edit is the one allowed edit to an existing file.
- A question only the orchestrator can settle (scope, which reading of the
  ticket): `kanban_comment(task_id=$HERMES_KANBAN_TASK, body="<question + your
  default>", await_reply_minutes=10)` and wait; the reply comes back in
  `replies`, empty means proceed on the default and say so in the summary.
- Obstacle: `kanban_block`, reason starting with the action and the path.
- Finish with `kanban_complete`; the summary lists the file written, its
  section headings, and the `UNVERIFIED` items.

## Task
