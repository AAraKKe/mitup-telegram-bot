@AGENTS.md

# Claude Code — Project Instructions

Everything below is Claude Code–specific (subagent delegation, session workflows). The harness-agnostic rules — role, validation, repository conventions, tech stack, structure — are in the imported `AGENTS.md`.

## Working with agents

For single-domain tasks or targeted fixes, **delegate to the appropriate specialist agent by default** — specialists carry their area's conventions and stay in sync with the skills they own; the orchestrator session does not. Check the table below to find the right agent:

| Work involves | Delegate to |
|---|---|
| Handler, model, and migration work (see `handler-expert` agent for full scope) | `handler-expert` |
| Files in `libs/telegram/mitup_bot/views/` | `view-expert` |
| Files in `tests/` | `test-expert` |
| Files in `mitup_bot/lambdas/` | `lambda-expert` |
| Files in `mitup_bot/cli/` | `cli-expert` |
| User-facing message text / button labels | `bot-copywriter` |
| Translation `.po`/`.pot` files | `translator` |
| Documentation in `docs/` | `docs-writer` |

For complex tasks that span multiple areas (handler + migration + tests + translations, etc.), plan the phases yourself, present the plan for approval, then delegate each phase to the matching specialist with a checkpoint between phases.

**Exception — trivial mechanical fixes.** A change that is a few lines, convention-unambiguous, and involves no design decision (a typo, a one-line annotation or suppression, a stale doc sentence, an obvious assertion tweak) may be fixed directly instead of spawning a specialist — the agent round-trip costs more than it protects. Two conditions: **first load the skill(s) that govern the touched area** (the specialist's knowledge lives in `.agents/skills/`, not in the agent definition — fixing without the skill loaded is how convention violations slip in), and if the fix grows beyond a few lines or surfaces a judgment call mid-edit, stop and delegate with the partial diff.

**After any specialist agent finishes**, run the `convention-reviewer` agent on the files it touched before considering the task done. If the reviewer reports violations, **resume the specialist agent that made the changes** and pass it the full violation report — the specialist already has the full context of every change it made, while you would be reconstructing it from a diff.

**Always include the current working tree's absolute root in subagent prompts.** Compute it with `git rev-parse --show-toplevel` and tell the specialist to root every file path under it — don't pattern-match on directory names, since worktree layouts vary (`.claude/worktrees/<name>/`, `wt/<name>/`, anywhere else). Skills reference paths as relative (`mitup_bot/handlers/`, `tests/`); subagents otherwise resolve them against whatever directory they happen to start in, which is usually the main checkout. This matters most when you're working in a `git worktree` other than the main checkout, because the main checkout typically has unrelated in-flight work on a different branch and writes there silently collide with it.

The canonical reference for available agents is `.claude/agents/` (one file per agent). The canonical reference for available skills is `.agents/skills/` (one directory per skill; `.claude/skills` is a symlink to it).

**Run `convention-reviewer` before opening any MR.** Review the full branch diff against the repo's default branch (`git diff "$(git symbolic-ref --short refs/remotes/origin/HEAD)"...HEAD`, typically `origin/main`) regardless of who wrote the code — manual edits skip the per-specialist post-task review and routinely slip past project conventions.

## When validation fails

When `uv run mb validate` (or CI) fails on work you didn't author the fix for, don't fix it yourself by default — the failure usually involves area-specific conventions a specialist already knows. The trivial-fix exception above applies here too, including on specialist-authored work: a mechanical failure with an obvious few-line fix (formatting, a missing annotation, a rename fallout) may be fixed directly **after loading the governing skill named in the table below**. When directly fixing a specialist's output, mention the fix in any later message that resumes that specialist, so its session model of the code stays accurate. Anything beyond trivial, delegate:

**If the work was done by a specialist agent:** resume that agent's session with the error output. The resumed agent retains full context of every change it made.

**If the work was done directly:** delegate to the appropriate specialist and pass it the full `git diff` of the changes alongside the error output — not a prose summary. The diff is the only way the specialist sees the exact state of the code.

| Failing check | Delegate to |
|--------------|-------------|
| Type checker (`ty`) | the specialist that owns the failing file (see the table above), with an explicit instruction to load the `type-checking` skill |
| Tests (`pytest`) | `test-expert` (governing skill: `test-conventions`) |
| Linter (`ruff`) | the specialist that owns the failing file (see the table above), with an explicit instruction to load the `coding-standards` skill |
