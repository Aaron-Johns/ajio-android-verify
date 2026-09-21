---
description: Phase 3 - direct API client, no emulator
---
Read CLAUDE.md and PROGRESS.md. Phase 2 must be complete.

Build qa/feed_client.py per §6: calls the feed endpoint directly with requests, using the confirmed headers/auth. If a live session token is needed, build qa/session_bootstrap.py to fetch and cache one via the emulator; skip this if the endpoint works statelessly. Parse responses into banner records. Add retries with backoff and redacted request/response logging per run.

Test against a saved, redacted sample response (not the live API). Then do one live call and show me the parsed banner records.

Log the phase in PROGRESS.md, list [A] defaults, stop, and suggest a commit message (remember: inputs/ and .env are gitignored).
