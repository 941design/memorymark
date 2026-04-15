# Feature 04: Capture Flow

## Context

From the main spec (sorted-imagining-journal.md), Section 4.1.

Wire the full capture flow: keyboard shortcut → DOM extraction → background worker → HTTP bridge → overlay feedback. This connects Features 01-03 into a working end-to-end capture pipeline.

## Requirements

### Capture Flow (Section 4.1)

```
1. User presses Ctrl+Shift+M (or Cmd+Shift+M on Mac)

2. Background service worker receives command event
   → Sends "extract" message to content script

3. Content script:
   a. Clones DOM
   b. Runs Readability → extracts article
   c. Runs Turndown → converts to Markdown
   d. Extracts metadata (title, URL, author, date)
   e. Returns payload to background worker

4. Background worker:
   a. Calls GET /api/drawers/by-url?url={source_url}
   b. If exists → sends "already-exists" to content script
   c. If not → calls POST /api/drawers with:
      {
        "wing": "Web",
        "room": "Captures",
        "content": "# Title\n\n> Source: ...\n\n{markdown body}",
        "metadata": {
          "source_url": "https://...",
          "title": "Page Title",
          "hall": "hall_discoveries",
          "timestamp": "2026-04-13T09:00:00Z"
        }
      }
   d. Sends result status to content script

5. Content script:
   → Shows overlay with appropriate state (saved / already exists / error)
   → Auto-dismiss after 4 seconds
```

### MemPalace Defaults (v1)

| Field | Default Value |
|-------|--------------|
| `wing` | `"Web"` |
| `room` | `"Captures"` |
| `hall` | `"hall_discoveries"` |
| `timestamp` | Current ISO 8601 |

### Extension Settings (stored in chrome.storage.local)

| Setting | Default | Description |
|---------|---------|-------------|
| `bridgeUrl` | `http://localhost:21517` | MemPalace bridge URL |
| `autoClose` | `4000` | Overlay auto-dismiss time (ms) |

### background.js Updates

- Read `bridgeUrl` from `chrome.storage.local` (default: `http://localhost:21517`)
- On capture command:
  1. Send `{ action: "extract" }` to content script
  2. Receive extraction payload from content script
  3. Check duplicate: `GET {bridgeUrl}/api/drawers/by-url?url={source_url}`
  4. If not duplicate: `POST {bridgeUrl}/api/drawers` with payload
  5. Send result to content script: `{ action: "capture-result", status: "saved"|"already-exists"|"error", data: {...} }`

### Overlay Feedback States (content.js)

Update overlay to show appropriate state:

- **Saving**: Spinner + "Saving page..."
- **Saved**: Checkmark + title + wing: `✓ Page saved to MemPalace / "Article Title" / Wing: Web`
- **Already exists**: Info icon + "Already in MemPalace"
- **Error**: Red icon + error message + retry button
- **Bridge unreachable**: Warning + "MemPalace bridge not running. Start with `docker compose up -d`"

### Overlay Position & Behavior

- Fixed, top-right corner of viewport
- Auto-dismisses after 4 seconds (configurable via `autoClose` setting) unless hovered
- Contains "[Open Chat]" button (non-functional placeholder for now)

## Verification

1. Start Docker bridge: `docker compose up -d`
2. Load extension in Chrome
3. Navigate to any article page
4. Press Ctrl+Shift+M → overlay shows "Saving..." then checkmark with title
5. Press Ctrl+Shift+M again → overlay shows "Already in MemPalace"
6. Stop Docker bridge → Press shortcut → overlay shows "Bridge not running" warning
7. Verify in bridge: `curl 'http://localhost:21517/api/search?query=<page title>'`
