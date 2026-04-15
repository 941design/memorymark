# Feature 09: Firefox Compatibility

## Context

From the main spec (sorted-imagining-journal.md), Section 7.

Adapt the extension for Firefox. Handle API differences, offscreen document gap, and WebGPU availability.

## Requirements

### Cross-Browser Compatibility

| Feature | Chrome | Firefox |
|---------|--------|---------|
| Manifest | V3 | V3 (Firefox 109+) |
| Offscreen document (Gemma host) | `chrome.offscreen` API | Not available — use persistent background page or extension tab |
| WebGPU | Stable | Behind flag (`dom.webgpu.enabled`) |
| Keyboard shortcuts | `chrome.commands` | `browser.commands` |
| Storage | `chrome.storage` | `browser.storage` |

### Abstraction Layer

Use `globalThis.browser || globalThis.chrome` for API compatibility throughout the extension.

### Firefox Manifest

Create `extension/manifest.firefox.json` with Firefox-specific overrides if needed. Key differences:
- Background script may need `"persistent": true` for Gemma hosting
- No `"offscreen"` permission

### Gemma in Firefox

- WebGPU must be enabled (`dom.webgpu.enabled` flag)
- If unavailable, chat is disabled gracefully — capture still works
- Offscreen document alternative: use a hidden extension page (`about:blank` with extension origin) or a persistent background page

### Testing

1. Load extension in Firefox (about:debugging → This Firefox → Load Temporary Add-on)
2. Enable WebGPU flag if needed
3. Test capture flow (shortcut → extraction → bridge → overlay)
4. Test chat if WebGPU available

## Verification

1. Load extension in Firefox via about:debugging
2. Press Ctrl+Shift+M → capture works
3. Verify overlay appears and shows correct states
4. If WebGPU enabled, verify chat works
5. If WebGPU disabled, verify graceful degradation (capture works, chat shows message)
