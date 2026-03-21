---
name: em
description: Engineering manager orchestration workflow. Invoke with /em <task> to plan and coordinate complex multi-domain features using specialist agents.
argument-hint: "<task description> | implement <phase-file-path> [phase-file-path ...]"
allowed-tools: Read, Write, Edit, Bash, Glob, Grep, Agent, AskUserQuestion, TaskCreate, TaskUpdate, TaskList, EnterWorktree
---

<task>
$ARGUMENTS
</task>

<hard_constraints>
  <rule>Do NOT use built-in Explore agents at any point.</rule>
  <rule>Do NOT write any implementation code yourself.</rule>
  <rule>Do NOT start implementation delegation until the user has explicitly approved the plan.</rule>
  <rule>After presenting the plan, output ONLY: "Awaiting your approval to proceed." and STOP. Do not take any further action until the user responds.</rule>
  <rule>When invoking a specialist for feasibility, always ensure that the agent should not implement code.</rule>
  <rule>All implementation work MUST happen inside a worktree. Use `EnterWorktree` before executing any phase. See `<worktree>` for details.</rule>
  <rule>Never pass `isolation: "worktree"` when spawning agents — that creates a separate worktree per agent. All agents must share the same worktree.</rule>
  <rule>Every implementation agent prompt MUST start with the worktree preamble from `<worktree>` and end with the file-tracking instruction.</rule>
</hard_constraints>

<available_agents>
  <agent name="handler-expert">
    Handlers in `mitup_bot/handlers/` — registration, conversation flows, HandlerId enums, PTB filters.
    Also owns shared callback definitions in `mitup_bot/utils/callbacks.py` — new `CallbackData` instances go here.
    Feasibility: ask about handler structure, state machine design, guard requirements, callback data naming.
  </agent>
  <agent name="view-expert">
    Expert in UX and screens in `mitup_bot/views/` — MitupView, PaginatedMitupView, ButtonConfig, factory functions.
    Feasibility: ask about view layout, callback data constraints, factory coverage.
  </agent>
  <agent name="lambda-expert">
    Lambda functions in `mitup_bot/lambdas/` — BotAdapter, cold-start constraints.
    Feasibility: ask about execution time, warm-state assumptions, API access patterns.
  </agent>
  <agent name="cli-expert">
    CLI commands in `mitup_bot/cli/` — Click, operational scripts.
    Feasibility: ask about command placement, production vs bin classification.
  </agent>
  <agent name="bot-copywriter">
    User-facing text — messages, button labels, prompts, notifications in `messages.py`.
    Feasibility: ask about new message classes needed, tone consistency with existing strings.
  </agent>
  <agent name="docs-writer">
    Documentation in `docs/` — MkDocs pages.
    Feasibility: ask about navigation placement and any existing pages to update.
  </agent>
  <agent name="test-expert">
    pytest tests — unit tests and DB integration tests.
    Feasibility: ask about test coverage gaps, fixture needs, failure mode registration.
  </agent>
  <agent name="translator">
    Locale translations — invoke after new strings are added and `hatch run dev:update-locales` is complete.
    No feasibility phase needed — this is a pure implementation step.
  </agent>
  <agent name="convention-reviewer">
    Convention audit — invoke after all implementation phases to catch violations before CI.
    No feasibility phase needed — this is a post-implementation check.
  </agent>
  <agent name="type-checking">
    Type error review — invoke as the final pass after all implementation is complete.
    No feasibility phase needed — this is a post-implementation check.
  </agent>
</available_agents>

<worktree>
## Why a worktree

Implementation work always happens in a git worktree so that the user's main working directory stays clean. This lets them continue other work while phases execute, and keeps half-finished changes off the main checkout.

## How it works

1. **One shared worktree** — call `EnterWorktree` once with the feature slug as the name (e.g., `EnterWorktree(name: "recurring-meetings")`). This switches the session's CWD to the new worktree directory. Store the **absolute worktree path** — you will need it for every agent prompt.
2. **Agents do NOT inherit the worktree CWD.** Spawned agents default to the original repository root, not the worktree. You must explicitly tell every agent the worktree path and instruct it to work there. Do NOT pass `isolation: "worktree"` to any agent — that creates a separate worktree per agent, defeating the purpose.
3. **Worktree preamble** — prepend the following block (with the actual path substituted) to **every** implementation agent prompt. This is the single most important instruction for correct worktree operation:

```
IMPORTANT — WORKTREE CONTEXT
You are working inside a git worktree at: <WORKTREE_ABSOLUTE_PATH>
All file reads, edits, writes, and bash commands MUST target files under this path.
When using Read, Edit, Write, or Glob, always use absolute paths starting with <WORKTREE_ABSOLUTE_PATH>/.
When using Bash, always `cd <WORKTREE_ABSOLUTE_PATH>` first (or use absolute paths).
Do NOT touch any files outside this directory — the main repository checkout is a different directory.
```

## File tracking

Every implementation agent must also report which files it created or modified. Include this instruction verbatim at the end of every implementation agent prompt (after the phase brief):

```
When you are done, list every file you created or modified under a "## Files touched" heading at the end of your response. Use one bullet per file, relative paths only, no nesting or extra text. Example:

## Files touched
- mitup_bot/handlers/foo/bar.py
- mitup_bot/views/foo_view.py
- tests/handlers/test_foo.py
```

After each agent completes, extract the file list from the `## Files touched` section (one path per bullet line). Accumulate all file lists across phases — you will need them for the convention review step.
</worktree>

<workflow>
## Mode detection

Read `<task>` first and determine the mode:

- If `<task>` clearly states to implment a previously created plan, enter **Implementation mode** — skip directly to Step 6. Parse all phase file paths that follow `implement` (e.g., `implement .plans/slug-phase-1-handlers.md .plans/slug-phase-3-views.md`). Do not re-run feasibility.
- Otherwise, enter **Planning mode** — execute Steps 1–5b in order.

---

## Planning mode

Execute these steps in strict order. Do not skip ahead.

**Step 1 — Clarify (if needed)**
If the task is ambiguous or missing key information, use `AskUserQuestion` to ask the minimum questions needed. Do not proceed until resolved. If the task is clear, skip to Step 2.

**Step 2 — Identify domains**
Read only the specific files needed to understand the current state. Use `Grep` and `Glob` to locate relevant symbols, entry points, and patterns. Keep this minimal — just enough to determine which agents from `<available_agents>` are relevant to this task.

**Step 3 — Specialist feasibility (parallel)**
Consult `<available_agents>` to select the relevant specialists. Invoke all relevant feasibility agents **in parallel** (simultaneous `Agent` tool calls). For each one:
- Describe the specific part of the feature that touches their domain.
- Provide the entry-point files or modules to read.
- Ask them to report: risks, gaps, constraints, questions, and their recommended implementation approach.
- Pass `tools: "Read, Glob, Grep, Bash"` to enforce read-only access.
- Explicitly include: "Assess only — do NOT write or modify any code."

Wait for all specialists to report before moving to Step 4.

**Step 4 — Interactive Q&A round**
Synthesize all questions, uncertainties, and ambiguities surfaced by feasibility agents into one consolidated list. Use `AskUserQuestion` to present them to the user in a single interactive round. Resolve all open questions before writing the plan. This surfaces risks early and prevents implementing against wrong assumptions.

**Step 5 — Plan**
Synthesize the specialist reports and Q&A answers into a phased implementation plan using the format in `<planning>`. Each phase maps directly to the work a specific specialist will do during implementation. For each phase, explicitly identify whether it is parallelizable and list its dependencies. Note any risks or constraints surfaced during feasibility.

Present the plan to the user, including a summary of any significant findings from feasibility. Then output: "Awaiting your approval to proceed." and stop completely.

**Step 5b — Save phase files (after approval)**
Once the user approves, write the phase files to `.plans/` before starting any implementation:

1. Derive a feature slug from the task description (kebab-case, max 4 words, e.g., `recurring-meetings`).
2. Write `.plans/<slug>-overview.md` containing the full plan (overview, feasibility notes, all phases).
3. For each phase, write `.plans/<slug>-phase-<N>-<phase-name>.md` using the format in `<phase_file_format>`.
4. Report the file paths written, then ask: "Phase files saved. Proceed with implementation now, or run `/em implement .plans/<slug>-phase-1-<name>.md` to implement each phase manually?"
5. If the user says to proceed, continue to Step 6. Otherwise, stop.

---

## Implementation mode (Steps 6–9)

**Step 6 — Enter worktree and load context**

Before any implementation begins, create a shared worktree for all phases:

1. Derive the feature slug from the phase file names or task description (kebab-case, max 4 words).
2. Call `EnterWorktree(name: "<slug>")`. This switches the session's CWD into the worktree.
3. Store the absolute worktree path (the new CWD after `EnterWorktree`). You will embed this path in every agent prompt via the worktree preamble from `<worktree>`.
4. Load `.plans/<slug>-overview.md` (if it exists alongside the phase files) plus all requested phase files. Read the `## Dependencies` section of each phase file to determine which phases are independent.

**Step 7 — Execute phases**
Create a `TaskCreate` task for each phase being implemented.

Group phases by independence:
- Phases with no dependencies on each other → spawn their specialist agents **in parallel** (simultaneous `Agent` tool calls). Update each task to `in_progress` before invoking its agent.
- Phases that depend on others → run sequentially after their prerequisites complete.

Every agent prompt must be structured as:
1. The worktree preamble from `<worktree>` (with the actual absolute path substituted)
2. The full phase file contents as the implementation brief
3. The file-tracking instruction from `<worktree>`

Do not restrict tools — the agent `.md` files already declare the right tool sets.

After each agent completes, parse its response for the `## Files touched` section and accumulate all file paths into a running list. You will pass this list to the convention reviewer.

Update each task to `completed` when its agent finishes.

> **Production bugs found during a phase:** Specialists are empowered to fix broken production code they discover while completing their assigned work — even if it is outside the strict scope of the phase. A stale symbol reference, a broken import, or an incorrect guard call found in passing should be fixed in-place rather than left for a later phase or ignored.

**Step 7.5 — Convention review**
After all implementation phases complete, run the `convention-reviewer` agent. The convention-reviewer only has Read, Grep, and Glob — it cannot run git commands. You must pass it the explicit list of files to review.

Include in the convention-reviewer prompt:
- The worktree preamble (so it reads files from the correct directory)
- The complete list of files touched across all phases (the accumulated list from Step 7), one per line

Example prompt structure:
```
IMPORTANT — WORKTREE CONTEXT
You are working inside a git worktree at: /path/to/worktree
All file reads and searches MUST target files under this path.
When using Read, Grep, or Glob, always use absolute paths starting with /path/to/worktree/.
Do NOT access any files outside this directory — the main repository checkout is a different directory.

Review the following files for convention violations:
- mitup_bot/handlers/foo/bar.py
- mitup_bot/views/foo_view.py
- tests/handlers/test_foo.py
```

If the reviewer reports violations:
- Identify which specialist agent(s) own the violated files (using the domain table in CLAUDE.md).
- Resume each relevant specialist agent and pass it the full violation report for its domain (include the worktree preamble again when resuming).
- Do NOT fix violations yourself.
- Re-run convention-reviewer (with the same explicit file list and worktree preamble) after the specialist finishes until it reports no violations.

**Step 8 — Retrospective**
After all requested phases complete (and convention review is clean), run a single retrospective. Do NOT report per-phase status — that is already communicated via task updates. Focus exclusively on learnings and follow-up work.

Output the retrospective in this format:

```
Retrospective
=============
Agent/skill improvements:
  - <specific improvement to an agent or skill file>

Follow-up work identified:
  - <tasks or features surfaced during implementation that weren't in scope>

Risks / open questions:
  - <anything unresolved that the user should be aware of>
```

If there is nothing to report in a section, omit it entirely rather than writing "none".

**Step 9 — Summary**
Provide a one-paragraph summary: what was changed across all phases, decisions made, and anything to verify before merging.
</workflow>

<planning>
```
## Overview
> One paragraph describing the final result.

## Feasibility notes
> Key risks, constraints, or decisions surfaced during specialist consultation.

## Proposed implementation plan

### Phase N: <name>
#### Parallelizable: yes/no
#### Depends on: [list phase numbers, or "none"]

#### Goals

#### Acceptance criteria

#### Agent: <specialist-name>

#### Entry points
> Files or modules the agent should start reading.

#### Implementation notes
> Constraints, risks, and decisions from feasibility relevant to this phase.
```
</planning>

<phase_file_format>
Each phase file is fully self-contained. A specialist agent reading it should have everything needed to implement without any other context.

```markdown
# Phase N: <name>

## Feature context
> What the overall feature is and why it is being built.
> Include enough background that the agent understands the goal, not just the task.

## This phase
> What this specific phase accomplishes and how it fits into the overall feature.

## Goals
- ...

## Acceptance criteria
- ...

## Agent
<specialist-name>

## Dependencies
- none | phase-1, phase-2

## Entry points
- `path/to/file.py` — reason this is the starting point

## Implementation notes
> Constraints, risks, and decisions from feasibility that apply to this phase.
> Include anything the specialist surfaced during their feasibility assessment.
```
</phase_file_format>
