# Feature Spec: Knowledge Graph Integration

## Problem Statement

MemPalace includes a knowledge graph layer (SQLite + `mempalace_kg_add` MCP tool) that can store named entities and relationships extracted from documents. MemoryMark does not currently use this layer. Users who capture many pages on related topics cannot discover connections between entities (people, organizations, concepts, technologies) across their saves.

## Core Functionality

After a page is captured, Gemma extracts named entities and key relationships from the Markdown content and stores them in MemPalace's knowledge graph via the MCP KG tools. Gemma's free-form chat can then answer cross-document relationship queries ("What sources mention both X and Y?") by searching both the vector store and the KG.

## Functional Requirements

1. After `POST /api/drawers` succeeds, trigger an async KG extraction job in the background
2. Extraction: send the stored Markdown to Gemma with a prompt requesting entities (person, org, concept, technology, place) and relationships (works at, authored, references, implements, related to)
3. Each extracted entity is stored via `mempalace_kg_add` with the drawer ID as provenance
4. Each relationship is stored as a triple: `(entity_a, relation, entity_b, source_drawer_id)`
5. KG extraction must not block the capture flow — runs after the overlay shows "Saved"
6. If Gemma is unavailable, KG extraction is skipped silently; the capture still succeeds
7. Gemma's chat system prompt is updated to include KG search instructions and a new tool `query_knowledge_graph` that calls the bridge KG query endpoint
8. Users can ask "What do I know about {entity}?" and receive answers from both vector search and KG

## Critical Constraints

- KG extraction failure must not cause the capture to fail or show an error
- Extraction must run without user interaction (fully background)
- KG data must be attributed to source drawers so deletion of a drawer also removes its KG entries
- Gemma extraction accuracy need not be perfect — false positives in the KG are acceptable; missing entities are not surfaced as errors

## Integration Points

- Bridge: new endpoint `POST /api/kg/triples` wrapping MemPalace KG tools (or call via `/mcp`)
- `offscreen.js` — Gemma entity extraction after capture confirmation
- `background.js` — trigger KG extraction job asynchronously after drawer creation
- Gemma system prompt — add `query_knowledge_graph` tool declaration
- `GET /api/drawers/{id}` — used to look up provenance when a drawer is deleted

## Out of Scope

- Manual entity annotation by the user
- Displaying the knowledge graph visually (graph UI)
- Entity disambiguation across capture sessions
- Real-time KG updates while the user is reading a page
