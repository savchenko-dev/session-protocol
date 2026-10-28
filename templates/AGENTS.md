## Session memory

Before doing anything in this project, read `_meta/hot.md` - what we worked on recently.

## End-of-session protocol

Before we end any working session (when I say "end session", "wrap up", or similar), you MUST:

1. **Refresh live state for anything we touched.** Re-read the current state of every issue,
   PR and branch worked on this session, so the summaries reflect reality, not mid-session
   assumptions:
   - **Issues / PRs** - re-read status, reviewers, open threads (e.g. `gh issue view`, `gh pr view`).
   - **Branches** - `git status` and `git log -1` for the real HEAD and whether the tree is
     committed and pushed. Never infer commit or push state from your own edits: I may have
     committed or reset things myself while you kept working.
   - If live state contradicts what was said earlier in the session, live state wins - flag the delta.
2. **Update `_meta/hot.md`.** Add a block for this session right under the intro (never above
   the frontmatter):
   - What we worked on
   - Open threads / decisions made
   - What's next
   Move finished threads out of hot.md (to `_meta/shipped-log.md`, one line each).
3. **Append to `_meta/sessions/YYYY-MM-DD.md`** - a new `# Session N - <topic>` section in this format:

   ```markdown
   # Session N - <topic>

   ## What we did
   - <bullets>

   ## Decisions
   - <decision>: <reasoning>

   ## Files touched
   - <file> - <what changed>

   ## Open threads
   - <thing>

   ## Next time
   - <thing>
   ```

4. Confirm to me that hot.md and the session log are written.
