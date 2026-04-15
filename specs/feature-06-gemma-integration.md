# Feature 06: Gemma Integration

## Context

From the main spec (sorted-imagining-journal.md), Section 3.5.

Integrate Gemma 4 WASM into the browser extension using MediaPipe Tasks GenAI + WebGPU. The model runs in a Chrome offscreen document and is cached in OPFS.

## Requirements

### Model

- Gemma 4 E2B (quantized, ~2GB `.task` file)
- Runtime: MediaPipe Tasks GenAI + WebGPU

### Offscreen Document

- `extension/offscreen.html` — hosts the WebGPU context and WASM runtime
- `extension/offscreen.js` — MediaPipe + Gemma initialization and chat interface
- Created on extension install, persists while needed
- Chrome's offscreen document API (`chrome.offscreen`)

### Model Caching

- On extension install, background worker triggers model download
- Model cached in Origin Private File System (OPFS) for persistence across sessions
- Download progress shown via extension badge (e.g., "47%" on the icon)
- If WebGPU/shader-f16 unavailable, skip download and disable chat (capture still works)

### Initialization Flow

```
Extension Install
  → Check WebGPU + shader-f16 support
  → If supported: stream-download model to OPFS (~2GB)
  → Badge shows progress: "DL 47%"
  → On complete: badge shows "✓"
  → Model ready for chat

First Chat Open
  → Create offscreen document (if not exists)
  → Load model from OPFS into MediaPipe
  → Initialize ChatMediaPipeGenAI
  → Ready for inference
```

### background.js Updates

- On install: check WebGPU support, start model download if supported
- Badge progress: update extension badge text during download
- Manage offscreen document lifecycle (create/destroy)
- Relay messages between content script and offscreen document

### offscreen.js

- Initialize MediaPipe GenAI with WebGPU backend
- Load Gemma model from OPFS
- Expose message-based interface for chat:
  - Receive: `{ action: "chat", message: "..." }`
  - Send: `{ action: "chat-response", text: "...", done: boolean }`
- Stream responses token-by-token

### Extension Settings

| Setting | Default | Description |
|---------|---------|-------------|
| `gemmaEnabled` | `true` | Whether to download/enable Gemma |

### Error Handling

- Gemma model download fails: retry with exponential backoff, badge shows error state, chat disabled until resolved
- WebGPU not available: skip Gemma entirely, capture works, chat shows "Gemma requires WebGPU" message

## Verification

1. Install extension in Chrome with WebGPU enabled
2. Badge should show download progress ("DL 47%")
3. After download, badge shows checkmark
4. Open DevTools → Application → Storage → OPFS → verify model file cached
5. Check that offscreen document is created when needed
6. If WebGPU unavailable, verify chat is disabled gracefully
