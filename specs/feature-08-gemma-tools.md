# Feature 08: Gemma Tools

## Context

From the main spec (sorted-imagining-journal.md), Sections 3.5 and 4.2-4.3.

Register tool declarations with Gemma so it can search MemPalace and retrieve page content. Implement the `/summarize` command flow.

## Requirements

### Gemma Tools (registered via tool binding)

| Tool | Description | Bridge Endpoint |
|------|-------------|-----------------|
| `search_memory` | Search MemPalace for relevant documents | `GET /api/search` |
| `get_page_content` | Retrieve stored content for a specific URL | `GET /api/drawers/by-url` |
| `list_recent` | List recently captured pages | `GET /api/search` (time-sorted) |

### System Prompt for Gemma

```
You are a memory assistant running locally in the browser.
You help the user recall and understand web pages they have saved.
Use the search_memory tool to find relevant saved pages before answering.
Use get_page_content to retrieve full page content when needed.
Keep answers concise. Cite sources by title and URL.
When the user says /summarize, summarize the current page's stored content.
```

### Tool Execution Flow

Tools called by Gemma are executed by the offscreen document or background worker against the FastAPI bridge:

```
Gemma decides to call search_memory("machine learning")
  → offscreen.js intercepts tool call
  → Sends message to background worker
  → Background worker calls GET /api/search?query=machine+learning
  → Results returned to offscreen.js
  → Gemma receives tool results
  → Gemma generates response using results
```

### /summarize Command Flow

```
1. User types "/summarize" in chat

2. Background worker:
   a. Gets current tab URL
   b. Calls GET /api/drawers/by-url?url={current_url}
   c. If found: passes stored content to Gemma with prompt
      "Summarize this document concisely: {content}"
   d. If not found: "This page hasn't been captured yet. Press Ctrl+Shift+M to save it first."

3. Gemma generates summary → streamed to chat overlay
```

### /search Command

```
1. User types "/search machine learning"
2. Background worker calls GET /api/search?query=machine+learning
3. Results displayed in chat as a list of matching pages with titles and URLs
```

### /status Command

```
1. User types "/status"
2. Background worker calls GET /api/status
3. Results shown in chat: drawer count, wing list, etc.
```

## Verification

1. Capture a page, open chat
2. Type `/summarize` → should get a summary of the captured page
3. Type `/search <topic>` → should list matching saved pages
4. Type `/status` → should show MemPalace stats
5. Type a free-form question → Gemma should search and answer
6. Verify tool calls hit the bridge (check Docker logs)
