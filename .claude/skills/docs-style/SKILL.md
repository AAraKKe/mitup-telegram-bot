---
name: docs-style
description: Documentation style conventions for mitup_bot. Auto-load when writing or editing files in docs/.
user-invocable: false
---

# Documentation Style

Documentation files in `docs/` are served with MkDocs. Configuration is in `mkdocs.yml` at the project root.

## Markdown style

- Be friendly without sounding over the top.
- When explaining something technical, add expandable code blocks (only if the content is more than 10 lines).
- Use Twemoji shortcodes for emojis (e.g., `:pen:`, `:check_mark:`) — only on headers, never within paragraphs.
- When adding examples, do not assume meetings are only business meetings — they are social gatherings. Show varied scenarios.
- Use `*` for bullet points, not `-` (some environments do not render `-` properly).
- Any new file must be named in `snake_case`.

## Headings

Always surround headings with blank lines:

```markdown
## This heading
- Won't
- Properly generate
- The bullet point list

## But this heading

- Will do it
- Properly
```

The same applies to bullet point lists — always surround them with blank lines.

## End of file

End every file with an empty line.

## Linking to non-doc files

Any non-documentation file must be linked with the full URL pointing to the main branch of the Mitup GitLab repository. Project files are not deployed with the documentation.

## Adding a new page

Every time a new page is added, add it to the appropriate place in `mkdocs.yml` so it appears in the navigation.

## Button references

When referring to bot interface buttons in the docs:

1. Identify the button mentioned (e.g., "New meeting").
2. Find the corresponding entry in `ButtonMessages` in `mitup_bot/utils/messages.py` to confirm the exact text and emoji (e.g., `➕ New meeting`).
3. Determine the Twemoji shortcode for the emoji (e.g., `➕` → `:heavy_plus_sign:`).
4. Format as: `*:twemoji_shortcode: Button Text*{.button-like}`
   - Example: `*:heavy_plus_sign: New meeting*{.button-like}`
5. Rules:
   - Wrap in Markdown italics (`*...*`).
   - Do NOT use backtick monospace.
   - The `.button-like` class must be inside the closing asterisk.

The `.button-like` CSS class handles all visual styling automatically.

## Validation

After modifying any documentation file, validate the build:

```bash
hatch run dev:build-docs
```
