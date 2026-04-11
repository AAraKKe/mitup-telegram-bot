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
Features are built in three stages that run sequentially: **scaffolding → implementation → finishing**. Not every feature needs all stages — small fixes may only need implementation.

Each agent has a **default stage** listed below, but phases can override it. For example, handler-expert defaults to implementation but can do scaffolding work (e.g., a DB migration that must exist before the handler). The stage is always determined by the phase's `## Stage` field, not by the agent name.

**Splitting large work:** When a feature requires a lot of work from a single agent type, split it into multiple focused phases rather than one massive prompt. Smaller, focused prompts produce better results — a single handler-expert instance building three handlers will struggle; three phases with one handler each will succeed. Each phase gets its own agent instance.

## Scaffolding stage
Produces the building blocks the feature needs. All scaffolding phases run in parallel.

  <agent name="bot-copywriter" default-stage="scaffolding">
    User-facing text — messages, button labels, prompts, notifications in `messages.py`.
    Feasibility: ask about new message classes needed, tone consistency with existing strings.
  </agent>
  <agent name="view-expert" default-stage="scaffolding">
    Expert in UX and screens in `mitup_bot/views/` — MitupView, PaginatedMitupView, ButtonConfig, factory functions.
    Feasibility: ask about view layout, callback data constraints, factory coverage.
  </agent>
  <agent name="docs-writer" default-stage="scaffolding">
    Documentation in `docs/` — MkDocs pages.
    Feasibility: ask about navigation placement and any existing pages to update.
  </agent>
  <agent name="lambda-expert" default-stage="scaffolding">
    Lambda functions in `mitup_bot/lambdas/` — BotAdapter, cold-start constraints.
    Feasibility: ask about execution time, warm-state assumptions, API access patterns.
  </agent>
  <agent name="cli-expert" default-stage="scaffolding">
    CLI commands in `mitup_bot/cli/` — Click, operational scripts.
    Feasibility: ask about command placement, production vs bin classification.
  </agent>

## Implementation stage
The core feature work. Handler-expert and test-expert run as a coordinated team (see `<handler_test_team>`). This stage starts only after all scaffolding is complete, because the handler wires together the views, messages, and models that scaffolding produced.

  <agent name="handler-expert" default-stage="implementation">
    Handlers in `mitup_bot/handlers/` — registration, conversation flows, HandlerId enums, PTB filters.
    Also owns shared callback definitions in `mitup_bot/utils/callbacks.py` — new `CallbackData` instances go here.
    Also owns DB models (`mitup_bot/models/`) and Alembic migrations — when a feature needs schema changes, put the migration in a scaffolding phase so it runs before the handler.
    Feasibility: ask about handler structure, state machine design, guard requirements, callback data naming.
    Team: sends a structured test brief to test-expert via `SendMessage` after finishing implementation.
  </agent>
  <agent name="test-expert" default-stage="implementation">
    pytest tests — unit tests and DB integration tests.
    Feasibility: ask about test coverage gaps, fixture needs, failure mode registration.
    Team: does prep work in parallel, then receives test brief from handler-expert via `SendMessage`. Can DM handler-expert for clarification.
  </agent>

## Finishing stage
Post-implementation work that depends on the feature being complete. All finishing phases run in parallel after implementation.

  <agent name="translator" default-stage="finishing">
    Locale translations — invoke after new strings are added and `hatch run dev:update-locales` is complete.
    No feasibility phase needed — this is a pure implementation step.
  </agent>
  <agent name="convention-reviewer" default-stage="finishing">
    Convention audit — invoke after all implementation phases to catch violations before CI.
    No feasibility phase needed — this is a post-implementation check.
  </agent>
  <agent name="type-checking" default-stage="finishing">
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

<handler_test_team>
When the approved plan contains both a **handler-expert phase** and a **test-expert phase**, run them as a coordinated team instead of independent phases. This enables direct agent-to-agent communication so test-expert receives a structured test brief from handler-expert rather than inferring coverage from source code.

## Team lifecycle

1. **Spawn both agents in parallel** — handler-expert and test-expert both receive the worktree preamble and their respective phase briefs.
2. **handler-expert** begins implementation immediately. When it finishes, it sends a test brief to test-expert via `SendMessage` describing all behaviors, edge cases, guards, state transitions, callbacks, views, and data setup needed for comprehensive tests. It stays available for follow-up questions.
3. **test-expert** begins productive prep work in parallel — reading test-conventions skill, existing test files in the relevant directory, and helpers/fixtures. When it receives the test brief from handler-expert, it writes tests using the brief as its source of truth. If anything is unclear, it DMs handler-expert directly for clarification.
4. **Completion** — when both agents are done, the orchestrator collects `## Files touched` from both and proceeds to convention review.

## Prompt additions

Append to handler-expert's prompt (after the phase brief, before the file-tracking instruction):

> You are in a team with **test-expert**. When you finish implementation, send a test brief to test-expert via `SendMessage`. The brief must cover: behaviors to cover, edge cases, guards applied, state transitions (if conversation handler), callbacks and views used, and data setup notes. The brief must be thorough enough that test-expert can write complete tests without reading your handler source.

Append to test-expert's prompt (after the phase brief, before the file-tracking instruction):

> You are in a team with **handler-expert**. Start immediately with your prep work: read the test-conventions skill, study existing test files and helpers relevant to this feature area, and review available fixtures. Then wait for handler-expert to send you a test brief via `SendMessage`. Use that brief as your source of truth for what needs coverage — do not independently read handler source. If anything in the brief is unclear or incomplete, DM handler-expert directly for clarification before writing tests.

## Scope

This team pattern applies ONLY when both handler-expert and test-expert phases exist in the same plan. All other phases use the standard parallel/sequential logic. The handler-test team and other independent phases can run concurrently.
</handler_test_team>

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
Synthesize the specialist reports and Q&A answers into a staged implementation plan using the format in `<planning>`. Assign each phase to the correct stage based on the agent's `stage` attribute in `<available_agents>`. Omit stages that have no phases. Note any risks or constraints surfaced during feasibility.

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
4. Load `.plans/<slug>-overview.md` (if it exists alongside the phase files) plus all requested phase files. Read the `## Stage` field of each phase file to determine stage membership and execution order.

**Step 7 — Execute phases by stage**
Create a `TaskCreate` task for each phase being implemented. Execute stages in order — each stage waits for the previous one to complete before starting. Skip any stage that has no phases.

**7a — Scaffolding stage**
Spawn all scaffolding-stage agents **in parallel** (simultaneous `Agent` tool calls). These produce the building blocks (messages, views, migrations, docs, CLI commands) that the handler will wire together. Update each task to `in_progress` before invoking its agent. Wait for all scaffolding agents to complete before proceeding.

**7b — Implementation stage**
Launch the handler-expert and test-expert as a coordinated team per `<handler_test_team>`. Spawn both agents in parallel — append the team-specific instructions from `<handler_test_team>` to each agent's prompt (after the phase brief, before the file-tracking instruction). Wait for both to complete before proceeding.

If the plan has a handler-expert phase but no test-expert phase (or vice versa), run that single agent as a normal phase without team coordination.

**7c — Finishing stage**
Spawn all finishing-stage agents **in parallel**: translator, convention-reviewer, type-checking. Wait for all to complete.

**Small tasks:** When the plan has only one or two phases (e.g., a handler bug fix, or a test fix), there may be only one stage with one agent. That's fine — just run it directly. Don't force the three-stage structure onto simple work.

Every agent prompt must be structured as:
1. The worktree preamble from `<worktree>` (with the actual absolute path substituted)
2. The full phase file contents as the implementation brief
3. Team-specific instructions (if part of handler-test team, per `<handler_test_team>`)
4. The file-tracking instruction from `<worktree>`

Do not restrict tools — the agent `.md` files already declare the right tool sets.

After each agent completes, parse its response for the `## Files touched` section and accumulate all file paths into a running list. You will pass this list to the convention reviewer.

Update each task to `completed` when its agent finishes.

> **Production bugs found during a phase:** Specialists are empowered to fix broken production code they discover while completing their assigned work — even if it is outside the strict scope of the phase. A stale symbol reference, a broken import, or an incorrect guard call found in passing should be fixed in-place rather than left for a later phase or ignored.

**Step 7.5 — Convention review (part of finishing stage)**
The convention-reviewer runs as part of the finishing stage (Step 7c). It only has Read, Grep, and Glob — it cannot run git commands. You must pass it the explicit list of files to review.

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
Organize phases into three stages. Omit any stage that has no phases — a small fix may only have an implementation stage.

```
## Overview
> One paragraph describing the final result.

## Feasibility notes
> Key risks, constraints, or decisions surfaced during specialist consultation.

## Proposed implementation plan

### Scaffolding stage
> Phases that produce building blocks (messages, views, migrations, docs). All run in parallel.

#### Phase N: <name>
**Agent:** <specialist-name>
**Goals:** ...
**Acceptance criteria:** ...
**Entry points:** ...
**Implementation notes:** ...

### Implementation stage
> The core feature. Handler-expert and test-expert run as a coordinated team.

#### Phase N: <name>
**Agent:** handler-expert
**Goals:** ...
**Acceptance criteria:** ...
**Entry points:** ...
**Implementation notes:** ...

#### Phase N: <name>
**Agent:** test-expert
**Goals:** ...
**Acceptance criteria:** ...
**Entry points:** ...
**Implementation notes:** ...

### Finishing stage
> Post-implementation work (translations, convention review, type checking). All run in parallel.

#### Phase N: <name>
**Agent:** <specialist-name>
**Goals:** ...
**Acceptance criteria:** ...
**Entry points:** ...
**Implementation notes:** ...
```

**Small tasks:** When a task only needs one or two agents (e.g., fixing a test, fixing a button), skip the full stage structure. Create only the phases actually needed and assign them to the right stage. A handler bug fix is just one implementation-stage phase with handler-expert — no scaffolding, no finishing.
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

## Stage
scaffolding | implementation | finishing

## Entry points
- `path/to/file.py` — reason this is the starting point

## Implementation notes
> Constraints, risks, and decisions from feasibility that apply to this phase.
> Include anything the specialist surfaced during their feasibility assessment.
```
</phase_file_format>
