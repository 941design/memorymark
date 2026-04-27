// MemoryMark — Offscreen Document
// Hosts Gemma 4 E2B LLM via MediaPipe Tasks GenAI + WebGPU
// Receives chat messages from background worker, streams responses back

import { FilesetResolver, LlmInference } from './lib/mediapipe/tasks-genai.js';

// ---------------------------------------------------------------------------
// Configuration
// ---------------------------------------------------------------------------

const MODEL_FILENAME = 'gemma-4-E2B-it-web.task';
const MODEL_URL =
  'https://huggingface.co/litert-community/gemma-4-E2B-it-litert-lm/resolve/main/gemma-4-E2B-it-web.task';

// ~2 GB model; anything under 1.5 GB is a partial download.
const MIN_MODEL_SIZE = 1_500_000_000;

const LLM_OPTIONS = {
  maxTokens: 8192,
  temperature: 0.2,
  topK: 40,
  randomSeed: 101,
};

// Gemma 4 prompt tags (different from Gemma 2/3)
const TURN_OPEN = '<|turn>';
const TURN_CLOSE = '<turn|>';

// System prompt — injected as a system turn.  Tool declarations use the
// Gemma 4 native <|tool> tag format so the model can emit <|tool_call> tags
// on its own when the pre-fetched context is insufficient.
const SYSTEM_PROMPT =
  'You are a memory assistant running locally in the browser. ' +
  'You help the user recall and understand web pages they have saved to MemPalace. ' +
  'Answer based ONLY on the context provided. If no relevant pages are found, say so. ' +
  'Keep answers concise. Cite sources by title.';

const TOOL_DECLARATIONS =
  '<|tool>declaration:search_memory{"description":"Search MemPalace for relevant documents","parameters":{"type":"object","properties":{"query":{"type":"string"}},"required":["query"]}}<tool|>\n' +
  '<|tool>declaration:get_page_content{"description":"Retrieve full stored content for a URL","parameters":{"type":"object","properties":{"url":{"type":"string"}},"required":["url"]}}<tool|>\n' +
  'Use declared tools when they improve accuracy. Emit <|tool_call>call:name{"param":"value"}<tool_call|> for tool requests.';

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

let llmInference = null;
let modelLoading = false;
let modelReady = false;

// ---------------------------------------------------------------------------
// WebGPU preflight
// ---------------------------------------------------------------------------

async function checkWebGPU() {
  if (typeof navigator === 'undefined' || !navigator.gpu) return false;
  try {
    const adapter = await navigator.gpu.requestAdapter();
    if (!adapter) return false;
    return adapter.features.has('shader-f16');
  } catch {
    return false;
  }
}

// ---------------------------------------------------------------------------
// OPFS model caching
// ---------------------------------------------------------------------------

async function getModelFromOPFS() {
  try {
    const root = await navigator.storage.getDirectory();
    const handle = await root.getFileHandle(MODEL_FILENAME);
    const file = await handle.getFile();
    if (file.size >= MIN_MODEL_SIZE) {
      return file;
    }
    await root.removeEntry(MODEL_FILENAME);
  } catch {
    // Not cached yet.
  }
  return null;
}

// Clear stale cached models from previous versions.
async function clearOldModels() {
  try {
    const root = await navigator.storage.getDirectory();
    for await (const [name] of root.entries()) {
      if (name !== MODEL_FILENAME && (name.endsWith('.task') || name.endsWith('.bin'))) {
        console.log('[MemoryMark] Removing old cached model:', name);
        await root.removeEntry(name);
      }
    }
  } catch { /* ignore */ }
}

// ---------------------------------------------------------------------------
// Model download — streams response body directly into an OPFS writable so
// the full ~2 GB model never lives in JS heap at once.
// ---------------------------------------------------------------------------

async function downloadModelToOPFS(progressCallback) {
  const response = await fetch(MODEL_URL);
  if (!response.ok) {
    throw new Error(`Model download failed: HTTP ${response.status}`);
  }

  const total = parseInt(response.headers.get('content-length') || '0', 10);
  const root = await navigator.storage.getDirectory();

  // If a prior partial download is sitting in OPFS, remove it before we start
  // — getModelFromOPFS already rejected it via the MIN_MODEL_SIZE check.
  try { await root.removeEntry(MODEL_FILENAME); } catch { /* nothing there */ }

  const handle = await root.getFileHandle(MODEL_FILENAME, { create: true });
  const writable = await handle.createWritable();

  if (!response.body) {
    // No streaming body (very unlikely in modern Chrome). Fall back to
    // arrayBuffer — still a single 2 GB allocation but at least matches
    // the previous fallback behavior.
    try {
      const buffer = await response.arrayBuffer();
      await writable.write(new Uint8Array(buffer));
      await writable.close();
    } catch (err) {
      try { await writable.abort(); } catch { /* ignore */ }
      try { await root.removeEntry(MODEL_FILENAME); } catch { /* ignore */ }
      throw err;
    }
    return;
  }

  // Dual-stream: progress tracking + OPFS write run concurrently from the
  // same tee'd body, so we keep live progress updates without buffering.
  const [progressStream, modelStream] = response.body.tee();

  const progressPromise = (async () => {
    const reader = progressStream.getReader();
    let received = 0;
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      if (value) received += value.byteLength;
      if (progressCallback) progressCallback(received, total);
    }
  })();

  const modelReader = modelStream.getReader();
  try {
    while (true) {
      const { done, value } = await modelReader.read();
      if (done) break;
      if (value) await writable.write(value);
    }
    await writable.close();
    await progressPromise;
  } catch (err) {
    try { await writable.abort(); } catch { /* ignore */ }
    try { await root.removeEntry(MODEL_FILENAME); } catch { /* ignore */ }
    await progressPromise.catch(() => {});
    throw err;
  }

  const file = await handle.getFile();
  if (file.size < MIN_MODEL_SIZE) {
    await root.removeEntry(MODEL_FILENAME);
    throw new Error(
      `Download incomplete: got ${file.size} bytes, expected >= ${MIN_MODEL_SIZE}`
    );
  }

  console.log('[MemoryMark] Model streamed to OPFS');
}

// ---------------------------------------------------------------------------
// MediaPipe initialization
// ---------------------------------------------------------------------------

async function initializeFromFile(modelFile) {
  const genaiFileset = await FilesetResolver.forGenAiTasks(
    chrome.runtime.getURL('lib/mediapipe/')
  );

  const blobUrl = URL.createObjectURL(modelFile);
  try {
    llmInference = await LlmInference.createFromOptions(genaiFileset, {
      baseOptions: { modelAssetPath: blobUrl },
      maxTokens: LLM_OPTIONS.maxTokens,
      temperature: LLM_OPTIONS.temperature,
      topK: LLM_OPTIONS.topK,
      randomSeed: LLM_OPTIONS.randomSeed,
    });
  } finally {
    URL.revokeObjectURL(blobUrl);
  }

  modelReady = true;
}

// ---------------------------------------------------------------------------
// Full model initialization flow
// ---------------------------------------------------------------------------

async function initModel() {
  if (modelLoading || modelReady) return;
  modelLoading = true;

  try {
    const webgpuOk = await checkWebGPU();
    if (!webgpuOk) {
      notifyBackground({
        action: 'webgpu-unavailable',
        reason: 'WebGPU with shader-f16 is not available.',
      });
      modelLoading = false;
      return;
    }

    // Clean up any old model files from previous versions
    await clearOldModels();

    // Try OPFS cache first
    const cached = await getModelFromOPFS();
    if (cached) {
      notifyBackground({ action: 'model-loading' });
      await initializeFromFile(cached);
      notifyBackground({ action: 'model-ready' });
      return;
    }

    // Download fresh — stream chunks directly to OPFS so the full 2 GB
    // model never lives in JS heap at once.
    notifyBackground({ action: 'download-start' });

    try {
      await downloadModelToOPFS((received, total) => {
        notifyBackground({ action: 'download-progress', received, total });
      });
    } catch (err) {
      notifyBackground({ action: 'download-error', error: err.message });
      modelLoading = false;
      return;
    }

    notifyBackground({ action: 'download-complete' });
    notifyBackground({ action: 'model-loading' });

    // Load from the OPFS file we just wrote — same path as the cached
    // branch above, MediaPipe streams from a blob URL without us ever
    // holding the model bytes in JS.
    const freshFile = await getModelFromOPFS();
    if (!freshFile) {
      notifyBackground({
        action: 'model-error',
        error: 'Downloaded model missing from OPFS after write',
      });
      modelLoading = false;
      return;
    }
    await initializeFromFile(freshFile);

    notifyBackground({ action: 'model-ready' });
  } catch (err) {
    notifyBackground({ action: 'model-error', error: err.message });
  } finally {
    modelLoading = false;
  }
}

// ---------------------------------------------------------------------------
// Gemma 4 prompt construction
// ---------------------------------------------------------------------------

function buildPrompt({ systemText, toolDeclarations, contextBlock, contextHint, userMessage }) {
  let prompt = '';

  // Tool declarations system turn
  if (toolDeclarations) {
    prompt += `${TURN_OPEN}system\n${toolDeclarations}\n${TURN_CLOSE}\n`;
  }

  // Main system turn
  let system = systemText;
  if (contextHint) system += contextHint;
  if (contextBlock) system += contextBlock;
  prompt += `${TURN_OPEN}system\n${system}\n${TURN_CLOSE}\n`;

  // User turn
  prompt += `${TURN_OPEN}user\n${userMessage}\n${TURN_CLOSE}\n`;

  // Open model turn for generation
  prompt += `${TURN_OPEN}model\n`;

  return prompt;
}

// ---------------------------------------------------------------------------
// Tool execution
// ---------------------------------------------------------------------------

const pendingToolCalls = new Map();
let nextCallId = 1;

function requestToolExecution(toolName, args, tabId) {
  return new Promise((resolve) => {
    const callId = String(nextCallId++);
    pendingToolCalls.set(callId, resolve);
    notifyBackground({
      action: 'execute-tool',
      toolName,
      args,
      callId,
      tabId,
    });
    setTimeout(() => {
      if (pendingToolCalls.has(callId)) {
        pendingToolCalls.delete(callId);
        resolve({ error: 'Tool execution timed out' });
      }
    }, 15000);
  });
}

// ---------------------------------------------------------------------------
// Gemma 4 tool-call parsing
// ---------------------------------------------------------------------------

const TOOL_CALL_RE = /<\|tool_call\|?>call:(\w+)(\{.*?\})<(?:tool_call\||\|tool_call)>/s;

function parseToolCall(text) {
  const match = text.match(TOOL_CALL_RE);
  if (!match) return null;
  try {
    return { name: match[1], args: JSON.parse(match[2]) };
  } catch {
    return null;
  }
}

function extractVisibleText(text) {
  // Strip all Gemma 4 control tags from visible output.
  // Covers: <|tool_call>...<tool_call|>, <|tool_response>...<tool_response|>,
  //         <|tool>...<tool|>, <|turn>..., <turn|>, and partial variants.
  let result = text
    .replace(/<\|tool_call\|?>[\s\S]*?<(?:tool_call\||\|tool_call)>/g, '')
    .replace(/<\|tool_response\|?>[\s\S]*?<(?:tool_response\||\|tool_response)>/g, '')
    .replace(/<\|tool\|?>[\s\S]*?<(?:tool\||\|tool)>/g, '')
    .replace(/<\|turn\|?>[\s\S]*?<(?:turn\||\|turn)>/g, '')
    .replace(/<\|turn>/g, '')
    .replace(/<turn\|>/g, '');

  // During streaming, strip unclosed control tags that extend to the end.
  // This prevents partial tool-call markup from leaking into visible text
  // before the closing tag has been emitted.
  result = result.replace(/<\|(?:tool_call|tool_response|tool|turn)[\s\S]*$/, '');

  // Strip a partially formed tag prefix at the end (e.g. "<|", "<|too")
  result = result.replace(/<\|[\w]*$/, '');

  return result;
}

// ---------------------------------------------------------------------------
// Chat handling
// ---------------------------------------------------------------------------

async function handleChat(userMessage, tabId, sourceUrl) {
  if (!modelReady || !llmInference) {
    sendChatResponse(tabId, {
      text: 'Gemma model not loaded. Please wait for download to complete.',
      done: true,
      error: true,
    });
    return;
  }

  // Always pre-fetch relevant context from the bridge so the model has
  // grounding material even if it doesn't emit its own tool calls.
  sendChatResponse(tabId, { text: '', done: false, toolCall: 'Searching memory...' });

  const searchResult = await requestToolExecution(
    'search_memory', { query: userMessage, limit: 3 }, tabId
  );
  console.log('[MemoryMark] Auto-search for:', userMessage, '→', searchResult);

  const hits = searchResult?.results || [];

  // Show search metadata in chat
  if (hits.length > 0) {
    const sources = hits.map(r => {
      const meta = r.metadata || {};
      const label = meta.title || meta.source_url || 'unknown';
      return `${label} (${r.wing}/${r.room}, distance ${r.distance})`;
    }).join(', ');
    sendChatResponse(tabId, {
      text: '', done: false,
      toolCall: `Found ${hits.length} page(s): ${sources}`
    });
  } else {
    sendChatResponse(tabId, {
      text: '', done: false,
      toolCall: 'No matching pages found in memory.'
    });
  }

  let contextBlock = '';
  if (hits.length > 0) {
    contextBlock = '\n\nRelevant saved pages:\n' +
      hits.map((r, i) => {
        const meta = r.metadata || {};
        const label = meta.title || meta.source_url || 'unknown';
        return `[${i + 1}] "${label}" (${r.wing}/${r.room}, distance: ${r.distance})\n` +
          `${(r.content || '').substring(0, 1500)}`;
      }).join('\n\n');
  }

  let contextHint = '';
  if (sourceUrl) {
    contextHint = `\nThe user is currently viewing: ${sourceUrl}`;
  }

  const prompt = buildPrompt({
    systemText: SYSTEM_PROMPT,
    toolDeclarations: TOOL_DECLARATIONS,
    contextBlock,
    contextHint,
    userMessage,
  });

  console.log('[MemoryMark] Prompt length:', prompt.length, 'chars, context hits:', hits.length);

  await generateWithToolCalls(prompt, tabId, 0);
}

async function generateWithToolCalls(prompt, tabId, depth) {
  if (depth > 2) {
    sendChatResponse(tabId, { text: 'Reached maximum tool call depth.', done: true });
    return;
  }

  try {
    let fullResponse = '';
    let emittedVisible = '';
    let tokenCount = 0;
    const startTime = performance.now();

    await new Promise((resolve) => {
      llmInference.generateResponse(prompt, (partialResult, done) => {
        fullResponse += partialResult;
        tokenCount++;

        // Live visible-text filtering (blueprint pattern): run extractVisibleText
        // on the full accumulated response, then emit only the new delta.
        // This correctly handles tags that straddle chunk boundaries.
        const allVisible = extractVisibleText(fullResponse);
        const delta = allVisible.slice(emittedVisible.length);
        if (delta) {
          emittedVisible = allVisible;
          sendChatResponse(tabId, { text: delta, done: false });
        }

        // Send progress updates so the UI knows generation is active
        // even when visible text hasn't changed (e.g. model emitting control tags)
        if (tokenCount % 5 === 0 || done) {
          const elapsed = ((performance.now() - startTime) / 1000).toFixed(1);
          const tokSec = (tokenCount / Math.max(0.1, parseFloat(elapsed))).toFixed(0);
          sendChatResponse(tabId, {
            text: '', done: false,
            progress: { tokens: tokenCount, elapsed, tokSec }
          });
        }

        if (done) resolve();
      });
    });

    console.log('[MemoryMark] Full response length:', fullResponse.length);

    // Check if the model emitted a tool call
    const toolCall = parseToolCall(fullResponse);

    if (toolCall) {
      console.log('[MemoryMark] Model-initiated tool call:', toolCall.name, toolCall.args);
      const argsStr = Object.values(toolCall.args).join(', ');
      sendChatResponse(tabId, { text: '', done: false, toolCall: `${toolCall.name}(${argsStr})` });

      const result = await requestToolExecution(toolCall.name, toolCall.args, tabId);
      console.log('[MemoryMark] Tool result:', result);

      // Show result summary
      const resultHits = result?.results?.length;
      if (resultHits !== undefined) {
        sendChatResponse(tabId, { text: '', done: false, toolCall: `Got ${resultHits} result(s)` });
      }

      // Continue generation with tool results injected
      const toolResultStr = JSON.stringify(result, null, 2).substring(0, 4000);
      const continuation = prompt + fullResponse + `${TURN_CLOSE}\n` +
        `${TURN_OPEN}tool\n${toolResultStr}\n${TURN_CLOSE}\n` +
        `${TURN_OPEN}model\n`;

      await generateWithToolCalls(continuation, tabId, depth + 1);
    } else {
      sendChatResponse(tabId, { text: '', done: true });
    }
  } catch (err) {
    console.error('[MemoryMark] Generation error:', err);
    sendChatResponse(tabId, { text: 'Error: ' + err.message, done: true, error: true });
  }
}

// ---------------------------------------------------------------------------
// Message helpers
// ---------------------------------------------------------------------------

function sendChatResponse(tabId, data) {
  const msg = {
    target: 'background',
    action: 'chat-response',
    tabId: tabId,
    text: data.text || '',
    done: data.done || false,
    error: data.error || false,
    toolCall: data.toolCall || null,
  };
  if (data.progress) msg.progress = data.progress;
  chrome.runtime.sendMessage(msg);
}

function notifyBackground(data) {
  chrome.runtime.sendMessage({ target: 'background', ...data });
}

// ---------------------------------------------------------------------------
// Message listener
// ---------------------------------------------------------------------------

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.target !== 'offscreen') return;

  switch (message.action) {
    case 'chat':
      handleChat(message.message, message.tabId, message.sourceUrl);
      break;

    case 'init-model':
      initModel();
      break;

    case 'tool-result': {
      const resolver = pendingToolCalls.get(message.callId);
      if (resolver) {
        pendingToolCalls.delete(message.callId);
        resolver(message.result);
      }
      break;
    }
  }
});
