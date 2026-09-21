# Phase 1 Static Analysis Findings — AJIO Android App

Source: `inputs/apk-source/` (apktool/jadx decompile of `com.ril.ajio`), cross-checked against `inputs/modified-apk/ajio_media3_fix.apk`.
All paths below are relative to `inputs/apk-source/` unless stated otherwise.

---

## ⚠️ Phase 2 correction (2026-09-21) — read this first

A live traffic capture on the `ajio_nps` emulator (see §6 below and `analysis/traffic_capture/`) **contradicts Phase 1's conclusion about which endpoint and data model actually drive the home screen.** Static analysis found a legacy/parallel AJIO-native CMS path (`home_cms` = `storefront/cms/page`, Kotlin `Banner`/`CtaSettings` models) — that code exists in the APK, but **it is not what's on the wire for the current production home screen.** The live app instead calls a **Fynd Platform "theme" API** with a completely different URL shape, auth scheme, and response structure. §6 below is authoritative for Phase 3/4; §1–§2's static findings are kept for reference (the legacy code may still be reachable via some other flow, e.g. an A/B test or fallback) but should not be used as-is for the API client.

Also corrected: Phase 1 said "no HMAC/signing scheme found" — **that was wrong.** There is a signing scheme (`x-fp-signature`/`x-fp-date`), it's just not implemented in the Kotlin/smali code Phase 1 searched — it's in the bundled React Native JavaScript (`assets/index.android.bundle`), which Phase 1 didn't search. See §6.4.

---

## 1. Home-feed / CMS-widget API endpoint

### 1.1 Endpoint URL and config source

All backend URLs are **not hardcoded per-call** — they're resolved at runtime from a bundled JSON config asset via `UrlHelper.getApiUrl(section, key, args)` (`smali_classes6/com/ril/ajio/services/helper/UrlHelper.smali`). The config lives at:

- **`assets/prod_api_new.json`** — production endpoint map, keyed by section (e.g. `homepage`, `ajiogram`, `misc`, `plp`, `pdp`, `fynd_api`).

The primary home-screen CMS/widget endpoint:

```
homepage.home_cms = https://cms-edge.services.ajio.com/storefront/cms/page
```

Called from `HomeApi.getHomeData(url, homeReq, extraBodyParams, extraHeaders)` — `smali_classes5/com/ril/ajio/kmm/shared/network/api/HomeApi.smali`, method `getHomeData` (lines 276–2045). This is a **Ktor client** call (not Retrofit — this is the newer KMM/shared module), resolved from `Ktor` request builder (`LQZ1;`/`HttpRequestBuilder`) via `AJioServiceLocator.getHttpApiClient()`.

**HTTP method:** a JSON request body is explicitly constructed and attached (`iput-object v0, v10, LQZ1;->d:Ljava/lang/Object;`) before the call executes — no explicit `HttpMethod.Get`/`HttpMethod.Post` call was found in the visible bytecode, so the verb is set either by a Ktor default/plugin or inside `Ltl5;->b(...)` (URL-builder helper) which wasn't traced further. **A body-bearing call strongly suggests `POST`, but this needs to be confirmed empirically in Phase 2** — do not assume without a live capture.

**Request body fields** (built as a `JsonObject`, all optional/conditional on `HomeReq` fields being non-null/non-empty):
```
channel        (Platform.getChannel())
platform       = "MOBILE"
tenantId       = "AJIO"
pcGroup        (HomeReq.getPcGroup())
store          (HomeReq.getStore())
pageUrl        (HomeReq.getPageUrl())
previewId      (HomeReq.getPreviewId())
pageId         (HomeReq.getPageID())
userStatus     (HomeReq.getLoggedIn())
userType       (HomeReq.getUserType())
city           (HomeReq.getCity())
state          (HomeReq.getState())
zone           (HomeReq.getZone())
pincode        (HomeReq.getPincode())
timeStamp      (HomeReq.getTimeStamp())
apiVersion     (HomeReq.getApiVersion())
appVersion     (Platform.getClientVersion())
experiments    (from extraBodyParams, comma-split)
userSubGroup   (from extraBodyParams, comma-split)
```

Response deserializes into `com.ril.ajio.kmm.shared.model.home.CMSData`.

`smali_classes5/Yg.smali` (`.source "AjioHomeFragment.kt"`) is the actual home-screen fragment that resolves and calls `home_cms` (confirmed at two call sites, ~lines 37135–37160 and ~60077–60100), building the `HomeReq` request object (fields: `pageUrl, loggedIn, userType, city, state, zone, pincode, timeStamp, pageID, store, isProductWidgetEnabled, isSTLEnabled, isRecentlyViewedEnabled, apiVersion, isTrendingCategoryEnabled, isImageSearchEnabled, requestType, isVideoEnableOnSTL, isVideoEnableOnCMS, ...`) and passing it into a home use-case (`Lk41;->c(...)`) which ultimately calls `HomeApi.getHomeData` (§1.1 above).

**Decompile-fidelity caveat (affects HTTP-method certainty specifically):** this apk-source was extracted with apktool onto a case-insensitive Windows filesystem. Several obfuscated single/double-letter Kotlin class names collide only by case (e.g. `Lk41;` vs `LK41;`, `Lm02;` vs `LM02;`, `LHZ4;` vs `Lhz4;`) — only one physical `.smali` file can exist per case-insensitive name, so one of each colliding pair was silently overwritten during extraction. Confirmed hit: `k41.smali` on disk is actually a `DialogInterface$OnShowListener`, not the real home use-case class; `HZ4.smali` on disk is `Supervisor.kt`, not the class actually referenced from `HomeApi`'s Ktor builder. This is why the exact opcode that sets the HTTP verb on the `home_cms` Ktor call can't be pinned down from this extraction — **re-decompile on a case-sensitive filesystem (WSL/Linux) if 100% verb certainty is needed before Phase 2**, otherwise confirm empirically via traffic capture.

### 1.2 Other relevant endpoints (from `assets/prod_api_new.json`)

```
homepage.home_content          = https://cms-edge.services.ajio.com/rilfnlwebservices/v2/rilfnl/payloadReductionLandingPage?pageId=%s&uiel=Mobile
homepage.home_curated_widget   = https://search-edge.services.ajio.com/rilfnlwebservices/rilfnl/products/curated/widget/list
homepage.cms_nav_menu          = https://cms-edge.services.ajio.com/storefront/cms/navigation
homepage.nav_menu              = https://cms-edge.services.ajio.com/rilfnlwebservices/v2/rilfnl/navigation/navnodes
homepage.banner_ads_url        = https://mercury.jio.com/delivery/mapi.php     (3rd-party ad banners, not CMS-declared)
misc.banner_ad_different_api   = https://cms-edge.services.ajio.com/storefront/cms/component
misc.third_party_banner_ads    = https://ajio-ba.o-s.io/v2/bsda/pt
ajiogram.feed                  = https://ajiogram-edge.services.ajio.com/feed-engine/v1/feed        (AJIO's Instagram-style "Ajiogram" feed, not the home screen)
ajiogram.feed_pagination       = https://ajiogram-edge.services.ajio.com/feed-engine/v2/feed
plp.new_user_banner            = https://api.services.ajio.com/rilfnlwebservices/v2/rilfnl/plpTopBannerURL
```

`home_cms` (`storefront/cms/page`) is the one that matches CLAUDE.md's "home-screen feed" description — it returns the full home page layout (slots/components/banners). `ajiogram/feed` is a different feature (a social-style content feed), not the home-screen banner carousel, despite the similar name — **don't confuse the two in Phase 3**.

There's also a **separate, banners-only Retrofit-based endpoint** worth having in Phase 3's back pocket if `home_cms` turns out to be too heavyweight or awkward to call directly: `misc.banner_ad_different_api` = `GET https://cms-edge.services.ajio.com/storefront/cms/component` (`BannerAdsServiceApi.getBannersForPages(@Url, @QueryMap, @Query("userCohortValues"))`). HTTP verbs for this whole Retrofit family are **confirmed with certainty** (unlike `home_cms`'s Ktor call) via the actual `retrofit2.http.*` annotation class definitions (`smali_classes6/UD1.smali` = `GET.java`, `smali_classes6/AD3.smali` = `POST.java`, `smali_classes6/To5.smali` = `Url.java`):

| Method | Endpoint (config key) | Verb |
|---|---|---|
| `BannerAdsServiceApi.getBannerAds` | dynamic `@Url` | **GET** |
| `BannerAdsServiceApi.getBannersForPages` (`banner_ad_different_api`) | `storefront/cms/component` | **GET** |
| `BannerAdsServiceApi.getThirdPartyAdBannersForPages` | dynamic `@Url` | **GET** |
| `BannerAdsServiceApi.callBannerImpressionAPI` / `callClickImpressionAPI` | dynamic `@Url` | **GET** |
| `BannerAdsServiceApi.callPlaImpressionAPI`, `pdpBannerAdApi` | dynamic `@Url` | **POST** |
| `AjioHomeApi.getCuratedWidgetOptions` (`home_curated_widget`) | `search-edge.../products/curated/widget/list` | **GET** |
| `AjioHomeApi.getAjioCouponPromotions` | dynamic `@Url` | **GET** |
| `AjioHomeApi.initiateBuyCouponRequest` | dynamic `@Url` | **POST** (mutating — out of scope, read-only project) |

All of these Retrofit interfaces take the path as a dynamic `@Url` parameter (never hardcoded in the interface) — the concrete URL always comes from `assets/prod_api_new.json` via `UrlHelper`, same as the KMM path.

### 1.3 Base URL / environment selection

Base URL and API host switch by build flavor, read from `BuildConfig`-equivalent flavor string compared against `"prod"`/`"qa"`/`"qaregression"`/`"dev"`/`"replica"` (`smali_classes5/Bm3.1.smali`, lines ~145–220, class `OkHttpClientInitializer.kt`):

```
prod            -> https://www.ajio.com
qa / qaregression / dev -> https://qa.services.ajio.com
replica         -> https://gcprep-ajio.jio.com
```

This is the `backend` value; the actual `cms-edge.services.ajio.com` / `api.services.ajio.com` / `search-edge.services.ajio.com` hosts used for API calls are separate edge hostnames baked into `prod_api_new.json` (a `qa_api_new.json` / similar almost certainly exists for non-prod, not yet located).

---

## 2. Banner/widget data model

Full response shape (KMM / kotlinx.serialization path, the current one):

```
CMSData
├── data: Data                                          (smali_classes5/.../home/CMSData.smali, Data.smali)
│   ├── page: Page
│   │   ├── pageName: String
│   │   ├── pageTitle: String
│   │   └── slots: List<Slot>                           (Page.smali)
│   │       └── Slot
│   │           ├── position: Int
│   │           └── component: Component                (Slot.smali)
│   │               ├── componentName: String            <- CMS widget-type discriminator
│   │               ├── type: String
│   │               ├── heading / subHeading / bgColor / bottomPadding / carouselTimer / isDynamicPage
│   │               └── banners: List<Banner>            (Component.smali)
│   ├── baseValue: BaseValue
│   └── seo: SEO
├── usergroup: String   (from response header "usergroup")
└── httpStatusCode: Int
```

There is also a parallel, older Gson/Retrofit-era model (`com.ril.ajio.services.entity.*`) with the same shape (`BannerResponse -> Component -> Banner`) — likely legacy/unused-in-current-UI but still present and referenced (see §1.1).

### 2.1 `Banner` (KMM) — `smali_classes5/com/ril/ajio/kmm/shared/model/home/Banner.smali`

Largest, most-complete banner model (32 ctor params). Full field list:

| Field | Type | Notes |
|---|---|---|
| `bannerUrl` | String | **destination — primary click-through URL** |
| `extendedUrl` | String | **destination — secondary/extended link** |
| `ctaSettings` | `CTASettings` | **destination wrapper** (see §2.2) |
| `dynamicPageMetadata` | `DynamicPageMetadata` | **nested destination wrapper** (see §2.3) — can carry a *second*, independent `ctaSettings` |
| `hotspots` | `List<Hotspot>` | each has its own `hotspotUrl` destination |
| `bannerType`, `bannerCategory` | String | |
| `products` | `List<CMSProduct>` | |
| `subComponents` | `List<SubComponent>` | image sub-regions, no nav field |
| `uuid`, `adSpotId`, `curatedId` | String | |
| `height`, `width` | Double | |
| `showTimer`, `timerType`, `timerLabel`, `timerEndTime`, `duration` | | countdown-banner support |
| `thumbnailImage`, `mediaType`, `videoSettings`, `audioSettings` | | video/audio banner support |
| `widgetLevel`, `feedLogic`, `noOfProducts` | | |
| `enableAdsOnPlp` (default `true`), `isAdBanner` (default `false`), `adSpotId` | | |
| `altText`, `showDefault`, `isInfiniteScroll`, `isCurated` | | |

### 2.2 `CTASettings` — `smali_classes5/com/ril/ajio/kmm/shared/model/home/CTASettings.smali`
```
enable: Boolean
ctaText: String
ctaLink: String        <- destination
bgColor: String
textColor: String
```
(Legacy equivalent: `com.ril.ajio.services.entity.CtaSettings`, identical shape.)

### 2.3 `DynamicPageMetadata` — `smali_classes5/com/ril/ajio/kmm/shared/model/home/DynamicPageMetadata.smali`
```
extendedUrl: String        <- destination
extendedImageUrl: String
ctaSettings: CTASettings   <- nested destination wrapper (again has ctaLink)
title, subTitle: String
mediaType: String
duration: Long
videoSettings, audioSettings
```

### 2.4 `Hotspot` — `smali_classes6/com/ril/ajio/services/entity/Hotspot.smali` (legacy; KMM variant exists at `smali_classes5/.../home/Hotspot.smali`, same shape expected)
```
hotspotUrl: String     <- destination, per tappable region
type: String
x, y, width, height: Double
```

### 2.5 Ad-tracking metadata — `smali_classes5/com/ril/ajio/kmm/shared/model/home/transform/BannerAdsMetaData.smali`
```
url: String             <- destination (ad click-through)
clickTracker, impressionTracker: String   (tracking pixels, not destinations)
bannerId, bannerIndex, bannerType, campaignId, clickId, clientId, creativeName, ctaFb, imageName, impressionStatus, sclId, uclId
```

### 2.6 Field-name summary — no field is literally named `redirectUrl`/`deepLink`/`targetUrl`/`actionUrl`/`navigationUrl`/`linkType`/`linkValue`

AJIO's own convention instead:
- flat `bannerUrl` / `extendedUrl` string fields for the primary tap target,
- a `ctaSettings`/`CTASettings` **action sub-object** (`{enable, ctaText, ctaLink, bgColor, textColor}`) for CTA-button navigation — **note a `Banner` can carry two independent `ctaSettings`**: its own, and one nested inside `dynamicPageMetadata`,
- a per-hotspot `hotspotUrl` for tappable image regions.

**Implication for Phase 4:** `destination_raw` should be resolved by checking, in priority order: `ctaSettings.ctaLink` (if `enable=true`) → `dynamicPageMetadata.ctaSettings.ctaLink` → `bannerUrl` → `extendedUrl` → per-hotspot `hotspotUrl` if the banner has multiple tap targets. Confirm this priority against real data in Phase 2/5 — it's inferred from field presence, not confirmed against a live payload.

---

## 3. Deep-link URL grammar

### 3.1 Manifest intent-filters — `AndroidManifest.xml`, lines 133–980

**Every deep link routes to a single activity: `com.ril.ajio.launch.activity.SplashScreenActivity`** (`exported="true"`). A disabled activity-alias (`DynamicIconActivity2`, seasonal launcher icon, `enabled="false"`) duplicates the same filters but is inert.

**Custom scheme:**
```xml
<data android:scheme="ajioapps"/>   <!-- ajioapps://... -->
```
App code rewrites `ajioapps://` → `https://` before further processing (see §3.2).

**Web host `*.ajio.com` (http/https, `android:autoVerify="true"` — Android App Links), path grammar:**

| Path pattern | Destination type |
|---|---|
| `/` or empty | Home |
| `/p/...` (also `/*/p/...`) | **Product detail page (PDP)** |
| `/c/...` (also `/*/c/...`) | **Category listing (PLP)** |
| `/b/...` | **Brand listing (PLP)** |
| `/s/...`, `/s1/...`, `/search`, `/find/...` | Search |
| `/cp/...` | Campaign page |
| `/brand/...` | Brand page |
| `/shop/...` | Shop/collection |
| `/sections/...` | CMS sections page |
| `/static-cms/...` | Generic CMS/webview page |
| `/capsule/...` | Capsule collection |
| `/jio-engage`, `/couponbonanza`, `/assured-gifts`, `/offers` | Campaign/promo pages |
| `/login`, `/my-account`, `/wishlist`, `/cart`, `/cart/add`, `/ajio-wallet`, `/sharedcloset` | Account/cart (not product-facing) |
| `/games`, `/gamezone`, `/top-shopper` | Gamification |
| `/faq`, `/contactus`, `/selfcare`, `/help/termsAndCondition`, `/return-refund`, `/verify/`, `/membership`, `/companion_app`, `/rcc`, `/odp` | Support/misc, not banner-relevant |

**Other autoVerify hosts** (also routed to `SplashScreenActivity`): `ajio.page.link` (Firebase Dynamic Links), `ajio.onelink.me` / `ajioapps.onelink.me` (AppsFlyer OneLink attribution wrappers — the real destination is typically embedded in a query param on these, unwrapped client-side, see §3.2).

Non-AJIO schemes also declared in the same manifest (payment-app callbacks, unrelated to banners): `upi://`, `nb://pay`, `credpay://checkout`, `juspay://pay`.

### 3.2 Client-side handling — no dedicated `DeepLink`/`Router` class

There is **no dedicated `Deeplink`/`Navigator`/`Router`/`UriParser` class** anywhere under `com.ril.ajio.*`. Logic is inline, duplicated across two places:

1. **`SplashScreenActivity`** (`smali_classes5/com/ril/ajio/launch/activity/SplashScreenActivity.smali`, ~lines 1570–1790 and 4020–4220): reads `getIntent().getData()`, does **plain substring containment checks** for `/p/`, `/c/`, `/b/`, `/s/` (via `kotlin.text.StringsKt.contains`) to set two static classification flags (`sB3.e` = "is PDP-type", `qD3.a` = "is PLP-type: category/brand/search"), used for attribution/analytics only at this stage. Stashes the raw `Intent`/`Uri` in a `Bundle` under key **`"deeplinkData"`** and hands off to `AjioHomeActivity`.

2. **`AjioHomeActivity.M3(String)`** (`smali_classes5/com/ril/ajio/home/AjioHomeActivity.smali`, lines 31087–33017 — the real `handleDeeplink()`): the actual classification/dispatch point.
   - De-dupes repeat fires within 3 seconds.
   - **Unwraps attribution wrapper links**: if the URL has a query param `deep_link_value` (used by marketing SDKs / OneLink), extracts that value; if it starts with `"ajioapps"`, rewrites the scheme to `"https"`. (`AppUtils.f(String)`, `smali_classes6/com/ril/ajio/utility/AppUtils.smali`, lines 6467–6655.)
   - Re-applies the same `/p/`, `/c/`, `/b/`, `/s/` substring classification.
   - **Multi-store/tenant disambiguation**: checks for `www.ajio.com` vs `luxe.ajio.com` (and references a `rush` store elsewhere) to route to the correct backend tenant — `AppUtils.P(String)` extracts the subdomain via regex `([a-zA-Z0-9-]+)\.(services\.)?ajio\.com`.
   - Populates a `com.ril.ajio.plp.PLPExtras` object and hands off to `NewProductListFragment` for `/c/`, `/b/`, `/s/`-type links (category, brand, and search **share the same PLP fragment**, differentiated by which `PLPExtras` field is set — exact field-by-field path→field mapping wasn't resolved from static analysis, see caveat below).
   - `/p/` (product) links are classified separately but the destination PDP fragment/activity wasn't confirmed within the scanned code.

**Important: the app does not parse IDs out of the URL path on-device** — no `Uri.getPathSegments()`/`getLastPathSegment()` calls were found anywhere in `com.ril.ajio.*`. It only does substring-based *type* classification client-side. The friendly slug+ID → actual category/brand/product resolution happens **server-side**. The strongest lead for this is:

```
com.ril.ajio.services.network.api.NavigationTypeApi
  getCMSCategoryNavigation(@Url url, @Body JsonObject, contentType, requestId) -> CmsNavigationData
```
(`smali_classes6/com/ril/ajio/services/network/api/NavigationTypeApi.smali`) — a server-side URL-to-navigation-data resolver. This is the most promising candidate for Phase 4's `deeplink_resolve.py` if a banner's `destination_raw` turns out to be a friendly URL rather than a directly-typed deep link — **needs live traffic capture in Phase 2 to confirm its request/response shape**, no call site wiring a raw deep-link URL into it was found statically.

**Caveat (decompile integrity):** `AjioHomeActivity.smali` references classes `LzD3;`/`LjA3;` (lowercase-initial) but the only matching files on disk (case-insensitively, since apktool ran on a Windows/case-insensitive filesystem) are `ZD3.smali`/`JA3.smali` — unrelated classes. These two classes' real content was likely overwritten during decompilation and is **not recoverable from this tree**. If exact PLPExtras field-mapping logic lived there, it's lost; re-decompiling on a case-sensitive filesystem (WSL/Linux) would recover it if ever needed.

---

## 4. Authentication mechanism

**No HMAC/request-signing scheme exists anywhere in the network layer.** The only crypto usage in the OkHttp stack is `MessageDigest.getInstance("SHA-256")` for TLS certificate pinning (§5) — not for signing requests. This is good news for Phase 3: no signing secret to reverse-engineer.

### 4.1 Bearer token, attached globally by an interceptor

Two (functionally equivalent) global OkHttp interceptors — `UnifiedInterceptor` and `RequestResponseInterceptor` (`smali_classes6/com/ril/ajio/services/network/interceptors/`) — add default headers to every request and manage the `Authorization` header:

**Default headers on every request:**
```
Accept: application/json
User-Agent: <IUserAgentProvider>
client_type: Android
client_version: <app version>
ad_id: <advertising ID, if available>
device-id: <NetworkUtil.getAjioTraceId()>, if available
X-Tenant: B2C
```
(plus anything persisted under the `"pref_headers"` preference key). `ad_id`/`device-id` are added opportunistically (only if non-empty) — **not required** for a call to succeed.

**Token flow** (`ServiceUtil.getToken`, `smali_classes6/com/ril/ajio/services/utils/ServiceUtil.smali`):
- Logged-in user → `userInformation.getSecureAccessToken()`.
- Not logged in → `AnonymousToken.getAccessToken()` (`smali/com/ajio/ril/core/network/AnonymousToken.smali`, a static in-memory guest-token holder).
- On 401, refreshes and retries once; on `InvalidGrantError`, force-logs-out via a broadcast (`invalid_refresh_token`).

### 4.2 Guest/anonymous token bootstrap — no login required

```
POST https://api.services.ajio.com/uaas/jwt/token/client
Headers: Accept: application/json, client_type: Android, client_version: <x.y.z>, X-Tenant: B2C
Body (form): grantType=client_credentials&clientName=trusted_client&clientSecret=secret
```
`clientName`/`clientSecret` are **literal hardcoded constants** in the smali (not per-device secrets). Response parses into `UserInformationModel` (`access_token`, `refresh_token`); the guest access token is cached in the static `AnonymousToken` holder and reused as `Authorization: Bearer <token>` on subsequent calls.

Logged-in refresh uses the same shape against a different endpoint (`oauth_token_uaas_generate_refresh_token` = `.../uaas/jwt/token/refresh`) with `Authorization: Bearer <refresh_token>`.

**Conclusion: the home-feed endpoint does not require a logged-in session.** A standalone script can reproduce the guest OAuth call above with no emulator, no device ID, and no login, then call `home_cms` with `Authorization: Bearer <guest_token>`. **This should still be empirically confirmed in Phase 2** — static analysis shows the mechanism exists and is wired to work this way, but whether `home_cms` specifically accepts the guest token (vs. requiring a logged-in one) hasn't been observed on the wire yet.

**[A] Session handling default:** given the above, §6's session-bootstrap requirement is very likely simple (a single unauthenticated POST, cacheable with normal token-expiry handling) rather than needing a full Appium-driven emulator bootstrap. Flagging for user confirmation once Phase 2 verifies it live — if true, `qa/session_bootstrap.py` can skip Appium entirely and just do the POST.

---

## 5. Certificate pinning

### 5.1 What's in `apk-source` (the unmodified decompile)

Pinning **is present**, but implemented unusually — split across two independent layers:

**Layer 1 — chain/CA trust: already fully disabled by the app itself.**
`SSLProvider.getX509TrustManager()` (`smali/com/ajio/ril/core/network/SSLProvider.smali` → inner class `SSLProvider$1.smali`) implements `X509TrustManager` with **no-op `checkServerTrusted`/`checkClientTrusted`** (explicitly `@SuppressLint("TrustAllX509TrustManager")`). So standard chain-of-trust validation is a no-op app-wide, regardless of any proxy CA — **this is not what blocks mitmproxy.**

**Layer 2 — the actual gate: a custom SHA-256 public-key pin check in the HostnameVerifier.**
`OkHttpClientProvider.handleHostnameVerifiers()` (`smali_classes6/com/ril/ajio/services/network/OkHttpClientProvider.smali`) installs a custom `HostnameVerifier` that, for hosts matching `isPinningRequiredForHost()` (substring match on `"ajio.com"`, `"uat"`, `"qa.services"`, `"qa-ajiogram.services"`, `"qa-luxe.services"`), computes SHA-256 hashes of the server's certificate public keys and checks them against a pin set from `SslPinningUtil.getSslPinsForPinning()`. That pin set falls back to ~23 hardcoded base64 SPKI-pin hashes hardcoded in `SSLProvider` (`SSL_FALLBACK_KEYS`/`LEAF_SSL_FALLBACK_KEYS`, `smali/com/ajio/ril/core/network/SSLProvider.smali` lines 30–182) unless overridden by `SslPinningUtil.update(...)` (a runtime/remote-config hook — not traced further, likely Firebase Remote Config). **This will reject mitmproxy's self-signed leaf cert** since its public key won't match any pinned hash.

### 5.2 The pinning bypass is a documented, first-party toggle — not something that needs patching

The whole pin check is gated by a boolean, literally named in the Kotlin metadata as **`sslPinningDisabledFromAppSettings`**, threaded through `AjioOkHttpClient.Builder.build()` → `OkHttpClientProvider.getClient/getOptimisedClient(..., p3: Boolean, ...)`. If `true`, `handleHostnameVerifiers()` (the pin check) is **skipped entirely**.

Its value is read directly from **SharedPreferences**:
```
File: "com.ril.ajio_preferences"  (SharedPreferences, MODE_PRIVATE)
Key:  "ssl_pinning"   (Boolean, default false)
```
(`smali_classes5/Bm3.1.smali` lines ~113–126: `BasePreferences.getPreference("ssl_pinning", false)`.)

**And there's an in-app Dev Settings screen with a literal checkbox for it:**
```
res/layout/activity_dev_settings.xml:
  <CheckBox android:id="@id/ssl_pinning" ... android:text="Disable SSL Pinning" />
```
wired up in `com.ril.ajio.devsettings.DevSettingsActivity` (`smali_classes5/com/ril/ajio/devsettings/DevSettingsActivity.smali`, references at lines 551, 559, 1710, 1735, 1755) which writes that same `"ssl_pinning"` preference key.

`DevSettingsActivity` is declared in the manifest (line 128) with **no `exported` attribute (defaults to not-exported) and no intent-filter** — I did not find any in-app trigger (long-press, tap-count gesture, hidden menu item) that launches it from within `com.ril.ajio.*` smali. It may be reachable via `adb shell am start -n com.ril.ajio/com.ril.ajio.devsettings.DevSettingsActivity` (works from `adb shell` on most devices even for non-exported activities, but not guaranteed on every Android version/OEM), or there may be an entry point in obfuscated/JS (React Native) code not covered by this search. **This is the cleanest lead for Phase 2** — flip that one preference (via the Dev Settings screen, or `adb shell run-as com.ril.ajio` writing to the prefs XML directly, or Frida) rather than reaching for a wholesale rebuild.

### 5.3 What actually changed in `inputs/modified-apk/ajio_media3_fix.apk`

**The filename itself is a red flag: `ajio_media3_fix.apk` suggests this patch addresses a `media3`/ExoPlayer issue, not SSL pinning.** Confirmed by extracting all 7 `classes*.dex` files from the modified APK and searching for the pinning-related class/method name strings found in `apk-source`:

```
isPinningRequiredForHost, SslPinningUtil, TrustAllX509TrustManager, checkServerTrusted,
OkHttpClientProvider, handleHostnameVerifiers, getValidSSLFallbackPins,
getValidLeafSSLFallbackPins, network_security_config, CertificatePinner
```

**All of these strings are still present** in the modified APK's dex files (concentrated in `classes6.dex`, matching `apk-source`'s `smali_classes6`). This only proves the classes/methods weren't *deleted or renamed* — it does **not** rule out an in-place bytecode patch (e.g. forcing `isPinningRequiredForHost` to always return `false`, or `isValidCertificatePin` to always return `true`) — that level of change wouldn't show up as a missing string. Also compared `res/xml/network_security_config.xml` from `apk-source` (unremarkable — only a `debug-overrides` block trusting system+user CAs, which only takes effect if the app is `android:debuggable="true"`) but could not decode the modified APK's binary equivalent (no `apktool`/`aapt2` available in this environment — see §5.4).

**Working hypothesis, to be confirmed in Phase 2:** given the filename and that string-level pinning code is untouched, `ajio_media3_fix.apk` most likely still enforces SSL pinning as designed, and the pinning bypass path for Phase 2 should be either (a) the `"ssl_pinning"` SharedPreferences flag from §5.2, or (b) Frida (CLAUDE.md's §5 Phase 2 plan already anticipates this — "if it still has pinning ... set up Frida"). **Don't assume pinning is already removed just because this is "the modified APK."**

### 5.4 Tooling gap

No `apktool`, `baksmali`, or `aapt2` was available in this environment to fully decompile/diff the modified APK's binary XML (manifest, `network_security_config.xml`) or disassemble its dex bytecode for a true instruction-level diff. The comparison above is string-presence-only. If a deeper diff is needed later, either install `apktool`/Android SDK `build-tools` (`aapt2`), or do the diff on-device empirically in Phase 2 (does the modified APK's traffic actually get through mitmproxy without touching the `ssl_pinning` preference?).

---

## Summary for Phase 2

1. **Endpoint to capture:** `POST(?) https://cms-edge.services.ajio.com/storefront/cms/page` (confirm verb; also grab `home_content`/`home_curated_widget` if `home_cms` alone doesn't carry banner destinations).
2. **Auth to verify:** try the guest OAuth bootstrap (`POST https://api.services.ajio.com/uaas/jwt/token/client`, `grantType=client_credentials&clientName=trusted_client&clientSecret=secret`) completely outside the emulator first — if it works and `home_cms` accepts the resulting token, §6's Appium-based session bootstrap may not be needed at all.
3. **Pinning bypass to try first:** the `"ssl_pinning"` SharedPreferences flag (`com.ril.ajio_preferences` / key `ssl_pinning` / bool) — via Dev Settings UI if reachable, or `adb shell run-as com.ril.ajio` + editing the prefs XML, or a one-line Frida hook — before falling back to full Frida SSL-unpinning scripts.
4. **Deep-link grammar to validate against real taps:** `/p/` (PDP), `/c/` (category), `/b/` (brand), `/s/` (search) — and check whether real banner `ctaLink`/`bannerUrl` values are raw deep links or friendly `ajio.com` URLs requiring the `NavigationTypeApi.getCMSCategoryNavigation` server-side resolve step.
5. **Response shape to confirm:** `CMSData.data.page.slots[].component.banners[]`, and which of `ctaSettings.ctaLink` / `dynamicPageMetadata.ctaSettings.ctaLink` / `bannerUrl` / `extendedUrl` actually carries the destination in practice (all exist as fields; real payloads will show which are populated for which banner types).

**Update:** all five of the above turned out to point at the wrong endpoint/model. See §6.

---

## 6. Phase 2 live capture — actual findings (supersedes §1–§2 for API-client purposes)

### 6.0 Setup notes (environment-specific, may matter for reproducing this)

- Emulator: AVD `ajio_nps`, API 36 (Android 16), `x86_64`, launched via `emulator -avd ajio_nps -no-window -no-audio -gpu auto`. App: `com.ril.ajio` v9.38.1 (versionCode 3761), **not debuggable** (`run-as` fails; `pkgFlags` has no `DEBUGGABLE`) despite earlier assumption — root access came from `adb root` working directly (the AVD system image itself is a rootable `userdebug`/`eng` build, `ro.debuggable=1`), not from the app being debuggable.
- **SSL pinning bypass:** flipped the `ssl_pinning` SharedPreferences flag to `true` (`/data/data/com.ril.ajio/shared_prefs/com.ril.ajio_preferences.xml`, added `<boolean name="ssl_pinning" value="true" />`) by pulling the file as root, editing it, pushing it back, and restoring its original owner (`u0_a230:u0_a230`), mode (`660`), and SELinux context (`u:object_r:app_data_file:s0:c230,c256,c512,c768`) via `chown`/`chmod`/`restorecon -F`. This alone was **not** sufficient to get mitmproxy traffic through (see next point) but is likely still necessary — not isolated whether it's strictly required given the CA-trust fix below fixed everything at once.
- **CA trust — the real blocker, and it's Android-version-specific:** installing mitmproxy's CA into `/system/etc/security/cacerts/` (the traditional approach) **had no effect on this device** — literally every HTTPS connection from every app failed with "client does not trust the proxy's certificate," not just AJIO's. Root cause: **on Android 14+ (API 34+), the system CA trust store moved into a read-only Conscrypt APEX module** (`/apex/com.android.conscrypt/cacerts/`, backed by a dm-verity-protected block device) as part of Google's move to make root CA updates independent of full OTAs. `/system/etc/security/cacerts/` is vestigial on these versions. Fix: mount a `tmpfs` overlay over `/apex/com.android.conscrypt/cacerts/` (same technique, different path) containing all 143 original certs plus mitmproxy's CA, with matching ownership (`system:system`), permissions (`644`), and **SELinux context set explicitly** (`chcon u:object_r:system_security_cacerts_file:s0`, not `restorecon` — `restorecon` on this tmpfs path resolves to the wrong type, `appdomain_tmpfs`, giving a false sense that context is fine when it will still be denied).
- **Even with the correct cert in place, the fix didn't take effect until the Android framework was restarted** (`adb shell stop && adb shell start` — not a full emulator reboot, which would have unmounted the tmpfs overlays). Working theory: Conscrypt's in-memory trust-anchor set is built once by `zygote`/`system_server` at boot and inherited by every forked app process; mounting the overlay after boot doesn't get picked up by already-running or freshly-forked processes until the framework (and therefore zygote) restarts. A `stop`/`start` cycle is much cheaper than a full reboot and preserves the tmpfs mounts.
- Net effect: **the app does not crash or "refuse to run" when it detects the untrusted cert** — it shows a graceful in-app error screen ("Unable to load content, please check your connection and try again" with a Refresh button). This is much less alarming than it sounded before capture; no crash-on-tamper-detection was observed. It's unclear whether this graceful failure is TLS-handshake-level (most likely) or an Akamai Bot Manager decision (see §6.3) — once the CA/framework-restart fix was applied, everything succeeded, so the two weren't distinguished empirically.

### 6.1 The real home-feed endpoint: Fynd Platform "theme" API

```
GET https://api.services.ajio.com/api/service/application/theme/v1.0/{applicationId}/{slug}?company=1
```

Confirmed on the wire, e.g.:
- `.../theme/v1.0/6924384620d2931b59eb94ec/home?company=1` — the home screen
- `.../theme/v1.0/6924384620d2931b59eb94ec/menswear?company=1`, `.../womenswear?company=1`, `.../kidswear?company=1` — the Men/Women/Kids top-nav tabs, same shape

`{applicationId}` (`6924384620d2931b59eb94ec`) is a fixed per-tenant ID (constant across calls in this session). `{slug}` matches the `path`/`slug` fields inside the response itself (e.g. `"slug": "home"`, `"slug": "sections/menswear"`) — i.e. it's a CMS page-slug lookup, not a numeric page ID.

This is **[Fynd Platform](https://fynd.com) / GoFynd's headless commerce "Theme" API** (`x-fynd-trace-id` response header, `x-sc-cache-key` values literally containing `store1.ajiojcp.com` — "AJIO JioCommerce Platform" — confirming AJIO's storefront runs on/through Fynd's platform, not the AJIO-native Hybris-style `cms-edge.services.ajio.com` backend Phase 1 found statically). The static-analysis `home_cms`/`storefront/cms/page` endpoint and its Kotlin `Banner` models were **not observed on the wire at all** during this capture — they may be dead code, an A/B-tested alternate path, or reserved for a different surface (e.g. web/PWA); this needs to stay an open question, not be assumed dead.

HTTP method is **GET** (no request body; query string is just `?company=1`, all the real context comes in via headers, see §6.2).

### 6.2 Request headers (auth + personalization context)

```
accept: application/json, text/plain, */*
authorization: Bearer <token>              -- from the guest OAuth flow, §6.3
x-currency-code: INR
user-agent: Platform/Android
x-location-detail: {"country":"INDIA","city":"BENGALURU","pincode":"560029","state":"KARNATAKA"}
user-groups: l1:nontransacted|l2:p_null,false,unisex,noasp     -- note: BOTH spellings sent
user_groups: l1:nontransacted|l2:p_null,false,unisex,noasp     -- (hyphen and underscore variants, same value)
x-experimental-features: CMSABExp2,CMSABExp10,CMSABExp4         -- active A/B test bucket IDs
x-fp-sdk-version: 1.10.6-9
x-fp-date: 20260921T041433Z                -- ISO8601-basic UTC timestamp, changes per request
x-fp-signature: v1.1:<64 hex chars>        -- per-request signature, see §6.4
device-id: <persistent per-install id>
accept-encoding: gzip
cookie: <redacted>                         -- includes Akamai bot-manager cookies (_abck etc.) and session state
cache-control: no-cache                    -- present on a forced refresh (pull-to-refresh), absent on initial load
x-skip-cache: true                         -- also only present on forced refresh
```

Response carries a matching cache layer on Fynd's side: `x-sc-cache-status: MISS|EXPIRED|HIT` (varies per request) and `x-sc-cache-key` (a composite of URL + location + platform + user-group + experiment-bucket — i.e. **the CDN cache key itself is personalized**, so two guest sessions in different cities/segments can get different cached home pages).

### 6.3 Auth: guest OAuth bootstrap — confirmed exactly as Phase 1 predicted

```
POST https://api.services.ajio.com/uaas/jwt/token/client
Headers: client_type: Android, accept: application/json, client_version: 9.38.1,
         user-agent: Ajio/9.38000.0 (Android 16), ad_id: <advertising id>,
         device-id: <persistent id>, x-tenant: B2C,
         x-acf-sensor-data: <long Akamai Bot Manager sensor blob>
Body (form): grantType=client_credentials&clientName=trusted_client&clientSecret=secret

Response 200:
{
  "access_token": "<redacted — bearer token>",
  "token_type": "bearer",
  "expires_in": 1545719,     -- ~17.9 days
  "scope": "extended",
  "redirectToOTP": false
}
```

**This is exactly the hardcoded-credential guest flow Phase 1 predicted from static analysis — confirmed working, no login required.** The resulting token is accepted by the Fynd theme endpoint (`authorization: Bearer <token>` on all the `theme/v1.0/...` calls). Given the ~18-day expiry, for Phase 3/6 purposes this token is effectively **static per QA run** — cache it and only refresh on a 401, no need to re-bootstrap per feed check.

**One new wrinkle:** the guest-token request itself carries `x-acf-sensor-data`, a very large (~5KB) opaque Akamai Bot Manager payload (base64/binary blob, present in the app's `com.akamai.botman.preferences.xml` as `sensor_data`, regenerated periodically). This is **not** a static value — it's Akamai's native device/behavior fingerprint, refreshed by `libakamaibap.so` (bundled native library, arm64 split only — see §6.5). It was **not required to be "fresh" from this specific device** for the guest-token call to succeed in this test (the request succeeded), but that doesn't mean a standalone script's request (with no sensor data at all, or a stale/replayed one) will always be accepted — Akamai's server-side risk scoring can vary by IP reputation, request volume, and other server-side signals not observable from a single successful test. **Flag this as a real risk for Phase 3, not a solved problem**: a headless script hitting these endpoints at a modest, human-plausible interval (matching the [A] 2-hour default cadence) is far less likely to trip Akamai's abuse heuristics than one polling aggressively or omitting all the standard mobile headers (`client_type`, `client_version`, `user-agent`, `device-id`) — send all of those even without real sensor data.

### 6.4 The signing scheme Phase 1 missed: `x-fp-signature`

Phase 1 said "no HMAC/request-signing scheme found." That search only covered the decompiled Kotlin/Java (smali). **The app also bundles a full React Native JavaScript app** (`libhermes.so`/`libreactnative.so`/`libjsi.so` in `inputs/additional-apks/split_config.arm64_v8a_trace.apk:lib/arm64-v8a/`, and the JS bundle itself at `assets/index.android.bundle`, 4.9MB, **with a full source map** at `assets/index.android.bundle.map`, 24.8MB) — and the Fynd Platform theme calls are signed from *there*, not from native Android code.

Confirmed by grepping the bundle directly — the signing code is plain (minified but not obfuscated/native) JS, structured like an AWS-SigV4-style request signer:
```
...r(d[4]).sign(q); ...n.headers["x-fp-date"]=x["x-fp-date"], n.headers["x-fp-signature"]=x["x-fp-signature"]...
...this.prepareRequest(),this.request.headers["x-fp-signature"]=this.signature(),{"x-fp-signature":...,"x-fp-date":...}
```
i.e. a `Signer` class/object with `.prepareRequest()`, `.signature()`, `.sign()` methods, taking `this.request` (method, path, headers, query, body) as input, format `x-fp-signature: v1.1:<64 hex chars>` (looks like an HMAC-SHA256 hex digest, algorithm version-tagged `v1.1`).

**This is genuinely reverse-engineerable** — unlike a native/compiled signing routine, this is readable JavaScript with a source map available, meaning Phase 3 can very likely pretty-print/de-minify it and read the exact signing algorithm (key derivation, canonical-request format, which headers/fields are included) directly, the same way you'd read AWS SigV4 signer source. This is materially better news than Phase 1's original (wrong) "no signing scheme" conclusion would have suggested, but it **is** real work that Phase 3 needs to budget for — this is not a "just add a Bearer header" API client, it's "add a Bearer header AND replicate an HMAC-style per-request signature."

#### 6.4.1 Extracted algorithm (2026-09-21, read directly from `assets/index.android.bundle`)

This is Fynd's open-source signer (`@gofynd/fp-signature`, https://github.com/gofynd/fp-signature) bundled unmodified. AJIO's call site is `r(d[4]).sign(q)` with **no options argument**, so the library default secret applies: `secret: "1234567"` — the public library default, not an AJIO-specific credential. The signature is an integrity/replay check, not authentication (auth is the Bearer token from §6.3).

```
sign(request):                       request = {method, host, path(+query), body, headers}
  headers["x-fp-date"] = now UTC as YYYYMMDDTHHMMSSZ   (getDateTime; reused if already set)
  signature = "v1.1:" + HMAC_SHA256(key=secret, msg=stringToSign).hex
  stringToSign    = x-fp-date + "\n" + SHA256(canonicalString).hex
  canonicalString = join("\n", [
      METHOD (default GET),
      canonicalPath,           # split on "/", collapse "//", resolve "."/"..", encodeRfc3986Full each segment
      canonicalQuery,          # keys+values encodeRfc3986Full, sorted by key (array values sorted), "k=v" joined by "&"
      canonicalHeaders + "\n", # "lowercase-name:value" lines, sorted by lowercase name
      signedHeaders,           # the same lowercase names, sorted, joined by ";"
      SHA256(body or "").hex ])
  Signed headers = only those matching  x-fp-.*  or  host   (the Host header is added from request.host)
  Never signed (HEADERS_TO_IGNORE): authorization, connection, x-amzn-trace-id, user-agent, expect, presigned-expires, range
  Primitives: crypto-js SHA256 and HmacSHA256, default hex output
```

In practice the signed set is `host`, `x-fp-date`, `x-fp-sdk-version` (set by the app from the FDK client version, see §6.2). Query string comes from `path`, minus any `x-fp-signature` param. **Phase 3 must verify a Python port against the signatures in the saved capture before trusting it** (details such as the exact `getDateTime` formatting, header value trimming and `encodeRfc3986Full` are not yet confirmed byte-for-byte).

### 6.5 Certificate pinning / anti-tamper — corrected

- Confirmed the app does **not** crash when the TLS handshake fails against an untrusted cert (§6.0) — it degrades gracefully to an in-app error screen. The originally-reported "doesn't run" behavior was almost certainly this graceful failure screen, not a crash or an active tamper-detection lockout.
- **Akamai Bot Manager is real and active** (`libakamaibap.so` native library in the arm64 split, `com.akamai.botman.preferences.xml` with `sensor_data`/`cpr_token`/`device_id`, `x-acf-sensor-data` request header, dedicated `/_bm/get_params` and `/_bm/get_info` endpoints on `api.services.ajio.com` called at app startup) — but in this test, with the CA-trust fix applied, **all Akamai-related calls returned 200 OK** and the app functioned normally. No block/challenge was observed. This doesn't rule out Akamai mattering for Phase 3 (see §6.3's caution about request volume/patterns), but it did not block this session's traffic.
- The native library manifest for the `arm64-v8a` split (`inputs/additional-apks/split_config.arm64_v8a_trace.apk`) also confirms: React Native (`libreactnative.so`, `libhermes.so`, `libjsi.so`, `libworklets.so`, `libreanimated.so`), Sentry (`libsentry.so`), image handling (`libavif_android.so`, `libglide-webp.so`, `libstatic-webp.so`, `libgifimage.so`), and Facebook's `libfbjni.so`. **No Fynd-specific native library** — reinforcing that Fynd Platform integration is JS-side (§6.4).

### 6.6 Actual banner/widget data model (Fynd Platform "theme" response) — supersedes §2

Response shape (from `GET .../theme/v1.0/{appId}/home?company=1`, saved as `analysis/traffic_capture/home_theme_response_sample.json` — one example section per type, plus every destination URL found across the full 93-section response, redacted of nothing since this is public-facing CMS content with no user secrets):

```
{ _id, page_mapper, page_mappings[], predicate, props, sections[], sections_meta, seo, theme, type, ... }
sections[] = {
  _id, name,                     <- section TYPE, e.g. "hybrid-banner", "hybrid-dynamic-banner", "hybrid-swipe-gallery"
  label, predicate, status,
  props: { <field>: {type, value}, ... },     <- flat sections (single banner)
  blocks: [ { props: { <field>: {type, value}, ... } }, ... ]   <- repeating sections (carousel/dynamic banner, one per slide)
}
```

**93 sections in the live home response**, by type:
```
hybrid-swipe-gallery    37   (carousels — payment-partner banners, category tiles, bank offers)
hybrid-banner           31   (single static banners)
hybrid-dynamic-banner   10   (rotating/scheduled banner sets)
curated-widget           4   (product widgets, e.g. "same day delivery" — ties to search-edge.services.ajio.com/.../products/category/... calls)
recommended-products      3
floating-widget            1   (e.g. a floating CTA button)
curated-widget-new         1
osmos                       1   (ad-tech/PLA integration — matches "plaAdsProvider=OSMOS" seen in the curated-widget product-search calls)
recently-viewed             1
ratings                     1
trends-widget                1
plp-landing-page-widget      1
raw-html                     1
```

**Destination fields by section type** (this is the real equivalent of Phase 1's `bannerUrl`/`ctaLink` — none of those field names appear anywhere in this response):

| Section type | Field path | Example value |
|---|---|---|
| `hybrid-banner` | `props.redirectURL.value` | `https://www.ajio.com/s/4hoursdelivery-160865?isPDBanner=true` |
| `hybrid-dynamic-banner` | `blocks[].props.redirectURL.value` | `https://ajio.com/s/min70percentoffcurated-402882` |
| `hybrid-dynamic-banner` | `blocks[].props.image.value.hotspots[].url` | (per-hotspot, same shape as `hybrid-swipe-gallery` below) |
| `hybrid-swipe-gallery` | `blocks[].props.redirectImageURL.value` | `https://ajio.com/s/tshirts-398168`, `www.ajio.com/c/clearance-store-1789044034`, `/reliance-sbi-tnc` (relative!) |
| `hybrid-swipe-gallery` | `blocks[].props.image.value.hotspots[].url` | per-hotspot destination within one slide's image |
| `floating-widget` | `props.cta_redirect_url.value` | `https://www.ajio.com/s/new30-166553` |

327 destination URLs total across the captured response (full list in `home_theme_response_sample.json`). **Confirms Phase 1's deep-link path grammar is still accurate** even though the endpoint/model was wrong — real URLs observed include `/s/...` (search/campaign, the large majority), `/c/...` (category), `/shop/...`, `/sections/...`, and bare relative paths with no leading `https://www.ajio.com` at all (e.g. `/reliance-sbi-tnc` — Phase 4's deep-link resolver needs to handle relative URLs, not just absolute ones). No `/p/` (PDP) or `/b/` (brand) examples happened to appear on the home screen in this capture; the manifest grammar from §3 should still be treated as the reference until a PDP/brand banner is observed directly.

**No stable numeric `banner_id`** was found on individual banner blocks — the closest analog is the section's own `_id` (a Mongo-style ObjectId, e.g. `"_id": "6a6b3d1cd2e75493f26addbf"`) plus its `predicate.slug`. §8 of this document (reference-data join key) should use **section `_id`** as the primary join key candidate, falling back to `(slug, section position, redirectURL)` if `_id` turns out to be unstable across CMS edits — this needs confirming against real reference-data rows once the user provides them.

### 6.7 Files saved for this capture

- `analysis/traffic_capture/guest_token_and_home_theme_requests.txt` — full redacted request/response headers+bodies for the guest-token call and the home/menswear/kidswear/womenswear theme calls (secrets replaced with `<REDACTED>`: `access_token`, `authorization`, `cookie`, `set-cookie`).
- `analysis/traffic_capture/home_theme_response_sample.json` — structural sample of the home theme response: one example section per type (13 types) plus every destination URL found in the full 93-section response, with section type/label context. The full unredacted capture (`home_feed.flow`, 12MB, includes third-party SDK tokens for Facebook/Amplitude/Sentry/AppsFlyer/Akamai in the clear) was **not** committed to the repo — kept only in the local scratchpad for this session, per CLAUDE.md's "headers redacted of anything secret" requirement.

### 6.8 Correction (Phase 3, 2026-09-21): the theme endpoint does NOT use the guest JWT

§6.3 was half right. The guest OAuth call (`POST /uaas/jwt/token/client`) happens and returns a valid 550-char RS256 JWT, but **the theme endpoint's `authorization` header is a different, much shorter token**. Sending the guest JWT to the theme endpoint returns `401 {"error":"Invalid authorization token"}` (tested live).

Structure of the real theme bearer (values deliberately not recorded here): `base64("<24 hex chars>:<9 chars>")`, 48 chars encoded, single segment, not a JWT. It is identical across all four theme calls in the capture and also appears as the `authorization` header on `/api/service/application/content/v2.0/navigations`. Its 24-hex part appears in the plaintext body of `GET /ext/headless/api/external/v1/config/list` (called at startup), but the 9-char part appears in no AJIO response, so it is baked into the app. This matches Fynd Platform's storefront convention (`applicationId:applicationToken`, a static public-read credential), not a per-session token.

**Live result:** with this bearer plus a fresh `x-fp-date`/`x-fp-signature` from `qa/fp_signer.py`, a plain Python `requests` call returned 200 with the full 93-section home feed. No emulator, no Frida, no guest JWT, and no `x-acf-sensor-data` were needed, so Akamai Bot Manager did not block a standalone script for this endpoint (one call; sustained polling is untested).

**Consequences:**
- No `session_bootstrap` is needed for the feed. CLAUDE.md section 6's conditional bootstrap is skipped: the endpoint works with a static credential.
- The bearer is a secret and lives in `.env` as `AJIO_THEME_BEARER`. Nothing in the repo records its value.
- **Where it lives in the app (found after the first write-up of this section):** `inputs/apk-source/smali_classes5/Hs0.smali` (`.source "ConfigValues.kt"`, method `T()`) holds a `fynd_sdk_initialization_keys` JSON string with `dev`, `uat`, `prod` and `replica` entries, each `{"application_Token": ..., "application_ID": ...}`. The `prod` entry is exactly the two halves of the captured bearer, so **`AJIO_THEME_BEARER = base64("<prod application_ID>:<prod application_Token>")`** and it can be re-derived from the decompiled source without a live capture (it is not in `assets/index.android.bundle`). The same class also has the Rush landing page slug (`rn-rush-landingpage`) and navigation slugs, useful leads for other feed surfaces. Values are deliberately not recorded here.
- The credential has no visible expiry. If it starts returning 401 after an app update, re-derive it from the new `ConfigValues` smali (or re-capture per section 6.0) instead of assuming a rotation schedule.

**Feed content is personalized.** The response's `x-sc-cache-key` includes city, platform, `user-groups` and the experiment list (`CMSABExp2/4/10`). `feed_client.py` pins these to the captured values (Bengaluru 560029, `l1:nontransacted|l2:p_null,false,unisex,noasp`, guest) via env-overridable defaults. Different values return different banners, so the reference data must say which context it was authored for.

### 6.9 Banner record model used by `qa/feed_client.py`

One record per flat banner section (`hybrid-banner`, `floating-widget`) and one per block for carousel sections (`hybrid-dynamic-banner`, `hybrid-swipe-gallery`). `banner_id` is the section `_id` (flat) or `<section _id>:<block index>` (blocks). The block `__renderKey` is **not** unique within a section (observed repeats), so it cannot be used. Block index is unique but shifts if the CMS reorders blocks. Destination is the first non-empty of `redirectURL`, `redirectImageURL`, `cta_redirect_url` (a block can carry both `redirectURL` and `redirectImageURL`, with one empty). Also captured per record: `hotspot_urls`, `schedule` (cron windows from `predicate`) and `user_type`.

Live home feed on 2026-09-21: 93 sections parsed into 373 records (1 floating-widget, 31 hybrid-banner, 118 hybrid-dynamic-banner, 222 hybrid-swipe-gallery, 1 osmos). 65 have an empty destination (47 of them under the labels "DP mz new" and "top mz new UHP", which look like zone/ad-driven slots), 51 have no image, 79 carry a schedule window, 1 destination is `http://` and 1 is relative.
