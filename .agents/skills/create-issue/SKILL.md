---
name: create-issue
description: Create a GitLab issue using the correct project template and labels. Use this skill whenever the user asks to open, create, file, or track an issue — even if they don't say "GitLab" or "issue template" explicitly. Also use it when the user says things like "let's track this", "open a ticket for this", or "file a bug for X".
user-invocable: true
allowed-tools: Bash, Read, AskUserQuestion
model: haiku
---

## Goal

Create a GitLab issue with the right template and labels, asking the user as few questions as possible. The skill is typically invoked mid-work, so there is prior context — use it to fill in sections automatically.

## Step 1 — Pick a template

Use the conversation context to infer which template fits best:

| Signals | Template |
|---|---|
| Something is broken, unexpected behaviour, error | Bug |
| New functionality, enhancement, proposal | Feature Proposal |
| Work item, chore, non-feature task | Task |
| Adding support for a new language | New Language Request |
| Missing, incorrect, or unclear docs | Improve Documentation |
| Wrong or missing translation string | Translation |
| Support request, service desk | Service Desk Request |

Only ask the user to choose if the context genuinely doesn't make it clear. If asking, list the available options by reading `.gitlab/issue_templates/` at that moment — don't trust the table above as exhaustive, templates get added or renamed over time.

## Step 2 — Read the template

List `.gitlab/issue_templates/` and read the file whose name matches the template you picked. Template filenames are the same as the template label followed by `.md` (e.g., "Bug" → `Bug.md`, "New Language Request" → `New Language Request.md`). If the name doesn't match exactly, fall back to listing the directory and picking by visual similarity.

## Step 3 — Fill in the sections

For each section in the template, try to fill it from available context (conversation history, files already read, work already done). Do **not** ask the user to fill in sections you can infer.

Only ask for information that is genuinely missing after exhausting the context. When you do ask, batch all missing pieces into a single question rather than asking one at a time.

Derive the issue title from context too. Only ask for the title if there is no clear candidate.

## Step 4 — Build the issue body

Replace the HTML comment placeholders with the content gathered above. **Keep the `/label` or `/labels` quick-action line(s) at the bottom exactly as they appear in the template** — do not add, remove, or modify any labels. The labels come from the template; never invent your own.

## Step 5 — Create the issue

```bash
glab issue create \
  --title "TITLE" \
  --description "$(cat <<'EOF'
BODY
EOF
)" \
  --no-editor
```

Output the issue URL returned by `glab`.

## Step 6 — Materialize dependencies as issue links

An ordering relationship that only exists as prose in a description ("depends on #N", "after #N", "part 2 of…") is invisible to GitLab's boards and blocked-issue indicators. Whenever the new issue depends on another issue, blocks another issue, or is part of a sequenced plan (including other issues created in the same batch), create the corresponding **blocking links** — never leave the relationship as prose only.

`glab` has no subcommand for issue links; use the API. The project's tier supports blocking links:

```bash
# BLOCKER blocks BLOCKED — run once per relationship
glab api "projects/:id/issues/BLOCKER_IID/links" --method POST \
  -f target_project_id=PROJECT_ID -f target_issue_iid=BLOCKED_IID -f link_type=blocks
```

- Always phrase links in the `blocks` direction (create them from the blocker's endpoint); `is_blocked_by` from the other end produces the same link, so pick one convention and keep it.
- When creating several issues of a plan in one sitting, add the links right after the last issue is created, then verify with `glab api "projects/:id/issues/IID/links"`.
- Only link true ordering constraints. "Related but independent" work stays unlinked (or use `link_type=relates_to` if the connection is worth surfacing).
