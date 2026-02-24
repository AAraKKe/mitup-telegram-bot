---
name: mr
description: Generate a GitLab merge request description following the project template.
user-invocable: true
allowed-tools: Bash, Read, mcp__GitLab__create_merge_request, mcp__GitLab__get_merge_request
---

1. Read `.gitlab/merge_request_templates/Default.md` for the template structure.
2. Run `git fetch origin` to ensure the local view of `main` is up to date.
3. Run `git log origin/main..HEAD --oneline` to list commits in this MR.
4. Run `git diff origin/main...HEAD --stat` to summarize file changes.
5. Fill in all template sections:
   - **What does this MR do and why?**: Describe the change and reference the issue with `#N` format.
   - **Screenshots or screen recordings**: Add placeholder or ask user.
   - **How to set up and validate locally**: Numbered steps to test.
   - **MR acceptance checklist**: Check off what applies.
6. Output the complete description as plain Markdown.
7. Ask the user if they want to create the MR via the GitLab MCP tool or copy-paste it manually.
