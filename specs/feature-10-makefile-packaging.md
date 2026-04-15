# Feature 10: Makefile + Packaging

## Context

From the main spec (sorted-imagining-journal.md), Section 10.

Create a Makefile with build targets for Docker management and extension packaging.

## Requirements

### Makefile Targets

| Target | Description |
|--------|-------------|
| `make up` | `docker compose up -d memorymark-bridge` |
| `make down` | `docker compose down` |
| `make logs` | `docker compose logs -f memorymark-bridge` |
| `make build` | Build Docker image |
| `make ext-chrome` | Package Chrome extension as zip |
| `make ext-firefox` | Package Firefox extension as zip (with manifest overrides) |
| `make ext-all` | Build both Chrome and Firefox zips |
| `make clean` | Remove build artifacts |

### Chrome Extension Zip

- Output: `dist/memorymark-chrome.zip`
- Contents: everything in `extension/` except dev files
- Must include all `lib/` files and icons

### Firefox Extension Zip

- Output: `dist/memorymark-firefox.zip`
- Replace `manifest.json` with Firefox-specific version if needed
- Include same lib/ and icon files

### File Structure

```
memorymark/
  Makefile
  dist/                          # Build output (gitignored)
    memorymark-chrome.zip
    memorymark-firefox.zip
```

## Verification

1. `make up` → Docker bridge starts
2. `make ext-chrome` → `dist/memorymark-chrome.zip` created
3. `make ext-firefox` → `dist/memorymark-firefox.zip` created
4. Unzip Chrome package → load as unpacked extension → works
5. `make clean` → dist/ removed
