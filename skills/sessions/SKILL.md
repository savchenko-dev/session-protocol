---
name: sessions
description: Show sessions that ended without a session log, and record or dismiss them. Use when the user asks about unrecorded, unclosed or forgotten sessions.
argument-hint: "[close <id>|close all|clear <id>|clear all]"
allowed-tools: Bash(~/.session-protocol/session-protocol:*)
---

Run `~/.session-protocol/session-protocol list` and show the result as a short table (id, date, topic, status).
Nothing there: say so in one line.

If the user asked for an action (arguments: `$ARGUMENTS`), or once they pick one:

- record: `~/.session-protocol/session-protocol close <id|all>` - starts the background run and returns at once; the user gets
  a notification when it is done (1-5 minutes per session);
- dismiss without recording: `~/.session-protocol/session-protocol clear <id|all>`.

Ids can be the 8-character short id from the list.
