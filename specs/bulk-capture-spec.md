# Feature Spec: Bulk Capture (Context Menu)

## Problem Statement

Researchers who encounter a list of relevant links (e.g., a search results page, a reading list, a bibliography) must visit each link individually and press `Ctrl+Shift+M` to capture it. Bulk capture via right-click context menu would allow saving multiple linked pages without navigating to each one.

## Core Functionality

A right-click context menu item on links lets the user queue linked pages for background capture. The extension fetches the HTML of each linked URL, extracts article content, and saves it to MemPalace — all without navigating away from the current page.

## Functional Requirements

1. Context menu item "Capture linked page to MemPalace" appears on right-click of any `<a>` element with an `href`
2. Clicking triggers a background fetch of the linked URL's HTML
3. Fetched HTML is processed through Readability + Turndown in the background (not requiring a content script injection on the target page)
4. Extracted content and metadata are sent to `POST /api/drawers` with the linked URL as `source_url`
5. Progress notification: a browser notification or badge update confirms "Saved: {title}" or "Failed: {url}"
6. If the URL is already captured, shows "Already in MemPalace" notification (no duplicate stored)
7. Up to 5 URLs may be queued simultaneously; additional items are queued and processed serially
8. Same-origin restriction applies: only http/https URLs can be fetched (no file://, chrome://, etc.)

## Critical Constraints

- Background fetch must respect CORS — only pages that allow cross-origin reads can be fetched this way; pages that block cross-origin reads will show a "Could not fetch" error
- Must not inject content scripts into the target URL (background fetch only)
- Duplicate detection must use the same `/api/drawers/by-url` endpoint as normal capture

## Integration Points

- `chrome.contextMenus` API — register "Capture linked page" item
- `chrome.notifications` API — capture confirmation notification
- Background fetch + Readability/Turndown in service worker context
- `POST /api/drawers` — same capture endpoint as keyboard shortcut
- `GET /api/drawers/by-url` — duplicate check before fetch

## Out of Scope

- Recursive crawling (capture all links on a page)
- Scheduled or periodic background captures
- Capturing pages behind authentication
