# General Guidelines

+ MUST use uv as python package manager.
+ ALWAYS use uv when running python.

## Project state
Project orientation lives in `BACKLOG.json` (machine-readable;
validated by `plugins/base/schemas/backlog.schema.json`). On a fresh
session — or when resuming work after idle time — run `/base:orient` to
get a 3-line "you are here" plus ranked next moves. Use
`/base:backlog list` to inspect findings interactively, or
`Skill("base:backlog", args: "list …")` programmatically. Do not inline
backlog content into this file.
