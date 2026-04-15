# Feature Spec: Gemma Auto-Categorization

## Problem Statement

Users who don't want to configure URL rules (custom-room-mapping) but still want intelligent organization need a smarter default. Gemma already runs in the extension and has context about the page content — it can suggest the most appropriate wing/room before saving.

## Core Functionality

When a page is captured, Gemma analyzes a short excerpt of the extracted Markdown and suggests a wing/room from those that exist in the user's MemPalace. The suggestion is shown in the overlay. The user can accept with one click or override via the wing/room picker (configurable-wing-selection feature).

## Functional Requirements

1. After DOM extraction, send the first ~500 tokens of Markdown to Gemma with a short classification prompt
2. Classification prompt: `"Given these existing categories: {wings/rooms}, which best fits this content? Title: {title}. Excerpt: {excerpt}. Reply with wing and room only."`
3. If Gemma returns a recognized wing/room pair within 3 seconds, show "Suggested: {wing} / {room}" in the overlay with Accept and Change buttons
4. If no suggestion arrives in 3 seconds, fall back to default wing/room and proceed
5. Accepted suggestion is used in `POST /api/drawers`; accepted suggestion updates the `lastWing`/`lastRoom` preference
6. If Gemma is not available (Firefox, WebGPU missing, disabled), this feature is silently skipped — URL rule mapping and defaults still apply
7. Classification inference must not block the capture flow — run in parallel with the duplicate-detection check

## Critical Constraints

- Must not delay the capture by more than 3 seconds (hard timeout)
- Must degrade gracefully when Gemma is unavailable
- Classification must only suggest wings/rooms that already exist (no hallucinated new categories)

## Integration Points

- `GET /api/taxonomy` — fetch existing wings and rooms to constrain suggestions
- `offscreen.js` — Gemma inference for classification
- `POST /api/drawers` — `wing` and `room` set from accepted suggestion
- Overlay UI — new "Suggested" state

## Out of Scope

- Creating new wings/rooms based on Gemma suggestions
- Multi-label classification (one wing/room per capture)
- Syncing suggestions across devices
