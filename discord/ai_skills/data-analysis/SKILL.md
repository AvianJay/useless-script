---
name: data-analysis
description: Analyze and transform user-provided tables or structured data with calculations, reusable JavaScript state, and downloadable CSV, JSON or text results.
allowed-tools: list_attachments read_attachment node_repl create_resource send_resource
---

Inspect the actual input and column names before computing. Use attachment tools
and `read_resource` to read user files. Distinguish missing values from zero,
preserve IDs as strings, and check units, date formats and row counts.

Use `node_repl` for persistent JavaScript calculations. Read this skill's
[runtime API](references/repl.md) before using it.
The kernel is scoped to this request, has no network or bot credentials, and
does not promise installed npm packages. Parse simple data with standard JS;
use the existing `execute_code` sandbox for another supported language if useful.
If Node's container is unavailable, use `execute_code` or explain the limitation.

Store large intermediate text using `create_resource` or `resources.write`.
Return concise findings, meaningful validation and a downloadable result through
`send_resource` or `resources.send`. These support textual CSV/JSON/Markdown/code;
do not label text as a binary XLSX, PDF or image. Use `upload_html` for a requested
interactive chart, keeping private data out of the public upload.

For CSV output, quote fields correctly and neutralize spreadsheet formula injection
in untrusted text cells beginning with =, +, - or @. Check the final totals or a
small independent sample. Do not claim that code ran when a tool failed.
