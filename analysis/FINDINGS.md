# Phase 1 Static Analysis Findings — AJIO Android App

Source: `inputs/apk-source/` (apktool/jadx decompile of `com.ril.ajio`), cross-checked against `inputs/modified-apk/ajio_media3_fix.apk`.
All paths below are relative to `inputs/apk-source/` unless stated otherwise.

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

