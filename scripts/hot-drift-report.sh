#!/usr/bin/env bash
# Scheduled drift check for a project's _meta/hot.md.
#
# Read-only by design: the headless Claude run is --restricted (your settings files and
# their allow rules are ignored) and only gets Read/Glob/Grep plus read-only git/gh. It prints a delta report; this script saves it to
# _meta/hot-drift/YYYY-MM-DD.md and optionally pipes it to a notifier.
# Folding the drift back into hot.md stays a manual `hot-actualize` run.
#
# Usage: hot-drift-report.sh <project-dir> [--force]
#
# Env:
#   HOT_DRIFT_MIN_DAYS    skip if the last run was fewer days ago (default 13)
#   HOT_DRIFT_TOOLS       --allowedTools for the run
#                         (default: Read,Grep,Glob plus read-only git and gh)
#   HOT_DRIFT_NOTIFY_CMD  command that gets the report on stdin, e.g. a Slack/Telegram sender

set -uo pipefail

PROJECT="${1:?usage: hot-drift-report.sh <project-dir> [--force]}"
FORCE="${2:-}"
CFG="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
STAMP="$CFG/state/hot-drift-$(printf '%s' "$PROJECT" | cksum | cut -d' ' -f1)"
OUTDIR="$PROJECT/_meta/hot-drift"
MIN_DAYS="${HOT_DRIFT_MIN_DAYS:-13}"
TOOLS="${HOT_DRIFT_TOOLS:-Read,Grep,Glob,Bash(git status:*),Bash(git log:*),Bash(git branch --show-current),Bash(git branch -vv),Bash(gh pr view:*),Bash(gh pr list:*),Bash(gh issue view:*),Bash(gh issue list:*)}"
TODAY="$(date +%Y-%m-%d)"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

notify() {
  [ -n "${HOT_DRIFT_NOTIFY_CMD:-}" ] || return 0
  printf '%s\n' "$1" | sh -c "$HOT_DRIFT_NOTIFY_CMD" || log "notify failed"
}

if [ "$FORCE" != "--force" ] && [ -f "$STAMP" ]; then
  last="$(cat "$STAMP" 2>/dev/null || echo 0)"
  age_days=$(( ( $(date +%s) - last ) / 86400 ))
  if [ "$age_days" -lt "$MIN_DAYS" ]; then
    log "skip: last run ${age_days}d ago (< ${MIN_DAYS}d)"
    exit 0
  fi
fi

[ -f "$PROJECT/_meta/hot.md" ] || { log "FATAL: no _meta/hot.md in $PROJECT"; exit 1; }
command -v claude >/dev/null || { log "FATAL: claude not on PATH"; exit 1; }
mkdir -p "$OUTDIR" "$(dirname "$STAMP")"

read -r -d '' PROMPT <<'EOF'
You are running headless and unattended for a scheduled drift check. You have NO write
tools. Do not try to modify, create or delete any file. Print a report and nothing else.

Task: compare the claims in _meta/hot.md with live state.

1. Read _meta/hot.md. Collect every issue key, PR number, branch and repo path it
   mentions, plus the status hot.md claims for each.
2. Read live state: issues and PRs in bulk where possible; for each repo,
   `git status --short --branch` and `git log -1 --oneline`. Never infer commit or push
   state from anything else.

Then output EXACTLY this structure, terse, no preamble:

## SUMMARY
One line: N status drifts, N finished threads still in hot, N repos with unsaved work.

## STATUS DRIFT
`KEY  hot says X -> live is Y`, one per line. Omit the section if none.

## FINISHED, STILL IN HOT
Closed issues and merged PRs that still sit in hot.md.

## UNSAVED WORK
Repos with uncommitted or unpushed changes - flag loudly.

## STALE CLAIMS
Concrete claims in hot.md that live state contradicts.

If you cannot read live state at all, print exactly "LIVE STATE UNAVAILABLE" as the
first line and stop - do not guess.
EOF

log "running headless drift check for $PROJECT"
TIMEOUT=""; command -v timeout >/dev/null && TIMEOUT="timeout 900"
ERR="$(mktemp)"; trap 'rm -f "$ERR"' EXIT
REPORT="$(cd "$PROJECT" && $TIMEOUT claude -p "$PROMPT" --restricted --tools "Read,Glob,Grep,Bash" \
  --permission-mode dontAsk --allowedTools "$TOOLS" 2>"$ERR")"
rc=$?

fail() {
  log "FAILED: $1"
  notify "hot-drift: run failed ($1) for $PROJECT. Stamp not advanced, will retry next time."
  exit 1
}

[ $rc -ne 0 ] && { cat "$ERR"; fail "claude exit $rc"; }
[ -z "$(printf '%s' "$REPORT" | tr -d '[:space:]')" ] && fail "empty report"
printf '%s' "$REPORT" | head -1 | grep -q 'LIVE STATE UNAVAILABLE' && fail "live state unavailable"

OUT="$OUTDIR/$TODAY.md"
{
  echo "---"
  echo "title: Hot drift $TODAY"
  echo "created: $TODAY"
  echo "---"
  echo
  echo "# Hot drift $TODAY"
  echo
  echo "Automatic check of [[../hot]] against live state. Read-only: hot.md was not changed."
  echo "Fold it in with the \`hot-actualize\` skill."
  echo
  printf '%s\n' "$REPORT"
} > "$OUT"
log "report written: $OUT"

notify "$(printf 'hot.md drifted from reality (%s)\n\n%s' "$PROJECT" "$REPORT")"
date +%s > "$STAMP"
log "done"
