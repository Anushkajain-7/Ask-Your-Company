# Data sources

This project ships with no proprietary sample data. It is designed for
**bring-your-own-data**: any signed-up workspace uploads its own files.

## Supported source types (v1)

| Source type | File extensions | Chunking unit | Citation locator |
|---|---|---|---|
| Markdown / wiki | `.md`, `.markdown`, `.txt` | Heading section | `§ 4.2 Parental Leave` |
| PDF | `.pdf` | Page | `p.7` |
| Slack-style threads | `.json` (see schema below) | Thread | `#people-ops` |
| Spreadsheet rows | `.csv`, `.xlsx` | Row | `row 14` |

## Slack-style JSON schema

```json
{
  "threads": [
    {
      "channel": "people-ops",
      "messages": [
        { "user": "priya", "text": "Reminder: parental leave form due Friday." },
        { "user": "aisha", "text": "Thanks, submitting today." }
      ]
    }
  ]
}
```

A bare list of thread objects (without the top-level `"threads"` key) is
also accepted.

## Sample data for local testing

For a quick local demo, create a small markdown file like:

```markdown
# HR Wiki

## 4.2 Parental Leave (India)
In 2026, employees in India are entitled to 26 weeks of paid parental
leave per child. The leave can be taken up to 12 months from the birth
or adoption date.
```

Upload it as a `markdown` source, then ask: *"What is our 2026 parental
leave policy in India?"*

## Source visibility

Each source has a `visible_to_roles` label:
- `all`: every user in the workspace can retrieve from the source.
- `admin`: only admins can retrieve from the source.
- A custom role such as `hr`: admins and users with exactly that role can
  retrieve from the source.

Documents inherit visibility from their source. Workspace isolation still
applies first, so roles never grant access across companies.

## Duplicate handling

Exact duplicate uploads are rejected with `409`. Near-duplicates are handled
more gently: if a new document is very similar to an existing ready document in
the same workspace, the upload is saved with `status="needs_review"` and its
error field names the matched `document_id` and similarity score. Documents in
`needs_review` are excluded from retrieval until a human review flow marks them
ready.

## License and provenance

Because every workspace supplies its own data, this project takes no
position on data licensing beyond: **do not upload data you don't have the
right to store and query.** Each workspace's uploads are private to that
workspace (see `docs/adr/ADR-003-permissions-model.md`) and are never sent
anywhere except the configured Hugging Face Inference API for embedding
and generation.
