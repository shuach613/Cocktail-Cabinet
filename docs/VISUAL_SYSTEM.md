# Visual system · The Cocktail Cabinet

The interface remains deliberately **Bordeaux and Champagne**—an editorial
cocktail-cabinet aesthetic, not a generic dashboard. This document describes
the design principles used for the current UI and the responsive review process.

## Identity (unchanged)

- **Typeface:** Lato throughout the website, installed PWA and authentication.
  Bold, carefully spaced display headings; restrained letterspacing for tiny
  eyebrow labels; comfortable line heights for recipes and form descriptions.
- **Brand Bordeaux:** light `--accent: #742E43`; dark accent `#E9B7C6`.
- **Champagne:** light `--champagne: #C5A174`; dark `#E4C18D`.
- **Background:** light `#F8F4EE`; dark `#181619`.
- **No new independent hues:** success uses the established Champagne family;
  dangerous actions use the existing `--danger` token; photography supplies
  natural warmth without altering UI colours.

The base source of truth for these tokens is `static/premium.css`.
`static/finishing.css` is the final, structured component layer. It defines
only derived `--ui-*` radius, spacing, border and shadow values, plus
component styles. Never introduce one-off hardcoded hex colours to this layer.

## Visual hierarchy

1. **The drink first.** Discover cards use genuine 4:3 photos; they don't crop
   the glass into an accidental widescreen strip. Method badges stay quiet.
2. **One consistent control language.** Actions are at least 44px tall for
   touch, with the same radii and visible keyboard focus. Champagne carries
   emphasis; secondary actions stay neutral.
3. **A clear reading rhythm.** Pages use consistent headings, eyebrows,
   supporting descriptions, sections, panels and label spacing.
4. **Ownership isn't preparation.** Recipe rows reserve three stable columns:
   the *manual* preparation checkbox, ingredient name with an understated
   ownership line and circular check, and a right-aligned quantity. The
   ownership row does not change the recipe or touch the user's shelf.
5. **Calm surfaces.** UI hierarchy comes from carefully spaced strokes,
   warm panel fills and lightweight shadows—no excessive gradients, neon
   status highlights, or unrelated accent colours.
6. **Dark mode as a first-class theme.** The exact same component hierarchy
   applies in light and dark mode. Surface contrast, accents, shadows and
   status readability derive from theme tokens, never separately hardcoded.

## Components and screens

| Screen | Primary design requirement |
| --- | --- |
| Discover | Stable photo crop, aligned captions and titles, unclipped ingredient preview, light-touch hover |
| Recipe | Readable hero, stable availability icon, separate prep checkbox, accessible progress indicator, ordered instructions |
| My Shelf | Indented nested families, legible generic-versus-brand options, clear selected states and persistent save action |
| Create / edit | Comfortable field widths, clear errors, responsive inline ingredient/category editors, workable dialogs |
| Settings | Equal-weight navigation panels, an appearance control that aligns at every width |
| Catalogue | Keyboard-operable categories/products tabs, understandable tree hierarchy, legible nested forms |
| Manage recipes | Distinct photo, name and edit actions; photo upload menu stays inside phone viewport |
| Members / invites | Roles and account status remain visible on mobile; long emails wrap instead of clipping |
| API access | Long token prefixes wrap safely; issuer form and token list share visual rhythm |
| Sign-in / setup | Same Lato and brand colours with calm focus outlines and readable forms |
| OAuth consent | Shared panel, responsive action controls, clear permissions; no custom inline styling |
| Invitation / token result | High-contrast one-time secret field and clear copy/finish actions |

## Responsive and accessibility review

Breakpoints are **1440**, **1024**, **768**, **390**, and **360** CSS pixels.
Both light and dark mode are tested in Chromium using a **throwaway** seeded
database and loopback server:

```bash
pip install -r requirements-dev.txt playwright==1.57.0
python -m playwright install chromium
python tools/visual_audit.py
```

The browser script screenshots ten representative app screens per viewport and
theme (100 PNGs), checks that the selected theme stays correct, and fails on
horizontal document overflow, uncaught page JavaScript errors and broken
images. Files are saved under `artifacts/ui-review/` and ignored by git.
On GitHub, the `Responsive visual QA` workflow attaches the screenshots and
text report to the PR's Actions run.

**Browser automation is a guardrail, not a guarantee of pixel perfection.**
Review the resulting screenshots manually for tone, visual alignment, tap
targets, truncation, panel balance, photography crop and legibility in both
themes. Also test an installed PWA and one iOS Safari device when practical,
as Chromium alone does not cover every engine or native safe-area behaviour.

## Safe changes

The design pass does not alter recipe data, shelf matching, catalogue ownership,
API permissions, or database schema. UI changes should preserve every
underlying action and existing keyboard flow. Where visual adjustments require
markup changes, add regression tests for the rendered semantics.
