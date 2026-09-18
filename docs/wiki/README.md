# Wiki-ready publication

[Documentation home](../README.md)

The documentation suite uses ordinary Markdown, fenced examples and text-based Mermaid diagrams. The export script produces a flat GitHub Wiki-ready directory, rewrites page links to wiki slugs, and turns code/reference links into immutable GitHub source links at the documented baseline. Large generated API/schema/dictionary pages stay in the repository and are linked, keeping the wiki navigable.

```bash
python scripts/export_handoff_wiki.py --output /path/to/new-wiki-export
```

The output directory must be absent or empty. Publish the documentation commit before publishing the wiki so its immutable generated-reference links resolve. The exporter uses current HEAD for documentation links (`--docs-ref` can select an explicit documentation commit) and the recorded baseline for existing source links. Inspect `Home.md`, `_Sidebar.md` and the exported pages, then copy/commit them into your authorized wiki checkout. This task prepares pages; it does not create or publish a remote wiki or message collaborators. Re-run export after source documentation changes.

For a wiki that cannot render Mermaid, preserve the fenced diagram as text or render it in the hosting platform's supported tooling. The adjacent prose describes the same boundaries.
