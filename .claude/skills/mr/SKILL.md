---
name: mr
description: Generate a GitLab merge request description following the project template.
user-invocable: true
allowed-tools: Bash, Read, mcp__GitLab__create_merge_request, mcp__GitLab__get_merge_request
model: haiku
---

1. Read `.gitlab/merge_request_templates/Default.md` for the template structure. For the checkboxes at the end of the template, explain why you mark them and for those that are not relevant, use `- [~]` to mark them as not applicable and explain why.
2. Run `git fetch origin` to ensure the local view of `main` is up to date.
3. Run `git log origin/main..HEAD --oneline` to list commits in this MR.
4. Run `git diff origin/main...HEAD --stat` to summarize file changes.
5. Fill in all template sections:
   - **What does this MR do and why?**: Describe the change and reference the issue with `#N` format.
   - **Screenshots or screen recordings**: Add placeholder or ask user.
   - **How to set up and validate locally**: Numbered steps to test.
   - **MR acceptance checklist**: Check off what applies.
6. Output the complete description as plain Markdown.
7. Read `commits_check_config.yaml` (repo root) to get the full emoji-to-type mapping, then pick the emoji that best matches the nature of this MR. The title must start with that emoji.
   Create the MR using `glab mr create` with the following flags:
   - `--title` — commit-style title (emoji prefix from `commits_check_config.yaml` matching the change type)
   - `--description` — the full Markdown body via `"$(cat <<'EOF' ... EOF)"`
   - `--source-branch` — current branch (`git branch --show-current`)
   - `--target-branch main`
   - `--no-editor`

   Example:
   ```bash
   glab mr create \
     --title "🧪 Add tests for X" \
     --description "$(cat <<'EOF'
   ## What does this MR do and why?
   ...
   /assign me
   EOF
   )" \
     --source-branch my-branch \
     --target-branch main \
     --no-editor
   ```
8. Output the MR URL returned by `glab`.
