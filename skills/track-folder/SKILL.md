---
name: track-folder
description: Add the current folder to session memory, so sessions here keep the shared hot.md and session log up to date - or stop tracking it. Use when the user says "track this folder", "remember work in this folder", "add this folder to memory", "отслеживай эту папку", "добавь папку в память", or asks to stop tracking.
argument-hint: "[off]"
allowed-tools: Bash(~/.session-protocol/session-protocol:*)
---

Arguments: `$ARGUMENTS`

- To stop tracking (the user said "off", "stop", "untrack" or similar), run
  `~/.session-protocol/session-protocol watch remove .` and confirm in one line.
- Otherwise run `~/.session-protocol/session-protocol watch add .`

(If that path does not exist yet, the plugin hooks have not run: start a new session and retry.)

After adding, the command prints the memory folder. Read `<memory folder>/_meta/hot.md` now,
then tell the user in two or three lines:
- this folder (and everything inside it) is now tracked; the memory lives in `<memory folder>`;
- this session counts too: say "end session" to record it, and if they forget, it is recorded
  in the background;
- `watch` in the same command lists all tracked folders.

From now on in this session, follow the End-of-session protocol in `<memory folder>/AGENTS.md`
and write the memory files there, not in this folder.
