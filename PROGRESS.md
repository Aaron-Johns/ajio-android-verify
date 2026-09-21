# PROGRESS

## Phase 1: Static analysis — done (2026-09-18)

### What was built
- `analysis/FINDINGS.md` — full write-up of the home-feed endpoint, banner/widget data model, deep-link grammar, auth mechanism, and certificate-pinning status, with exact file paths and smali/JSON snippets.

### What was tested
- No capture/client code written yet (per Phase 1 scope — static analysis only).
- Verified `inputs/apk-source/` and `inputs/modified-apk/` both contained real content (not placeholders) before starting.
- Cross-checked the modified APK (`ajio_media3_fix.apk`) against the decompiled source by extracting its 7 `classes*.dex` files and searching for pinning-related class/method name strings (`isPinningRequiredForHost`, `SslPinningUtil`, `checkServerTrusted`, etc.) — all present, unremoved.
- Traced the full response-shape chain in source (`CMSData -> Data -> Page -> Slot -> Component -> Banner`) to confirm the banner data model write-up.

### Key findings (see FINDINGS.md for full detail)
1. **Endpoint:** `homepage.home_cms` = `https://cms-edge.services.ajio.com/storefront/cms/page`, resolved at runtime from `assets/prod_api_new.json` via `UrlHelper`. Called from `AjioHomeFragment` (`Yg.smali`) through a Ktor client (`HomeApi.getHomeData`, KMM module) with a JSON request body (store/city/pincode/user-status/etc. context). HTTP verb is very likely POST (body-bearing call) but **not 100% confirmed** — a decompile artifact (case-insensitive filesystem, see below) prevented pinning the exact opcode. Secondary banner-only endpoint also found: `GET https://cms-edge.services.ajio.com/storefront/cms/component` (`misc.banner_ad_different_api`), verb confirmed via Retrofit `@GET` annotation.
2. **Data model:** `Banner` (KMM: `com.ril.ajio.kmm.shared.model.home.Banner`) carries destinations in `bannerUrl`, `extendedUrl`, `ctaSettings.ctaLink`, `dynamicPageMetadata.ctaSettings.ctaLink`, and per-hotspot `hotspots[].hotspotUrl`. No field is literally named `deepLink`/`redirectUrl`/etc. — AJIO's own naming convention is used instead. Priority order for resolving `destination_raw` is inferred, not yet confirmed against live data.
3. **Deep-link grammar:** all deep links (custom scheme `ajioapps://`, and `*.ajio.com` App Links) route through one activity (`SplashScreenActivity` → `AjioHomeActivity.M3()`). Path types: `/p/` (PDP), `/c/` (category), `/b/` (brand), `/s/` (search), plus ~25 other non-product paths (cart, login, support, etc.). The app does **not** parse IDs out of the path client-side — resolution is server-side, likely via `NavigationTypeApi.getCMSCategoryNavigation`, not yet confirmed live.
4. **Auth:** no HMAC/signing scheme anywhere. Bearer token via a global OkHttp interceptor; a **guest/anonymous token is obtainable with a single unauthenticated POST** (`https://api.services.ajio.com/uaas/jwt/token/client`, hardcoded `clientName=trusted_client`/`clientSecret=secret`). Home feed likely doesn't require a logged-in session — needs Phase 2 confirmation, but if true, the Appium-based session-bootstrap in §6 of CLAUDE.md may not be needed at all.
5. **Cert pinning:** present (custom SHA-256 SPKI pin check in a hostname verifier, ~23 hardcoded fallback pins), but the X509TrustManager already trusts all certs unconditionally (chain validation is a no-op app-wide). **There's a first-party bypass**: a SharedPreferences flag (`com.ril.ajio_preferences` / key `ssl_pinning`, bool) with a literal "Disable SSL Pinning" checkbox in an in-app Dev Settings screen (`DevSettingsActivity`, not exported, no found in-app launch trigger). The modified APK (`ajio_media3_fix.apk`) — whose filename suggests an ExoPlayer/media3 fix, not SSL — still contains all pinning code/strings intact; **don't assume pinning is already bypassed** in it.

### [A] Assumptions made this phase
- Assumed `home_cms` (not `ajiogram/feed`, which is a differently-named, unrelated "social feed" feature) is the correct "home-screen feed" endpoint per CLAUDE.md's description. Fairly confident given the request shape (store/city/pincode/user context) and the response model chain ending in `banners: List<Banner>`, but not yet confirmed against a live capture.
- Assumed the HTTP verb for `home_cms` is POST based on it constructing and attaching a JSON body — not confirmed by an explicit opcode (decompile artifact, see below).
- Assumed the KMM/Ktor path (`HomeApi.getHomeData`) is the one actually wired up in the current build UI, rather than the older Gson/Retrofit-era `Yg.smali` legacy path that references the same `home_cms` config key — both exist in source; Phase 2 traffic capture will show which is live.
- Assumed destination-field priority for a `Banner` (`ctaSettings.ctaLink` > `dynamicPageMetadata.ctaSettings.ctaLink` > `bannerUrl` > `extendedUrl` > `hotspots[].hotspotUrl`) — inferred from field structure, not from real payload examples.
- Assumed `DevSettingsActivity`'s "Disable SSL Pinning" checkbox is reachable via `adb shell am start -n` despite not being exported, since no in-app trigger (gesture/hidden menu) was found in the smali searched — this needs to be verified hands-on in Phase 2, with a direct SharedPreferences-file edit (`adb shell run-as`) as the fallback if the activity can't be launched directly.

### Known gap
- No `apktool`, `baksmali`, or `aapt2` was available in this Windows environment, so the modified APK's binary `AndroidManifest.xml` and `res/xml/network_security_config.xml` could not be fully decoded/diffed against the original — the pinning comparison in FINDINGS.md §5.3 is string-presence-only (dex string scan), not an instruction-level diff.
- `inputs/apk-source/` was decompiled onto a case-insensitive filesystem (Windows), which silently overwrote at least 2 confirmed colliding obfuscated class pairs (`k41`/`K41`, `HZ4`/`hz4`, likely others). This is why the exact HTTP verb for `home_cms` couldn't be pinned to 100% certainty from static analysis alone. If it matters before Phase 2, re-decompiling on a case-sensitive filesystem (WSL/Linux) would recover it.

### Open items for the user (raised, not yet answered — see CLAUDE.md §9)
1. Confirm live whether `home_cms` needs a logged-in session or works with just the guest OAuth token (static analysis says "probably guest is enough").
2. Real reference data rows (banner_id / expected_brand / expected_category / expected_deeplink_type) — still not provided.
3. Confirm `brand_aliases.draft.json` groups + the 3 colliding-pair dedupe.
4/5. Feed-check and spot-check cadence — still open, defaults stand ([A] 2h feed / weekly-5-banner spot-check).
6. Scope boundary confirmation (read-only, no redistribution) — still open.

