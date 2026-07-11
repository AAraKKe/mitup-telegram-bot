---
name: comment-mr
description: Reply to a GitLab MR discussion thread (review comments).
user-invocable: true
argument-hint: "<mr_iid>"
allowed-tools: Bash(uv run python tools/gl_reply_thread.py*)
---

> **Always use this skill when replying to MR review threads.** Do not call `glab api` directly to post notes — go through `uv run python tools/gl_reply_thread.py` so that all interactions use the same entry point and the skill can be invoked consistently.

> **Always invoke via `uv run python tools/gl_reply_thread.py`**, never with the system `python`. The script targets this repo's pinned Python version (`requires-python` in pyproject.toml), and only the uv-managed environment guarantees it.

Use `uv run python tools/gl_reply_thread.py` to reply to comment threads in a GitLab MR.
The `$ARGUMENTS` value is the MR IID (e.g., `/comment-mr 251`).

---

## Step 1 — List open discussion threads

Get a compact summary of all unresolved threads:

```bash
uv run python tools/gl_reply_thread.py --list <mr_iid>
```

Output format per thread:
```
id:     <discussion_id>
author: <username>
body:   <first 50 chars of the opening note>
```

This is intentionally compact — just enough to identify the thread. Use `--get` to read the full content before composing a reply.

---

## Step 2 — Read a specific thread (if needed)

If the first line is not enough context to compose a reply, fetch the full thread:

```bash
uv run python tools/gl_reply_thread.py --get <mr_iid> <discussion_id>
```

This prints every note in the thread with its author and full body — use this instead of fetching all discussions at once to minimise token usage.

---

## Step 3 — Compose the reply

Start the reply with **`**Answered by: Claude Code**`** on its own line, then explain what was done or decided. Be concise and specific — reference the exact code changes that address each point.

---

## Step 4 — Post the reply

### Single reply (body from stdin)

```bash
uv run python tools/gl_reply_thread.py <mr_iid> <discussion_id> <<'EOF'
**Answered by: Claude Code**

Your explanation here.
EOF
```

### Multiple replies at once (--reply-batch)

When you have replies ready for several threads, post them all in one call instead of looping. Pass a JSON array via stdin (`-`) or from a file:

```bash
uv run python tools/gl_reply_thread.py --reply-batch <mr_iid> - <<'EOF'
[
  {
    "discussion_id": "abc123",
    "body": "**Answered by: Claude Code**\n\nFirst reply."
  },
  {
    "discussion_id": "def456",
    "body": "**Answered by: Claude Code**\n\nSecond reply."
  }
]
EOF
```

Each entry requires `discussion_id` (the full thread ID from `--list`) and `body` (the reply text, may contain newlines as `\n`).

---

## Step 5 — Resolve the thread (optional)

After replying, mark the thread as resolved if the issue has been fully addressed:

```bash
uv run python tools/gl_reply_thread.py --resolve <mr_iid> <discussion_id>
```

---

## Example (full flow)

```bash
# 1. Get a compact overview of all open threads
uv run python tools/gl_reply_thread.py --list 251

# 2. If the first line is ambiguous, read the full thread before replying
uv run python tools/gl_reply_thread.py --get 251 abc123

# 3. Post replies — use --reply-batch when you have multiple replies ready
uv run python tools/gl_reply_thread.py --reply-batch 251 - <<'EOF'
[
  {"discussion_id": "abc123", "body": "**Answered by: Claude Code**\n\nFixed in the latest commit."},
  {"discussion_id": "def456", "body": "**Answered by: Claude Code**\n\nTypo corrected."}
]
EOF

# 4. Resolve the threads
uv run python tools/gl_reply_thread.py --resolve 251 abc123
uv run python tools/gl_reply_thread.py --resolve 251 def456
```
