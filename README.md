# session-protocol

Session memory for **Claude Code** and **Codex** that writes itself.

At the end of a working session the agent updates two files in your project:

- `_meta/hot.md` - the open threads: what is in flight, what was decided, what's next.
  The next session reads it first and picks up where you left off.
- `_meta/sessions/YYYY-MM-DD.md` - a log of what each session did.

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

**Both:** restart the agent once so the hooks load. Then, in each project you want a memory
for, ask it to **set up session memory** (Claude Code: `/session-protocol:session-setup`).

## Use

Work as usual. When you are done, say **"end session"**. Forgot? Close the terminal anyway -
the session gets recorded in the background, and you get a notification.

| Skill | When |
|---|---|
| `session-setup` | once per project: protocol in `AGENTS.md`, `CLAUDE.md` imports it, `_meta/` files |
| `end-session` | wrap up now (same as saying "end session") |
| `skip-session` | this session is not worth recording - a quick question, an experiment |
| `sessions` | list sessions that were not recorded; record or dismiss them |
| `hot-actualize` | sync hot.md with live issue / PR / git state, move finished threads to the shipped log |

In Claude Code they are `/session-protocol:<skill>`; in Codex, ask for them by name or pick
them from the skills list. You rarely need `sessions`: when a session was left unrecorded, the
next session in that project tells you in one line.

The same commands work in a terminal:

```bash
~/.session-protocol/session-protocol list
~/.session-protocol/session-protocol close <id|all>
~/.session-protocol/session-protocol config
```

## One protocol for both agents

`session-setup` puts the protocol into `AGENTS.md` (Codex reads it) and adds `@AGENTS.md` to
`CLAUDE.md` (Claude Code reads that). The protocol is plain text: language, file layout, which
trackers to re-read - edit it freely and keep the heading `End-of-session protocol`. That
heading is how the plugin knows a project uses it.

## What happens when you forget

1. While you work, the plugin counts your prompts. Writing the session log marks the session
   as recorded.
2. You exit a session that has the protocol, has 2+ prompts and is not recorded. You get a
   desktop notification, and a background run starts.
3. The background run resumes your session headless and does what "end session" would do:
   - `claude -p --resume <id>` for Claude Code sessions;
   - `codex exec resume <id>` for Codex sessions.

   It re-reads live state, then writes hot.md and the log. A second notification tells you
   the result.
4. If it fails (no network, laptop went to sleep), the next session in the project mentions
   it, and the `sessions` skill retries.

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
| `auto_close` | `true` | `false` = only notify and remind |
| `model` | agent default | model for the background run; a cheap one is usually enough |
| `min_prompts` | `2` | shorter sessions count as trivial |
| `extra_tools` | - | Claude Code: extra allowed tools for the background run, e.g. read-only MCP tools of your issue tracker |
| `add_dirs` | - | other repos your sessions touch, `:`-separated |
| `notify_command` | desktop notification | gets `<title> <body>` as two arguments - Slack, Telegram, ntfy, ... |
| `max_turns` | `80` | Claude Code: turn limit for the background run |
| `marker` | `End-of-session protocol` | text that opts a project in |
| `session_log_pattern` | `_meta/sessions/\d{4}-\d{2}-\d{2}\.md` | regex for the session log path |
| `prompt_file` | built in | your own background prompt; `{date}`, `{reason}`, `{instructions}` are filled in |

## Safety

The background run works on your transcript and on issue / PR text, which can contain
instructions from other people. So it runs locked down.

**Claude Code:** `--restricted --permission-mode dontAsk`
- your settings files and their allow rules are ignored, and `bypassPermissions` is refused;
- edits only inside the project; writes to settings and git files are refused;
- shell only for read-only commands (`git status/log/diff/show`, `gh pr view/list`,
  `gh issue view/list`, `ls`, `date`), with `git --output` denied.

**Codex:** `--sandbox workspace-write`
- writes only inside the project;
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

## Requirements

macOS or Linux, Python 3.8+, and a recent Claude Code (the background run uses
`--restricted`) or Codex CLI.

## License

MIT
