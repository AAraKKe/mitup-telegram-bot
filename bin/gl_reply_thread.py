#!/usr/bin/env python3
"""Interact with GitLab MR discussion threads.

Commands:
  --list <mr_iid>
      List all open (unresolved) threads. Shows id, author, and first line of
      the opening note — enough to identify which thread to inspect or reply to.

  --get <mr_iid> <discussion_id>
      Print the full content of a single thread (all notes with author and body).
      Use this after --list to read a thread before composing a reply.

  --resolve <mr_iid> <discussion_id>
      Mark a thread as resolved.

  --reply-batch <mr_iid> <json_file|->
      Post multiple replies in one go. <json_file> must be a JSON array where
      each element has "discussion_id" and "body" keys:

        [
          {"discussion_id": "abc123", "body": "First reply."},
          {"discussion_id": "def456", "body": "Second reply."}
        ]

      Pass "-" as <json_file> to read from stdin.

  <mr_iid> <discussion_id>   (reply — body from stdin)
      Post a reply to the given thread. Read the body from stdin:

        bin/gl_reply_thread.py 251 abc123def <<'EOF'
        **Answered by: Claude Code**

        Explanation here.
        EOF
"""

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

USAGE = """\
Usage:
  bin/gl_reply_thread.py --list <mr_iid>
  bin/gl_reply_thread.py --get <mr_iid> <discussion_id>
  bin/gl_reply_thread.py --resolve <mr_iid> <discussion_id>
  bin/gl_reply_thread.py --reply-batch <mr_iid> <json_file|->
  bin/gl_reply_thread.py <mr_iid> <discussion_id>   (body from stdin)
"""


def glab_api(path: str, *, method: str = "GET", fields: dict[str, str] | None = None) -> Any:
    """Run a glab api call and return parsed JSON output."""
    cmd = ["glab", "api", path, "-X", method]
    for key, value in (fields or {}).items():
        cmd += ["-f", f"{key}={value}"]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(result.stderr, file=sys.stderr)
        sys.exit(result.returncode)
    return json.loads(result.stdout) if result.stdout.strip() else None


def cmd_list(mr_iid: str) -> None:
    discussions = glab_api(f"projects/:fullpath/merge_requests/{mr_iid}/discussions")
    assert isinstance(discussions, list)
    for d in discussions:
        notes = d.get("notes", [])
        if not notes:
            continue
        first = notes[0]
        if not first.get("resolvable") or first.get("resolved"):
            continue
        body_preview = first["body"].splitlines()[0][:50]
        print(f"id:     {d['id']}")
        print(f"author: {first['author']['username']}")
        print(f"body:   {body_preview}")
        print()


def cmd_get(mr_iid: str, discussion_id: str) -> None:
    discussion = glab_api(f"projects/:fullpath/merge_requests/{mr_iid}/discussions/{discussion_id}")
    assert isinstance(discussion, dict)
    for note in discussion["notes"]:
        print(f"[{note['author']['username']}] {note['body']}")
        print("---")


def cmd_resolve(mr_iid: str, discussion_id: str) -> None:
    glab_api(
        f"projects/:fullpath/merge_requests/{mr_iid}/discussions/{discussion_id}",
        method="PUT",
        fields={"resolved": "true"},
    )
    print(f"Thread {discussion_id} marked as resolved.")


def cmd_reply(mr_iid: str, discussion_id: str, body: str) -> None:
    glab_api(
        f"projects/:fullpath/merge_requests/{mr_iid}/discussions/{discussion_id}/notes",
        method="POST",
        fields={"body": body},
    )
    print(f"Reply posted to discussion {discussion_id} on MR !{mr_iid}.")


def cmd_reply_batch(mr_iid: str, json_source: str) -> None:
    if json_source == "-":
        raw = sys.stdin.read()
    else:
        try:
            raw = Path(json_source).read_text()
        except OSError as e:
            print(f"Error: unable to read batch JSON file '{json_source}': {e}", file=sys.stderr)
            sys.exit(1)

    try:
        replies = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"Error: failed to parse batch JSON: {e}", file=sys.stderr)
        sys.exit(1)

    if not isinstance(replies, list):
        print("Error: batch JSON must be an array.", file=sys.stderr)
        sys.exit(1)

    for idx, raw_entry in enumerate(replies, start=1):
        if not isinstance(raw_entry, dict) or "discussion_id" not in raw_entry or "body" not in raw_entry:
            print(f"Error: batch JSON entry #{idx} must be an object with 'discussion_id' and 'body'.", file=sys.stderr)
            sys.exit(1)
        entry = cast(dict[str, str], raw_entry)
        cmd_reply(mr_iid, entry["discussion_id"], entry["body"])


def main() -> int:
    args = sys.argv[1:]

    if not args:
        print(USAGE)
        return 1

    match args:
        case ["--list", mr_iid]:
            cmd_list(mr_iid)
        case ["--get", mr_iid, discussion_id]:
            cmd_get(mr_iid, discussion_id)
        case ["--resolve", mr_iid, discussion_id]:
            cmd_resolve(mr_iid, discussion_id)
        case ["--reply-batch", mr_iid, json_source]:
            cmd_reply_batch(mr_iid, json_source)
        case [mr_iid, discussion_id] if not mr_iid.startswith("--"):
            body = sys.stdin.read()
            if not body.strip():
                print("Error: reply body is empty. Pipe the body via stdin.", file=sys.stderr)
                return 1
            cmd_reply(mr_iid, discussion_id, body)
        case _:
            print(USAGE)
            return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
