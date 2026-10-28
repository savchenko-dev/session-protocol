# session-protocol

Session memory for **Claude Code** and **Codex** that writes itself.

You pick the folders you work in. Every session started in one of them reads one shared
memory at the start and updates it at the end:

```text
~/agent-memory/
├── AGENTS.md                  the protocol: what to do at the end of a session
└── _meta/
    ├── hot.md                 open threads across all your folders - what's in flight,
    │                          what was decided, what's next. Read first by every session.
    └── sessions/YYYY-MM-DD.md what each session did that day
```

The weak spot of any "write it down at the end" routine is the end: you close the terminal
and forget. This plugin notices that and **records the session for you in the background**.

## Install

**Claude Code**

```text
/plugin install session-protocol --marketplace savchenko-dev/session-protocol
```

<details>
<summary>Older Claude Code, or from a terminal</summary>

```bash
claude plugin marketplace add savchenko-dev/session-protocol
claude plugin install session-protocol@savchenko-dev
```

</details>

**Codex**

```bash
codex plugin marketplace add savchenko-dev/session-protocol
codex plugin add session-protocol@savchenko-dev
```

Then run `codex`, open `/hooks`, review and trust the plugin's hooks.

**Both:** restart the agent once so the hooks load. Then, in a folder you want remembered,
say **"track this folder"** (Claude Code: `/session-protocol:track-folder`). That's it: this
session and every later one started in that folder or below it now use the shared memory.
Sessions anywhere else are left alone.

## Use

Work as usual. When you are done, say **"end session"**. Forgot? Close the terminal anyway -
the session gets recorded in the background, and you get a notification.

| Skill | When |
|---|---|
| `track-folder` | add the current folder to memory; `off` removes it |
| `end-session` | wrap up now (same as saying "end session") |
| `skip-session` | this session is not worth recording - a quick question, an experiment |
| `sessions` | list sessions that were not recorded; record or dismiss them |
| `hot-actualize` | sync hot.md with live issue / PR / git state, drop finished threads |

In Claude Code they are `/session-protocol:<skill>`; in Codex, ask for them by name or pick
them from the skills list. You rarely need `sessions`: when a session was left unrecorded, the
next session in a tracked folder tells you in one line.

The same commands work in a terminal:

```bash
~/.session-protocol/session-protocol watch                 # tracked folders + memory path
~/.session-protocol/session-protocol watch add ~/work ~/src
~/.session-protocol/session-protocol watch remove ~/src
~/.session-protocol/session-protocol list                  # unrecorded sessions
~/.session-protocol/session-protocol close <id|all>
~/.session-protocol/session-protocol config
```

## The protocol

The protocol is plain text in `~/agent-memory/AGENTS.md`: language, format, which trackers to
re-read, what goes into hot.md. Edit it freely and keep the heading `End-of-session protocol`.
Both agents get it the same way: when a session starts in a tracked folder, the plugin tells
the agent where the memory is. Your own `CLAUDE.md` / `AGENTS.md` files are not touched.

hot.md keeps only open threads. A finished thread is simply deleted - its history stays in
the session logs.

### A separate memory for one project

If a project should keep its memory inside its own repo instead, run
`~/.session-protocol/session-protocol init` in its root. That puts the protocol into the
project's `AGENTS.md` (and `@AGENTS.md` into its `CLAUDE.md`) and creates `_meta/` there.
A project with its own protocol always uses it, even inside a tracked folder.

## What happens when you forget

1. While you work, the plugin counts your prompts. Writing the session log marks the session
   as recorded.
2. You exit a session in a tracked folder that has 2+ prompts and is not recorded. You get a
   desktop notification, and a background run starts.
3. The background run resumes your session headless and does what "end session" would do:
   - `claude -p --resume <id>` for Claude Code sessions;
   - `codex exec resume <id>` for Codex sessions.

   It re-reads live state, then writes hot.md and the log. A second notification tells you
   the result.
4. If it fails (no network, laptop went to sleep), the next session in a tracked folder
   mentions it, and the `sessions` skill retries.

Desktop apps keep sessions open for days and may never report that a session ended. So when
a new session starts, any session of the same memory that has been idle for `stale_hours`
(6 by default) and is not recorded is treated as ended and recorded the same way.

Sessions resumed later and continued are tracked too: new prompts after a close reopen them.

## Settings

```bash
~/.session-protocol/session-protocol config                 # show all
~/.session-protocol/session-protocol config model haiku     # set one
~/.session-protocol/session-protocol config model           # back to default
```

In Claude Code you can also set them in `/plugin` → session-protocol → configure. Those win.

| Setting | Default | Meaning |
|---|---|---|
| `memory_dir` | `~/agent-memory` | where the shared memory lives - e.g. a folder in your notes vault |
| `auto_close` | `true` | `false` = only notify and remind |
| `stale_hours` | `6` | an idle unrecorded session counts as ended after this many hours (for apps that never report session end) |
| `model` | agent default | model for the background run; a cheap one is usually enough |
| `min_prompts` | `2` | shorter sessions count as trivial |
| `extra_tools` | - | Claude Code: extra allowed tools for the background run, e.g. read-only MCP tools of your issue tracker |
| `add_dirs` | - | other repos your sessions touch, `:`-separated |
| `notify_command` | desktop notification | gets `<title> <body>` as two arguments - Slack, Telegram, ntfy, ... |
| `max_turns` | `80` | Claude Code: turn limit for the background run |
| `marker` | `End-of-session protocol` | text that opts a project in |
| `session_log_pattern` | `_meta/sessions/\d{4}-\d{2}-\d{2}\.md` | regex for the session log path |
| `prompt_file` | built in | your own background prompt; `{date}`, `{reason}`, `{instructions}`, `{root}` are filled in |

## Safety

The background run works on your transcript and on issue / PR text, which can contain
instructions from other people. So it runs locked down.

**Claude Code:** `--restricted --permission-mode dontAsk`
- your settings files and their allow rules are ignored, and `bypassPermissions` is refused;
- edits only inside the memory folder - never in your code; writes to settings and git files
  are refused;
- shell only for read-only commands (`git status/log/diff/show`, `gh pr view/list`,
  `gh issue view/list`, `ls`, `date`), with `git --output` denied.

**Codex:** `--sandbox workspace-write`
- writes only inside the session's folder and the memory folder;
- no network, so it cannot push or post (it also cannot re-read PRs; it marks them "not
  re-checked").

Both are told not to post, commit or push. Only one background run goes at a time. In Claude
Code, `extra_tools` and `add_dirs` widen what the run can do, so add only read-only tools there.

Each background run is a real agent run and costs tokens - a few cents with a small model.

State and the log live in `~/.session-protocol/` (a session's topic is the first 70 characters
of its first prompt). Old entries expire after 14 days.

## Drift report (Claude Code, optional)

hot.md goes stale when work moves on outside the agent: a PR gets merged, someone closes an
issue. [`scripts/hot-drift-report.sh`](scripts/hot-drift-report.sh) is a read-only headless
check for cron or launchd. It writes `_meta/hot-drift/YYYY-MM-DD.md` with what disagrees with
reality:

```bash
curl -fsSLo ~/bin/hot-drift-report.sh \
  https://raw.githubusercontent.com/savchenko-dev/session-protocol/main/scripts/hot-drift-report.sh
chmod +x ~/bin/hot-drift-report.sh
hot-drift-report.sh ~/notes            # skips if the last run was < 13 days ago
```

Schedule it weekly: the 13-day stamp makes it fortnightly and catches up after sleep. Fold the
result in with `hot-actualize`. `HOT_DRIFT_NOTIFY_CMD` gets the report on stdin, and
`HOT_DRIFT_TOOLS` adds your tracker's read-only tools.

## Desktop apps

- **Claude Desktop, Code tab** runs Claude Code, so the plugin works there: install it with
  the same `/plugin` command in the Code tab. The plain chat tab has no plugins or hooks.
- **Codex desktop app** picks up plugins installed with `codex plugin add`; restart the app
  after installing.
- The background run needs a CLI: `claude` with `--restricted` (Claude Desktop ships an older
  copy without it, which is not used) or `codex`. If none is found, nothing runs unrestricted -
  the next session tells you, and `npm i -g @anthropic-ai/claude-code` / `@openai/codex` fixes it.

## Requirements

macOS or Linux, Python 3.8+, and a recent Claude Code (the background run uses
`--restricted`) or Codex CLI.

## License

MIT
