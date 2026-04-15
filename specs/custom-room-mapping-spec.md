# Feature Spec: Custom Room Mapping

## Problem Statement

Users who capture from specific domains (GitHub repositories, arXiv papers, Stack Overflow answers) want those pages to land in dedicated rooms without manually selecting a wing/room each time. Rule-based auto-assignment by URL pattern would make large archives self-organizing.

## Core Functionality

Users define URL pattern → wing/room mappings in extension settings. When a page is captured, the extension matches the URL against configured patterns (in order) and uses the first match. If no pattern matches, falls back to the default or the last-selected wing/room.

## Functional Requirements

1. Settings panel exposes a list of URL pattern rules: each rule has a URL glob pattern, a target wing, and a target room
2. Patterns are evaluated in order; first match wins
3. Pattern syntax: glob-style with `*` wildcard (e.g., `github.com/*`, `*.arxiv.org/*`)
4. Rules are persisted in `chrome.storage.local` under a `roomMappings` key
5. The overlay shows which rule matched (e.g., "Matched rule: github.com/* → Code / Repos")
6. If configurable wing selection (separate feature) is also active, the matched rule pre-fills the picker
7. At least 20 rules must be supported without performance degradation
8. Rules can be imported/exported as JSON

## Critical Constraints

- Pattern matching must complete in <5ms to avoid capture latency
- Invalid patterns must be caught at settings-entry time with a clear error message
- Rules must survive extension updates (stored in `chrome.storage.local`, not extension bundle)

## Integration Points

- `chrome.storage.local` — `roomMappings: [{pattern, wing, room}]`
- `POST /api/drawers` — `wing` and `room` fields set by matched rule
- Extension settings UI — new "Room Mappings" section

## Out of Scope

- Regex pattern syntax (glob only in v1)
- Server-side or bridge-enforced rules
- Automatic rule suggestions based on browsing history
