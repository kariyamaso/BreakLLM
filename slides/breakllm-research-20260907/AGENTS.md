# Kariyama Research HTML slides

This directory is a reusable user-owned template asset, not an installed plugin or gallery template.
The canonical directory is `/Users/kariyamaso/workspace/agent/assets/slide-templates/kariyama-research`. A generated folder with `gallery.html` is a working copy: edit its `starter.js`; retain `gallery.html` and `layouts.js` as examples.

When asked to use it:
- Read README.md, DESIGN.md and SOURCES.md first.
- Preserve this canonical asset; create a new deck with tools/new-deck.mjs or a separate copy.
- Use the requested output directory and HTML format. Do not silently convert to PPTX or install a global skill.
- Keep the 1440×810 canvas, palette roles, typography, fine header rule and footer.
- Select layouts from layouts.json; reuse the HTML components and SVG assets.
- Replace all demonstration content. Never present layout example values as experiment results.
- Preserve data provenance, original figure attribution, GT/model distinctions and implementation/evaluation boundaries.
- Use concise Japanese, figures and readable tables; avoid decorative slogans or dense dashboard cards.
- Keep assets local and include the Noto font license.
- Run Node tests where applicable, render every page, inspect at full size, and validate PDF print output.
- Do not change the SilentSense app, datasets, runtime settings or source reference PDFs as part of making a deck.
