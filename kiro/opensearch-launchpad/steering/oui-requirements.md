---
inclusion: fileMatch
fileMatchPattern: 'opensearch_orchestrator/ui/**'
---

# OUI Requirements Document

## Introduction

This document defines the requirements for the OpenSearch-branded OUI design system tokens. Requirements cover CSS custom property generation, light/dark mode token definitions, font setup, dark mode toggling, and WCAG contrast compliance.

## Key Token Requirements

### Light Mode (`:root`)

- `--primary`: HSL of `#0369a1` (sky-700) — buttons, links, active states
- `--background`: HSL of `#ffffff`
- `--accent`: HSL of `#e0f2fe` (sky-100) — hover states, highlights
- `--border`: HSL of `#d4d4d4`
- `--input`: HSL of `#e5e5e5`
- `--muted-foreground`: HSL of `#737373`
- Radius tokens: `--radius-md` (4px), `--radius-lg` (6px), `--radius-xl` (8px), `--radius-2xl` (16px)
- Shadow tokens: `--shadow-xs`, `--shadow-sm`, `--shadow-lg`

### Dark Mode (`.dark`)

- `--background`: HSL of `#0a0a0a` (neutral-950)
- `--foreground`: HSL of `#fafafa`
- `--primary`: HSL of `#0284c7` (sky-600)
- `--primary-foreground`: HSL of `#171717` (neutral-900)
- `--card`: HSL of `#171717` (neutral-900)
- `--secondary` / `--muted`: HSL of `#262626` (neutral-800)
- `--accent`: HSL of `#0c4a6e` (sky-900)
- `--border`: alpha white `rgba(255,255,255,0.2)`
- `--input`: alpha white `rgba(255,255,255,0.15)`
- `--muted-foreground`: HSL of `#a3a3a3` (neutral-400)
- Shadows: minimized or disabled in dark mode

### Font Setup

- `--font-sans`: `"Inter", system-ui, sans-serif`
- `--font-mono`: `"Inter Mono", ui-monospace, monospace`
- Inter weights: 300, 400, 500, 600, 700 with `font-display: swap`

### Dark Mode Toggle

- Add/remove `.dark` class on `<html>` or `<body>`
- All styles update instantly via CSS variable resolution — no JS re-render needed

### No Raw Color Values

- All color references must use `var(--token-name)` — never raw hex, rgb, or hsl literals

### WCAG Contrast

- Normal text foreground/background pairs: minimum 4.5:1 contrast ratio
- Large text and UI components: minimum 3:1 contrast ratio
- Both light and dark modes must meet these ratios
