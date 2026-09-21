---
description: Phase 4 - deep-link resolution, brand resolver port, comparison, statuses
---
Read CLAUDE.md and PROGRESS.md. Phase 3 must be complete.

Before going further, ask me for 2-3 real reference rows (§8) unless I've supplied them below, and ask me to confirm inputs/brand_aliases.draft.json and the dedupe of the 3 colliding pairs (§7.1, §9 item 3). Update CLAUDE.md with my answers.

Then build §7: deep-link parsing into a structured target, the ported brand resolver (verbatim matching logic, tested against inputs/resolver_fixtures.json), alias-aware comparison, and the status set including AMBIGUOUS_DEEPLINK for the word_boundary multi-candidate case.

Test every status with fixtures. Log the phase in PROGRESS.md, stop, and suggest a commit message.

Notes from me for this phase: $ARGUMENTS
