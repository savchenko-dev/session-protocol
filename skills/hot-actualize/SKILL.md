---
name: hot-actualize
description: Sync the project's _meta/hot.md with live state (issue and PR statuses, git branches), move finished threads to _meta/shipped-log.md. Use when the user says "actualize hot", "sync hot", "update hot with real state", "clean up hot", or hands you a drift report from scripts/hot-drift-report.sh.
---

# hot-actualize

Refresh `_meta/hot.md` so every status in it reflects **live** reality instead of what was
assumed mid-session, and move closed threads to `_meta/shipped-log.md`. This is the
on-demand cousin of the End-of-session protocol in `AGENTS.md`.

## Steps

1. **Back up first.** Copy `_meta/hot.md` and `_meta/shipped-log.md` to a dated backup
   (the notes folder is often not under version control).
2. **Harvest references.** From hot.md collect every issue key, PR number, branch and
   worktree path it mentions, plus the status hot.md claims for each.
3. **Read live state in bulk.**
   - Issues and PRs: one listing call where possible (`gh issue list --search ...`,
     `gh pr list --state all --search ...`, or your tracker's bulk search) instead of N
     single reads.
   - Branches: `git -C <repo> status --short --branch` and `git -C <repo> log -1 --oneline`.
     This is the only source of truth for commit and push state - never infer it from edits
     made in the session.
   - If a source is unavailable (no auth, no network), keep the old fact and mark it
     "not re-checked" - never invent a status.
4. **Edit hot.md.**
   - Closed / merged threads: move to shipped-log (`## YYYY-MM`, newest first, one line:
     key + what shipped + link to the session log) and remove them from hot.md.
   - Open threads: update the status and who the ball is on. Keep the detail.
   - Fix stale claims that live state contradicts (a branch said to be dirty that is clean,
     a path that no longer exists).
   - Bump `updated:` in the frontmatter of both files.
5. **Verify.** Frontmatter is still on line 1, no orphaned bullets or double blank lines,
   file ends with a newline.

## Pitfalls

- hot.md can grow large - do not read it whole if it is hundreds of KB; map blocks by
  their `## ` headings and edit with a script.
- New session blocks go right under the `# Hot` intro, never above the frontmatter.
- This pass is read-only towards issue trackers and code hosts. If a status should change
  there, ask the user.
