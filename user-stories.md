# User Stories

## Personas

**Researcher** — A knowledge worker, academic, or journalist conducting ongoing research across many sources. Captures 10–50 pages per week, frequently revisits saves, and uses semantic search to cross-reference material. Works in privacy-sensitive contexts and dislikes cloud dependencies.

**Casual Reader** — A general web user who saves interesting articles with minimal friction. Captures 2–5 pages per week, rarely revisits saves, and prefers simple visual feedback over advanced features.

**Agent Developer** — An engineer building autonomous agent workflows (Claude Code, Cursor, Codex, Zed) who needs the user's captured web knowledge available as a tool-callable memory store.

---

## Epics

### Epic: Capture

One-keystroke capture of any web page as clean, structured Markdown into local storage.

#### [Implemented] Story: Save a page with one keystroke
**As a** researcher, **I want** to save a web page with one keystroke, **so that** I can build a personal archive without interrupting my reading flow.

**Acceptance Criteria**:
- [ ] `Ctrl+Shift+M` (Mac: `Cmd+Shift+M`) triggers capture on any page
- [ ] Overlay appears immediately showing "Saving…" state
- [ ] Overlay transitions to "Saved" with the page title and wing
- [ ] Overlay auto-dismisses after 4 seconds

#### [Implemented] Story: Detect duplicate captures
**As a** researcher, **I want** to be told when I try to save a page I've already captured, **so that** I don't clutter my archive with duplicates.

**Acceptance Criteria**:
- [ ] Re-capturing a URL shows "Already in MemPalace" with title
- [ ] No new drawer is created; storage is unchanged
- [ ] Badge shows checkmark (`✓`) on tabs whose URL has been captured

#### [Implemented] Story: Preserve page structure in Markdown
**As a** researcher, **I want** captured pages to preserve headings, lists, tables, and code blocks, **so that** the saved content is readable and structured.

**Acceptance Criteria**:
- [ ] Readability strips navigation, ads, and sidebars
- [ ] Turndown converts remaining HTML to clean Markdown with ATX headings and fenced code blocks
- [ ] GFM tables and strikethrough are preserved

#### [Implemented] Story: See real-time capture feedback
**As a** user, **I want** to see clear status feedback during capture, **so that** I know whether the save succeeded, failed, or was a duplicate.

**Acceptance Criteria**:
- [ ] Overlay shows distinct states: Saving, Saved, Already exists, Error, Bridge unreachable
- [ ] Error state includes a retry button; content held in memory until retry or dismiss
- [ ] Bridge-unreachable state shows the `docker compose up -d` command

#### [Implemented] Story: Capture on any page including restricted URLs
**As a** user, **I want** MemoryMark to work even on restricted pages (chrome://, about:*), **so that** I always have access to my memory.

**Acceptance Criteria**:
- [ ] On restricted pages, icon click opens a standalone chat tab
- [ ] Capture shortcut works on all unrestricted pages

---

### Epic: Retrieval

Find saved content through semantic search, URL lookup, and status queries.

#### [Implemented] Story: Search my saves by meaning, not just keywords
**As a** researcher, **I want** to search across my saved pages using natural language, **so that** I can find relevant content without remembering exact titles.

**Acceptance Criteria**:
- [ ] `/search <query>` returns semantically relevant matches with titles and URLs
- [ ] Search works in both Chrome and Firefox

#### [Implemented] Story: Know at a glance which pages I've saved
**As a** researcher, **I want** the extension badge to show when the current tab has already been captured, **so that** I can spot saved pages while browsing.

**Acceptance Criteria**:
- [ ] Badge shows `✓` on tabs matching a stored `source_url`
- [ ] Badge updates on tab switch and navigation

#### [Implemented] Story: Check my archive statistics
**As a** user, **I want** to see how many pages I've saved and which categories exist, **so that** I understand the scope of my archive.

**Acceptance Criteria**:
- [ ] `/status` command shows total drawer count and wing names
- [ ] Response is near-instant (no LLM generation required)

---

### Epic: Chat & Summarization

Conversational access to saved knowledge via an in-browser Gemma 4 LLM.

#### [Implemented] Story: Summarize a saved page without re-reading it
**As a** researcher, **I want** to get a concise summary of a page I've already captured, **so that** I can quickly recall its key points.

**Acceptance Criteria**:
- [ ] `/summarize` on a captured page generates a summary from the stored Markdown
- [ ] If the current page hasn't been captured, user is told to save it first

#### [Implemented] Story: Ask free-form questions about my saves
**As a** researcher, **I want** to ask natural language questions and have the LLM search my archive for answers, **so that** I can have a conversation with my research.

**Acceptance Criteria**:
- [ ] Free-form questions trigger `search_memory` tool call before Gemma responds
- [ ] Response cites source titles and URLs
- [ ] Tool call indicators shown inline ("Searching memory…")
- [ ] Requires Chrome with WebGPU + shader-f16

#### [Implemented] Story: See streaming responses with progress indicators
**As a** user, **I want** to see the LLM response stream in real time with token speed shown, **so that** I know the model is working and can estimate completion time.

**Acceptance Criteria**:
- [ ] Responses stream token-by-token into the chat panel
- [ ] Footer shows `<tokens> tokens · <elapsed>s · <tok/s> tok/s` while generating

#### [Implemented] Story: Chat without leaving the current page
**As a** researcher, **I want** the chat panel to appear as an overlay on the current page, **so that** I don't lose my reading context.

**Acceptance Criteria**:
- [ ] Chat opens as a slide-in panel (≈400×500px) on the right edge
- [ ] Opened via "Open Chat" button or second shortcut press
- [ ] Panel does not auto-dismiss while open

---

### Epic: Privacy & Local-First

Keep all data on the user's machine with no cloud dependencies.

#### [Implemented] Story: Store all captures locally with no cloud
**As a** privacy-conscious user, **I want** my captured pages to stay on my machine, **so that** my research is never shared with third parties.

**Acceptance Criteria**:
- [ ] Extension only communicates with `http://localhost:21517`
- [ ] MemPalace data lives in `./data/mempalace/` (Docker volume)
- [ ] No external API calls during normal operation

#### [Implemented] Story: Run the LLM entirely on-device
**As a** privacy-conscious user, **I want** Gemma to run locally in my browser, **so that** my questions and saved content are never sent to a cloud API.

**Acceptance Criteria**:
- [ ] Gemma 4 E2B runs via WebGPU in a Chrome offscreen document
- [ ] Model is downloaded once and cached in OPFS; subsequent launches load from cache
- [ ] No inference requests leave localhost

#### [Implemented] Story: Wipe my archive completely
**As a** user, **I want** to remove all my saved data cleanly, **so that** I can reset or delete sensitive research.

**Acceptance Criteria**:
- [ ] Stopping the bridge and deleting `./data/` removes all captured pages
- [ ] Clearing extension site storage removes OPFS-cached model and settings

---

### Epic: Configuration & Setup

Install and configure MemoryMark for different environments.

#### [Implemented] Story: Configure a custom bridge URL
**As a** power user, **I want** to point the extension at a bridge on a different host or port, **so that** I can use MemoryMark in custom network setups.

**Acceptance Criteria**:
- [ ] `bridgeUrl` in `chrome.storage.local` overrides the default `http://localhost:21517`

#### [Implemented] Story: Disable the Gemma download to save disk space
**As a** user, **I want** to opt out of the 2GB model download, **so that** I can use capture and search without consuming disk or bandwidth.

**Acceptance Criteria**:
- [ ] Setting `gemmaEnabled = false` prevents model download
- [ ] Capture and slash commands still work with Gemma disabled

#### [Implemented] Story: Adjust the overlay auto-dismiss timer
**As a** user, **I want** to control how long the overlay stays visible, **so that** I have enough time to read the feedback.

**Acceptance Criteria**:
- [ ] `autoClose` controls dismiss delay in milliseconds (default 4000)
- [ ] Overlay stays open while hovered regardless of setting

---

### Epic: Cross-Browser Support

Capture and search work in Chrome and Firefox; free-form chat requires Chrome.

#### [Implemented] Story: Use capture in Firefox
**As a** Firefox user, **I want** to capture pages and run slash commands just like in Chrome, **so that** I'm not forced to switch browsers.

**Acceptance Criteria**:
- [ ] Capture, duplicate detection, overlay, `/search`, `/status`, and `/summarize` work in Firefox 109+
- [ ] Extension loads from `manifest.firefox.json`

#### [Implemented] Story: Degrade gracefully when WebGPU is unavailable
**As a** user on a machine without WebGPU, **I want** MemoryMark to still capture and search, **so that** I don't lose core functionality.

**Acceptance Criteria**:
- [ ] Chat shows "Gemma requires WebGPU" if `shader-f16` is unavailable
- [ ] Capture and slash commands function normally without WebGPU

---

### Epic: Agent Integration

Expose the palace to AI agent harnesses over MCP.

#### [Implemented] Story: Access my saved pages from Claude Code
**As an** agent developer, **I want** Claude Code to search and retrieve my captured pages via MCP, **so that** agents can use my web research as grounding context.

**Acceptance Criteria**:
- [ ] `POST /mcp` exposes MemPalace tools over Streamable HTTP
- [ ] `claude mcp add memorymark http://localhost:21517/mcp --transport http` connects successfully
- [ ] Agent can call `mempalace_search`, `mempalace_status`, and other tools

#### [Implemented] Story: Share one palace between browser and agents
**As an** agent developer, **I want** browser captures and agent queries to use the same MemPalace, **so that** agents see everything the user has saved.

**Acceptance Criteria**:
- [ ] A page captured via the extension is immediately visible to MCP tool calls
- [ ] A drawer added via MCP is visible to the extension's `/search` command

---

### Epic: Planned Features

#### [Planned] Story: Choose which category to save a page to
**As a** researcher, **I want** to pick the wing and room before saving, **so that** my archive stays organized by domain.
See [`specs/configurable-wing-selection-spec.md`](specs/configurable-wing-selection-spec.md)

#### [Planned] Story: Auto-route pages to categories by URL pattern
**As a** power user, **I want** URL patterns to automatically determine the room (e.g., `github.com/*` → "Code"), **so that** captures self-organize without manual input.
See [`specs/custom-room-mapping-spec.md`](specs/custom-room-mapping-spec.md)

#### [Planned] Story: Let Gemma suggest the right category
**As a** user, **I want** Gemma to propose a wing/room based on page content, **so that** categorization is intelligent with minimal effort.
See [`specs/gemma-auto-categorization-spec.md`](specs/gemma-auto-categorization-spec.md)

#### [Planned] Story: Capture linked pages without visiting them
**As a** researcher, **I want** to right-click a link and capture the linked page in the background, **so that** I can batch-save related content without interrupting my flow.
See [`specs/bulk-capture-spec.md`](specs/bulk-capture-spec.md)

#### [Planned] Story: See my recent captures in the extension popup
**As a** user, **I want** the extension icon popup to show my last 10 captures with timestamps, **so that** I can quickly review what I've saved today.
See [`specs/capture-history-spec.md`](specs/capture-history-spec.md)

#### [Planned] Story: Extract entities and relationships from captures
**As a** researcher, **I want** MemoryMark to build a knowledge graph from my saved pages, **so that** I can discover connections between entities across captures.
See [`specs/knowledge-graph-integration-spec.md`](specs/knowledge-graph-integration-spec.md)
