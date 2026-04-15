# Feature Spec: Configurable Wing Selection

## Problem Statement

In v1, all web captures go to wing `Web`, room `Captures`. Users who maintain multiple research domains (work, personal, academic) cannot organize captures into separate wings at save time. They must manually reorganize via MCP or bridge API calls after the fact.

## Core Functionality

Before (or during) saving, the user can select a wing and optionally a room from those that already exist in their MemPalace, or type a new one. The selection defaults to the last-used wing/room to minimize friction for users who work in one domain at a time.

## Functional Requirements

1. When `Ctrl+Shift+M` is pressed, the overlay shows a wing/room picker before or alongside the "Saving…" state
2. The picker is pre-populated with existing wings and rooms from `GET /api/taxonomy`
3. The user can select an existing wing/room or type a new name to create one
4. The default selection is the last-used wing/room (persisted in `chrome.storage.local`)
5. If the user dismisses or does not interact within 3 seconds, the default is used and capture proceeds
6. The selected wing/room is sent in the `POST /api/drawers` request body
7. `/api/drawers/by-url` duplicate check uses the selected wing/room (or checks across all wings)

## Critical Constraints

- Must not add significant latency to the capture path; taxonomy fetch should be cached or pre-fetched
- Must not break existing default behavior when no selection is made
- Must remain accessible (keyboard-navigable picker)

## Integration Points

- `GET /api/taxonomy` — fetch available wings and rooms
- `POST /api/drawers` — `wing` and `room` fields, currently hardcoded to `Web`/`Captures`
- `chrome.storage.local` — persist `lastWing` and `lastRoom` between sessions

## Out of Scope

- Creating sub-rooms or hierarchies beyond wing/room
- Bulk re-categorization of existing captures
- Automatic wing suggestion (see `gemma-auto-categorization-spec.md`)
