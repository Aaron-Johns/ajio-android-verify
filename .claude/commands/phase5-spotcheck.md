---
description: Phase 5 (optional) - emulator tap-through spot-check with vision fallback
---
Read CLAUDE.md and PROGRESS.md. Phase 4 must be complete.

Build §10: sample a few banners from the latest feed pull, tap through on the emulator, and confirm the landing page matches the feed's declared destination. For any AMBIGUOUS_DEEPLINK case, use inputs/image_segmentation_2.py ported with the fixes noted in §10 (verification-flag OR, robust JSON-fence parsing, no getpass at import time, delete uploaded files after use) as a vision fallback.

Status set: CONFIRMED, APP_DEVIATES_FROM_FEED, INCONCLUSIVE. Flag APP_DEVIATES_FROM_FEED clearly — that's the interesting bug this phase exists to catch.

Log the phase in PROGRESS.md, list [A] defaults (sample size, cadence), stop, and suggest a commit message.
