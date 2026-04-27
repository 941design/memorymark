// MemoryMark — Content Script
// Handles overlay injection (capture feedback + chat panel)

(function () {
  'use strict';

  const OVERLAY_ID = 'memorymark-overlay';
  const CHAT_ID = 'memorymark-chat';
  let overlayEl = null;
  let chatEl = null;
  let chatMessages = [];
  let chatOpen = false;
  let autoCloseTimer = null;

  // --- Overlay (capture feedback) ---

  function getOrCreateOverlay() {
    if (overlayEl && document.body.contains(overlayEl)) return overlayEl;

    overlayEl = document.createElement('div');
    overlayEl.id = OVERLAY_ID;
    overlayEl.className = 'memorymark-overlay';
    document.body.appendChild(overlayEl);
    return overlayEl;
  }

  function showOverlay(state, data) {
    const el = getOrCreateOverlay();
    clearAutoClose();

    let html = '<div class="memorymark-overlay-header">' +
      '<span class="memorymark-overlay-title">MemoryMark</span>' +
      '<button class="memorymark-close-btn" data-action="close-overlay">&times;</button>' +
      '</div><div class="memorymark-overlay-body">';

    switch (state) {
      case 'saving':
        html += '<div class="memorymark-status memorymark-saving">' +
          '<span class="memorymark-spinner"></span> Saving page...</div>';
        break;
      case 'saved':
        html += '<div class="memorymark-status memorymark-saved">' +
          '<span class="memorymark-icon-check">&#10003;</span> Page saved to MemPalace' +
          '<div class="memorymark-saved-title">"' + escapeHtml(data?.title || '') + '"</div>' +
          '<div class="memorymark-saved-wing">Wing: Web</div></div>' +
          '<button class="memorymark-open-chat-btn" data-action="open-chat">Open Chat</button>';
        scheduleAutoClose();
        break;
      case 'already-exists':
        html += '<div class="memorymark-status memorymark-exists">' +
          '<span class="memorymark-icon-info">&#9432;</span> Already in MemPalace</div>' +
          '<button class="memorymark-open-chat-btn" data-action="open-chat">Open Chat</button>';
        scheduleAutoClose();
        break;
      case 'error':
        html += '<div class="memorymark-status memorymark-error">' +
          '<span class="memorymark-icon-error">&#10007;</span> ' + escapeHtml(data?.error || 'Unknown error') + '</div>' +
          '<button class="memorymark-retry-btn" data-action="retry">Retry</button>';
        break;
      case 'bridge-unreachable':
        html += '<div class="memorymark-status memorymark-warning">' +
          '<span class="memorymark-icon-warn">&#9888;</span> MemPalace bridge not running.' +
          '<div class="memorymark-hint">Start with <code>docker compose up -d</code></div></div>';
        break;
    }

    html += '</div>';
    el.innerHTML = html;
    el.classList.add('memorymark-visible');

    el.addEventListener('mouseenter', clearAutoClose);
    el.addEventListener('mouseleave', () => {
      if (state === 'saved' || state === 'already-exists') scheduleAutoClose();
    });
  }

  function hideOverlay() {
    clearAutoClose();
    if (overlayEl) {
      overlayEl.classList.remove('memorymark-visible');
      setTimeout(() => {
        if (overlayEl && !overlayEl.classList.contains('memorymark-visible')) {
          overlayEl.remove();
          overlayEl = null;
        }
      }, 300);
    }
  }

  function scheduleAutoClose() {
    clearAutoClose();
    chrome.storage.local.get({ autoClose: 4000 }, (settings) => {
      autoCloseTimer = setTimeout(() => {
        if (!chatOpen) hideOverlay();
      }, settings.autoClose);
    });
  }

  function clearAutoClose() {
    if (autoCloseTimer) {
      clearTimeout(autoCloseTimer);
      autoCloseTimer = null;
    }
  }

  // --- Chat panel ---

  function getOrCreateChat() {
    if (chatEl && document.body.contains(chatEl)) return chatEl;

    chatEl = document.createElement('div');
    chatEl.id = CHAT_ID;
    chatEl.className = 'memorymark-chat';

    chatEl.innerHTML =
      '<div class="memorymark-chat-header">' +
        '<span class="memorymark-chat-title">MemoryMark Chat</span>' +
        '<div class="memorymark-chat-header-actions">' +
          '<button class="memorymark-clear-btn" data-action="clear-chat" title="Clear conversation">&#8634;</button>' +
          '<button class="memorymark-close-btn" data-action="close-chat">&times;</button>' +
        '</div>' +
      '</div>' +
      '<div class="memorymark-chat-messages" id="memorymark-chat-messages"></div>' +
      '<div class="memorymark-chat-input-area">' +
        '<input type="text" class="memorymark-chat-input" ' +
          'id="memorymark-chat-input" placeholder="Type a message..." />' +
        '<button class="memorymark-chat-send" id="memorymark-chat-send">Send</button>' +
      '</div>';

    // Bind input handlers once at creation time
    chatEl.querySelector('#memorymark-chat-input').addEventListener('keydown', handleChatKeydown);
    chatEl.querySelector('#memorymark-chat-send').addEventListener('click', sendChatMessage);

    document.body.appendChild(chatEl);
    return chatEl;
  }

  function openChat() {
    chatOpen = true;
    clearAutoClose();
    hideOverlay();

    const el = getOrCreateChat();
    el.classList.add('memorymark-visible');

    const messagesContainer = el.querySelector('#memorymark-chat-messages');

    // Show welcome if no messages yet
    if (chatMessages.length === 0) {
      chatMessages.push({
        role: 'assistant',
        text: 'How can I help with your saved pages?'
      });
    }
    renderMessages(messagesContainer);

    const input = el.querySelector('#memorymark-chat-input');
    setTimeout(() => input.focus(), 100);
  }

  function clearChat() {
    chatMessages = [{ role: 'assistant', text: 'How can I help with your saved pages?' }];
    const container = document.getElementById('memorymark-chat-messages');
    renderMessages(container);
  }

  function closeChat() {
    chatOpen = false;
    if (chatEl) {
      chatEl.classList.remove('memorymark-visible');
      setTimeout(() => {
        if (chatEl && !chatEl.classList.contains('memorymark-visible')) {
          chatEl.remove();
          chatEl = null;
        }
      }, 300);
    }
  }

  function handleChatKeydown(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendChatMessage();
    }
  }

  function sendChatMessage() {
    const input = document.getElementById('memorymark-chat-input');
    if (!input) return;

    const text = input.value.trim();
    if (!text) return;

    input.value = '';

    // Add user message
    chatMessages.push({ role: 'user', text: text });

    // Add placeholder for assistant response
    chatMessages.push({ role: 'assistant', text: '', streaming: true });

    renderMessages(document.getElementById('memorymark-chat-messages'));

    // Send to background worker
    chrome.runtime.sendMessage({
      action: 'chat',
      message: text,
      sourceUrl: window.location.href
    });
  }

  function renderMarkdown(text, isStreaming) {
    if (!text) return '';
    // During streaming, partial markdown can produce broken HTML (unclosed tags)
    // that corrupts the parent DOM. Only render markdown on completed messages.
    if (isStreaming) return escapeHtml(text);
    try {
      if (typeof marked !== 'undefined' && marked.parse) {
        // Render inside a wrapper div so broken HTML can't escape the container
        return '<div class="memorymark-markdown">' + marked.parse(text, { breaks: true }) + '</div>';
      }
    } catch { /* fall through */ }
    return escapeHtml(text);
  }

  function renderMessages(container) {
    if (!container) return;

    container.innerHTML = chatMessages.map((msg) => {
      const roleClass = msg.role === 'user' ? 'memorymark-msg-user' : 'memorymark-msg-assistant';
      const label = msg.role === 'user' ? 'You' : 'MemoryMark';

      let parts = '';

      // Tool-use log (always shown above the response text)
      if (msg.toolLog && msg.toolLog.length > 0) {
        parts += '<div class="memorymark-tool-log">' +
          msg.toolLog.map(t =>
            '<span class="memorymark-tool-indicator">' + escapeHtml(t) + '</span>'
          ).join('') +
          '</div>';
      }

      if (msg.streaming && !msg.text) {
        let thinkLabel = 'Thinking...';
        if (msg.progress) {
          thinkLabel = msg.progress.tokens + ' tokens \u00b7 ' +
            msg.progress.elapsed + 's \u00b7 ' + msg.progress.tokSec + ' tok/s';
        }
        parts += '<span class="memorymark-thinking">' + thinkLabel + '</span>';
      } else if (msg.streaming && msg.text && msg.progress) {
        // Streaming with visible text — show counter below
        parts += renderMarkdown(msg.text, true);
        parts += '<span class="memorymark-progress">' +
          msg.progress.tokens + ' tokens \u00b7 ' +
          msg.progress.elapsed + 's \u00b7 ' + msg.progress.tokSec + ' tok/s</span>';
      } else if (msg.role === 'user') {
        parts += escapeHtml(msg.text);
      } else {
        // Render as markdown only when streaming is complete
        parts += renderMarkdown(msg.text, msg.streaming);
      }

      if (msg.error) {
        parts = '<span class="memorymark-msg-error">' + parts + '</span>';
      }

      return '<div class="memorymark-msg ' + roleClass + '">' +
        '<div class="memorymark-msg-label">' + label + '</div>' +
        '<div class="memorymark-msg-content">' + parts + '</div>' +
        '</div>';
    }).join('');

    container.scrollTop = container.scrollHeight;
  }

  // --- Handle streaming chat responses ---

  function handleChatResponse(message) {
    // Find the last assistant message that is streaming
    let lastAssistant = null;
    for (let i = chatMessages.length - 1; i >= 0; i--) {
      if (chatMessages[i].role === 'assistant' && chatMessages[i].streaming) {
        lastAssistant = chatMessages[i];
        break;
      }
    }

    if (!lastAssistant) {
      // No streaming message found; create one
      lastAssistant = { role: 'assistant', text: '', streaming: true };
      chatMessages.push(lastAssistant);
    }

    if (message.toolCall) {
      // Accumulate tool-use events as a list
      if (!lastAssistant.toolLog) lastAssistant.toolLog = [];
      lastAssistant.toolLog.push(message.toolCall);
    }

    if (message.text) {
      lastAssistant.text += message.text;
    }

    if (message.progress) {
      lastAssistant.progress = message.progress;
    }

    if (message.error) {
      lastAssistant.error = true;
      if (message.text) {
        lastAssistant.text = message.text;
      }
    }

    if (message.done) {
      lastAssistant.streaming = false;
      delete lastAssistant.progress;
    }

    renderMessages(document.getElementById('memorymark-chat-messages'));
  }

  // --- Message listener ---

  chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    switch (message.action) {
      case 'capture':
        handleCapture();
        break;
      case 'capture-result':
        showOverlay(message.status, message.data || { error: message.error });
        break;
      case 'chat-response':
        handleChatResponse(message);
        break;
      case 'toggle-chat':
        if (chatOpen) {
          closeChat();
        } else {
          openChat();
        }
        break;
    }
  });

  // --- DOM Extraction (Readability + Turndown) ---

  function extractMetadata() {
    return {
      title: document.querySelector('meta[property="og:title"]')?.content
             || document.title,
      author: document.querySelector('meta[name="author"]')?.content
              || document.querySelector('meta[property="article:author"]')?.content
              || '',
      date: document.querySelector('meta[property="article:published_time"]')?.content
            || '',
      description: document.querySelector('meta[name="description"]')?.content
                   || document.querySelector('meta[property="og:description"]')?.content
                   || '',
      source_url: window.location.href
    };
  }

  function extractContent() {
    const metadata = extractMetadata();

    // Configure Turndown
    const turndownService = new TurndownService({
      headingStyle: 'atx',
      codeBlockStyle: 'fenced',
      bulletListMarker: '-',
    });
    turndownService.use(turndownPluginGfm.gfm);

    // Try Readability first
    let markdown = '';
    try {
      const clone = document.cloneNode(true);
      const article = new Readability(clone).parse();
      if (article && article.content) {
        markdown = turndownService.turndown(article.content);
        // Use Readability's title if better
        if (article.title) {
          metadata.title = article.title;
        }
        if (article.byline && !metadata.author) {
          metadata.author = article.byline;
        }
      }
    } catch (e) {
      // Readability failed — fall through to fallback
    }

    // Fallback: convert full body if Readability failed
    if (!markdown) {
      try {
        markdown = turndownService.turndown(document.body.innerHTML);
      } catch (e) {
        markdown = document.body.innerText || '';
      }
    }

    // Compose final content with header
    const authorDate = [
      metadata.author ? 'Author: ' + metadata.author : '',
      metadata.date ? 'Date: ' + metadata.date : ''
    ].filter(Boolean).join(' | ');

    let content = '# ' + metadata.title + '\n\n' +
      '> Source: ' + metadata.source_url + '\n';
    if (authorDate) {
      content += '> ' + authorDate + '\n';
    }
    content += '\n' + markdown;

    // Truncate the assembled payload (header + body) to the bridge's content
    // limit. Truncating only the body before prepending the header pushed
    // long pages (e.g. arXiv HTML) past 100000 chars and produced a 400.
    const MAX_LENGTH = 100000;
    let truncated = false;
    if (content.length > MAX_LENGTH) {
      content = content.substring(0, MAX_LENGTH);
      truncated = true;
    }

    return {
      content: content,
      title: metadata.title,
      author: metadata.author,
      date: metadata.date,
      source_url: metadata.source_url,
      timestamp: new Date().toISOString(),
      truncated: truncated
    };
  }

  function handleCapture() {
    // If overlay is already visible (second press), open chat instead
    if (overlayEl && overlayEl.classList.contains('memorymark-visible')) {
      openChat();
      return;
    }

    // If chat is open (shortcut pressed again), close it
    if (chatOpen) {
      closeChat();
      return;
    }

    showOverlay('saving');

    try {
      const extracted = extractContent();
      if (extracted.truncated) {
        console.warn('[MemoryMark] Content truncated to 100,000 characters');
      }
      chrome.runtime.sendMessage({
        action: 'extract-result',
        data: extracted
      });
    } catch (e) {
      console.error('[MemoryMark] Extraction failed:', e);
      chrome.runtime.sendMessage({
        action: 'extract-result',
        data: {
          source_url: window.location.href,
          title: document.title,
          content: '# ' + document.title + '\n\n> Source: ' + window.location.href + '\n\n' +
            (document.body.innerText || '').substring(0, 5000),
          timestamp: new Date().toISOString()
        }
      });
    }
  }

  // --- Click delegation ---

  document.addEventListener('click', (e) => {
    const action = e.target.dataset?.action;
    if (!action) return;

    switch (action) {
      case 'close-overlay':
        hideOverlay();
        break;
      case 'close-chat':
        closeChat();
        break;
      case 'clear-chat':
        clearChat();
        break;
      case 'open-chat':
        openChat();
        break;
      case 'retry':
        handleCapture();
        break;
    }
  });

  // --- Utilities ---

  function escapeHtml(str) {
    if (!str) return '';
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
  }
})();
