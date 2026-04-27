// MemoryMark — Background Service Worker

const DEFAULT_BRIDGE_URL = 'http://localhost:21517';

async function getBridgeUrl() {
  const { bridgeUrl } = await chrome.storage.local.get({ bridgeUrl: DEFAULT_BRIDGE_URL });
  return bridgeUrl;
}

// --- Platform detection ---

const IS_CHROME = typeof chrome !== 'undefined' && !!chrome.offscreen;

// --- Offscreen document management (Chrome only) ---

let offscreenCreating = null;

async function ensureOffscreenDocument() {
  if (!IS_CHROME) {
    throw new Error('Gemma chat requires Chrome (offscreen documents not available in Firefox)');
  }

  const existingContexts = await chrome.runtime.getContexts({
    contextTypes: ['OFFSCREEN_DOCUMENT'],
    documentUrls: [chrome.runtime.getURL('offscreen.html')]
  });
  if (existingContexts.length > 0) return;

  if (offscreenCreating) {
    await offscreenCreating;
    return;
  }

  // Reset offscreenCreating in finally so a single rejection doesn't pin a
  // failed promise for the rest of the service-worker lifetime — every later
  // call would otherwise re-throw the same error and Gemma would stay dead.
  try {
    offscreenCreating = chrome.offscreen.createDocument({
      url: 'offscreen.html',
      reasons: ['WORKERS'],
      justification: 'Run Gemma LLM inference via WebGPU'
    });
    await offscreenCreating;
  } finally {
    offscreenCreating = null;
  }
}

function isGemmaSupported() {
  return IS_CHROME;
}

// --- Gemma model lifecycle ---

chrome.runtime.onInstalled.addListener(async () => {
  if (!isGemmaSupported()) {
    chrome.storage.local.set({ gemmaAvailable: false, gemmaError: 'Gemma requires Chrome (offscreen API)' });
    return;
  }
  const { gemmaEnabled } = await chrome.storage.local.get({ gemmaEnabled: true });
  if (!gemmaEnabled) return;

  try {
    await ensureOffscreenDocument();
    chrome.runtime.sendMessage({ target: 'offscreen', action: 'init-model' });
  } catch (err) {
    console.warn('[MemoryMark] Failed to start Gemma init on install:', err.message);
  }
});

// Also attempt model init when the service worker starts (handles browser
// restarts where onInstalled does not fire).
chrome.runtime.onStartup.addListener(async () => {
  if (!isGemmaSupported()) return;
  const { gemmaEnabled } = await chrome.storage.local.get({ gemmaEnabled: true });
  if (!gemmaEnabled) return;

  try {
    await ensureOffscreenDocument();
    chrome.runtime.sendMessage({ target: 'offscreen', action: 'init-model' });
  } catch (err) {
    console.warn('[MemoryMark] Failed to start Gemma init on startup:', err.message);
  }
});

// --- Saved-page badge indicator ---

async function updateBadgeForTab(tabId, url) {
  // Skip non-http pages
  if (!url || (!url.startsWith('http://') && !url.startsWith('https://'))) {
    chrome.action.setBadgeText({ text: '', tabId });
    return;
  }

  try {
    const bridgeUrl = await getBridgeUrl();
    const resp = await fetch(
      bridgeUrl + '/api/drawers/by-url?url=' + encodeURIComponent(url)
    );
    if (!resp.ok) {
      chrome.action.setBadgeText({ text: '', tabId });
      return;
    }
    const data = await resp.json();
    if (data.exists) {
      chrome.action.setBadgeText({ text: '\u2713', tabId });
      chrome.action.setBadgeBackgroundColor({ color: '#4CAF50', tabId });
    } else {
      chrome.action.setBadgeText({ text: '', tabId });
    }
  } catch {
    // Bridge unreachable — clear badge silently
    chrome.action.setBadgeText({ text: '', tabId });
  }
}

// Check when user switches tabs
chrome.tabs.onActivated.addListener(async (activeInfo) => {
  try {
    const tab = await chrome.tabs.get(activeInfo.tabId);
    updateBadgeForTab(activeInfo.tabId, tab.url);
  } catch { /* tab may have closed */ }
});

// Check when a tab navigates
chrome.tabs.onUpdated.addListener((tabId, changeInfo) => {
  if (changeInfo.url) {
    updateBadgeForTab(tabId, changeInfo.url);
  }
});

// --- Command handling ---

chrome.commands.onCommand.addListener(async (command) => {
  if (command === 'capture-page') {
    await triggerCapture();
  } else if (command === 'toggle-chat') {
    await triggerToggleChat();
  }
});

// Clicking the toolbar icon triggers capture (or opens chat on restricted pages)
chrome.action.onClicked.addListener(async () => {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab?.id || isRestrictedUrl(tab.url)) {
    await openOrFocusStandaloneChat();
  } else {
    await triggerCapture();
  }
});

async function triggerCapture() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab?.id) return;

  // Skip chrome:// and edge:// pages where content scripts can't run
  if (tab.url && (tab.url.startsWith('chrome://') || tab.url.startsWith('edge://') || tab.url.startsWith('about:'))) {
    return;
  }

  try {
    await chrome.tabs.sendMessage(tab.id, { action: 'capture' });
  } catch {
    // Content script not injected (tab was open before extension loaded).
    // Inject it now and retry.
    try {
      await chrome.scripting.executeScript({
        target: { tabId: tab.id },
        files: [
          'lib/readability.min.js',
          'lib/turndown.min.js',
          'lib/turndown-plugin-gfm.min.js',
          'lib/marked.min.js',
          'content.js',
        ],
      });
      await chrome.scripting.insertCSS({
        target: { tabId: tab.id },
        files: ['content.css'],
      });
      await chrome.tabs.sendMessage(tab.id, { action: 'capture' });
    } catch (injectErr) {
      console.error('[MemoryMark] Cannot inject content script:', injectErr.message);
    }
  }
}

function isRestrictedUrl(url) {
  return !url || url.startsWith('chrome://') || url.startsWith('edge://') ||
    url.startsWith('about:') || url.startsWith('chrome-extension://');
}

async function openOrFocusStandaloneChat() {
  const chatUrl = chrome.runtime.getURL('chat.html');
  const existing = await chrome.tabs.query({ url: chatUrl });
  if (existing.length > 0) {
    // Focus the existing standalone chat tab
    await chrome.tabs.update(existing[0].id, { active: true });
    await chrome.windows.update(existing[0].windowId, { focused: true });
  } else {
    await chrome.tabs.create({ url: chatUrl });
  }
}

async function triggerToggleChat() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });

  // No tab or restricted page — open standalone chat
  if (!tab?.id || isRestrictedUrl(tab.url)) {
    await openOrFocusStandaloneChat();
    return;
  }

  try {
    await chrome.tabs.sendMessage(tab.id, { action: 'toggle-chat' });
  } catch {
    try {
      await chrome.scripting.executeScript({
        target: { tabId: tab.id },
        files: [
          'lib/readability.min.js',
          'lib/turndown.min.js',
          'lib/turndown-plugin-gfm.min.js',
          'lib/marked.min.js',
          'content.js',
        ],
      });
      await chrome.scripting.insertCSS({
        target: { tabId: tab.id },
        files: ['content.css'],
      });
      await chrome.tabs.sendMessage(tab.id, { action: 'toggle-chat' });
    } catch (injectErr) {
      // Content script injection failed — fall back to standalone chat
      console.warn('[MemoryMark] Cannot inject content script, opening standalone chat:', injectErr.message);
      await openOrFocusStandaloneChat();
    }
  }
}

// --- Standalone chat routing ---
//
// Standalone chat lives in chat.html (an extension page). Its messages arrive
// without a sender.tab, so we use the literal "standalone" as a routing token
// instead of a numeric tabId. sendToChat broadcasts via chrome.runtime for
// that token (extension pages do not receive chrome.tabs.sendMessage).

const STANDALONE_TAB_ID = 'standalone';

function sendToChat(tabId, message) {
  if (!tabId) return;
  if (tabId === STANDALONE_TAB_ID) {
    chrome.runtime.sendMessage(message);
  } else {
    chrome.tabs.sendMessage(tabId, message);
  }
}

// --- Message routing ---

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.action === 'chat') {
    handleChatMessage(message, sender);
    return true;
  }

  if (message.action === 'extract-result') {
    handleExtractResult(message, sender);
    return true;
  }

  // Relay streaming chat responses from offscreen -> content script / standalone chat
  if (message.action === 'chat-response' && message.target === 'background') {
    const tabId = message.tabId;
    if (tabId) {
      const relay = {
        action: 'chat-response',
        text: message.text,
        done: message.done,
        error: message.error,
        toolCall: message.toolCall
      };
      if (message.progress) relay.progress = message.progress;
      sendToChat(tabId, relay);
    }
    return false;
  }

  // Tool execution requests from offscreen (Gemma tool calls)
  if (message.action === 'execute-tool' && message.target === 'background') {
    executeToolCall(message.toolName, message.args).then((result) => {
      chrome.runtime.sendMessage({
        target: 'offscreen',
        action: 'tool-result',
        toolName: message.toolName,
        callId: message.callId,
        result: result,
        tabId: message.tabId
      });
    });
    return true;
  }

  // --- Gemma model download / status messages from offscreen ---

  if (message.target === 'background') {
    handleOffscreenStatus(message);
    return false;
  }

  return false;
});

function handleOffscreenStatus(message) {
  switch (message.action) {
    case 'download-progress': {
      const total = message.total || 0;
      const received = message.received || 0;
      const pct = total > 0 ? Math.round((received / total) * 100) : 0;
      chrome.action.setBadgeText({ text: `${pct}%` });
      chrome.action.setBadgeBackgroundColor({ color: '#4A90D9' });
      break;
    }

    case 'download-complete':
      chrome.action.setBadgeText({ text: '\u2713' });
      chrome.action.setBadgeBackgroundColor({ color: '#4CAF50' });
      setTimeout(() => chrome.action.setBadgeText({ text: '' }), 5000);
      break;

    case 'download-error':
      console.error('[MemoryMark] Model download error:', message.error);
      chrome.action.setBadgeText({ text: '!' });
      chrome.action.setBadgeBackgroundColor({ color: '#F44336' });
      chrome.storage.local.set({ gemmaAvailable: false, gemmaError: message.error });
      break;

    case 'model-loading':
      chrome.action.setBadgeText({ text: '...' });
      chrome.action.setBadgeBackgroundColor({ color: '#FF9800' });
      break;

    case 'model-ready':
      chrome.action.setBadgeText({ text: '\u2713' });
      chrome.action.setBadgeBackgroundColor({ color: '#4CAF50' });
      chrome.storage.local.set({ gemmaAvailable: true, gemmaError: null });
      setTimeout(() => chrome.action.setBadgeText({ text: '' }), 5000);
      break;

    case 'model-error':
      console.error('[MemoryMark] Model init error:', message.error);
      chrome.action.setBadgeText({ text: '!' });
      chrome.action.setBadgeBackgroundColor({ color: '#F44336' });
      chrome.storage.local.set({ gemmaAvailable: false, gemmaError: message.error });
      break;

    case 'webgpu-unavailable':
      console.warn('[MemoryMark] WebGPU unavailable:', message.reason);
      chrome.storage.local.set({ gemmaAvailable: false, gemmaError: message.reason });
      break;

  }
}

// --- Slash command handling ---

async function handleChatMessage(message, sender) {
  // Standalone chat (chat.html) has no sender.tab; route via the sentinel
  // so sendToChat broadcasts back to the extension page.
  const tabId = message.standalone ? STANDALONE_TAB_ID : sender.tab?.id;
  const text = (message.message || '').trim();
  const sourceUrl = message.sourceUrl || '';

  try {
    // Handle slash commands directly (no Gemma needed)
    if (text.startsWith('/')) {
      await handleSlashCommand(text, tabId, sourceUrl);
      return;
    }

    // Free-form question → Gemma (Chrome only)
    if (!isGemmaSupported()) {
      sendDirectChat(tabId, 'Chat requires Chrome with WebGPU. Slash commands (/search, /summarize, /status) work in any browser.', true);
      return;
    }
    await ensureOffscreenDocument();
    chrome.runtime.sendMessage({
      target: 'offscreen',
      action: 'chat',
      message: text,
      tabId: tabId,
      sourceUrl: sourceUrl
    });
  } catch (err) {
    if (tabId) {
      sendToChat(tabId, {
        action: 'chat-response',
        text: 'Error: ' + err.message,
        done: true,
        error: true
      });
    }
  }
}

async function handleSlashCommand(text, tabId, sourceUrl) {
  const bridgeUrl = await getBridgeUrl();

  if (text === '/summarize') {
    await handleSummarize(bridgeUrl, tabId, sourceUrl);
  } else if (text.startsWith('/search ')) {
    const query = text.slice(8).trim();
    await handleSearch(bridgeUrl, tabId, query);
  } else if (text === '/status') {
    await handleStatus(bridgeUrl, tabId);
  } else {
    sendDirectChat(tabId, 'Unknown command. Available: /summarize, /search <query>, /status', true);
  }
}

async function handleSummarize(bridgeUrl, tabId, sourceUrl) {
  if (!sourceUrl) {
    sendDirectChat(tabId, 'Cannot determine current page URL.', true);
    return;
  }

  try {
    const resp = await fetch(
      bridgeUrl + '/api/drawers/by-url?url=' + encodeURIComponent(sourceUrl)
    );
    const data = await resp.json();

    if (!data.exists) {
      sendDirectChat(
        tabId,
        "This page hasn't been captured yet. Press Ctrl+Shift+M to save it first.",
        true
      );
      return;
    }

    // Fetch the exact drawer content by ID (not by search, which could match the wrong page)
    const drawerResp = await fetch(
      bridgeUrl + '/api/drawers/' + encodeURIComponent(data.drawer_id)
    );
    if (!drawerResp.ok) {
      sendDirectChat(tabId, 'Page is captured but content could not be retrieved.', true);
      return;
    }
    const drawer = await drawerResp.json();
    const content = drawer.content || '';

    if (!content) {
      sendDirectChat(tabId, 'Page is captured but content is empty.', true);
      return;
    }

    // Send to Gemma for summarization (or return directly if Gemma unavailable)
    if (!isGemmaSupported()) {
      sendDirectChat(tabId, 'Summary (Gemma unavailable, showing excerpt):\n\n' + content.substring(0, 2000), true);
      return;
    }
    await ensureOffscreenDocument();
    chrome.runtime.sendMessage({
      target: 'offscreen',
      action: 'chat',
      message: 'Summarize this document concisely:\n\n' + content.substring(0, 8000),
      tabId: tabId,
      sourceUrl: sourceUrl
    });
  } catch (err) {
    sendDirectChat(tabId, 'Bridge error: ' + err.message, true);
  }
}

async function handleSearch(bridgeUrl, tabId, query) {
  if (!query) {
    sendDirectChat(tabId, 'Usage: /search <query>', true);
    return;
  }

  try {
    const resp = await fetch(
      bridgeUrl + '/api/search?query=' + encodeURIComponent(query) + '&limit=10'
    );
    const data = await resp.json();
    const results = data.results || [];

    if (results.length === 0) {
      sendDirectChat(tabId, 'No results found for "' + query + '".', true);
      return;
    }

    let text = 'Found ' + results.length + ' result(s) for "' + query + '":\n\n';
    results.forEach((r, i) => {
      const meta = r.metadata || {};
      const source = meta.title || meta.source_url || 'unknown';
      text += (i + 1) + '. ' + (r.wing || '?') + '/' + (r.room || '?') +
              ' — ' + source + ' (distance: ' + r.distance + ')\n';
      const preview = (r.content || '').substring(0, 150).replace(/\n/g, ' ');
      if (preview) text += '   ' + preview + '...\n';
      text += '\n';
    });

    sendDirectChat(tabId, text.trim(), true);
  } catch (err) {
    sendDirectChat(tabId, 'Bridge error: ' + err.message, true);
  }
}

async function handleStatus(bridgeUrl, tabId) {
  try {
    const resp = await fetch(bridgeUrl + '/api/status');
    const data = await resp.json();

    let text = 'MemPalace Status:\n';
    text += '  Total drawers: ' + (data.total_drawers || 0) + '\n';

    const wings = data.wings || {};
    const wingNames = Object.keys(wings);
    if (wingNames.length > 0) {
      text += '  Wings: ' + wingNames.map(w => w + ' (' + wings[w] + ')').join(', ') + '\n';
    }

    const rooms = data.rooms || {};
    const roomNames = Object.keys(rooms);
    if (roomNames.length > 0) {
      text += '  Rooms: ' + roomNames.map(r => r + ' (' + rooms[r] + ')').join(', ') + '\n';
    }

    sendDirectChat(tabId, text.trim(), true);
  } catch (err) {
    sendDirectChat(tabId, 'Bridge error: ' + err.message, true);
  }
}

function sendDirectChat(tabId, text, done) {
  if (!tabId) return;
  sendToChat(tabId, {
    action: 'chat-response',
    text: text,
    done: done,
    error: false
  });
}

// --- Tool execution for Gemma ---

async function executeToolCall(toolName, args) {
  const bridgeUrl = await getBridgeUrl();

  try {
    let result;
    switch (toolName) {
      case 'search_memory': {
        const resp = await fetch(
          bridgeUrl + '/api/search?query=' + encodeURIComponent(args.query || '') +
          '&limit=' + (args.limit || 5)
        );
        result = await resp.json();
        break;
      }
      case 'get_page_content': {
        const lookupResp = await fetch(
          bridgeUrl + '/api/drawers/by-url?url=' + encodeURIComponent(args.url || '') +
          '&include_content=true'
        );
        const lookupData = await lookupResp.json();
        if (!lookupData.exists) {
          result = { error: 'No saved page found for that URL.' };
        } else {
          result = {
            title: lookupData.title || '',
            content: lookupData.content || '',
            drawer_id: lookupData.drawer_id,
          };
        }
        break;
      }
      default:
        result = { error: 'Unknown tool: ' + toolName };
    }
    return result;
  } catch (err) {
    return { error: err.message };
  }
}

async function bridgeErrorMessage(resp) {
  let detail = '';
  try {
    const body = await resp.json();
    if (body && typeof body.detail === 'string') {
      detail = body.detail;
    } else if (body) {
      detail = JSON.stringify(body);
    }
  } catch {
    try { detail = await resp.text(); } catch { /* ignore */ }
  }
  return 'Bridge returned ' + resp.status + (detail ? ': ' + detail : '');
}

async function handleExtractResult(message, sender) {
  const bridgeUrl = await getBridgeUrl();

  try {
    const checkUrl = bridgeUrl + '/api/drawers/by-url?url=' + encodeURIComponent(message.data.source_url);
    const checkResp = await fetch(checkUrl);

    if (!checkResp.ok) throw new Error(await bridgeErrorMessage(checkResp));

    const checkData = await checkResp.json();

    if (checkData.exists) {
      chrome.tabs.sendMessage(sender.tab.id, {
        action: 'capture-result',
        status: 'already-exists',
        data: checkData
      });
      return;
    }

    const saveResp = await fetch(bridgeUrl + '/api/drawers', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        wing: 'Web',
        room: 'Captures',
        content: message.data.content,
        metadata: {
          source_url: message.data.source_url,
          title: message.data.title,
          timestamp: new Date().toISOString()
        }
      })
    });

    if (!saveResp.ok) throw new Error(await bridgeErrorMessage(saveResp));

    const saveData = await saveResp.json();
    chrome.tabs.sendMessage(sender.tab.id, {
      action: 'capture-result',
      status: 'saved',
      data: { title: message.data.title, ...saveData }
    });

    // Update badge to show this page is now saved
    chrome.action.setBadgeText({ text: '\u2713', tabId: sender.tab.id });
    chrome.action.setBadgeBackgroundColor({ color: '#4CAF50', tabId: sender.tab.id });
  } catch (err) {
    const isNetworkError = err.message.includes('Failed to fetch') ||
                           err.message.includes('NetworkError');
    chrome.tabs.sendMessage(sender.tab.id, {
      action: 'capture-result',
      status: isNetworkError ? 'bridge-unreachable' : 'error',
      error: err.message
    });
  }
}
