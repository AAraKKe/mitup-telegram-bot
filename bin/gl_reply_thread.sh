#!/usr/bin/env bash
# Interact with GitLab MR discussion threads.
#
# Commands:
#   --list <mr_iid>
#       List all open (unresolved) threads. Shows id, author, and first line of
#       the opening note — enough to identify which thread to inspect or reply to.
#
#   --get <mr_iid> <discussion_id>
#       Print the full content of a single thread (all notes with author and body).
#       Use this after --list to read a thread before composing a reply.
#
#   --resolve <mr_iid> <discussion_id>
#       Mark a thread as resolved.
#
#   <mr_iid> <discussion_id>   (reply — body from stdin)
#       Post a reply to the given thread. Read the body from stdin:
#
#         bin/gl_reply_thread.sh 251 abc123def <<'EOF'
#         **Answered by: Claude Code**
#
#         Explanation here.
#         EOF

set -euo pipefail

usage() {
    echo "Usage:"
    echo "  bin/gl_reply_thread.sh --list <mr_iid>"
    echo "  bin/gl_reply_thread.sh --get <mr_iid> <discussion_id>"
    echo "  bin/gl_reply_thread.sh --resolve <mr_iid> <discussion_id>"
    echo "  bin/gl_reply_thread.sh <mr_iid> <discussion_id>   (body from stdin)"
    exit 1
}

[[ $# -lt 1 ]] && usage

case "$1" in
    --list)
        [[ $# -ne 2 ]] && usage
        mr_iid="$2"
        glab api "projects/:fullpath/merge_requests/${mr_iid}/discussions" \
            | jq -r '.[] | select(.notes[0].resolvable and (.notes[0].resolved | not))
                | "id:     \(.id)\nauthor: \(.notes[0].author.username)\nbody:   \(.notes[0].body | split("\n")[0] | .[0:50])\n"'
        ;;
    --get)
        [[ $# -ne 3 ]] && usage
        mr_iid="$2"
        discussion_id="$3"
        glab api "projects/:fullpath/merge_requests/${mr_iid}/discussions/${discussion_id}" \
            | jq -r '.notes[] | "[\(.author.username)] \(.body)\n---"'
        ;;
    --resolve)
        [[ $# -ne 3 ]] && usage
        mr_iid="$2"
        discussion_id="$3"
        glab api "projects/:fullpath/merge_requests/${mr_iid}/discussions/${discussion_id}" \
            -X PUT -f "resolved=true"
        echo "Thread ${discussion_id} marked as resolved."
        ;;
    *)
        [[ $# -ne 2 ]] && usage
        mr_iid="$1"
        discussion_id="$2"
        body=$(cat)
        if [[ -z "${body}" ]]; then
            echo "Error: reply body is empty. Pipe the body via stdin." >&2
            exit 1
        fi
        glab api \
            "projects/:fullpath/merge_requests/${mr_iid}/discussions/${discussion_id}/notes" \
            -X POST \
            -f "body=${body}"
        echo "Reply posted to discussion ${discussion_id} on MR !${mr_iid}."
        ;;
esac
