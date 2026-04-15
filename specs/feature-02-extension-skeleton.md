# Feature 02: Extension Skeleton

## Context

From the main spec (sorted-imagining-journal.md), Sections 3.2 and 3.4.

Create the browser extension file structure with Chrome Manifest V3. Implement the keyboard shortcut listener and empty overlay injection. This feature only sets up the skeleton — DOM extraction and bridge calls come in later features.

## Requirements

### File Structure

```
extension/
  manifest.json           # MV3 manifest (Chrome primary)
  background.js           # Service worker — shortcut handling
  content.js              # Overlay injection (empty for now)
  content.css             # Overlay styles
  offscreen.html          # Gemma host document (placeholder)
  offscreen.js            # MediaPipe + Gemma (placeholder)
  lib/                    # Third-party libs (added in Feature 03)
  icons/
    icon-16.png
    icon-48.png
    icon-128.png
```

### manifest.json (Chrome MV3)

```json
{
  "manifest_version": 3,
  "name": "MemoryMark",
  "version": "0.1.0",
  "description": "Capture web pages to MemPalace",
  "permissions": ["activeTab", "storage", "offscreen", "commands"],
  "host_permissions": ["http://localhost:21517/*"],
  "background": {
    "service_worker": "background.js"
  },
  "content_scripts": [
    {
      "matches": ["<all_urls>"],
      "js": ["content.js"],
      "css": ["content.css"]
    }
  ],
  "commands": {
    "capture-page": {
      "suggested_key": { "default": "Ctrl+Shift+M", "mac": "Command+Shift+M" },
      "description": "Capture current page to MemPalace"
    }
  },
  "icons": {
    "16": "icons/icon-16.png",
    "48": "icons/icon-48.png",
    "128": "icons/icon-128.png"
  }
}
```

### background.js

- Listen for `chrome.commands.onCommand` for `"capture-page"`
- When triggered, send a message to the active tab's content script: `{ action: "capture" }`

### content.js

- Listen for messages from background
- When `{ action: "capture" }` received, inject the overlay into the page DOM
- Overlay shows a placeholder "Saving..." state for now

### content.css

- Overlay styles: fixed position, top-right corner, z-index high enough to be above page content
- Styled as per Section 3.4.1 (capture feedback panel)
- Auto-dismiss after 4 seconds

### Overlay HTML structure (injected by content.js)

```
┌──────────────────────────────────┐
│  MemoryMark                   ✕  │
│                                  │
│  ⏳ Saving page...               │
│                                  │
└──────────────────────────────────┘
```

### Icons

Generate simple placeholder PNG icons at 16x16, 48x48, and 128x128 (can be solid color squares with "M" letter or similar minimal design).

## Verification

1. Load unpacked extension in Chrome (chrome://extensions → Developer Mode → Load unpacked)
2. Navigate to any page
3. Press Ctrl+Shift+M (or Cmd+Shift+M on Mac)
4. Overlay should appear in top-right corner with "Saving page..." text
5. Overlay should auto-dismiss after 4 seconds
6. Close button (✕) should dismiss overlay immediately
