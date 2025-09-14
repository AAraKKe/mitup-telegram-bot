# Documentation

## Style when writing markdown

- Be friendly without sounding too over the top
- When explaining something technical, be sure to add code blocks that can be expanded with a heading explaining what is hidden. Only make it expandable if the content is more than 10 lines of code.
- Try to use emojis for conveying emotions. Mostly on headers and never within paragraphs. Use the twimoji emojis instead of simple emojis, e.g. :pen: or :check_mark:
- When adding examples, do not assume meeting only refers to business meetings. These refer to all types of meetings, mostly with friends. Add examples that showcase different situations.
- When adding bullet points use `*` instead of `-` because some times they do not render properly.
- Any new file should be named in snake_case.

## Documentation Files

Documentation files, under the `docs` folder, are served with mkdocs. The mkdocs config file in the root of the project is used to handle mkdocs configuration.

Every time a new page is added to the documentation we need to ensure that it is added to the appropriate place in the mkdocs file to be accessible through the navigation in the docs site.

## Linking non-doc files

Any non documentation file should be linked with the full url of the Mitup repository pointing to the main branch. This is because the project files are not deployed with the documentation.

## Heading

When creating a heading (either through standard markdown heading or in bold to simulate a heading) always have the heading surrounded by blank lines to ensure that formatting is correct.

For example:

```markdown
## This heading
- Won't
- Properly generate
- The bullet point list

## But this heading

- Would do it
- Properly
```

The same applies for any bullet point, always surround them with blank lines

## End of line

End every file with an empty line.

## Buttons

When referring to buttons from the bot interface within the documentation:

1. Identify the specific button mentioned (e.g., "New meeting", "Settings").
2. Find the corresponding entry in the `ButtonMessages` class (`mitup_bot/utils/messages.py`) to confirm the exact button text and its associated Unicode emoji (e.g., `➕ New meeting`).
3. Determine the correct Twemoji shortcode for the Unicode emoji (e.g., `➕` is `:heavy_plus_sign:`). You can usually find these shortcodes with a quick web search or by referring to a Twemoji cheat sheet.
4. Format the button reference in the Markdown file using the following pattern: `*:twemoji_shortcode: Button Text*{.button-like}`.
    - **Example:** `*:heavy_plus_sign: New meeting*{.button-like}`
5. **Important:**
    - Wrap the formatted text (including the class attribute) in Markdown italics (`*...*`).
    - Do **not** use monospace/code backticks (`` `...` ``).
    - The `.button-like` class is essential and must be included exactly as shown, *inside* the closing asterisk.
6. The visual styling (italic text, light grey background, rounded corners) is handled automatically by the custom CSS associated with the `.button-like` class (`docs/assets/stylesheets/main.css`) and should not be added manually in the Markdown."

## Validate Docs

When modifying files in the doc folders, always run `hatch run dev:build-docs` to validate that the docs are building.
