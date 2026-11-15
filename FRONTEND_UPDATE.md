# ResearchLens — Frontend Update Specification

**Status:** Approved feature direction; implementation pending  
**Purpose:** Turn the current working research graph into a conversational, multi-session research workspace.  
**Target:** React + Vite + Cytoscape.js frontend, with targeted FastAPI/PostgreSQL backend extensions where genuinely necessary.

## 1. Context and goals

The current ResearchLens app has a working left research sidebar, top bar, live evidence graph, search, relationship filters, legend, graph controls, and backend integration. Preserve this foundation and the BioOKF-inspired visual language: near-white surfaces, subdued ink, hairline borders, rounded cards, system typography, and restrained blue accents.

The next iteration should make the graph **less visually crowded**, explain its relationships **on demand**, and let users **upload papers and converse with their own research**. It should also allow users to create, name, reopen, and switch among independent research sessions, similar to the conversation model in ChatGPT.

**Product principle:** The graph and chat are two views of the same grounded research workspace. A graph selection should be usable as chat context, and chat references should navigate back to the graph.

## 2. Feature requirements

### F1 — Collapsible right-hand research assistant panel

**Goal:** A user opens a tab or button on the right edge of the interface to reveal a functional research chatbot and upload controls.

- Add a persistent, clearly discoverable **right-side panel toggle**. The panel should open and close without losing the current graph, chat state, selections, or upload progress.
- Use visual styling consistent with the left sidebar: matching surfaces, section headings, borders, spacing, button shapes, and typography. Default desktop width may be approximately 380–440 px; make it responsive.
- The panel contains:
  - A conversation/message history with distinct user and assistant messages.
  - A text input, send action, loading/streaming or pending state, and visible error/retry behavior.
  - A visible **Upload PDF** control (drag/drop is optional) that uses the existing paper-ingestion workflow.
  - Context indicator(s) when a node or edge is attached to the user's question.
  - Clickable citations and graph references in assistant responses.
- **No fake chatbot UI.** The input must be connected to a real backend operation and yield answers grounded in the currently selected research session, its uploaded papers, claims, relationships, and source citations. When no chat API is available, visibly disable sending rather than pretending to answer.
- Support natural-language questions such as: “Why do Nodes 4 and 7 disagree?”, “Which papers support Node 5?”, and “What changed after I uploaded the latest paper?”
- Source-backed answers must distinguish **verbatim, verifiable paper excerpts** from model-generated interpretations; cite identifiable papers and, where available, validated sentence/page references. If the evidence is insufficient, say so rather than inventing support.
- Consider an optional compact mode or overlay on narrow screens so the graph remains usable.

**PDF uploads in the panel:**

- Permit a user to select one or more research PDFs; clearly communicate accepted formats, upload/analysis state, successes, and failures.
- Preserve the backend's existing two ingestion intents: **build/add to library** (`mode=library`) and **evaluate a new paper** (`mode=evaluate`). The UI must make the distinction understandable; never accidentally evaluate when the user intended only to add a foundational paper.
- After a successful upload, refresh workspace, graph, counts, and impact data. Keep the user in the current research session and maintain chat context.
- Gemini analysis can take 20–80+ seconds. Use the existing extended request timeout (approximately 180 seconds). If the browser times out, warn that server processing may still be underway; **refresh server state before suggesting re-upload** to avoid duplicate or destructive operations.
- Disable real uploads while viewing fictional sample fixtures or an offline snapshot, with a clear explanation and a retry connection action.

**Acceptance criteria:** Users can open/close the panel, send a grounded question, receive cited answers, upload PDFs into the intended workflow, see analysis results reflected in the graph, and navigate from chat references to graph elements.

### F2 — Clickable evidence-edge explanation popup / inspector

**Goal:** Clicking a scientific evidence connection gives a detailed explanation of what the connection means and why it matters.

- Clicking an **evidence edge** opens a contextual popup, floating inspector, or compact details card near the selection (or in a reliable fixed position if positioning would obstruct the graph). Provide a clear close action.
- Show:
  - Relationship type: supports, genuine contradiction, apparent contradiction, unresolved, or not comparable.
  - Human-readable rationale and **relevance to the central research question**.
  - Both connected claims, including their full text and source paper titles.
  - The original source sentences/quotes and verifiable citation identifiers where the backend provides them.
  - Differences in study conditions (e.g., population, sample size, method, duration, outcome measurement), when available.
  - Clear indication for **insufficient evidence** or an unverified citation; do not present these as confidently assessed relationships.
  - **Ask about this connection** action that opens the right-side chatbot with this edge in context.
- Keep structural links (question → paper, paper → claim) visually separate; these are provenance links, not scientific evidence assessments. Their behavior may differ from clickable scientific edges.
- Edge hit targets should be usable even when strokes are thin. Preserve the thin BioOKF-inspired visual style while improving pointer interaction/accessibility.
- This must be a real data inspector, not generated filler text. Prefer existing `GET /api/relationships/{id}` and data already in `GET /api/graph` before adding APIs.

**Acceptance criteria:** The user can select an evidence connection, understand the supporting/contradictory relationship and its research relevance, inspect source-backed evidence, close the popup, and ask a follow-up question in chat.

### F3 — Compact, numbered graph-node labels

**Goal:** Stop showing long research excerpts and paper titles across the canvas by default.

- Replace default full node labels with concise **Node 1**, **Node 2**, **Node 3**, etc., for paper and claim nodes (and other non-central nodes if appropriate).
- The question node is the exception: its default visible label must read **Central Question**, not the full research question and not a node number.
- Preserve the existing node-type encoding (question, original paper, newly evaluated paper, claim, new claim, discovery lead), colors, selection states, and relationship styles.
- **Show full text only in an intentional context:** when a node is clicked/selected, when the user explicitly asks the chatbot to explain or showcase it, or when an edge is selected and its endpoint text is relevant. Use a popup/inspector or chat-focused card rather than flooding the canvas with long labels.
- Selecting the Central Question should show the complete question and, where permitted, an edit action.
- Graph search should continue searching **full underlying titles and claim text**, even though the canvas shows short labels.
- Keep node references stable and unambiguous **within each research session**, including after re-layout, search, and subsequent uploads. Do not renumber existing nodes unpredictably when a new paper is added. Store a stable presentation-number mapping or derive numbers from a durable per-session insertion order, not ephemeral graph sort order. Keep the backend's canonical IDs unchanged and map display numbers to those IDs.
- Where relevant, show a tiny unobtrusive type cue in the inspector or tooltip so users can tell whether Node 7 is a paper or a claim.

**Acceptance criteria:** Default graph labels are compact and readable; the center displays “Central Question”; clicking or chat-referencing a node reveals its actual content and provenance; Node N references remain stable within a session.

### F4 — Research question authored through natural-language prompts

**Goal:** Users have a direct say in defining the question represented by the central graph node.

- When starting a new research session, allow the user to type an objective or research question in plain language in either the initial setup flow or the right-side chatbot.
- If the input is an exploratory prompt (e.g., “I want to study how tutoring affects learning”), the assistant may propose a clear central research question. **Show the proposal and let the user confirm or edit it before committing.**
- Also allow direct, explicit question entry without needing the assistant to rewrite it.
- Save the confirmed question as the session's active `question` and display it in full in the left research-question card and the Central Question inspector. The graph node itself remains labeled “Central Question.”
- Use the existing `PUT /api/workspace` contract for the current single-workspace stage when appropriate. Respect its explicit-reset/409 behavior: **never silently destroy existing analysis** when the question changes.
- After multi-session support is introduced, question changes must operate on the selected session alone. If changing the question would invalidate existing analysis, ask the user to create a new session or explicitly confirm a reset/reanalysis.

**Acceptance criteria:** A user can define, review, edit, and confirm the central question; the graph and chat use that question; existing analysis is never erased without explicit action.

### F5 — Renameable chat/session title in the header

**Goal:** Users can give a memorable name to a research conversation independent of the scientific question.

- Show the current **session title** in the top bar (e.g., “AI Tutors Literature Review”), with a clear rename affordance such as click-to-edit or an edit icon.
- Support save, cancel, validation, and persistence of a renamed title.
- Keep two distinct concepts:
  - **Session title:** short organizational label, freely renameable.
  - **Central question:** the scientific question grounding research extraction and graph analysis.
- Renaming the session must **not** modify the central question, change citations, or trigger ingestion/reanalysis.
- Retain clear access to the full central question through the left sidebar and the Central Question selection.

**Acceptance criteria:** Renaming a session is fast, persists after reload, and has no side effects on the graph or research question.

### F6 — Multiple independent research chats/sessions (ChatGPT-like)

**Goal:** Create separate persistent workspaces for different research projects, each with its own chat and documents.

- Provide a **New research** action and a **session switcher/list** (left sidebar, header menu, or another compact discoverable control).
- Each session must independently persist:
  - Session ID and renameable title.
  - Confirmed central research question.
  - Uploaded PDF/library records and evaluation history.
  - Extracted claims, source citations, and evidence relationships.
  - Graph state/reference-number mapping and latest impact analysis.
  - Chat messages and message-to-node/edge references.
- Switching sessions updates left sidebar, graph, right chatbot, upload destination, and metadata together; prevent information from one session leaking into another.
- Add create, list, open/switch, and rename actions. Delete/archive is optional for the initial release, but if present must require confirmation.
- A session should remain available after page reload and later return. An in-memory-only array of chats is **not sufficient**.
- Do not use `PUT /api/workspace` with `reset=true` as a substitute for new sessions; that would delete existing analysis rather than isolate it.
- Preserve the existing single-workspace behavior until a migration plan and session-aware backend endpoints are in place.

**Backend requirement:** The current API operates on one active workspace and does not yet provide persistent multi-chat management or grounded conversational replies. Implementing F6 and F1's true chat behavior requires targeted backend/database extensions (e.g., session-scoped tables and routes). Design these before wiring the frontend. Migrate or preserve the existing live research workspace rather than dropping its four ingested papers and graph data.

**Acceptance criteria:** A user can create two separate sessions with different questions and documents, switch between them, rename either, reload the page, and recover the correct chat and graph data for each without cross-session contamination.

## 3. Cross-feature interaction contracts

1. **Graph → chat:** Selecting a paper, claim, relationship, or Central Question can pass its canonical ID and session ID to the assistant as explicit context. A visible “Ask about this” control is preferable to silently changing the user's active conversation.
2. **Chat → graph:** Assistant responses can reference “Node 7” or an evidence edge. Clicking the reference focuses/highlights the correct item and opens its details. Resolve Node N through the stable session-local ID mapping.
3. **Upload → graph/chat:** Successful ingestion updates the paper list and graph and adds an understandable system status to the relevant conversation. If evaluating a paper, make the latest impact accessible without pretending that a relationship judgment establishes scientific truth.
4. **Question → analysis:** Only a confirmed central question is sent for analysis. Changing it when claims already exist must use an explicit conflict/resolution flow.
5. **Session switching:** Pending requests and state updates must be associated with the session that initiated them; a late response from Session A must never overwrite the UI for Session B.
6. **Research integrity:** Keep verified source sentences separate from extracted claims and model-assessed relationships. Label insufficient evidence, unavailable checks, sample fixtures, and offline snapshots accurately.

## 4. Layout guidance

- **Left:** existing research workspace / session navigation and paper summary. Keep the BioOKF-inspired 330px expanded treatment where appropriate.
- **Center:** Cytoscape graph with short labels, center node labeled “Central Question”, search/filter/zoom, concise legend, and an on-demand details popup.
- **Right:** collapsible research assistant with message history, input, PDF upload controls, and currently selected evidence context. Match left-sidebar design tokens and spacing.
- **Top bar:** editable session title, session switcher/new action, existing search/filter and citation-integrity indicators. Preserve access to the full central question elsewhere.
- Responsive behavior: on smaller screens, allow one side panel at a time or an overlay/drawer rather than shrinking the graph into an unusable strip. Preserve accessible toggles and keyboard navigation.

## 5. Implementation stages and review checkpoints

### Stage A — Graph readability and evidence inspection (quickest user-visible value)

- Replace canvas labels with stable Node N labels and “Central Question”.
- Add node/edge details cards with full text, evidence, rationale, and citations.
- Implement graph focus/navigation hooks to support future chat references.
- **Checkpoint:** user tests graph clicking, edge explanations, and label readability on the current four-paper live dataset.

### Stage B — Right-side chat and uploads

- Build the styled chat panel and session-scoped messaging contract.
- Add evidence-grounded chat service and citation/navigation responses (backend additions are required).
- Integrate library/evaluation PDF uploads, timeouts, refresh, failures, and offline restrictions.
- **Checkpoint:** user can ask about a selected edge, see a grounded answer, upload a paper, and inspect the updated graph.

### Stage C — Prompt-driven question setup

- Add new-research initialization and assistant-proposed question with explicit confirm/edit.
- Wire safe question updates and conflict handling.
- **Checkpoint:** user creates an analysis from a natural-language objective without silently replacing prior work.

### Stage D — Persistent multi-session UX

- Add session-aware data model and API routes; preserve/migrate existing research data.
- Add new, list, switch, rename, and durable chat history flows.
- Guard against cross-session state leakage and stale async responses.
- **Checkpoint:** two different research sessions retain separate graphs, documents, questions, and conversation history across reloads.

**Dependency note:** Backend session design should be planned **before** finalizing the chat persistence contract, even if the visible session-switcher UI is delivered later. The stages are UX milestones, not permission to create incompatible temporary schemas.

## 6. Quality, testing, and non-regressions

- Preserve the current functional backend, local PostgreSQL/pgvector integration, real Gemini extraction/embeddings, evidence graph schema, and citation-integrity checks wherever possible. Do not modify the `.env` or expose secrets.
- Retain all existing frontend tests and production-build success; add focused tests for label-to-ID mapping, edge selection/inspector data, chat-to-graph references, question confirmation, PDF upload mode, session switching/isolation, and persistence.
- Continue using clearly marked fictional fixture data for offline demos. Never silently blend sample and live research output.
- Avoid hallucinated evidence: grounded replies should acknowledge missing citations, insufficiency, or unavailable sources.
- Confirm existing data is preserved across schema migrations and that switching sessions cannot leak one session's papers, graph, or messages into another.
- Test desktop and narrow layouts, keyboard operation, long titles, sparse/large graphs, timeouts, and partial API failure.
- Keep the UI responsive during long model calls; do not invent progress percentages if real stage progress is unavailable.

## 7. Scope guardrails

- All six product changes above are **approved as requirements**; this is not evidence that the required backend features already exist.
- Favor incremental improvements to existing components and APIs over rewriting the working application.
- Keep the existing graph visual language and scientific relationship classifications.
- Do not confuse *verified citation provenance* with proof of scientific correctness.
- Do not implement an inert demonstration chatbot: the conversation must have a real, grounded response path when enabled.
- Request a quick visual/functional checkpoint at the end of each major stage before proceeding to the next.

## 8. Suggested immediate instruction to Claude Code

> Read `FRONTEND_UPDATE.md` and first produce a concise implementation plan mapping each feature to existing React components, FastAPI endpoints, database tables, and tests. Identify which updates are frontend-only versus which need new backend APIs. Preserve the current working research graph and live four-paper dataset. Then implement Stage A (compact stable node labels, Central Question, and source-backed edge/node popup), run tests and build, and stop for my review before building chat or multi-session persistence. Do not create a fake chatbot or use destructive workspace resets to simulate sessions.
