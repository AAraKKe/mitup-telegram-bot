---
name: mr
description: Open a GitLab merge request for the current branch — runs the pre-flight convention review, picks the right emoji from `commits_check_config.yaml`, fills the project's MR template, and submits via `glab mr create`. Use this skill whenever the user wants to create, open, raise, submit, push up, or file a merge request / MR / PR / pull request, or asks to "get this ready for review", "open it for review", "send it out", "ship this branch", "publish the branch", or any other phrasing that means "turn this branch into a review request on GitLab". Also use when the user just wants the MR description / body / write-up generated (without submission) so they can paste it manually. Trigger even if they don't say "GitLab" or "merge request" explicitly — "PR" and "pull request" are common synonyms on this project even though the platform is GitLab.
user-invocable: true
allowed-tools: Read, Bash(git fetch*), Bash(git log*), Bash(git diff*), Bash(git branch*), Bash(glab mr create*), Agent
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

1. **Resolve the base ref and worktree root.** Don't hardcode either — compute them:

   - Base ref: `git symbolic-ref --short refs/remotes/origin/HEAD` (typically `origin/main`, but always derive it). Use the result as `<base>` everywhere below.
   - Worktree root: `git rev-parse --show-toplevel`. Use the result as `<root>` everywhere below.

   If `git symbolic-ref` fails (the remote HEAD isn't set locally), run `git remote set-head origin --auto` once and retry.

2. **Pre-flight convention review.** Spawn the `convention-reviewer` agent against the full branch diff before doing anything else:

   ```
   Agent({
     subagent_type: "convention-reviewer",
     description: "Pre-MR convention review",
     prompt: "Review the diff `git diff <base>...HEAD` from <root> for project-convention compliance. Report findings as a punch list — pass/fail per file with specifics."
   })
   ```

   Substitute `<base>` and `<root>` with the values resolved in step 1.

   - **Blocking violations** (broken conventions the reviewer flags as clear failures): stop. Report them to the user and ask whether to fix-then-MR or open the MR anyway with the violations called out in the description.
   - **Warnings / nits** (style preferences, pre-existing issues not introduced by this branch): surface them in the conversation and continue.
   - **All clear**: continue to step 3.

   Skip this step only if the user explicitly says "skip review" — never silently.
3. Read `.gitlab/merge_request_templates/Default.md` for the template structure.
4. Gather context — run these in parallel (substitute `<base>` from step 1):
   - `git fetch origin`
   - `git log <base>..HEAD --oneline`
   - `git diff <base>...HEAD --stat`
5. Read `commits_check_config.yaml` to pick the right title emoji.
6. Fill in all template sections:
   - **What does this MR do and why?** — Describe the change and reference the issue with `#N` format.
   - **Screenshots or screen recordings** — Add placeholder or ask user.
   - **How to set up and validate locally** — Numbered steps to test.
   - **MR acceptance checklist** — Check off what applies. For checkboxes that are not
     relevant, use `- [~]` to mark them as not applicable and explain why.
7. Create the MR, targeting the base ref's branch (strip the `origin/` prefix from `<base>`):

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
     --target-branch "${base#origin/}" \
     --no-editor
   ```

8. Output the MR URL returned by `glab`.
