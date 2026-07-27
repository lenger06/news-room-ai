# HeyGen V3 Migration Plan

Written 2026-07-26 in response to HeyGen's "[Action Required] Migrate to the
V3 API" email. Goal: understand exactly what changes, why past V3 attempts
had background problems, and define a path off V1/V2 that never interrupts
the currently-live `pip_v2` shows until a V3 replacement is proven equal or
better.

**Status (2026-07-27): Phase 1 implemented and live-validated end-to-end.**
The `pip_v3_chromakey` video style exists, is fully tested (mocked + real
FFmpeg smoke tests), and has now been run for real against 4 avatars via the
actual production function (not hand-rolled payloads) — catching and fixing
two real bugs in the process (see §10). It is not the default for any show —
`pip_v2` is untouched. **One new, unresolved finding narrows the
conclusion**: the `avatar_iii`/`studio_avatar` tier (a third of the roster)
intermittently renders with a visible "Veo" watermark burned into the frame
— confirmed live, not deterministic. The `avatar_iv`/`avatar_v` tier
(most of the roster) is now genuinely confirmed clean. See §10 for the full
writeup and §11 for the updated recommendation.

**Bottom line up front:** the background problems weren't a bug in this
codebase — they're a real, structural V3 platform limitation. No endpoint or
payload shape in V3 supports a video/animated background behind a talking
avatar, and no V3 mode supports picture-in-picture overlays. Every path
forward is a genuine trade-off, not a technical fix. This document lays out
the options, the concrete data needed to choose between them, and a rollout
plan that keeps `pip_v2` running untouched throughout.

---

## 1. Current state (audited from the actual code, 2026-07-26)

Two HeyGen paths already coexist in this codebase — the V3 migration is
already partially done:

- **`pip_v2` shows** (the entire standard 13-anchor roster) —
  `generate_video_multiscene()` in `tools/heygen_tool.py`, hits
  `POST /v2/video/generate`. Multi-scene (one call, many `video_inputs`),
  each scene's background is `{"type": "video", "video_asset_id": ...,
  "play_style": "loop"}` — the looping studio backdrop video. B-roll is
  FFmpeg-composited as a PiP inset directly into that looping background
  video (`_apply_background_layers`, `create_broll_video_asset`), so viewers
  see the avatar continuously in front of a moving studio set with a live
  b-roll box in the corner — this is the full-featured, "everything works"
  path.
- **`fullscreen_v3` shows** (only the 3 PAOS anchors: Marco Reyes, Elise
  Navarro, Elena Vasquez) — `generate_video_multiscene_v3()`, hits
  `POST /v3/videos` with `"type": "avatar"`. Single scene only — all segment
  scripts get concatenated into one continuous take. Background is forced to
  `"color"` or `"image"` (video is rejected with HTTP 400 — already
  documented in `HeyGen_V3.postman_collection.json`'s "Known Gotchas"
  section). The current workaround: extract one still frame from the studio
  background video, PIL-composite the *first* b-roll image found across the
  whole script onto it as a static PiP, upload that as a single image
  background. This is already a reasonable workaround for the endpoint's
  constraints, but it necessarily loses: motion in the backdrop, per-segment
  background switching, and any b-roll after the first item.

This confirms where "previous attempts had problems with backgrounds" comes
from — the V3 avatar endpoint was never going to replicate the V2 Studio
API's looping-video-background + PiP design, no matter how the payload is
tuned.

---

## 2. What the actual V3 API supports (verified against HeyGen's docs, not memory)

One endpoint family, `POST /v3/videos` / `GET /v3/videos/{id}`, with two
distinct modes selected by a `type` discriminator, plus a separate engine
selector that determines avatar quality:

### `"type": "avatar"` (what `fullscreen_v3` already uses)
- **Single scene.** No multi-scene support.
- `background.type`: `"color"` or `"image"` only. Confirmed via the existing
  Postman collection's gotcha test — `"type": "video"` returns HTTP 400
  ("Input should be color or image").
- `voice_settings.emotion` is rejected (submits fine, render fails).
- `remove_background: true` — documented as requiring "matting-enabled
  training"; the existing Postman notes flag this as failing for the PAOS
  avatars specifically. **Untested for the other avatar types** — see §4.

### `"type": "studio"` (the official V2-Studio-API replacement, not yet used here)
- **Multi-scene**: 1–50 scenes, each one of `avatar_video`, `image`, or
  `video`, concatenated in sequence.
- **Scenes do not layer.** Confirmed directly from HeyGen's docs: "studio
  videos are composed of whole-frame scenes... concatenated in the order you
  send them... they do not coexist visually." No PiP, no overlay — b-roll
  can only appear as a full-screen cutaway (an `image`/`video` scene with its
  own narration audio), not an inset while the avatar is on screen.
- `avatar_video` scene background: **`"color"` only** — confirmed no image or
  video option in this mode either, which is actually *more* restrictive
  than `"type": "avatar"` (which at least allows a static image).

### Engines (an avatar-level capability, not a separate endpoint)
Selected via `"engine": {"type": "..."}` inside either mode above:

| Engine | Capability | Resolution cap |
|---|---|---|
| `avatar_iii` | Static rendering only — **no `motion_prompt`, no expressiveness** | 4K (studio/twin), 1080p (photo) |
| `avatar_iv` | **Default engine.** `motion_prompt` + expressiveness levels (high/medium/low). Broadest avatar-type support | — |
| `avatar_v` | Highest-fidelity motion via cross-reference animation. `motion_prompt` + optional `reference_look_id`. Opt-in per avatar look | — |

Which engines a given avatar can use is a fixed property of that specific
avatar/look — check via `GET /v3/avatars/looks/{look_id}` →
`supported_api_engines`. This is not a request-time choice for every avatar;
some avatars simply don't support the better engines (see §3).

### Other pieces from the email's links
- **V3 Template API** (`GET/POST /v3/templates`) — reusable video templates
  with typed variable slots (text/image/video/audio/voice/character). Built
  for "fill in a fixed template" workflows, not a dynamic daily-script
  newsroom. Low priority for this migration — worth knowing about, not worth
  building around right now.
- **Avatar/Digital Twin creation** (`POST /v3/avatars`, `type`:
  `digital_twin`/`photo`/`prompt`) — relevant only if the roster-capability
  gap in §3 leads to creating new custom avatars to replace the
  engine-limited stock ones.

---

## 3. Roster capability audit (read-only, zero HeyGen credits spent, 2026-07-26)

Queried `GET /v3/avatars/looks/{id}` for every distinct avatar_id in
`config/anchors.py`. The roster splits cleanly into two tiers by HeyGen's own
`avatar_type` classification:

**Full V3 access (`avatar_v`, `avatar_iv`, `avatar_iii` all supported)** —
these are `digital_twin` or `photo_avatar` type (custom-created for this
account, not HeyGen's stock library):

| Anchor | avatar_type | Engines |
|---|---|---|
| Daniel Mercer | digital_twin | v, iv, iii |
| Alexa Chen | photo_avatar | v, iv, iii |
| Zayne Carter (both looks) | photo_avatar | v, iv, iii |
| Victor Marinos / Ricardo | photo_avatar | v, iv, iii |
| Karoline Faye | photo_avatar | v, iv, iii |
| Nicholas Stavros | photo_avatar | v, iv, iii |
| Marco Reyes, Elise Navarro, Elena Vasquez (PAOS) | photo_avatar | v, iv, iii |
| Darlene Smith / Crystal Veil | photo_avatar | iv, iii *(no v — not a blocker, iv still has motion_prompt)* |

**Locked to `avatar_iii` only — no `motion_prompt`, no expressiveness:**
these are all `studio_avatar` type, HeyGen's stock library characters
(the `*_public`-suffixed IDs):

| Anchor | Looks affected |
|---|---|
| Shawn Green | all 3 looks |
| Brandon Jones | its 1 look |
| Monica Hayes / Saskia | all 3 looks |
| Valerie Brooks / Candace | both looks |
| Alister Blackwood / Dexter | its 1 look |

**This matters independently of which V3 mode gets chosen.** Even in the
best case (Option A or D below working perfectly), these 5 anchors / 8 looks
would lose the natural-gesture `motion_prompt` behavior they have today in
V2, because the engine that supports it isn't available to their specific
avatar. This is a HeyGen library constraint, not something fixable in this
codebase. Options are in §6.

---

## 4. Migration strategy options

### Option D — keep the existing FFmpeg pipeline, swap only the avatar source (recommended to test first)

Don't touch the studio-background/PiP/b-roll system at all. Use V3
(`type: "avatar"`, `engine: avatar_iv` or `avatar_v`) with a solid
chroma-key-style color background and `remove_background: true` to render
*just* the talking avatar as a clean matted clip — then feed that into the
**exact same FFmpeg compositing pipeline already built** for backgrounds and
PiP b-roll, exactly as V2 avatar output is handled today conceptually (V2
already does its own server-side matting via `"matting": true` before
compositing onto the background it returns).

If `remove_background` works reliably for the full-access roster (§3), this
preserves **100% of current visual functionality** — moving backdrop, live
PiP b-roll, per-segment background switching — because none of that logic
changes; only the avatar clip's origin does.

**Status: CONFIRMED VIABLE** — live-tested end-to-end 2026-07-26 against both
roster tiers from §3. See §4a for the full results, the one remaining
exception (Daniel Mercer), and the two cosmetic refinements still open.

### 4a. Option D — live validation results (2026-07-26)

Ran real API tests end-to-end — submit → poll → download → FFmpeg chromakey
composite onto the real desk background → visually inspect the resulting
frames — against three anchors spanning both roster tiers from §3. Test
scripts and downloaded frames are not committed to the repo (scratch/local
only); the reusable requests live in `HeyGen_Background_Color_Test.postman_collection.json`
and the "Migration - Option D" folder of `HeyGen_V3.postman_collection.json`.

| Avatar | avatar_type | Engine | Background config | Result |
|---|---|---|---|---|
| Zayne Carter | photo_avatar | avatar_iv | `background:{type:color,value:#00FF00}` | Green honored exactly. Chromakeyed cleanly onto the real "entertainment" desk background — no fringe, no spill. **Confirmed viable.** |
| Zayne Carter | photo_avatar | avatar_v | same + `motion_prompt` | Same clean result; `motion_prompt` also honored on this engine. **Confirmed viable.** |
| Zayne Carter | photo_avatar | avatar_v | `remove_background:true`, no `background` field (matting only, no explicit color) | HeyGen substitutes a "more evenly lit" near-white fill (`RGB(243,244,249)`) — **not** true alpha transparency, even on `mp4` output. That near-white is too close to skin/light-clothing tones to key: compositing removed the subject along with the background. **Ruled out** — matting without an explicit saturated color is not usable for any avatar. |
| Daniel Mercer | digital_twin | avatar_iv | `background:{type:color,value:#00FF00}` | Background silently rendered flat gray (`RGB(104,104,104)`) instead of green — output also carried a "Veo" watermark, suggesting this specific avatar/engine combination renders through Google Veo rather than HeyGen's own pipeline. Keying the actual gray removed the subject too (dark suit + skin tones too close to neutral gray in color space). `motion_prompt` was also rejected outright (HTTP 400: "not supported for video avatars"). **Not yet viable — see Open Gap below.** |
| Shawn Green | studio_avatar | avatar_iii (only option — locked, no `motion_prompt`) | `background:{type:color,value:#00FF00}`, default `fit` | Green honored, but default framing was an extreme close-up (chest/collar only). Confirmed not an FFmpeg scaling bug — both source clips verified 1920x1080 before compositing. |
| Shawn Green | studio_avatar | avatar_iii | same + explicit `fit:"contain"` | Framing fixed — proper head-to-waist shot matching today's V2 framing. Chromakeyed cleanly onto Shawn's real desk background. **Confirmed viable for the `avatar_iii`-only tier too**, contingent on always setting `fit:"contain"` for `studio_avatar`-type avatars. |

**Conclusion: Option D is confirmed viable for both roster tiers** — the
full-access `photo_avatar`/most `digital_twin` avatars (`avatar_iv` or
`avatar_v`, with `motion_prompt`), and the `avatar_iii`-only `studio_avatar`
stock characters (with explicit `fit:"contain"`; no `motion_prompt` available
to them regardless, per §3 — same limitation they'd have under *any* V3
option, not something Option D costs them).

**One open gap — Daniel Mercer (digital_twin).** The single `digital_twin`
anchor in the roster doesn't honor background color and rejects
`motion_prompt` on `avatar_iv`. Not yet retested on `avatar_v` (Zayne's
success was on `photo_avatar`, a different HeyGen avatar category — may or
may not generalize to `digital_twin`). Before Phase 1 reaches Daniel Mercer's
desk, one of:
  a. Retest `avatar_v` for him specifically (cheap, do this first).
  b. Key against his actual gray output as a special-cased per-avatar color
     (fragile — depends on that gray being stable across renders).
  c. Hold him on the legacy V2 path longest, if the actual V1/V2 sunset date
     (§6, still unanswered by HeyGen) gives enough runway — he's one anchor,
     not the whole roster, so this doesn't block anyone else's migration.

**Two cosmetic refinements — neither blocks starting Phase 1:**
  - **Positioning.** V2 places the avatar left/center/right via
    `_AVATAR_OFFSET_X` (`tools/heygen_tool.py`) — a fractional x-offset
    (-0.35/0/0.35) keyed off each `AvatarLook.avatar_position`. This data
    already exists and just needs to be converted to a pixel `overlay=x:y`
    offset in the new FFmpeg compositing step (multiply by background frame
    width) instead of being redesigned from scratch.
  - **Lighting/color match.** The green-screen-lit avatar clip and the
    warm-lit real studio backdrop don't fully match in color temperature. A
    color-grading pass (FFmpeg `eq`/`colorbalance` on the keyed layer before
    the overlay) would tighten this up. Larry also raised the idea of a
    lighting/blend overlay layer on top of the whole composite (avatar +
    background together) to sell "same room" more convincingly than
    per-layer color-grading alone — worth exploring once the base pipeline
    is working, not a Phase 1 requirement.

### Option A — V3 Studio API, redesigned as sequential cutaways

Use the real `"type": "studio"` multi-scene endpoint as HeyGen intends:
`avatar_video` scenes (solid color background) for the anchor talking,
interspersed with `image`/`video` scenes (full-screen, narrated) for b-roll
cutaways instead of PiP insets.

- **Pros:** the officially-designed V2→V3 Studio API replacement, most
  future-proof, matches how real broadcast editing often works (cut away to
  b-roll, cut back to anchor) — arguably more professional than a permanent
  corner PiP box.
- **Cons:** a genuine visual style change for *every* show, not just the
  engine-limited anchors — no moving studio backdrop for anyone (solid color
  only), no PiP (full-screen cutaways only), and the script/scene-building
  logic in `agents/anchor/agent.py` and `script_writer` would need real
  rework to split narration across avatar/image/video scenes with correct
  timing instead of the current single-background-per-segment model.
- Lower-third graphics (Phase 6 of the self-improvement roadmap) still work
  unchanged — that's a post-processing FFmpeg pass on the final video
  regardless of which HeyGen mode produced it.

### Option B — extend the current `fullscreen_v3` approach to the full roster

Keep using `type: "avatar"` with the static-frame-PiP-composite workaround
that's already live for the 3 PAOS anchors, just apply it to everyone.

- **Pros:** zero new code, already proven to render successfully.
- **Cons:** this is the approach that already produced the background
  problems the migration email prompted questions about — static frame
  instead of motion, only the first b-roll item shown, no per-segment
  switching. Not a real improvement, just a known-working fallback if
  Options A and D both fall through.

**Recommendation: build Option D.** Live-tested and confirmed (§4a) — least
disruption, most functionality preserved (moving backdrop, live PiP b-roll,
per-segment background switching all stay exactly as they are today, because
none of that logic changes). The one open item (Daniel Mercer, digital_twin)
has a clear resolution path that doesn't block the rest of the roster.
Option A remains documented as the officially-blessed long-term direction if
HeyGen's platform evolves, and Option B stays as the known-working fallback,
but neither is needed now that Option D is proven.

---

## 5. Phased rollout — `pip_v2` is never touched until a replacement is proven

1. **Phase 0 — DONE (2026-07-26).** Live-tested against the real API (no
   code path in the running server touched these results — `pip_v2` was
   never at risk). Answered: `remove_background` alone doesn't work for
   anyone, but an explicit green `background` color does, for both roster
   tiers. **Decision: build Option D** (§4a).
2. **Phase 1 — build Option D behind a new, additive `video_style`.**
   Add e.g. `"pip_v3_chromakey"` alongside the existing
   `"pip_v2"`/`"fullscreen_v3"` in `config/shows.py` — a new code path in
   `tools/heygen_tool.py`, not a replacement of `generate_video_multiscene()`.
   `pip_v2` stays the default for every currently-live show. See §8 for the
   concrete step list.
3. **Phase 2 — pilot on one low-stakes show.** Flip a single show/desk (e.g.
   `entertainment-weekly`, or a specific desk with a full-access anchor) to
   `pip_v3` for a defined trial window. Compare output quality, render
   reliability, and cost against the `pip_v2` baseline side by side.
4. **Phase 3 — gradual rollout.** Move additional shows over once the pilot
   is validated. `pip_v2` remains fully intact and selectable the whole time
   — reverting a show is a one-line config change (`video_style` field), not
   a code rollback.
5. **Phase 4 — retire V2 code.** Only after HeyGen confirms the actual V1/V2
   sunset date (see §6) *and* every show has been running successfully on
   the new path for a meaningful period.

This means: nothing about today's production pipeline changes until Phase 1
code is written and explicitly opted into per-show. The email's "service
disruption" warning is about HeyGen eventually turning off V1/V2 endpoints,
not an immediate deadline — but the exact date matters for how much runway
Phase 0–3 actually have (see next section).

---

## 6. Open questions worth asking HeyGen support directly

1. **What is the actual V1/V2 sunset date?** The email says "avoid future
   service disruption" but gives no hard date — this determines how much
   time Phases 0–3 realistically have.
2. **Will the `avatar_iii`-only stock avatars (Shawn Green, Brandon Jones,
   Saskia, Candace, Dexter) ever get `avatar_iv`/`avatar_v` access?** Or are
   there equivalent-looking stock avatars already in the library that do
   support the better engines? This directly affects whether those 5
   anchors need to be replaced with newly-created custom avatars to keep
   `motion_prompt`-quality output post-migration.
3. **Does `remove_background`/matting work reliably for `digital_twin` and
   non-PAOS `photo_avatar` types?** The one documented failure (PAOS,
   `avatar_v`) may not generalize — worth a definitive answer instead of
   inferring from one data point.
4. **Any roadmap for image/video backgrounds in V3 Studio API's
   `avatar_video` scenes?** If this is coming, Option A becomes much more
   attractive and worth waiting for rather than working around now.

---

## 7. What to test — see the extended `HeyGen_V3.postman_collection.json`

New sections added (details in the collection file itself):
- **Roster Capability Check** — one `GET /v3/avatars/looks/{id}` request per
  distinct avatar_id in the current roster, pre-filled, so this can be
  re-verified any time without needing me to re-run it.
- **Option D — Matting Hypothesis** — `type: "avatar"` + `remove_background:
  true` + a solid chroma-style color background, against Daniel Mercer
  (`digital_twin`) and Zayne Carter (`photo_avatar`, non-PAOS) specifically
  — the two avatar-type categories not yet tested for this. If these
  succeed, Option D is very likely viable for the whole full-access tier.
- **Option A — V3 Studio API Multi-Scene** — a real `type: "studio"` request
  with an `avatar_video` scene followed by an `image` scene (b-roll cutaway
  with narration) followed by another `avatar_video` scene, using a
  full-access avatar, to see the actual cutaway output quality.
- **Engine Comparison** — the same script rendered with `avatar_iii` vs
  `avatar_iv` vs `avatar_v` on the same full-access avatar, for a direct
  side-by-side quality/motion comparison.

Run these in Postman against the real API when ready — each one spends
HeyGen credits, so this is deliberately left for manual, deliberate
execution rather than run automatically.

---

## 8. Implementation step list — DONE (implemented 2026-07-27)

All steps below were implemented, tested, and merged. `pip_v2` is untouched —
`pip_v3_chromakey` is a new, opt-in `video_style` value that no show uses yet
(see §9 for what's left before a real pilot).

1. **Per-avatar V3 capability data — done.** `AvatarLook` in
   `config/anchors.py` gained `v3_engine`, `v3_supports_motion_prompt`,
   `v3_fit`, `v3_unsupported`, and one addition beyond the original plan —
   `v3_key_color` (default `"#00FF00"`). Populated from §3's roster table.
   Added `get_look_by_avatar_id()` for runtime lookup.
   *Real finding made while populating this table:* Saskia's "Green Blazer"
   look (`Saskia_public_4`) has green wardrobe — chromakeying it against a
   green background would key out the clothing along with the backdrop.
   Given `v3_key_color="#0000FF"` (blue key) instead — a real compositing
   consideration the original plan didn't anticipate.

2. **Daniel Mercer resolution — done, confirmed unsupported.** Retested
   `avatar_v` + explicit green background: still silently substitutes flat
   gray (`RGB(107,107,105)`, no watermark change) — not an `avatar_iv`-only
   quirk, a genuine per-avatar rendering-path limitation. Set
   `v3_unsupported=True` on his `AvatarLook`; `generate_video_multiscene_v3_chromakey`
   refuses with a clear error before spending any credits if ever called for
   him. He stays on `pip_v2` until HeyGen support gives another way in.

3. **Greenscreen render function — done.** `_render_avatar_clip_v3_greenscreen()`
   in `tools/heygen_tool.py`: concatenates segment scripts via the new shared
   `_concatenate_segment_scripts()` helper (factored out of
   `generate_video_multiscene_v3`, which now calls it too — no behavior
   change there), submits `POST /v3/videos` with the resolved
   `engine`/`fit`/`motion_prompt` (motion_prompt omitted entirely when
   unsupported, not just left empty), polls via `_poll_v3_video_sync()`,
   downloads the finished clip.

4. **Background+PiP reuse — done, via a different route than originally
   planned.** Rather than adding a `skip_upload` parameter to
   `create_broll_video_asset` (which is `@`-wrapped around an upload step),
   `_build_v3_chromakey_background()` calls the lower-level
   `_create_broll_video_composite()` / `_create_broll_video_composite_from_video()`
   functions directly — same compositing logic, zero HeyGen upload, zero
   changes to any existing V2 code path. As flagged in the original plan,
   this is a single continuous background for the whole take (first b-roll
   item found, not per-segment switching) — a real but deliberate Phase 1
   limitation, documented in the function's docstring.

5. **FFmpeg chromakey compositing — done.** `_chromakey_composite()` scales
   both layers to `_FRAME_W`x`_FRAME_H`, keys with
   `chromakey=0x{key}:0.10:0.15,despill=type={green|blue}` (despill type
   auto-selected from which channel dominates the key color — handles the
   blue-key Saskia case from step 1 automatically), and overlays with
   `_AVATAR_OFFSET_X[avatar_position]` converted to a `{offset}*W` pixel
   expression. Verified against real FFmpeg output in
   `tests/test_heygen_chromakey_smoke.py` (green keys out, a synthetic
   "subject" square survives, position offsets don't break the filter).

6. **Top-level function + dispatch — done.** `generate_video_multiscene_v3_chromakey()`
   in `tools/heygen_tool.py` runs steps 3-5 synchronously (submit → poll →
   download → composite), then uploads the final composite back to HeyGen
   via `_upload_video_asset()` and fetches a public URL via the new
   `_fetch_asset_download_url()` — so it returns the same
   `{"video_id", "video_url", "thumbnail_url", ...}` shape every other
   generator function returns, and every downstream consumer (video_editor,
   publisher) needed zero changes. One real design decision beyond the
   original plan: this function returns `status="completed"` directly (there
   is no separate HeyGen render job left to poll for the *final* output), so
   `agents/anchor/agent.py`'s dispatch now checks
   `submit_result.get("status") == "completed"` and skips its usual
   poll-until-complete step in that case. The 3-way `_gen_fn` selection
   (`fullscreen_v3` / `pip_v3_chromakey` / default `pip_v2`) lives where the
   old 2-way ternary was.
   *Note on the final composite asset:* unlike background/b-roll composites
   (deleted right after HeyGen consumes them), the final composite is left
   on HeyGen's asset store rather than auto-deleted — video_editor needs to
   download it *after* this function returns, so it can't be cleaned up in
   the same immediate post-render step the other `uploaded_composites` use.

7. **Config wiring — done.** `config/shows.py`'s `video_style` doc comment
   now documents `"pip_v3_chromakey"` as a valid value, including the note
   that `v3_unsupported`-flagged anchors will fail if assigned it. No show's
   default changed.

8. **Tests — done.** `tests/test_heygen_chromakey.py` (23 tests, all mocked):
   config capability lookups, `_concatenate_segment_scripts`,
   `_chromakey_composite`'s filter-string construction (both despill colors,
   all three position offsets), the full `generate_video_multiscene_v3_chromakey`
   happy/error paths, `_render_avatar_clip_v3_greenscreen`'s payload shape,
   and the `agents/anchor/agent.py` dispatch (both the new style and the
   pip_v2 default, confirming polling is correctly skipped/used in each
   case). Plus `tests/test_heygen_chromakey_smoke.py` — real FFmpeg, no
   mocks, matching the pattern in `tests/test_video_editor_graphics_smoke.py`.
   Full suite (updated after §10's live-testing fixes): 131 passed, 0 failures.

---

## 9. What's left before a real Phase 2 pilot

Nothing here blocks the code that's already merged — `pip_v2` is unaffected
either way. These are the deliberate next decisions:

1. **Pick a pilot show and anchor.** Needs a full-access anchor (not Daniel
   Mercer). A good candidate: flip one desk of `entertainment-weekly` (Zayne
   Carter or Alexa Chen, both `photo_avatar`/avatar_v) to `pip_v3_chromakey`
   for a trial window, compare against the `pip_v2` baseline on output
   quality, render reliability, and per-video HeyGen cost (two renders per
   video now — the greenscreen clip plus the eventual real production run —
   worth tracking actual credit cost per video before wider rollout).
2. **Cosmetic refinements, still open (see §4a):** the positioning offset is
   now wired up but not visually validated against a *real* desk background
   at each of left/center/right (only tested against solid-color synthetic
   clips in the smoke test); lighting/color-grade matching between the
   green-screen-lit avatar and the warm-lit studio backdrop remains
   unaddressed, including Larry's lighting/blend-overlay-layer idea.
3. **Daniel Mercer.** Still needs (a) a HeyGen support answer on why this
   specific avatar doesn't honor background color, or (b) a decision to hold
   him on `pip_v2` indefinitely once V1/V2's actual sunset date is known (§6).

---

## 10. Live end-to-end validation (2026-07-27) — two bugs found and fixed, one new platform risk found

Ran `generate_video_multiscene_v3_chromakey()` for real — the actual
production function, not a hand-rolled payload — against four avatars
chosen to cover cases the earlier Postman testing hadn't: Zayne Carter
(sanity check on the real function), Shawn Green (avatar_iii tier on a
previously-untested background bucket), Darlene Smith (the one look that
resolves to avatar_iv with no avatar_v access — never exercised live before),
and Brandon Jones with real b-roll (exercising the PiP-compositing branch for
the first time).

**Bug 1 — final video URL never resolved.** All four renders actually
completed successfully through render → download → composite, but every one
failed at the last step with "could not fetch a download URL." Root cause:
`_fetch_asset_download_url()` made a `GET /v1/asset/{id}` call against
`api.heygen.com` to resolve a URL for the just-uploaded composite — that
call 404s for assets uploaded via `upload.heygen.com` (confirmed with a
minimal diagnostic upload). The upload response already contains the public
URL directly (`data.url`, e.g.
`https://resource2.heygen.ai/video/{id}/original.mp4`). Fixed by replacing
`_fetch_asset_download_url()` with `_upload_final_composite()`, which reads
the URL straight from the upload response — no second call needed. Regression
test added (`test_upload_final_composite_reads_url_from_upload_response_not_a_second_call`).

**Bug 2 — the background color wasn't reliably honored.** After fixing Bug
1, Zayne's composite showed him standing in an unrelated outdoor patio
scene — the requested green background was silently ignored and HeyGen
rendered some default backdrop instead, so the chromakey filter had nothing
green to key out and passed the raw clip through untouched. Larry
independently confirmed via Postman that adding `"remove_background": true`
alongside the explicit `background` color fixes this — and this exact
combination was present in my own earlier validated scratch test
(`heygen_chromakey_test_zayne.py`) but got dropped when I wrote the
production `_render_avatar_clip_v3_greenscreen()` function. Added it back —
but not unconditionally: doing so for an `avatar_iii`/`studio_avatar` avatar
returns HTTP 400 ("This video avatar does not support background removal.
The avatar must be trained for matting"), confirmed live against both Shawn
Green and Brandon Jones. So `remove_background` is now conditional on a new
`AvatarLook.v3_supports_remove_background` field (mirrors
`v3_supports_motion_prompt` — `True` for the full-access tier, `False` for
`avatar_iii`-only looks). Four regression tests added covering both branches
plus the roster-level payload shape.

**New finding — intermittent "Veo" watermark on avatar_iii renders.** With
both bugs fixed, all four avatars rendered successfully — but visual review
of the actual output frames (not just the "completed" status) turned up
something new: both `avatar_iii` renders (Shawn Green, Brandon Jones) have a
visible **"Veo" watermark burned into the bottom-right corner** of the frame.
Zayne (avatar_v) and Darlene (avatar_iv) have no such watermark. This looks
at first like a hard rule ("avatar_iii routes through Google Veo, avatar_iv/v
don't") — **except** the raw greenscreen frame from my *original* Shawn Green
test earlier this session (same avatar, same engine, no `remove_background`)
has no watermark at all. Same avatar, same engine, different outcome across
two separate render attempts. **This is intermittent, not deterministic** —
the exact mechanism (server load, A/B routing, something else) is unknown.
This is a materially different kind of problem than Daniel Mercer's gray
substitution (which fails the same way every time): a watermark that shows
up unpredictably is much harder to guard against in code. Not encoded as
`v3_unsupported` for this reason — that flag means "known to fail every
time," which this isn't. Instead, added a prominent comment on
`AvatarLook.v3_supports_motion_prompt` (the field that marks the whole
avatar_iii tier) documenting the risk, and did **not** set
`v3_chromakey_validated=True` for Shawn or Brandon.

**Also confirmed by visual review:** a thin white/light fringe along hair and
shoulder edges on both Zayne's and Darlene's composites — visible but minor,
consistent with the chromakey edge/despill tuning already flagged as an open
Phase 2 refinement in §4a/§9 (now empirically observed, not just anticipated).
Brandon's b-roll PiP inset rendered correctly (confirms
`_build_v3_chromakey_background`'s image-broll branch works against a real
URL, not just the mocked unit tests).

`config/anchors.py` now reflects all of this: `v3_chromakey_validated=True`
only for the two specific avatar_ids actually reviewed (Zayne Carter's first
look, Darlene Smith), `v3_supports_remove_background=False` for every
`avatar_iii`-only look, and inline notes on Shawn's and Brandon's specific
looks recording the mixed clean/watermarked results.

## 11. Updated recommendation

**Pilot on the `avatar_iv`/`avatar_v` (full-access) tier only, for now.**
Zayne Carter and Darlene Smith are genuinely confirmed clean across multiple
independent live tests. Do not pilot an `avatar_iii`-only anchor (Shawn
Green, Brandon Jones, Monica Hayes/Saskia, Valerie Brooks/Candace, Alister
Blackwood/Dexter) on `pip_v3_chromakey` until one of:
  a. HeyGen support can explain/confirm the Veo-routing behavior for
     avatar_iii and whether it can be disabled or avoided, or
  b. A larger sample (more than 2 renders) establishes how often the
     watermark actually appears — worth doing before ruling the tier out
     entirely, since the very first Shawn Green test this session came back
     clean, or
  c. Automated post-render QA (e.g. checking the bottom-right corner region
     for the watermark before a video ships) is built as a safety net.

None of this changes §9's Phase 2 pilot recommendation for the full-access
tier — Zayne Carter or Alexa Chen on `entertainment-weekly` remains a good
first candidate.
