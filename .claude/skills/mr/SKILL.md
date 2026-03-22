---
name: mr
description: Generate a GitLab merge request description following the project template.
user-invocable: true
allowed-tools: Read, Bash(git fetch*), Bash(git log*), Bash(git diff*), Bash(git branch*), Bash(glab mr create*)
model: haiku
---

## MR title format

The MR title becomes the squash commit message, so it must follow the project's commit
message format. Read `commits_check_config.yaml` (repo root) to get the emoji-to-type
mapping and pick the emoji that best represents the **primary intent** of the MR.

Format: `{emoji} Description`

- **Emoji** (required) — from `commits_check_config.yaml`, matching the dominant change type.
- **Description** — imperative mood, capitalize first letter, no trailing period.
  Describe what the MR does, not how.

Unlike individual commits, MR titles do not include a scope — MRs are often too broad
for a single scope to be meaningful.

**Examples:**

```
✨ Add recurring meetings support
🐛 Fix timezone offset in meeting reminders
🧹 Replace custom DateTimeMessageEntity with PTB native support
⬆️ [renovate] Update Python dependencies
👷 Fix validate-ci-languages skipped in merge trains
```

---

## Workflow

1. Read `.gitlab/merge_request_templates/Default.md` for the template structure.
2. Gather context — run these in parallel:
   - `git fetch origin`
   - `git log origin/main..HEAD --oneline`
   - `git diff origin/main...HEAD --stat`
3. Read `commits_check_config.yaml` to pick the right title emoji.
4. Fill in all template sections:
   - **What does this MR do and why?** — Describe the change and reference the issue with `#N` format.
   - **Screenshots or screen recordings** — Add placeholder or ask user.
   - **How to set up and validate locally** — Numbered steps to test.
   - **MR acceptance checklist** — Check off what applies. For checkboxes that are not
     relevant, use `- [~]` to mark them as not applicable and explain why.
5. Create the MR:

   ```bash
   glab mr create \
     --title "{emoji} Description" \
     --description "$(cat <<'EOF'
   ## What does this MR do and why?
   ...
   /assign me
   EOF
   )" \
     --source-branch "$(git branch --show-current)" \
     --target-branch main \
     --no-editor
   ```

6. Output the MR URL returned by `glab`.
