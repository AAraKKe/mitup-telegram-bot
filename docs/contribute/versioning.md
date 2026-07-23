---
icon: material/tag-outline
---

# Versioning

A version says how much the product changed. Milestones are named after versions (`vX.Y.Z`), so knowing how a change is classified tells you which milestone an issue lands in.

## Classify by three questions

Ask these in order. The first yes wins.

1. Does it change **what** a user can do, adding, removing, or replacing a capability? That is a major change. Removals are always major.
2. Does it change **how** a user does something that already exists, a reshaped flow, a restructured screen, different conversation routing, or a default users will notice? That is a minor change.
3. Otherwise it is a patch. Internal refactors, docs, monitoring, dependency bumps, and copy polish all live here.

The test is intent, not visibility. A bug fix restores what the bot was already meant to do, so it is a patch even when the difference is plain to users.

## Two riders

* A feature merged behind a flag counts when it is exposed to users, not when the code lands.
* Maintainers may promote a large internal change up one tier to signal deploy risk.

Cutting releases and deploying is maintainer work. This page exists so that issue classification and milestones make sense as you contribute.
