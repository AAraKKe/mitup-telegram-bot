---
name: comment-mr
description: Reply to a GitLab MR discussion thread (review comments).
user-invocable: true
argument-hint: "<mr_iid>"
allowed-tools: Bash(bin/gl_reply_thread.sh*)
---

Use `bin/gl_reply_thread.sh` to reply to comment threads in a GitLab MR.
The `$ARGUMENTS` value is the MR IID (e.g., `/comment-mr 251`).

---

## Step 1 — List open discussion threads

Get a compact summary of all unresolved threads:

```bash
bin/gl_reply_thread.sh --list <mr_iid>
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
bin/gl_reply_thread.sh --get <mr_iid> <discussion_id>
```

This prints every note in the thread with its author and full body — use this instead of fetching all discussions at once to minimise token usage.

---

## Step 3 — Compose the reply

Start the reply with **`**Answered by: Claude Code**`** on its own line, then explain what was done or decided. Be concise and specific — reference the exact code changes that address each point.

---

## Step 4 — Post the reply

```bash
bin/gl_reply_thread.sh <mr_iid> <discussion_id> <<'EOF'
**Answered by: Claude Code**

Your explanation here.
EOF
```

If there are multiple threads to address, repeat for each discussion ID.

---

## Step 5 — Resolve the thread (optional)

After replying, mark the thread as resolved if the issue has been fully addressed:

```bash
bin/gl_reply_thread.sh --resolve <mr_iid> <discussion_id>
```

---

## Example (full flow)

```bash
# 1. Get a compact overview of all open threads
bin/gl_reply_thread.sh --list 251

# 2. If the first line is ambiguous, read the full thread before replying
bin/gl_reply_thread.sh --get 251 abc123

# 3. Post a reply
bin/gl_reply_thread.sh 251 abc123 <<'EOF'
**Answered by: Claude Code**

Fixed in the latest commit — the `active_context` field now tracks the most-recently
entered conversation so `get_active_on_exit()` does a direct lookup instead of
scanning by insertion order.
EOF

# 4. Resolve the thread
bin/gl_reply_thread.sh --resolve 251 abc123
```
