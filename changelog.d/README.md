# Changelog fragments

Every merge request adds one fragment here, named after its MR number (`776.fix.md`).
At release time towncrier compiles the pending fragments into `CHANGELOG.md` and deletes them.
A file starting with `+` was written by Renovate or the Crowdin sync before its MR existed, and
`uv run mb release prepare` renames it to its MR number before compiling.
The naming rule, the five types and the wording rules are in the `changelog` skill
(`.agents/skills/changelog/SKILL.md`) and in the contributor docs (`docs/contribute/versioning.md`).
