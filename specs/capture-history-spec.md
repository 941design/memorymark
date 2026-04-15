# Feature Spec: Capture History Popup

## Problem Statement

Currently the extension has no popup UI — clicking the icon on a normal page triggers capture, and on restricted pages opens the chat tab. Users have no way to review what they've recently saved or jump to a saved page without opening the chat overlay and typing `/search`.

## Core Functionality

Clicking the extension icon opens a small popup listing the user's most recently captured pages with titles, domains, and timestamps. Each entry links to the source URL and offers a quick "Chat about this" action.

## Functional Requirements

1. The extension popup (`popup.html`) shows the 10 most recently captured pages
2. Each entry displays: page title, domain (not full URL), and relative timestamp (e.g., "2 hours ago")
3. Clicking an entry opens the source URL in a new tab
4. Each entry has a "Chat" button that opens the chat overlay on the source page (or the standalone chat tab if the source page is restricted)
5. Popup header shows total drawer count and a search input
6. Typing in the search input filters the list using `GET /api/search` (debounced, 300ms)
7. If the bridge is unreachable, popup shows "Bridge not running — run `make up`"
8. Popup data is fetched fresh from the bridge on each open (no local cache)

## Critical Constraints

- Popup must open within 200ms; bridge query must complete within 1 second or show a loading indicator
- Must not break current behavior on restricted pages (standalone chat tab still opens from background.js)
- Popup must be accessible (keyboard-navigable list, visible focus ring)

## Integration Points

- `GET /api/search` — fetch recent captures (sorted by `filed_at` descending, limit 10)
- `GET /api/status` — drawer count for popup header
- `popup.html` / `popup.js` — new extension popup files
- `manifest.json` — `"default_popup": "popup.html"` in `action` section
- `background.js` — may need to change icon click behavior to let popup open instead of triggering capture

## Out of Scope

- Editing or deleting captures from the popup
- Pagination beyond the 10 most recent entries (use chat `/search` for broader queries)
- Syncing history across browsers or devices
