# Feature 07: Chat Overlay

## Context

From the main spec (sorted-imagining-journal.md), Section 3.4.2.

Implement the chat UI in the content script overlay, with message routing to offscreen Gemma and streaming response rendering.

## Requirements

### Chat Mode

Opened via "Open Chat" button in capture feedback, or by pressing the shortcut when the overlay is already showing.

### Chat UI Layout

```
┌──────────────────────────────────┐
│  MemoryMark Chat              ✕  │
│──────────────────────────────────│
│                                  │
│  🤖 How can I help with your     │
│     saved pages?                 │
│                                  │
│  You: /summarize                 │
│                                  │
│  🤖 This article covers the      │
│     release of Grok 4...         │
│                                  │
│──────────────────────────────────│
│  [Type a message... ]     [Send] │
└──────────────────────────────────┘
```

### Size & Position

- ~400px wide, ~500px tall, resizable
- Positioned as a slide-in panel on the right edge of the viewport

### Chat Commands

- `/summarize` — Summarize the current page's stored content
- `/search <query>` — Search across all stored pages
- `/status` — Show MemPalace stats (drawer count, wings)
- Free-form questions — Gemma uses MemPalace search tools to answer

### Message Routing

```
Content Script (chat UI)
  → chrome.runtime.sendMessage({ action: "chat", message: "..." })
  → Background Worker
  → Relay to offscreen document
  → Gemma processes message
  → Stream response back via message passing
  → Content Script renders streaming response
```

### Streaming Rendering

- Show "Thinking..." indicator while waiting for first token
- Render tokens as they arrive (streaming)
- Tool call indicators shown inline (e.g., "Searching memory...")

### content.css Updates

- Chat panel styles
- Message bubbles (user vs assistant)
- Input area with send button
- Scroll behavior (auto-scroll to bottom)
- Resizable panel

## Verification

1. Capture a page, click "Open Chat"
2. Chat panel slides in from right
3. Type a message and press Enter or click Send
4. See "Thinking..." then streaming response
5. Close button dismisses chat
6. Pressing shortcut when overlay visible opens chat mode
