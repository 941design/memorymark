# Feature 03: DOM Extraction

## Context

From the main spec (sorted-imagining-journal.md), Section 3.3.

Integrate Readability.js and Turndown.js into the content script to extract article content from the current page and convert it to Markdown.

## Requirements

### Libraries to Add

- `@mozilla/readability` (~65KB) → `extension/lib/readability.min.js`
- `turndown.js` (~19KB) → `extension/lib/turndown.min.js`
- `turndown-plugin-gfm` (~5KB) → `extension/lib/turndown-plugin-gfm.min.js`

These must be bundled as local files in `extension/lib/` (not CDN).

### manifest.json Update

Add the library files to `content_scripts.js` array (they must load before `content.js`):

```json
"content_scripts": [
  {
    "matches": ["<all_urls>"],
    "js": [
      "lib/readability.min.js",
      "lib/turndown.min.js",
      "lib/turndown-plugin-gfm.min.js",
      "content.js"
    ],
    "css": ["content.css"]
  }
]
```

### Extraction Pipeline (in content.js)

```
1. Clone document           document.cloneNode(true)
2. Extract article          new Readability(clone).parse()
3. Convert to Markdown      turndownService.turndown(article.content)
4. Extract metadata         title, author, date, description from meta tags
5. Compose payload          { content: markdown, title, source_url, timestamp }
6. Send to background       chrome.runtime.sendMessage()
```

### Metadata Extraction

Direct DOM queries (no library needed):

```javascript
{
  title: document.querySelector('meta[property="og:title"]')?.content
         || document.title,
  author: document.querySelector('meta[name="author"]')?.content
          || document.querySelector('meta[property="article:author"]')?.content,
  date: document.querySelector('meta[property="article:published_time"]')?.content,
  description: document.querySelector('meta[name="description"]')?.content
               || document.querySelector('meta[property="og:description"]')?.content,
  source_url: window.location.href
}
```

### Turndown Configuration

```javascript
const turndownService = new TurndownService({
  headingStyle: 'atx',
  codeBlockStyle: 'fenced',
  bulletListMarker: '-',
});
turndownService.use(gfm);  // tables, strikethrough
```

### Content Format

The final markdown sent to MemPalace should be:

```markdown
# {title}

> Source: {source_url}
> Author: {author} | Date: {date}

{full markdown body from Readability + Turndown}
```

### Error Handling

- If Readability fails (no article extracted): fall back to `document.body.innerHTML` through Turndown (capture full page)
- If content exceeds 100,000 characters: truncate and show warning in overlay

## Verification

1. Load extension in Chrome
2. Navigate to an article page (e.g., a Wikipedia article, news article, blog post)
3. Press Ctrl+Shift+M
4. Check console logs — should see extracted markdown content
5. Test on 5+ diverse pages (news, Wikipedia, GitHub README, blog, documentation)
6. Verify markdown output preserves headings, lists, code blocks, tables
