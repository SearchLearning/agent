---
inclusion: fileMatch
fileMatchPattern: 'opensearch_orchestrator/ui/**'
---
# OpenSearch UI (OUI) Design System — Steering Document

## Purpose

This document defines the canonical design tokens and guidelines for the OpenSearch-branded shadcn theme. All UI development must conform to these tokens to ensure visual consistency across light and dark modes.

## Logo

### OpenSearch Logomark (Mark)

The canonical OpenSearch logomark SVG. Use this inline SVG for all brand placements. The mark uses `currentColor` for the outer elements and a deep navy (`#082F49`) for the center swirl. On dark backgrounds, pass `currentColor` or override fills as needed.

```svg
<svg viewBox="0 0 42.6667 42.6667" fill="none" xmlns="http://www.w3.org/2000/svg">
  <path d="M41.1583 15.6667C40.3252 15.6667 39.6499 16.342 39.6499 17.1751C39.6499 29.5876 29.5876 39.6499 17.1751 39.6499C16.342 39.6499 15.6667 40.3252 15.6667 41.1583C15.6667 41.9913 16.342 42.6667 17.1751 42.6667C31.2537 42.6667 42.6667 31.2537 42.6667 17.1751C42.6667 16.342 41.9913 15.6667 41.1583 15.6667Z" fill="#075985"/>
  <path d="M32.0543 25.3333C33.5048 22.967 34.9077 19.8119 34.6317 15.3947C34.06 6.24484 25.7726 -0.696419 17.9471 0.0558224C14.8835 0.350311 11.7379 2.84747 12.0173 7.32032C12.1388 9.26409 13.0902 10.4113 14.6363 11.2933C16.1079 12.1328 17.9985 12.6646 20.1418 13.2674C22.7308 13.9956 25.7339 14.8135 28.042 16.5144C30.8084 18.553 32.6994 20.9162 32.0543 25.3333Z" fill="#082F49"/>
  <path d="M2.6124 9.33333C1.16184 11.6997 -0.241004 14.8548 0.0349954 19.2719C0.606714 28.4218 8.89407 35.3631 16.7196 34.6108C19.7831 34.3164 22.9288 31.8192 22.6493 27.3463C22.5279 25.4026 21.5765 24.2554 20.0304 23.3734C18.5588 22.5339 16.6681 22.0021 14.5248 21.3992C11.9358 20.6711 8.93276 19.8532 6.62463 18.1522C3.85831 16.1136 1.96728 13.7505 2.6124 9.33333Z" fill="#075985"/>
</svg>
```

### Logo Size Variants

Sourced from Figma (`shadcn-OUI`, node `20139:27660`).

| Variant | Container | Mark Size | Padding | Usage |
|---|---|---|---|---|
| Sm | 16px | 14.2px | 0.89px | Favicons, compact UI, tab icons |
| Default | 24px | 21.3px | 1.33px | Navigation bars, inline branding |
| Lg | 32px | 28.4px | 1.78px | Page headers, card headers |
| Xl | 48px | 42.7px | 2.67px | Login screens, hero sections, splash |

### Logo Color Rules

- Outer paths (arc + bottom swirl): `#075985` (sky-800) — matches `--brand-primary` in light mode
- Center swirl: `#082F49` (sky-950)
- On dark backgrounds: use white (`#FAFAFA`) for outer paths, keep center swirl or lighten to `#0EA5E9` (sky-400) for contrast
- In dark mode, `--brand-primary` shifts to `#0284c7` (sky-600) and `--brand-secondary` to `#bae6fd` (sky-200) for decorative/accent use
- Never place the logo on a background that doesn't meet 3:1 contrast ratio against the mark fills
- Always maintain the original aspect ratio — do not stretch or distort

### OpenSearch Logo React Component

The logo is implemented as a reusable React component at `src/components/opensearch-logo.tsx`. All brand placements must use this component — do not create alternative logo implementations.

**Import:**
```tsx
import { OpenSearchLogo } from "@/components/opensearch-logo";
```

**Props:**

| Prop | Type | Default | Description |
|---|---|---|---|
| `size` | `"sm" \| "default" \| "lg" \| "xl"` | `"default"` | Size variant (16px, 24px, 32px, 48px) |
| `className` | `string` | — | Additional CSS classes passed to the SVG element |

**SVG Structure:**
- Three `<path>` elements with `viewBox="0 0 42.6667 42.6667"`
- Outer arc + bottom swirl: `fill-primary` (inherits from theme)
- Center swirl: `fill-foreground` (inherits from theme)
- Includes `aria-label="OpenSearch"` and `role="img"` for accessibility
- `shrink-0` applied by default to prevent flex shrinking

**Usage Examples:**
```tsx
{/* Sidebar / nav bar */}
<OpenSearchLogo size="default" />

{/* Page header */}
<OpenSearchLogo size="lg" />

{/* Sign-in / hero */}
<OpenSearchLogo size="xl" />

{/* On primary-colored backgrounds, override fills */}
<OpenSearchLogo
  size="lg"
  className="fill-primary-foreground [&>path]:fill-primary-foreground"
/>
```

**Rules:**
- Always use this component for the OpenSearch logo — never inline the SVG directly
- Do not modify the SVG paths or viewBox
- Do not change the default fill classes (`fill-primary`, `fill-foreground`) except when placing on colored backgrounds
- When overriding fills for colored backgrounds, use the `className` prop with `[&>path]:fill-*` selectors
- The component automatically adapts to dark mode via CSS custom properties

## Color System

### Semantic Color Tokens

All colors are expressed as CSS custom properties on `:root` (light) and `.dark` (dark) selectors using HSL values.

| Token | Light Mode | Purpose |
|---|---|---|
| `--background` | `#ffffff` | Page background |
| `--foreground` | `#0a0a0a` | Primary text |
| `--card` | `#ffffff` | Card surfaces |
| `--card-foreground` | `#0a0a0a` | Card text |
| `--primary` | `#0369a1` (sky-700) | Buttons, links, active states |
| `--primary-foreground` | `#fafafa` | Text on primary surfaces |
| `--secondary` | `#f5f5f5` | Secondary surfaces |
| `--secondary-foreground` | `#171717` | Text on secondary surfaces |
| `--muted` | `#f5f5f5` | Muted backgrounds |
| `--muted-foreground` | `#737373` | Muted/placeholder text |
| `--accent` | `#e0f2fe` (sky-100) | Hover states, highlights |
| `--accent-foreground` | `#171717` | Text on accent surfaces |
| `--border` | `#d4d4d4` | Default borders |
| `--input` | `#e5e5e5` | Input borders |

### Brand Tokens

| Token | Value | Purpose |
|---|---|---|
| `--brand-primary` | `#075985` (sky-800) | Brand-level primary |
| `--brand-secondary` | `#082f49` (sky-950) | Dark mode background, brand deep |

### Color Usage Rules

- Primary actions (buttons, links, chart active elements) use `--primary` (#0369a1).
- Hover/highlight states use `--accent` (#e0f2fe) in light mode.
- Dark mode background uses `--brand-secondary` (#082f49) as the deep navy base.
- Never use raw hex values in components — always reference CSS custom properties.
- Destructive actions use the `--destructive` token (red family).


## Typography

### Font Families

| Token | Value | Usage |
|---|---|---|
| `--font-sans` | `"Inter", system-ui, sans-serif` | All body text, headings, UI elements |
| `--font-mono` | `"Inter Mono", monospace` | Code blocks, technical values |

### Font Weights

| Name | Value | Usage |
|---|---|---|
| Light | 300 | De-emphasized text, captions |
| Normal | 400 | Body text default |
| Medium | 500 | Subheadings, labels, emphasis |
| Semibold | 600 | Headings, button text |
| Bold | 700 | Strong emphasis, primary headings |

### Type Scale

| Name | Size / Line Height | Usage |
|---|---|---|
| `xs` | 12px / 16px | Badges, fine print |
| `sm` | 14px / 20px | Secondary text, table cells |
| `base` | 16px / 24px | Body text default |
| `lg` | 18px / 28px | Section subheadings |
| `xl` | 20px / 28px | Card titles |
| `2xl` | 24px / 32px | Page section headings |
| `3xl` | 30px / 36px | Page titles |
| `4xl` | 36px / 40px | Hero/display text |

### Typography Rules

- Default body text: `base` size, `normal` weight, `--font-sans`.
- Headings: Use semibold (600) or bold (700) weight. Never use light weight for headings.
- Monospace font only for code snippets, data values, and technical identifiers.
- Line heights are fixed per size step — do not override.

## Spacing

All spacing uses a 4px base unit. Reference by Tailwind class or CSS custom property.

| Token | Value | Tailwind Class |
|---|---|---|
| `spacing-0` | 0px | `p-0`, `m-0` |
| `spacing-0.5` | 2px | `p-0.5`, `m-0.5` |
| `spacing-1` | 4px | `p-1`, `m-1` |
| `spacing-1.5` | 6px | `p-1.5`, `m-1.5` |
| `spacing-2` | 8px | `p-2`, `m-2` |
| `spacing-3` | 12px | `p-3`, `m-3` |
| `spacing-4` | 16px | `p-4`, `m-4` |
| `spacing-6` | 24px | `p-6`, `m-6` |
| `spacing-8` | 32px | `p-8`, `m-8` |
| `spacing-12` | 48px | `p-12`, `m-12` |
| `spacing-16` | 64px | `p-16`, `m-16` |
| `spacing-32` | 128px | `p-32`, `m-32` |

### Spacing Rules

- Component internal padding: `spacing-3` (12px) to `spacing-4` (16px).
- Card padding: `spacing-6` (24px).
- Page-level margins: `spacing-8` (32px) minimum.
- Gap between related elements: `spacing-2` (8px).
- Gap between sections: `spacing-6` (24px) to `spacing-8` (32px).

## Border Radius

| Token | Value | Usage |
|---|---|---|
| `--radius-md` | 4px | Small elements (badges, chips) |
| `--radius-lg` | 6px | Default for buttons, inputs, cards |
| `--radius-xl` | 8px | Larger containers, dialogs |
| `--radius-2xl` | 16px | Feature cards, hero sections |
| `--radius-full` | 9999px | Avatars, circular elements |

### Radius Rules

- shadcn `--radius` base variable: `0.375rem` (6px) — maps to `rounded-lg`.
- Buttons and inputs use `--radius` (the shadcn default calc).
- Cards use `--radius-lg` or `--radius-xl`.
- Never mix radius values on adjacent elements in the same visual group.

## Shadows

| Token | Value | Usage |
|---|---|---|
| `--shadow-xs` | `0 1px 2px 0 rgba(0,0,0,0.05)` | Subtle elevation (inputs, small cards) |
| `--shadow-sm` | `0 1px 2px -1px rgba(0,0,0,0.1), 0 1px 3px 0 rgba(0,0,0,0.1)` | Default card elevation |
| `--shadow-lg` | `0 4px 6px -4px rgba(0,0,0,0.1), 0 10px 15px -3px rgba(0,0,0,0.1)` | Dialogs, dropdowns, popovers |

### Shadow Rules

- Cards at rest: `--shadow-sm`.
- Elevated overlays (dialogs, dropdowns): `--shadow-lg`.
- Inputs: `--shadow-xs` or none.
- Dark mode: reduce shadow opacity or disable shadows — dark surfaces don't cast visible shadows.

## Border Width

| Token | Value | Usage |
|---|---|---|
| `border` | 1px | Default borders (cards, inputs, dividers) |
| `border-2` | 2px | Focus rings, emphasis borders |

## Sizing Reference

| Token | Value | Usage |
|---|---|---|
| `h-4` | 16px | Icons (small) |
| `h-5` | 20px | Icons (default) |
| `h-6` | 24px | Icons (medium) |
| `h-8` | 32px | Small buttons, compact inputs |
| `h-9` | 36px | Default button/input height |
| `h-10` | 40px | Large button/input height |
| `h-16` | 64px | Avatar (large), section headers |
| `max-w-xl` | 576px | Form max-width, content column |

## Opacity

| Token | Value | Usage |
|---|---|---|
| `opacity-25` | 25% | Heavily muted elements |
| `opacity-50` | 50% | Disabled states |
| `opacity-60` | 60% | Placeholder text alternative |

## Dark Mode Guidelines

- Dark mode is toggled via the `.dark` class on `<html>` or `<body>`.
- Background: `--brand-secondary` (#082f49) as the deep navy base.
- Foreground text: light neutral (#fafafa or similar).
- Primary color remains `--primary` but may shift slightly for contrast.
- Borders lighten (higher opacity white or lighter neutral).
- Shadows are minimized or removed in dark mode.
- All components must support both modes — never hard-code light-only colors.

## Component Guidelines

- All components use shadcn/ui primitives — do not build custom equivalents.
- Every color, spacing, radius, and shadow value must come from the tokens above.
- Use `cn()` utility (clsx + tailwind-merge) for conditional class composition.
- Prefer Tailwind utility classes over inline styles.
- Test every component in both light and dark mode before shipping.

# OpenSearch UI (OUI) Design System — Steering Document

## Purpose

This document defines the canonical design tokens and guidelines for the OpenSearch-branded shadcn theme. All UI development must conform to these tokens to ensure visual consistency across light and dark modes.

## Logo

### OpenSearch Logomark (Mark)

The canonical OpenSearch logomark SVG. Use this inline SVG for all brand placements. The mark uses `currentColor` for the outer elements and a deep navy (`#082F49`) for the center swirl. On dark backgrounds, pass `currentColor` or override fills as needed.

```svg
<svg viewBox="0 0 42.6667 42.6667" fill="none" xmlns="http://www.w3.org/2000/svg">
  <path d="M41.1583 15.6667C40.3252 15.6667 39.6499 16.342 39.6499 17.1751C39.6499 29.5876 29.5876 39.6499 17.1751 39.6499C16.342 39.6499 15.6667 40.3252 15.6667 41.1583C15.6667 41.9913 16.342 42.6667 17.1751 42.6667C31.2537 42.6667 42.6667 31.2537 42.6667 17.1751C42.6667 16.342 41.9913 15.6667 41.1583 15.6667Z" fill="#075985"/>
  <path d="M32.0543 25.3333C33.5048 22.967 34.9077 19.8119 34.6317 15.3947C34.06 6.24484 25.7726 -0.696419 17.9471 0.0558224C14.8835 0.350311 11.7379 2.84747 12.0173 7.32032C12.1388 9.26409 13.0902 10.4113 14.6363 11.2933C16.1079 12.1328 17.9985 12.6646 20.1418 13.2674C22.7308 13.9956 25.7339 14.8135 28.042 16.5144C30.8084 18.553 32.6994 20.9162 32.0543 25.3333Z" fill="#082F49"/>
  <path d="M2.6124 9.33333C1.16184 11.6997 -0.241004 14.8548 0.0349954 19.2719C0.606714 28.4218 8.89407 35.3631 16.7196 34.6108C19.7831 34.3164 22.9288 31.8192 22.6493 27.3463C22.5279 25.4026 21.5765 24.2554 20.0304 23.3734C18.5588 22.5339 16.6681 22.0021 14.5248 21.3992C11.9358 20.6711 8.93276 19.8532 6.62463 18.1522C3.85831 16.1136 1.96728 13.7505 2.6124 9.33333Z" fill="#075985"/>
</svg>
```

### Logo Size Variants

| Variant | Container | Mark Size | Padding | Usage |
|---|---|---|---|---|
| Sm | 16px | 14.2px | 0.89px | Favicons, compact UI, tab icons |
| Default | 24px | 21.3px | 1.33px | Navigation bars, inline branding |
| Lg | 32px | 28.4px | 1.78px | Page headers, card headers |
| Xl | 48px | 42.7px | 2.67px | Login screens, hero sections, splash |

### Logo Color Rules

- Outer paths (arc + bottom swirl): `#075985` (sky-800) — matches `--brand-primary` in light mode
- Center swirl: `#082F49` (sky-950)
- On dark backgrounds: use white (`#FAFAFA`) for outer paths
- Never place the logo on a background that doesn't meet 3:1 contrast ratio against the mark fills
- Always maintain the original aspect ratio — do not stretch or distort

## Color System

### Semantic Color Tokens

All colors are expressed as CSS custom properties on `:root` (light) and `.dark` (dark) selectors using HSL values.

| Token | Light Mode | Purpose |
|---|---|---|
| `--background` | `#ffffff` | Page background |
| `--foreground` | `#0a0a0a` | Primary text |
| `--card` | `#ffffff` | Card surfaces |
| `--card-foreground` | `#0a0a0a` | Card text |
| `--primary` | `#0369a1` (sky-700) | Buttons, links, active states |
| `--primary-foreground` | `#fafafa` | Text on primary surfaces |
| `--secondary` | `#f5f5f5` | Secondary surfaces |
| `--secondary-foreground` | `#171717` | Text on secondary surfaces |
| `--muted` | `#f5f5f5` | Muted backgrounds |
| `--muted-foreground` | `#737373` | Muted/placeholder text |
| `--accent` | `#e0f2fe` (sky-100) | Hover states, highlights |
| `--accent-foreground` | `#171717` | Text on accent surfaces |
| `--border` | `#d4d4d4` | Default borders |
| `--input` | `#e5e5e5` | Input borders |

### Brand Tokens

| Token | Value | Purpose |
|---|---|---|
| `--brand-primary` | `#075985` (sky-800) | Brand-level primary |
| `--brand-secondary` | `#082f49` (sky-950) | Dark mode background, brand deep |

### Color Usage Rules

- Primary actions (buttons, links, active elements) use `--primary` (#0369a1).
- Hover/highlight states use `--accent` (#e0f2fe) in light mode.
- Dark mode background uses `--brand-secondary` (#082f49) as the deep navy base.
- Never use raw hex values in components — always reference CSS custom properties.
- Destructive actions use the `--destructive` token (red family).

## Typography

### Font Families

| Token | Value | Usage |
|---|---|---|
| `--font-sans` | `"Inter", system-ui, sans-serif` | All body text, headings, UI elements |
| `--font-mono` | `"Inter Mono", monospace` | Code blocks, technical values |

### Font Weights

| Name | Value | Usage |
|---|---|---|
| Light | 300 | De-emphasized text, captions |
| Normal | 400 | Body text default |
| Medium | 500 | Subheadings, labels, emphasis |
| Semibold | 600 | Headings, button text |
| Bold | 700 | Strong emphasis, primary headings |

### Type Scale

| Name | Size / Line Height | Usage |
|---|---|---|
| `xs` | 12px / 16px | Badges, fine print |
| `sm` | 14px / 20px | Secondary text, table cells |
| `base` | 16px / 24px | Body text default |
| `lg` | 18px / 28px | Section subheadings |
| `xl` | 20px / 28px | Card titles |
| `2xl` | 24px / 32px | Page section headings |

## Spacing

All spacing uses a 4px base unit.

| Token | Value | Usage |
|---|---|---|
| `spacing-1` | 4px | Tight gaps |
| `spacing-2` | 8px | Gap between related elements |
| `spacing-3` | 12px | Component internal padding |
| `spacing-4` | 16px | Component internal padding |
| `spacing-6` | 24px | Card padding, section gaps |
| `spacing-8` | 32px | Page-level margins |

## Border Radius

| Token | Value | Usage |
|---|---|---|
| `--radius-md` | 4px | Small elements (badges, chips) |
| `--radius-lg` | 6px | Default for buttons, inputs, cards |
| `--radius-xl` | 8px | Larger containers, dialogs |
| `--radius-2xl` | 16px | Feature cards, hero sections |
| `--radius-full` | 9999px | Avatars, circular elements |

## Shadows

| Token | Value | Usage |
|---|---|---|
| `--shadow-xs` | `0 1px 2px 0 rgba(0,0,0,0.05)` | Subtle elevation (inputs, small cards) |
| `--shadow-sm` | `0 1px 2px -1px rgba(0,0,0,0.1), 0 1px 3px 0 rgba(0,0,0,0.1)` | Default card elevation |
| `--shadow-lg` | `0 4px 6px -4px rgba(0,0,0,0.1), 0 10px 15px -3px rgba(0,0,0,0.1)` | Dialogs, dropdowns, popovers |

## Dark Mode Guidelines

- Dark mode is toggled via the `.dark` class on `<html>` or `<body>`.
- Background: `--brand-secondary` (#082f49) as the deep navy base.
- Foreground text: light neutral (#fafafa or similar).
- Borders lighten (higher opacity white or lighter neutral).
- Shadows are minimized or removed in dark mode.
- All components must support both modes — never hard-code light-only colors.

## Standalone UI Notes

The search builder UI (`opensearch_orchestrator/ui/search_builder/`) is a plain HTML + React (UMD) app with no build step. It uses raw CSS custom properties in `styles.css` rather than Tailwind utilities. When working on this UI:

- Define OUI tokens as CSS custom properties in `:root` inside `styles.css`
- Reference them via `var(--token-name)` in all style rules
- Do not use raw hex/rgb color literals — map everything to a token
- Inter font can be loaded via Google Fonts (`@import url(...)`) since there is no bundler
