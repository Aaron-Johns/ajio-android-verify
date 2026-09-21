---
description: Phase 2 - live traffic capture to confirm Phase 1 findings
---
Read CLAUDE.md and PROGRESS.md. Phase 1 (analysis/FINDINGS.md) must exist; if not, stop and tell me.

Do only §5: set up mitmproxy, get the emulator's traffic through it (with Frida SSL-unpinning first if inputs/modified-apk/ still has pinning per Phase 1), launch the app, and capture the home-screen feed load. Compare against Phase 1's findings — same endpoint, same fields, and the exact auth headers/cookies/tokens actually sent. Note what's ephemeral vs static.

Save redacted captures to analysis/traffic_capture/ and update analysis/FINDINGS.md. Capture only the home feed load and, if needed, one destination-page load — nothing from checkout, payments, or account data.

Log the phase in PROGRESS.md, list [A] defaults used, stop, and tell me what Phase 3 needs (e.g. whether a live session is required).
