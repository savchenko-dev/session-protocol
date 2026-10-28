#!/usr/bin/env python3
"""End-of-session protocol guard for Claude Code and Codex, driven by explicit per-session
flags. It never parses transcripts.

A project opts in by having the marker text (default "End-of-session protocol") in its
AGENTS.md or CLAUDE.md. The protocol counts as done once the session writes its session log
(default: _meta/sessions/YYYY-MM-DD.md).

State: ~/.session-protocol/ (or $SESSION_PROTOCOL_HOME)
  config.json                 settings for every harness (`session-protocol config`)
  session-protocol            symlink to this script - a stable path for skills and terminals
  sessions/<session_id>/
    agent, prompts, topic, started, last, cwd   written by the prompt hook
    closed         {"at", "prompts"}: protocol ran at `prompts` prompts; later prompts reopen
    skip           never auto-close this session
    unclosed.json  auto-close bookkeeping: status pending | running | failed, pid, error

Hooks (hooks/claude-hooks.json, hooks/codex-hooks.json):
  hook <event> --agent claude|codex
    prompt         count prompts, remember topic and cwd
    written        the session log was written -> closed
    session-end    working session ended unclosed -> detached worker resumes it headless
                   (`claude -p --resume` / `codex exec resume`) and runs the protocol
    session-start  tell the new session about sessions that are still unclosed

Workers are locked down. Claude: `--restricted --permission-mode dontAsk`, edits only inside
the project, Bash only for a read-only allowlist. Codex: `--sandbox workspace-write`
(writes only inside the project, no network) and no approvals.
"""

import argparse
import datetime
import fcntl
import glob
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.realpath(__file__))
TEMPLATES = os.path.join(os.path.dirname(HERE), "templates")
HOME = os.path.expanduser("~")
BASE = os.environ.get("SESSION_PROTOCOL_HOME") or os.path.join(HOME, ".session-protocol")
LOG = os.path.join(BASE, "session-protocol.log")
STATE_DIR = os.path.join(BASE, "sessions")
CONFIG_FILE = os.path.join(BASE, "config.json")
PLUGIN_OPTIONS_FILE = os.path.join(BASE, "claude-plugin-options.json")
CLI_LINK = os.path.join(BASE, "session-protocol")
LOCK = os.path.join(BASE, "auto-close.lock")
WORKER_ENV = "SESSION_PROTOCOL_WORKER"
OPTION_PREFIX = "CLAUDE_PLUGIN_OPTION_"
DEFAULT_MARKER = "End-of-session protocol"
DEFAULT_LOG_PATTERN = r"_meta/sessions/\d{4}-\d{2}-\d{2}\.md"
INSTRUCTION_FILES = ("AGENTS.md", "CLAUDE.md", os.path.join(".claude", "CLAUDE.md"))
SHELL_TOOLS = ("Bash", "shell", "exec_command", "local_shell")
STATE_TTL_DAYS = 14
WORKER_TIMEOUT_S = 30 * 60
DEFAULTS = {"auto_close": "true", "stale_hours": "6", "model": "", "min_prompts": "2", "extra_tools": "",
            "add_dirs": "", "notify_command": "", "max_turns": "80",
            "marker": DEFAULT_MARKER, "session_log_pattern": DEFAULT_LOG_PATTERN, "prompt_file": ""}
CLAUDE_READ_ONLY_BASH = [
    "Bash(date:*)", "Bash(ls:*)", "Bash(pwd)",
    "Bash(git status:*)", "Bash(git log:*)", "Bash(git diff:*)", "Bash(git show:*)",
    "Bash(git branch --show-current)", "Bash(git branch -vv)", "Bash(git rev-parse:*)",
    "Bash(gh pr view:*)", "Bash(gh pr checks:*)", "Bash(gh pr list:*)",
    "Bash(gh issue view:*)", "Bash(gh issue list:*)",
]
CLAUDE_DENIED = ["Bash(git *--output*)", "Bash(gh * --web*)"]


def load_json(path):
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, path)


def opt(key):
    """Claude plugin settings (env, or their snapshot) win over config.json."""
    value = os.environ.get(OPTION_PREFIX + key.upper())
    if value is None:
        value = load_json(PLUGIN_OPTIONS_FILE).get(key)
    if value in (None, ""):
        value = load_json(CONFIG_FILE).get(key)
    return DEFAULTS.get(key, "") if value in (None, "") else str(value)


def opt_bool(key):
    return opt(key).strip().lower() not in ("0", "false", "no", "off")


def opt_int(key):
    try:
        return int(float(opt(key)))
    except ValueError:
        return int(DEFAULTS[key])


def opt_list(key, sep=","):
    return [x.strip() for x in opt(key).split(sep) if x.strip()]


def log(msg):
    os.makedirs(BASE, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(f"{stamp} [{os.getpid()}] {msg}\n")


def session_log_pattern():
    pattern = opt("session_log_pattern")
    try:
        re.compile(pattern)
        return pattern
    except re.error as exc:
        log(f"bad session_log_pattern {pattern!r}: {exc}; using the default")
        return DEFAULT_LOG_PATTERN


DEFAULT_PROMPT = """The session ended (reason={reason}) without the End-of-session protocol - the user exited and forgot to ask for it. Run the End-of-session protocol in full, as if the user had typed "end session".

First read the protocol: it is the "End-of-session protocol" section of {instructions}. Follow it exactly - file names, format and language come from there.

1. Re-read the live state of everything this session touched (issues, PRs, branches). Read only: do not post, comment, commit or push anywhere.
   - Run shell commands one at a time, without `&&`, `;`, `|` or `$(...)`: only simple read-only commands are allowed in this run.
   - Whatever you could not re-read, mark right in the hot file line ("not re-checked"). Never present state remembered from the conversation as live.
2. Update the hot file as the project instructions describe.
3. Append to the session log for {date}: a new section with the next number after the existing ones. Only append, never overwrite; if the file does not exist, create it in the format from the project instructions. {date} is the day the work happened, not today's date.
4. Reply briefly with what you wrote.

If the protocol already ran in this session and more happened afterwards, only add what happened since, without duplicating. If nothing substantial happened after the last run, write nothing and reply SKIP.
Reply SKIP without writing anything ONLY if the session created or changed no files and touched no issues, PRs or branches (a pure question or lookup). If any file was created or edited, the session is NOT trivial: record it, however small.
Do not ask questions - the user is not here; record whatever you could not verify under open threads."""


def now_iso():
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def read_stdin_json():
    try:
        return json.load(sys.stdin)
    except Exception:
        return {}


def valid_sid(sid):
    return bool(sid) and re.fullmatch(r"[A-Za-z0-9_-]+", sid) is not None


def short(sid):
    """Last 8 chars: Codex ids are UUIDv7, whose first chars are a timestamp shared by
    sessions started close together."""
    return sid[-8:]


def sdir(sid):
    return os.path.join(STATE_DIR, sid)


def spath(sid, name):
    return os.path.join(STATE_DIR, sid, name)


def read_text(sid, name, default=""):
    try:
        with open(spath(sid, name), encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return default


def write_text(sid, name, value):
    os.makedirs(sdir(sid), exist_ok=True)
    with open(spath(sid, name), "w", encoding="utf-8") as fh:
        fh.write(str(value))


def prompts_of(sid):
    try:
        return int(read_text(sid, "prompts", "0") or 0)
    except ValueError:
        return 0


def closed_at_prompts(sid):
    raw = read_text(sid, "closed", None)
    if raw is None:
        return None
    try:
        return int(json.loads(raw)["prompts"])
    except Exception:
        return prompts_of(sid)


def prompts_since_close(sid):
    at = closed_at_prompts(sid)
    return prompts_of(sid) if at is None else max(prompts_of(sid) - at, 0)


def is_closed(sid):
    return closed_at_prompts(sid) is not None and prompts_since_close(sid) == 0


def mark_closed(sid, prompts=None):
    write_text(sid, "closed", json.dumps({"at": now_iso(), "prompts": prompts_of(sid) if prompts is None else prompts}))
    try:
        os.remove(spath(sid, "unclosed.json"))
    except OSError:
        pass


def forget(sid):
    shutil.rmtree(sdir(sid), ignore_errors=True)


def read_unclosed(sid):
    m = load_json(spath(sid, "unclosed.json"))
    return m or None


def write_unclosed(sid, **fields):
    m = read_unclosed(sid) or {"session_id": sid}
    m.update(fields)
    write_text(sid, "unclosed.json", json.dumps(m, ensure_ascii=False, indent=2))
    return m


def session_date(sid):
    started = read_text(sid, "last") or read_text(sid, "started")
    try:
        return datetime.datetime.fromisoformat(started).astimezone().strftime("%Y-%m-%d")
    except Exception:
        return datetime.datetime.now().astimezone().strftime("%Y-%m-%d")


def has_marker(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return opt("marker") in fh.read()
    except OSError:
        return False


def find_protocol_root(cwd):
    cur = os.path.abspath(cwd)
    for _ in range(6):
        if any(has_marker(os.path.join(cur, name)) for name in INSTRUCTION_FILES):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    return None


APP_BUNDLED = {
    "claude": [os.path.join(HOME, "Library", "Application Support", "Claude", "claude-code", "*", "claude.app", "Contents", "MacOS", "claude")],
    "codex": [os.path.join(apps, "Codex.app", "Contents", sub, "codex")
              for apps in ("/Applications", os.path.join(HOME, "Applications"))
              for sub in ("Resources", "MacOS", os.path.join("Resources", "*"))],
}


def binary_candidates(name):
    """CLI on PATH first, then the copies desktop apps ship inside their bundles (GUI apps
    don't hand hooks the shell PATH)."""
    cands = [shutil.which(name), os.path.join(HOME, ".local", "bin", name),
             os.path.join(HOME, ".claude", "local", name), f"/opt/homebrew/bin/{name}", f"/usr/local/bin/{name}"]
    for pattern in APP_BUNDLED.get(name, []):
        cands += sorted(glob.glob(pattern), reverse=True)
    return [c for c in dict.fromkeys(cands) if c and os.access(c, os.X_OK)]


def find_binary(name, required_flag=None):
    for cand in binary_candidates(name):
        if not required_flag:
            return cand
        try:
            help_text = subprocess.run([cand, "--help"], capture_output=True, text=True, timeout=20).stdout
        except Exception:
            continue
        if required_flag in help_text:
            return cand
    return None


def run_quiet(cmd, timeout):
    try:
        subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=timeout)
    except Exception:
        pass


def notify(title, body):
    command = opt("notify_command")
    if command:
        run_quiet(shlex.split(command) + [title, body], 25)
    elif sys.platform == "darwin":
        script = 'on run argv\ndisplay notification (item 2 of argv) with title (item 1 of argv)\nend run'
        run_quiet(["osascript", "-e", script, title, body], 5)
    elif shutil.which("notify-send"):
        run_quiet(["notify-send", title, body], 5)


def spawn(*args):
    """Detached: SessionEnd hooks get 1-3 s, so nothing slow runs inline."""
    os.makedirs(BASE, exist_ok=True)
    out = open(LOG, "a")
    subprocess.Popen([sys.executable, "-I", os.path.realpath(__file__), *args],
                     stdin=subprocess.DEVNULL, stdout=out, stderr=out,
                     start_new_session=True, close_fds=True)


def pid_alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ValueError, TypeError):
        return False


def protocol_files(root):
    return [os.path.join(root, n) for n in INSTRUCTION_FILES if has_marker(os.path.join(root, n))]


def load_prompt(reason, date, root):
    template = DEFAULT_PROMPT
    if opt("prompt_file"):
        with open(os.path.expanduser(opt("prompt_file")), encoding="utf-8") as fh:
            template = fh.read()
    instructions = " and ".join(f"`{f}`" for f in protocol_files(root)) or "the project instructions"
    return template.replace("{reason}", reason).replace("{date}", date).replace("{instructions}", instructions)


def extra_dirs(root, cwd):
    dirs = [root] + [os.path.expanduser(d) for d in opt_list("add_dirs", os.pathsep)]
    return [d for d in dict.fromkeys(dirs) if d and os.path.isdir(d) and os.path.abspath(d) != os.path.abspath(cwd)]


def child_env():
    env = {k: v for k, v in os.environ.items() if k not in ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT")}
    env[WORKER_ENV] = "1"
    return env


def claude_command(sid, cwd, root):
    claude = find_binary("claude", "--restricted")
    if not claude:
        return None
    allowed = ["Read", "Glob", "Grep", f"Edit(/{os.path.abspath(root)}/**)"] + CLAUDE_READ_ONLY_BASH + opt_list("extra_tools")
    cmd = [claude, "-p", "--resume", sid, "--restricted",
           "--tools", "Read,Glob,Grep,Edit,Write,MultiEdit,Bash",
           "--permission-mode", "dontAsk", "--max-turns", str(opt_int("max_turns")),
           "--output-format", "json"]
    if opt("model"):
        cmd += ["--model", opt("model")]
    dirs = extra_dirs(root, cwd)
    if dirs:
        cmd += ["--add-dir", *dirs]
    return cmd + ["--allowedTools", *allowed, "--disallowedTools", *CLAUDE_DENIED]


def codex_command(sid, cwd, root, out_file):
    codex = find_binary("codex")
    if not codex:
        return None
    cmd = [codex, "exec", "--skip-git-repo-check", "--sandbox", "workspace-write",
           "--output-last-message", out_file]
    if opt("model"):
        cmd += ["--model", opt("model")]
    for d in extra_dirs(root, cwd):
        cmd += ["--add-dir", d]
    return cmd + ["resume", sid, "-"]


def run_protocol(agent, sid, cwd, root, reason, date):
    try:
        prompt = load_prompt(reason, date, root)
    except OSError as exc:
        return dict(ok=False, error=f"cannot read prompt_file: {exc}")
    with tempfile.TemporaryDirectory() as tmp:
        out_file = os.path.join(tmp, "last-message.txt")
        cmd = codex_command(sid, cwd, root, out_file) if agent == "codex" else claude_command(sid, cwd, root)
        if not cmd:
            hint = ("no Claude Code CLI with --restricted found - install or update it: "
                    "npm i -g @anthropic-ai/claude-code" if agent == "claude" else
                    "codex CLI not found - install it: npm i -g @openai/codex")
            return dict(ok=False, error=hint)
        try:
            proc = subprocess.run(cmd, cwd=cwd, env=child_env(), input=prompt,
                                  capture_output=True, text=True, timeout=WORKER_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            return dict(ok=False, error=f"{agent} timed out after {WORKER_TIMEOUT_S}s")
        except Exception as exc:
            return dict(ok=False, error=f"{agent} failed to start: {exc}")
        stderr_tail = (proc.stderr or "")[-400:].replace("\n", " ").strip()
        if agent == "codex":
            try:
                with open(out_file, encoding="utf-8") as fh:
                    result = fh.read().strip()
            except OSError:
                result = ""
            if proc.returncode != 0:
                return dict(ok=False, error=f"rc={proc.returncode} {stderr_tail}", result=result)
            return dict(ok=True, result=result)
    data = parse_result_json(proc.stdout.strip())
    if data is None:
        return dict(ok=False, error=f"no result object in output rc={proc.returncode}: {stderr_tail}")
    ok = proc.returncode == 0 and not data.get("is_error", False)
    err = None if ok else f"rc={proc.returncode} subtype={data.get('subtype')} {stderr_tail}".strip()
    return dict(ok=ok, result=str(data.get("result") or "").strip(), turns=data.get("num_turns"),
                cost=data.get("total_cost_usd"), error=err)


def parse_result_json(out):
    """`claude --output-format json` has shipped both a single result object and a list of
    messages ending with the result; accept either."""
    try:
        data = json.loads(out)
    except Exception:
        return None
    if isinstance(data, dict):
        return data
    if isinstance(data, list):
        results = [d for d in data if isinstance(d, dict) and d.get("type") == "result"]
        if results:
            return results[-1]
        dicts = [d for d in data if isinstance(d, dict)]
        return dicts[-1] if dicts else None
    return None


def close_session(sid, reason):
    sid8 = short(sid)
    topic = read_text(sid, "topic")
    agent = read_text(sid, "agent") or "claude"
    date = session_date(sid)
    marker = read_unclosed(sid) or {}
    cwd = read_text(sid, "cwd") or marker.get("cwd") or os.getcwd()
    root = marker.get("root") or find_protocol_root(cwd) or cwd
    if is_closed(sid):
        log(f"worker skip session={sid8}: closed, no prompts since")
        return 0
    write_unclosed(sid, status="running", pid=os.getpid(), started=now_iso())
    os.makedirs(BASE, exist_ok=True)
    with open(LOCK, "w") as lock_fh:
        log(f"worker session={sid8}: waiting for lock")
        fcntl.flock(lock_fh, fcntl.LOCK_EX)
        if is_closed(sid):
            log(f"worker skip session={sid8}: closed while waiting for the lock")
            return 0
        prompts_at_start = prompts_of(sid)
        log(f"worker session={sid8} agent={agent}: running protocol date={date} prompts={prompts_at_start} topic={topic!r}")
        started = time.time()
        try:
            res = run_protocol(agent, sid, cwd, root, reason, date)
        except Exception as exc:
            res = dict(ok=False, error=f"worker exception: {exc!r}")
        fcntl.flock(lock_fh, fcntl.LOCK_UN)
    elapsed = int(time.time() - started)
    result_text = (res.get("result") or "").replace("\n", " ")
    cost = res.get("cost")
    cost_txt = f", ${cost:.2f}" if isinstance(cost, (int, float)) else ""
    if not res.get("ok"):
        log(f"worker FAIL session={sid8}: {res.get('error')} result={result_text[:300]!r}")
        write_unclosed(sid, status="failed", error=res.get("error"), finished=now_iso())
        notify("Session not recorded", f"{sid8} ({topic}) - auto-close failed: {res.get('error') or 'error'}")
        return 1
    skipped = result_text.strip().upper().startswith("SKIP")
    log(f"worker done session={sid8}: {'SKIP' if skipped else 'closed'} turns={res.get('turns')} "
        f"time={elapsed}s{cost_txt} result={result_text[:400]!r}")
    mark_closed(sid, prompts_at_start)
    if skipped:
        notify("Session was trivial", f"{sid8} ({topic}) - nothing to record ({elapsed}s{cost_txt})")
    else:
        notify("Session recorded", f"{sid8} ({topic}) - hot.md and session log written ({elapsed}s{cost_txt})")
    return 0


def tool_command_text(tool_input):
    command = tool_input.get("command")
    if isinstance(command, list):
        return " ".join(str(c) for c in command)
    return str(command or tool_input.get("cmd") or "")


def hook_prompt(data, agent):
    sid = data.get("session_id") or ""
    if not valid_sid(sid):
        return 0
    n = prompts_of(sid) + 1
    write_text(sid, "prompts", n)
    write_text(sid, "last", now_iso())
    if n == 1:
        prompt = re.sub(r"\s+", " ", str(data.get("prompt") or "")).strip()[:70]
        write_text(sid, "agent", agent)
        write_text(sid, "topic", prompt)
        write_text(sid, "started", now_iso())
        write_text(sid, "cwd", data.get("cwd") or os.getcwd())
    return 0


def hook_written(data, agent):
    sid = data.get("session_id") or ""
    tool_input = data.get("tool_input") or {}
    if not valid_sid(sid) or not isinstance(tool_input, dict):
        return 0
    pattern = session_log_pattern()
    text = tool_command_text(tool_input)
    if data.get("tool_name") in SHELL_TOOLS and not text.lstrip().startswith("apply_patch"):
        writes = (r"""(?:>>?\s*|\btee\b(?:\s+-a)?\s+|\bsed\s+-i\b[^|;&]*?)(?:"[^"]*?|'[^']*?|[^\s"'|;&]*?)"""
                  + pattern + r"""(?=["'\s;|&)]|$)""")
        hit = re.search(writes, text) is not None
    else:
        hit = re.search(pattern + r"(?![\w.])", json.dumps(tool_input, ensure_ascii=False)) is not None
    if hit:
        mark_closed(sid)
    return 0


def hook_session_end(data, agent):
    reason = data.get("reason") or "unknown"
    sid = data.get("session_id") or ""
    if not valid_sid(sid) or reason == "resume":
        return 0
    cwd = read_text(sid, "cwd") or data.get("cwd") or os.getcwd()
    sid8 = short(sid)
    n = prompts_of(sid)
    topic = read_text(sid, "topic")
    root = find_protocol_root(cwd)
    if not root:
        forget(sid)
        return 0
    if os.path.exists(spath(sid, "skip")):
        log(f"ok session={sid8} reason={reason} skipped by the user")
        return 0
    if is_closed(sid):
        log(f"ok session={sid8} reason={reason} closed prompts={n} topic={topic!r}")
        return 0
    if closed_at_prompts(sid) is None and n < opt_int("min_prompts"):
        log(f"ok session={sid8} reason={reason} trivial prompts={n} topic={topic!r}")
        return 0
    mark_unclosed(sid, cwd, root, reason, agent)
    return 0


def needs_recording(sid):
    if os.path.exists(spath(sid, "skip")) or is_closed(sid):
        return False
    return closed_at_prompts(sid) is not None or prompts_of(sid) >= opt_int("min_prompts")


def mark_unclosed(sid, cwd, root, reason, agent):
    sid8 = short(sid)
    n = prompts_of(sid)
    topic = read_text(sid, "topic")
    date = session_date(sid)
    write_unclosed(sid, status="pending", agent=read_text(sid, "agent") or agent, cwd=cwd, root=root,
                   date=date, topic=topic, prompts=n, reason=reason, at=now_iso())
    if opt_bool("auto_close"):
        log(f"UNCLOSED session={sid8} reason={reason} date={date} prompts={n} topic={topic!r} -> worker")
        spawn("worker", sid, "--reason", reason)
    else:
        log(f"UNCLOSED session={sid8} reason={reason} date={date} prompts={n} topic={topic!r} (auto-close off)")
        spawn("notify", "Session not recorded", f"{sid8} ({topic}) - run the sessions skill to close it")


def newest_mtime(sid):
    d = sdir(sid)
    stamps = [os.path.getmtime(d)]
    for name in os.listdir(d):
        try:
            stamps.append(os.path.getmtime(os.path.join(d, name)))
        except OSError:
            pass
    return max(stamps)


def age_s(iso):
    try:
        return time.time() - datetime.datetime.fromisoformat(iso).timestamp()
    except Exception:
        return 1e9


def pending_markers(root=None):
    out = []
    if not os.path.isdir(STATE_DIR):
        return out
    now = time.time()
    for sid in sorted(os.listdir(STATE_DIR)):
        if not os.path.isdir(sdir(sid)):
            continue
        if (now - newest_mtime(sid)) / 86400 > STATE_TTL_DAYS:
            forget(sid)
            continue
        m = read_unclosed(sid)
        if not m:
            continue
        if is_closed(sid) or os.path.exists(spath(sid, "skip")):
            mark_closed(sid)
            continue
        if m.get("status") == "running" and not pid_alive(m.get("pid")):
            m = write_unclosed(sid, status="failed", error="the worker died (sleep, reboot or kill)")
        if root and m.get("root") != root:
            continue
        out.append(m)
    return out


def describe(m):
    status = m.get("status")
    if status == "running" or (status == "pending" and opt_bool("auto_close") and age_s(m.get("at")) < 120):
        return "closing in the background now"
    if status == "failed":
        return f"auto-close failed: {str(m.get('error'))[:120]}"
    return "not recorded"


def adopt_stale_sessions(root, current, agent):
    """Desktop apps keep sessions open for days and may never send SessionEnd. A session of
    this project idle for `stale_hours` that still needs recording is treated as ended."""
    if not os.path.isdir(STATE_DIR):
        return
    limit = opt_int("stale_hours") * 3600
    for sid in os.listdir(STATE_DIR):
        if sid == current or read_unclosed(sid) or not os.path.isdir(sdir(sid)):
            continue
        cwd = read_text(sid, "cwd")
        if not cwd or age_s(read_text(sid, "last")) < limit or not needs_recording(sid):
            continue
        if find_protocol_root(cwd) == root:
            log(f"STALE session={short(sid)}: idle {int(age_s(read_text(sid, 'last')) // 3600)}h without SessionEnd")
            mark_unclosed(sid, cwd, root, "stale", agent)


def hook_session_start(data, agent):
    root = find_protocol_root(data.get("cwd") or os.getcwd())
    if not root:
        return 0
    current = data.get("session_id") or ""
    adopt_stale_sessions(root, current, agent)
    pending = [m for m in pending_markers(root) if m.get("session_id") != current]
    if not pending:
        return 0
    lines = [f"- `{short(m['session_id'])}` {m.get('date')} \"{m.get('topic')}\" - {describe(m)}" for m in pending]
    context = (
        "[session-protocol] Earlier sessions of this project without a session log:\n" + "\n".join(lines) +
        "\n\nMention this to the user in one short line in your first reply. If any are not being closed "
        "right now, suggest the `sessions` skill of the session-protocol plugin to close or dismiss them. "
        "Do not close them on your own."
    )
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart",
                                             "additionalContext": context}}, ensure_ascii=False))
    log(f"session-start reminder: {len(pending)} pending for {root}")
    return 0


HOOKS = {"prompt": hook_prompt, "written": hook_written,
         "session-end": hook_session_end, "session-start": hook_session_start}


def install_cli_link():
    if os.path.realpath(CLI_LINK) == os.path.realpath(__file__):
        return
    os.makedirs(BASE, exist_ok=True)
    tmp = f"{CLI_LINK}.{os.getpid()}"
    os.symlink(os.path.realpath(__file__), tmp)
    os.replace(tmp, CLI_LINK)


def run_hook(name, agent):
    install_cli_link()
    if agent == "claude" and os.environ.get("CLAUDE_PLUGIN_ROOT"):
        current = {k[len(OPTION_PREFIX):].lower(): v for k, v in os.environ.items() if k.startswith(OPTION_PREFIX)}
        if current != load_json(PLUGIN_OPTIONS_FILE):
            save_json(PLUGIN_OPTIONS_FILE, current)
    if os.environ.get(WORKER_ENV):
        return 0
    return HOOKS[name](read_stdin_json(), agent)


def resolve(ref):
    """Full id, the 8-char short id shown in lists and notifications, or `all` (unclosed ones)."""
    if ref == "all":
        return [m["session_id"] for m in pending_markers()]
    if not valid_sid(ref):
        raise SystemExit(f"invalid session id: {ref!r}")
    if os.path.isdir(sdir(ref)):
        return [ref]
    hits = [s for s in (os.listdir(STATE_DIR) if os.path.isdir(STATE_DIR) else []) if s.startswith(ref) or s.endswith(ref)]
    if len(hits) != 1:
        raise SystemExit(f"{'no' if not hits else 'ambiguous'} session matches {ref!r}; see `list`")
    return hits


def current_session():
    for var in ("CLAUDE_CODE_SESSION_ID", "CODEX_THREAD_ID", "CODEX_SESSION_ID"):
        sid = os.environ.get(var, "")
        if valid_sid(sid) and os.path.isdir(sdir(sid)):
            return sid
    cwd = os.path.realpath(os.getcwd())
    mine = [s for s in (os.listdir(STATE_DIR) if os.path.isdir(STATE_DIR) else [])
            if os.path.realpath(read_text(s, "cwd") or "/nonexistent") == cwd and not read_unclosed(s)]
    return max(mine, key=lambda s: read_text(s, "last"), default="")


def cmd_list(_):
    pending = pending_markers()
    if not pending:
        print("No unrecorded sessions.")
        return 0
    for m in pending:
        print(f"{short(m['session_id'])}  {m.get('date')}  {m.get('agent', 'claude'):<6}  "
              f"\"{m.get('topic')}\"  - {describe(m)}  ({m.get('cwd')})")
    return 0


def cmd_close(args):
    sids = [s for s in resolve(args.session) if read_unclosed(s)]
    if not sids:
        print("Nothing to close.")
        return 0
    for sid in sids:
        write_unclosed(sid, status="pending", at=now_iso())
        spawn("worker", sid, "--reason", "manual", "--now")
        print(f"{short(sid)}: closing in the background (1-5 min); you'll get a notification. Check with `list`.")
    return 0


def cmd_clear(args):
    for sid in resolve(args.session):
        forget(sid)
        print(f"{short(sid)}: forgotten")
    return 0


def cmd_skip(args):
    sid = resolve(args.session)[0] if args.session else current_session()
    if not sid:
        print("Could not tell which session this is - pass its id (see `list`).")
        return 2
    write_text(sid, "skip", now_iso())
    try:
        os.remove(spath(sid, "unclosed.json"))
    except OSError:
        pass
    print(f"{short(sid)}: this session will not be recorded")
    return 0


def cmd_config(args):
    config = load_json(CONFIG_FILE)
    if args.key is None:
        for key in DEFAULTS:
            src = "plugin" if os.environ.get(OPTION_PREFIX + key.upper()) or load_json(PLUGIN_OPTIONS_FILE).get(key) else \
                  "config" if config.get(key) not in (None, "") else "default"
            print(f"{key:<20} {opt(key)!r:<40} ({src})")
        print(f"\nfile: {CONFIG_FILE}")
        return 0
    if args.key not in DEFAULTS:
        print(f"unknown setting {args.key!r}; one of: {', '.join(DEFAULTS)}")
        return 2
    if args.value is None:
        config.pop(args.key, None)
        print(f"{args.key}: reset to default")
    else:
        config[args.key] = args.value
        print(f"{args.key} = {args.value!r}")
    save_json(CONFIG_FILE, config)
    return 0


def append_once(path, text, marker):
    if os.path.exists(path):
        with open(path, encoding="utf-8", errors="replace") as fh:
            if marker in fh.read():
                return f"{path}: already set up"
    existed = os.path.exists(path)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(("\n" if existed else "") + text)
    return f"{path}: {'updated' if existed else 'created'}"


def cmd_init(args):
    root = os.path.abspath(args.dir)
    with open(os.path.join(TEMPLATES, "AGENTS.md"), encoding="utf-8") as fh:
        section = fh.read()
    if any(has_marker(os.path.join(root, n)) for n in INSTRUCTION_FILES):
        print("Protocol already present in the project instructions, left as is")
    else:
        print(append_once(os.path.join(root, "AGENTS.md"), section, opt("marker")))
    claude_md = os.path.join(root, "CLAUDE.md")
    if not has_marker(claude_md):
        print(append_once(claude_md, "@AGENTS.md\n", "@AGENTS.md"))
    os.makedirs(os.path.join(root, "_meta", "sessions"), exist_ok=True)
    today = datetime.date.today().isoformat()
    for name in ("hot.md", "shipped-log.md"):
        dst = os.path.join(root, "_meta", name)
        if os.path.exists(dst):
            print(f"{dst}: exists, left as is")
            continue
        with open(os.path.join(TEMPLATES, "_meta", name), encoding="utf-8") as fh:
            body = fh.read().replace("YYYY-MM-DD", today)
        with open(dst, "w", encoding="utf-8") as fh:
            fh.write(body)
        print(f"{dst}: created")
    return 0


def main():
    ap = argparse.ArgumentParser(prog="session-protocol",
                                 description="Session memory guard for Claude Code and Codex.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    h = sub.add_parser("hook", help="entry point for the harness hooks")
    h.add_argument("event", choices=HOOKS)
    h.add_argument("--agent", choices=("claude", "codex"), default="claude")
    sub.add_parser("list", help="sessions that ended without a session log").set_defaults(func=cmd_list)
    p = sub.add_parser("close", help="record <id|prefix|all> now, in the background")
    p.add_argument("session")
    p.set_defaults(func=cmd_close)
    p = sub.add_parser("clear", help="forget <id|prefix|all> without recording")
    p.add_argument("session")
    p.set_defaults(func=cmd_clear)
    p = sub.add_parser("skip", help="don't record a session (default: the current one)")
    p.add_argument("session", nargs="?")
    p.set_defaults(func=cmd_skip)
    p = sub.add_parser("init", help="set up a project: protocol in AGENTS.md, CLAUDE.md import, _meta/")
    p.add_argument("dir", nargs="?", default=".")
    p.set_defaults(func=cmd_init)
    p = sub.add_parser("config", help="show settings, or `config <key> [value]` to set / reset one")
    p.add_argument("key", nargs="?")
    p.add_argument("value", nargs="?")
    p.set_defaults(func=cmd_config)
    w = sub.add_parser("worker")
    w.add_argument("session")
    w.add_argument("--reason", default="unknown")
    w.add_argument("--now", action="store_true")
    n = sub.add_parser("notify")
    n.add_argument("title")
    n.add_argument("body")
    args = ap.parse_args()
    if args.cmd == "hook":
        return run_hook(args.event, args.agent)
    if args.cmd == "worker":
        if not valid_sid(args.session):
            return 2
        log(f"worker start session={short(args.session)} reason={args.reason}")
        if not args.now:
            time.sleep(5)
            notify("Session not recorded",
                   f"{short(args.session)} ({read_text(args.session, 'topic')}) - recording it in the background")
        return close_session(args.session, args.reason)
    if args.cmd == "notify":
        notify(args.title, args.body)
        return 0
    return args.func(args)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as exc:
        log(f"crashed ({' '.join(sys.argv[1:3])}): {exc!r}")
        sys.exit(0)
