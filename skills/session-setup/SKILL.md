---
name: session-setup
description: Set up session memory in the current project - the End-of-session protocol in AGENTS.md (imported by CLAUDE.md), plus _meta/hot.md, _meta/shipped-log.md and _meta/sessions/. Use when the user asks to set up or enable session memory / the session protocol.
allowed-tools: Bash(~/.session-protocol/session-protocol:*)
---

Run in the project root:

```bash
~/.session-protocol/session-protocol init .
```

(If that path does not exist yet, the plugin hooks have not run: start a new session and retry.)

Then tell the user in two or three lines what was created and that from now on:
- they say "end session" to wrap up (or use the `end-session` skill);
- if they forget, the session is recorded automatically in the background;
- the protocol is plain text in `AGENTS.md` - edit it freely (language, file layout, which
  trackers to re-read), keeping the heading `End-of-session protocol`.
