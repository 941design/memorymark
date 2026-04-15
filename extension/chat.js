// MemoryMark — Standalone Chat Page
// Full-page chat for use when no website tab is active.

(function () {
  'use strict';

  let chatMessages = [
    { role: 'assistant', text: 'How can I help?' }
  ];

  const messagesContainer = document.getElementById('memorymark-chat-messages');
  const input = document.getElementById('memorymark-chat-input');
  const sendBtn = document.getElementById('memorymark-chat-send');
  const clearBtn = document.getElementById('memorymark-clear-chat');

  // --- Rendering ---

  function escapeHtml(str) {
    if (!str) return '';
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
  }

  function renderMarkdown(text, isStreaming) {
    if (!text) return '';
    if (isStreaming) return escapeHtml(text);
    try {
      if (typeof marked !== 'undefined' && marked.parse) {
        return '<div class="memorymark-markdown">' + marked.parse(text, { breaks: true }) + '</div>';
      }
    } catch { /* fall through */ }
    return escapeHtml(text);
  }

  function renderMessages() {
    messagesContainer.innerHTML = chatMessages.map(function (msg) {
      var roleClass = msg.role === 'user' ? 'memorymark-msg-user' : 'memorymark-msg-assistant';
      var label = msg.role === 'user' ? 'You' : 'MemoryMark';

      var parts = '';

      if (msg.toolLog && msg.toolLog.length > 0) {
        parts += '<div class="memorymark-tool-log">' +
          msg.toolLog.map(function (t) {
            return '<span class="memorymark-tool-indicator">' + escapeHtml(t) + '</span>';
          }).join('') +
          '</div>';
      }

      if (msg.streaming && !msg.text) {
        var thinkLabel = 'Thinking...';
        if (msg.progress) {
          thinkLabel = msg.progress.tokens + ' tokens \u00b7 ' +
            msg.progress.elapsed + 's \u00b7 ' + msg.progress.tokSec + ' tok/s';
        }
        parts += '<span class="memorymark-thinking">' + thinkLabel + '</span>';
      } else if (msg.streaming && msg.text && msg.progress) {
        parts += renderMarkdown(msg.text, true);
        parts += '<span class="memorymark-progress">' +
          msg.progress.tokens + ' tokens \u00b7 ' +
          msg.progress.elapsed + 's \u00b7 ' + msg.progress.tokSec + ' tok/s</span>';
      } else if (msg.role === 'user') {
        parts += escapeHtml(msg.text);
      } else {
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

    messagesContainer.scrollTop = messagesContainer.scrollHeight;
  }

  // --- Send ---

  function sendMessage() {
    var text = input.value.trim();
    if (!text) return;

    input.value = '';

    chatMessages.push({ role: 'user', text: text });
    chatMessages.push({ role: 'assistant', text: '', streaming: true });
    renderMessages();

    chrome.runtime.sendMessage({
      action: 'chat',
      message: text,
      sourceUrl: '',
      standalone: true
    });
  }

  // --- Receive ---

  function handleChatResponse(message) {
    var lastAssistant = null;
    for (var i = chatMessages.length - 1; i >= 0; i--) {
      if (chatMessages[i].role === 'assistant' && chatMessages[i].streaming) {
        lastAssistant = chatMessages[i];
        break;
      }
    }

    if (!lastAssistant) {
      lastAssistant = { role: 'assistant', text: '', streaming: true };
      chatMessages.push(lastAssistant);
    }

    if (message.toolCall) {
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

    renderMessages();
  }

  chrome.runtime.onMessage.addListener(function (message) {
    if (message.action === 'chat-response') {
      handleChatResponse(message);
    }
  });

  // --- Events ---

  input.addEventListener('keydown', function (e) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  });

  sendBtn.addEventListener('click', sendMessage);

  clearBtn.addEventListener('click', function () {
    chatMessages = [{ role: 'assistant', text: 'How can I help?' }];
    renderMessages();
  });

  // --- Init ---

  renderMessages();
  input.focus();
})();
