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

Only ask the user to choose if the context genuinely doesn't make it clear. If asking, present a numbered list of the 7 options.

## Step 2 — Read the template

Read the corresponding file from `.gitlab/issue_templates/`:
- Bug → `Bug.md`
- Feature Proposal → `Feature Proposal.md`
- Task → `Task.md`
- New Language Request → `New Language Request.md`
- Improve Documentation → `Improve Documentation.md`
- Translation → `Translation.md`
- Service Desk Request → `Service Desk Request.md`

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
