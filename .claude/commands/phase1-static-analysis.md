---
description: Phase 1 - static analysis of the decompiled APK
---
Read CLAUDE.md fully, and PROGRESS.md if it exists.

If inputs/apk-source/ still only contains the placeholder .txt, stop and tell me to drop the decompiled source in first.

Do only §4:
1. Find the home-feed / CMS-widget API endpoint (URL pattern + method).
2. Find the banner/widget data model class(es) and list every field, flagging any that could hold a destination (redirectUrl, deepLink, targetUrl, actionUrl, navigationUrl, linkType/linkValue, or similarly named).
3. Find the deep-link intent-filter grammar in the manifest and the class that parses an incoming deep link.
4. Find how the app authenticates requests (session token, device ID, cookie, request signature/HMAC).
5. Find any certificate-pinning code, and if inputs/modified-apk/ has a real APK (not the placeholder), try to determine what was changed to bypass it (unzip both and diff relevant classes/resources).

Write analysis/FINDINGS.md with exact file paths and code snippets for each finding, not just descriptions. Don't write any capture or client code yet. Log the phase in PROGRESS.md, list every [A] default you had to make, stop, and tell me what Phase 2 needs from me (e.g. is the emulator ready with the modified APK installed).
