# Banner Contract for Shop Clients

This document defines the client-neutral contract for the home-screen banner carousel backed by the Shop API (`banners` and `settings`). It is written for native Android and iOS engineers who have not read the web client code.

---

## 1. Purpose and reading order

Every Biotact client renders the same home-screen banner carousel from a single `banners` table in the Shop backend:

- Public website (`new.biotact.uz`)
- Telegram Mini App
- Native Android and iOS apps (planned)

A banner can carry still images and short silent video clips for wide screens (3:1) and phones (3:2), plus localized Russian and Uzbek headline and subtitle fields. Every client follows the same display, playback, fallback, text, and navigation rules so visitors see identical behaviour across platforms.

**Strict client prohibitions:**
- Clients **MUST NOT** bake or render text inside media assets; headline, subtitle, and button label are rendered natively by the client in the visitor's language.
- Clients **MUST NOT** play audio from banner clips.
- Clients **MUST NOT** display native media-player chrome (scrub bars, timeline, volume toggles, or fullscreen controls).

**Reading order:** API Reads (§2) → Fields & Server Guarantees (§3) → Formats & Layout Classes (§4) → Media File Standard (§5) → Playback & Carousel Behaviour (§6) → Text & Button Label (§7) → Navigation (§8) → Rotation Interval (§9) → Change Policy (§10) → Separate Note: How the Web Clients Do It (§11).

---

## 2. Reading banners

### List active banners

```http
GET /api/v1/banners?is_active=true&limit=100&skip=0
```

- **Authentication:** None (public endpoint).
- **Query parameters:**
  - `is_active` (`boolean`, optional): pass `true` for live carousels; omit to read all banners.
  - `limit` (`integer`, optional, `1..100`, default `100`): maximum items per page.
  - `skip` (`integer`, optional, `>= 0`, default `0`): zero-based offset.
- **Ordering:** `sort_order` ascending, then `id` ascending.
- **Response (`200 OK`):**
  ```json
  {
    "items": [ ... ],
    "total": 4
  }
  ```
- **Caching header:** `Cache-Control: no-store`. Clients must not cache banner list responses past the session/screen policy so that admin updates appear on the next read.

### Read one banner by ID

```http
GET /api/v1/banners/{id}
```

- **Authentication:** None (public endpoint).
- **Behaviour:** Returns one banner object regardless of its `is_active` value (allowing preview of hidden banners), or HTTP `404` when no banner with that ID exists. Also returns `Cache-Control: no-store`.

---

## 3. Fields and server guarantees

### Banner object fields

| Field | Type | Nullable | Meaning |
|---|---|---|---|
| `id` | `integer` | No | Unique banner identifier. |
| `title` | `string` (max 200) | No | Russian headline (always present and non-blank on valid banners). |
| `title_uz` | `string` (max 200) | Yes | Uzbek headline (required on video banners). |
| `subtitle` | `string` (max 500) | Yes | Optional Russian subtitle. |
| `subtitle_uz` | `string` (max 500) | Yes | Optional Uzbek subtitle (required when `subtitle` is non-blank on a video banner). |
| `image_url` | `string` (max 500) | Yes | Wide still image (3:1). Baseline fallback for every client. |
| `image_mobile_url` | `string` (max 500) | Yes | Phone still image (3:2). |
| `video_url` | `string` (max 500) | Yes | Wide clip (3:1 MP4). |
| `video_mobile_url` | `string` (max 500) | Yes | Phone clip (3:2 MP4). |
| `gradient` | `string` (max 200) | Yes | Legacy web-only field. Native clients **MUST** ignore it. |
| `link_url` | `string` (max 500) | Yes | Tap destination inside the app or external `https:` link (see §8). |
| `sort_order` | `integer` | No | Carousel ordering key (`0` by default). |
| `is_active` | `boolean` | No | Visibility flag (`true` by default at API level). |
| `created_at` | `string` (ISO 8601) | No | Creation timestamp with timezone. |
| `updated_at` | `string` (ISO 8601) | No | Last update timestamp with timezone. |

Clients **MUST** ignore any unknown fields returned in the JSON payload.

### Server guarantees (enforced on create and update)

1. **Media combinations:**
   - `video_url` non-null requires `image_url` non-null.
   - `image_mobile_url` non-null requires `image_url` non-null.
   - `video_mobile_url` non-null requires both `video_url` non-null and `image_mobile_url` non-null.
2. **Video banner text:** whenever `video_url` is non-null, `title` is non-blank, `title_uz` is non-blank, and if `subtitle` is non-blank then `subtitle_uz` is also non-blank.
3. **URL format at write time:** any non-null value written to `image_url`, `image_mobile_url`, `video_url`, or `video_mobile_url` through the API is an absolute `https:` URL without username or password and at most 500 characters long.

### Client URL validation and resilience

- **Clips (`video_url`, `video_mobile_url`):** usable **only** if the value is a string containing an absolute `https:` URL with no username and no password.
- **Phone still image (`image_mobile_url`):** usable if it is such an `https:` URL **or** a site-relative path beginning with a single `/` (never `//`) and containing no backslash (`\`). (Clients keep the same rule for `image_url`.) Because native apps have no document origin, if a legacy row contains a site-relative path, resolve it against the Shop media origin (`https://app.biotact.uz`, matching the API upload base URL); note that all URLs written through the current API are already absolute `https:` URLs.
- Clients **MUST** refuse any other scheme (`http:`, `javascript:`, `file:`, etc.) and any URL containing credentials (`user:pass@` or `user@`).
- An absent key is treated identically to `null`.
- An unusable or absent value **MUST NOT** fail parsing or drop a banner (except `image_url`):
  - If `image_url` is absent or unusable, skip that banner from **displayed banners**.
  - Unusable `video_url` → treat the banner as an **image banner** (no clip, no drawn text).
  - Unusable `video_mobile_url` → treat the banner as having no phone clip.
  - Unusable `image_mobile_url` → treat the banner as having no phone picture.

---

## 4. Formats and layout classes

Every client classifies its carousel container into one of two layout classes:

- **Wide layout:** desktop and tablet viewports.
- **Narrow layout:** phones.

Reference breakpoints of the web clients (in density-independent CSS `px`; native apps map `px` values in this contract to density-independent logical units — `dp` on Android, `pt` on iOS):
- Website switches at `700` px carousel width (narrow when `<= 700` px).
- Mini App switches at `768` px screen width (narrow when `< 768` px).

A native app chooses its own device/size-class rule; a phone in portrait orientation is **narrow**.

### Phone format vs 3:1 fallback

In the **narrow** layout, the carousel enters **phone format** (media area aspect ratio **3:2**, i.e., height equals `2/3` of carousel width) **only when**:
1. There is at least one displayed banner, **and**
2. **Every** displayed banner has a usable `image_mobile_url`.

Otherwise, the narrow layout stays in the **3:1 fallback** (existing compact narrow height). In the **wide** layout, the media area always keeps the wide 3:1 sizing.

**One height per carousel:** all slides in the carousel share the exact same height at any time. Changing the layout class or the set of displayed banners re-evaluates the format.

### Rendition choice per slide

| Carousel state | Still image | Clip (video banners only) |
|---|---|---|
| **Wide layout** | `image_url` | `video_url` |
| **Narrow: 3:1 fallback** | `image_url` | `video_url` |
| **Narrow: Phone format (3:2)** | `image_mobile_url` | `video_mobile_url` if usable; otherwise **no clip** |

The wide clip (`video_url`) is **never** played in the **phone format**.

---

## 5. Media file standard

Clips and still images produced for banners follow this specification:

| Property | Wide rendition | Phone rendition |
|---|---|---|
| Container | MP4 (`.mp4`), fast start (`moov` atom before `mdat`) | MP4 (`.mp4`), fast start (`moov` atom before `mdat`) |
| Video codec | H.264, High or Main profile | H.264, High or Main profile |
| Pixel format | `yuv420p`, 8-bit, SDR (BT.709); no HDR or 10-bit | `yuv420p`, 8-bit, SDR (BT.709); no HDR or 10-bit |
| Frame rate | Constant 25 or 30 fps | Constant 25 or 30 fps |
| Audio & other streams | None (no audio, subtitles, or data tracks) | None (no audio, subtitles, or data tracks) |
| Duration | 4–8 s recommended; hard limit 10 s | 4–8 s recommended; hard limit 10 s |
| Frame size | `1920 × 640` (3:1) | `1080 × 720` (3:2) |
| Clip target size / hard limit | ≤ 1.5 MB target / 5 MiB upload limit | ≤ 0.6 MB target / 5 MiB upload limit |
| Still image target size | ≤ 250 kB | ≤ 250 kB |

- **Hero-frame rule:** the first frame and last frame of each clip match the corresponding still image (`wide.jpg` / `phone.jpg`), so switching from still image to playing video and resting on the final frame produces no visual jump.
- **Calm text zones:** clips leave the left 45% (wide) and bottom 30% (phone) quiet for native text legibility, with important subjects at least 5% from all edges.
- **Serving characteristics:** uploaded files receive unique UUID filenames, never change at a given URL, and are served with long-lived HTTP caching and byte-range (`206 Partial Content`) support.

---

## 6. Behaviour and playback states

Keep these two definitions strictly distinct:

- **Video banner:** a displayed banner with a usable `video_url` and a usable `image_url`. It **always** displays the drawn text (§7) in every layout state and playback state (wide, phone format, 3:1 fallback, still-only, playing, paused, ended), and displays the button label (§7) in the wide layout and the phone format (not in the 3:1 fallback).
- **Video slide:** a slide whose chosen rendition (§4) includes a clip (`video_url` in wide / 3:1 fallback; `video_mobile_url` in phone format). Only a **video slide** loads/plays a clip and displays the play/pause control.

**Worked case (video banner without a phone clip on a phone in 3:2):**
Suppose banner #1 has usable `image_url`, `video_url`, `image_mobile_url`, `title`, `title_uz`, and `video_mobile_url = null`, and every active banner has a usable `image_mobile_url`. On a phone in the **phone format (3:2)**, banner #1 is still a **video banner**, so it draws `title`/`title_uz` (and subtitle/button label) over `image_mobile_url` with the dark scrim. However, its chosen phone rendition has no clip, so it is a **still-only slide** (not a video slide): it does **not** fetch `video_url`, displays **no** play/pause control, and advances on the banner interval like an image slide.

### Loading priority and active-slide isolation

1. The still image of the first slide **MUST** be requested first and at high priority.
2. Other slides' still images and all clips **MUST NOT** be requested until the first slide's still image has finished loading or failed.
3. Other slides' still images may then load.
4. A clip is requested **only** for the currently active video slide, and **only** after that active slide's still image has loaded. Non-active video slides show their still image and **MUST NOT** attach or request their clip source.

### Playback state table (for a video slide)

| State | Trigger / Condition | Visual & media behaviour | Control & carousel advance |
|---|---|---|---|
| **Still (loading / pre-start)** | Active video slide before video playback begins | Show the slide's still image; mute clip, attach source after still image loads | Show play control when playback has not started automatically (or keep control hidden in favour of an existing rotation pause button while auto-rotation is active). Do not advance on interval once playback begins; if auto-start stalls past the start budget, enter **Start budget expired**. |
| **Playing** | Video frame rendering has started | Show live muted clip inline without player chrome | Video control shows **pause** (or existing rotation pause control handles pause when auto-rotation is active). Carousel does **not** advance on the interval while playing. |
| **Ended** | Clip reaches its end | If `total displayed == 1`, **loop** the clip continuously. If `total displayed > 1`, rest on the clip's last frame | If auto-rotation is active and `total displayed > 1`: advance to the next slide once **both** the clip has ended **and** the banner interval since this slide's activation has elapsed (wait until whichever happens later). If paused/stopped manually: stay on last frame; control shows **play** (tap replays from start). |
| **Visitor-paused** | Visitor taps video control while playing | Pause playback at current frame | Video control shows **play** (tap resumes from paused position). Carousel **MUST NOT** advance while visitor-paused. |
| **Hidden / off-screen** | Screen/app goes to background or carousel is outside the visible screen area | Pause active clip at current position; resume from that position when visible and on screen again | Slide does **not** advance while hidden or off-screen. |
| **Refused** | Automatic playback was rejected by the platform | Keep showing the slide's still image | Show visible **play** control; follow normal image-slide interval timing until visitor taps play (which starts playback inside that tap gesture). Re-try auto-start on the next activation of the slide. |
| **Reduced motion** *(still-only)* | Platform's equivalent reduced-motion accessibility setting is enabled | Show still image only; do not load or play clip | **No** video control drawn. Advance on standard banner interval. Takes precedence over Refused. |
| **Data saver** *(still-only)* | Platform's equivalent data-saving / low-data mode setting is enabled | Show still image only; do not load or play clip | **No** video control drawn. Advance on standard banner interval. Takes precedence over Refused. |
| **Media error** *(still-only)* | Clip load or decode fails | Show still image only | **No** video control drawn. Advance on standard banner interval. |
| **Start budget expired** *(still-only)* | Auto-started clip has not started rendering within `4000 ms` *(reference value)* of starting its request | Abandon clip for this activation; keep still image (never swap to video later in this activation). Budget does **not** apply to tap-started playback. | **No** video control drawn. Advance at the later of banner interval since activation and budget expiry. |

### Video play/pause control rules

- **Placement & look:** one small circular/compact button placed in the top-right corner of the media area *(reference position)*, visually distinct from any carousel auto-rotation toggle if present, with a localized accessible label for pausing and playing the clip from the client's own translations in the visitor's language, distinct from any rotation toggle's label.
- **Visibility:**
  - Never drawn in any **still-only** state (reduced motion, data saver, media error, start budget expired) or on a banner without a clip in the active rendition.
  - Drawn on every other video slide (including **refused** state as play, **playing** as pause, **paused** as play, and **ended** as play when stopped).
- **Interaction:** tapping the video control **MUST NOT** trigger banner navigation (§8). Tapping pause pauses the clip; tapping play starts from the paused offset (or from the beginning if not started or already ended).

---

## 7. Text and button label

Drawn text appears **only** on **video banners** (§3, §6) and in **every** state of a video banner (including still-only states and the phone 3:2 still-only case). The button label appears on **video banners** in the wide layout and the phone format only (not in the 3:1 fallback). A carousel with no video banners draws no text overlay, no button label, no scrim, and no reserved text area.

### Language selection

- **Russian:** render `title`, followed by `subtitle` if `subtitle` is non-blank.
- **Uzbek:** render `title_uz`, followed by `subtitle_uz` if `subtitle_uz` is non-blank. If a field is blank, show **no line** (never fall back to Russian text on video banners).
- **Line limits:** maximum **2 lines** for title and maximum **2 lines** for subtitle, truncating overflow with an ellipsis (`…`).
- **Accessible banner label:** use the displayed headline in the visitor's language (for image banners without `title_uz`, fall back to `title`).

### Placement, scrim, and reference visual values

All numeric dimensions (`px` = density-independent logical units: `dp` on Android, `pt` on iOS) and opacity values below are **starting reference values** (to be tuned on real devices in the web clients and synchronized here; see §10):

| Element | Wide layout | Phone format (3:2) | 3:1 fallback (narrow without phone format) |
|---|---|---|---|
| **Text block** | Over media: inside left `45%` of frame, vertically centred, `6%` left inset | Over media: bottom-left, `16 px` side inset, bottom edge `28 px` above frame bottom (clear of dots), max `70%` width | Directly below media area, full width |
| **Title** | `28 px` / `34 px` line-height, bold | `18 px` / `22 px` line-height, bold | `16 px` / `20 px` line-height, bold |
| **Subtitle** | `16 px` / `22 px` line-height | `13 px` / `17 px` line-height | `13 px` / `17 px` line-height |
| **Text colour** | White on dark scrim | White on dark scrim | Standard page foreground text colour |
| **Scrim** | Left-to-right gradient: `55%` black at left edge to transparent at `55%` width | Bottom-to-top gradient: `60%` black at bottom edge to transparent at `55%` height | None |
| **Button label** | Below subtitle; pill `36 px` high; white background, brand action text colour (`#0c3d2b`) | Bottom-right, aligned with bottom of text block; pill `28 px` high; same colours | Not drawn |
| **Reserved text area** | None | None | Reserve space for 2 title lines + 2 subtitle lines below every slide whenever ≥ 1 video banner exists in the carousel (image slides leave it blank with page background) |

### Button label (`Подробнее` / `Batafsil`)

- Fixed localized copy from the client's own translation bundle: **`Подробнее`** (Russian) / **`Batafsil`** (Uzbek).
- Visual indicator only: it is part of the single banner tap target (§8), **MUST NOT** be exposed as a separate interactive control or accessible element, and **MUST NOT** be appended to the banner's accessible label.
- Drawn in **wide layout** and **phone format** on **video banners** only; **never** drawn in the **3:1 fallback** or on **image banners**.

---

## 8. Navigation

The entire video banner (media, scrim, headline, subtitle, button label, and below-media text area in 3:1 fallback) is a **single tap target** acting on `link_url`. Only the video play/pause control (§6) sits outside navigation.

### `link_url` routing vocabulary

| `link_url` value (trimmed) | Destination |
|---|---|
| Empty / `null` | Whole product catalogue |
| `/` | Home screen |
| `/catalog` | Whole product catalogue |
| `/catalog/<slug>` | Category matching `<slug>` (see category lookup below); falls back to whole catalogue if slug is unknown |
| `/product/<id>` | Product detail screen for numeric `<id>` |
| Absolute `https:` URL (without credentials) | Open externally outside the app |
| Anything else | Whole product catalogue |

**Category lookup for `/catalog/<slug>`:**
```http
GET /api/v1/categories?page=1&size=100
```
- Paginated by `page` (`>= 1`) and `size` (`1..100`, default `20`; note this differs from `/banners` which uses `limit`/`skip`).
- Active categories only by default (`active_only=true`).
- Response shape: `{"items": [...], "total": n, "page": p, "size": s, "pages": k}`.
- Match on `slug` to resolve `id`.

---

## 9. Rotation interval

Read the global banner rotation interval from Settings:

```http
GET /api/v1/settings/banner_interval
```

- **Response (`200 OK`):**
  ```json
  {
    "id": 1,
    "key": "banner_interval",
    "value": "4000",
    "description": "...",
    "updated_at": "2026-..."
  }
  ```
- **Fallback:** if the request fails or `value` does not parse as a positive number, use **`3000` ms**.
- **Video timing:** when `total displayed > 1` and auto-rotation is active, each slide stays for at least the banner interval from its activation. If a clip is shorter than the interval, the slide rests on the clip's last frame after the clip finishes until the full interval has elapsed; if a clip is longer than the interval, the slide waits until the clip finishes. The next slide receives its own full interval counted from its activation.

---

## 10. Change policy

- **Additive compatibility:** all new banner fields (`image_mobile_url`, `video_url`, `video_mobile_url`) are additive and nullable; the API has no version bump.
- **Reference values:** values labeled *reference values* in this document (start budget `4000 ms`, layout breakpoints, text placement offsets, font/pill sizes, scrim opacities, control corner position) are owned by this contract document. Any tuning measured on real devices in the web clients will be updated here first so native clients share the exact tuned constants.

---

## 11. How the web clients do it

> This section is the only place in the contract that references web-specific APIs, CSS, DOM attributes, and file paths.

- **Website (`web-biotact`, `src/Hero.tsx`, `src/hero.css`, `src/catalogue.ts`, `src/navigation.ts`):**
  - *Existing today:* layout width uses CSS container query `@container hero (max-width: 700px)`; the existing `.hero-rotation` button toggles carousel auto-rotation (`playing` flag in `Hero.tsx`); `bannerTarget()` implements §8 strictly, falling back non-`https:` or credentialed external URLs and unknown routes to `#catalogue`.
  - *Planned for the website video-banner update:* in narrow layout while loading, `.hero-loading` uses 3:2 height; the media element is `<video>` with `muted`, `playsInline`, `poster=<still>`, and no `controls`; playback visibility swaps still image to `<video>` on the `playing` event; end/error/autoplay refusal use `ended`, `error`, and `play()` promise rejection; reduced motion and data saving read `window.matchMedia("(prefers-reduced-motion: reduce)")` and `navigator.connection?.saveData === true`; background/offscreen pause uses `document.hidden` and `IntersectionObserver`; while auto-rotation is active, `.hero-rotation` serves as pause and the video control appears only on `play()` refusal (as play), whereas once the visitor stops auto-rotation (via `.hero-rotation`, dot/arrow navigation, or keyboard focus), the separate video play/pause control appears beside `.hero-rotation` with its own accessible label in the visitor's language (`--bt-action` highlights the button pill); inactive banner preview opens via `/#home?preview_banner=<id>&lang=ru|uz`, fetching `GET /api/v1/banners/{id}` instead of the list.

- **Telegram Mini App (`miniapp-biotact`, `packages/frontend/src/pages/HomePage.tsx`, `src/api/catalogue.ts`):**
  - *Existing today:* layout class uses Tailwind's `md` breakpoint (`768px` screen width) and keeps `h-32` / `md:h-72` outside phone format; `handleBannerClick` routes non-`http` `link_url` values directly to the Mini App router and calls `window.open(..., '_blank')` for `http`/`https` links (unchanged by this task).
  - *Planned for the Mini App video-banner update:* `parseBanner()` will treat wrongly typed values on the three new fields as absent so a malformed field never rejects the whole list read; on every video slide (when not still-only), the single video control will render directly (the Mini App has no `.hero-rotation` button).
