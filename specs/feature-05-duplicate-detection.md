# Feature 05: Duplicate Detection

## Context

From the main spec (sorted-imagining-journal.md), Sections 3.1.1 and 4.1.

Add dedicated duplicate detection endpoint to the bridge and integrate it into the capture flow with proper visual feedback.

Note: Basic duplicate checking is part of Feature 04's capture flow. This feature adds the dedicated endpoint and polishes the UX.

## Requirements

### Bridge Endpoint

`GET /api/drawers/by-url?url={url}`

- Exact match on `source_url` in drawer metadata
- Returns `{ "exists": true, "drawer_id": "...", "title": "..." }` or `{ "exists": false }`

### Overlay "Already Exists" State

When a duplicate is detected:

```
┌──────────────────────────────────┐
│  MemoryMark                   ✕  │
│                                  │
│  ℹ  Already in MemPalace         │
│     "Article Title"              │
│                                  │
│  [Open Chat]                     │
└──────────────────────────────────┘
```

- Info icon (not error — this is not a failure)
- Shows the title of the existing capture
- "Open Chat" button available to ask questions about it

## Verification

1. Capture a page via Ctrl+Shift+M
2. Press Ctrl+Shift+M again on the same page
3. Overlay should show "Already in MemPalace" with the page title
4. Verify via curl: `curl 'http://localhost:21517/api/drawers/by-url?url=https://example.com'`
