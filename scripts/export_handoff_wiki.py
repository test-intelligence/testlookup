#!/usr/bin/env python3
"""Export narrative handoff pages with wiki-local and immutable source links."""

from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
SOURCE_SHA = "44be1f2023d50bbbd9554bc91ce668c5c0789db5"
REPOSITORY = "https://github.com/test-intelligence/testlookup"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, required=True, help="New or empty directory"
    )
    parser.add_argument(
        "--docs-ref", help="Documentation commit SHA; defaults to current HEAD"
    )
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        parser.error(
            "Output must be absent or an empty directory; existing files are never overwritten."
        )

    def git(*arguments: str) -> str:
        return subprocess.check_output(["git", *arguments], cwd=ROOT, text=True).strip()

    docs_ref = git("rev-parse", "--verify", (args.docs_ref or "HEAD") + "^{commit}")
    if docs_ref != git("rev-parse", "HEAD"):
        parser.error(
            "Check out --docs-ref first so page contents and links use the same commit."
        )
    if git("status", "--porcelain", "--untracked-files=all"):
        parser.error(
            "Commit documentation changes before exporting immutable wiki links."
        )
    changed = set(git("diff", "--name-only", SOURCE_SHA, docs_ref, "--").splitlines())
    pages = [ROOT / "docs/README.md", ROOT / "docs/wiki/Home.md"]
    for folder in (
        "product",
        "architecture",
        "pipelines",
        "api",
        "operations",
        "handoff",
    ):
        pages.extend(sorted((ROOT / "docs" / folder).glob("*.md")))
    names = {}
    for path in pages:
        rel = path.relative_to(ROOT / "docs")
        names[path.resolve()] = (
            "Home"
            if rel.as_posix() == "wiki/Home.md"
            else (
                "Documentation"
                if rel.as_posix() == "README.md"
                else "-".join(rel.with_suffix("").parts).replace("README", "Overview")
            )
        )
    if len(set(names.values())) != len(names):
        parser.error("Wiki page-name collision")
    output.mkdir(parents=True, exist_ok=True)

    def rewrite(page: Path, match: re.Match) -> str:
        label, target = match.group(1), match.group(2)
        parsed = urlsplit(target.strip("<>"))
        if parsed.scheme or parsed.netloc or not parsed.path:
            return match.group(0)
        path = (page.parent / unquote(parsed.path)).resolve()
        suffix = "#" + parsed.fragment if parsed.fragment else ""
        if path in names:
            return f"[{label}]({names[path]}{suffix})"
        rel = path.relative_to(ROOT).as_posix()
        sha = docs_ref if rel in changed else SOURCE_SHA
        kind = "tree" if path.is_dir() else "blob"
        return f"[{label}]({REPOSITORY}/{kind}/{quote(sha, safe='')}/{quote(rel, safe='/')}{suffix})"

    for page, slug in names.items():
        raw = page.read_text(encoding="utf-8")
        chunks = re.split(
            r"(^```[^\n]*\n.*?^```\s*$)", raw, flags=re.MULTILINE | re.DOTALL
        )
        for i in range(0, len(chunks), 2):
            chunks[i] = re.sub(
                r"\[([^\]\n]*)\]\(([^)\n]+)\)",
                lambda m, page=page: rewrite(page, m),
                chunks[i],
            )
        (output / f"{slug}.md").write_text(
            "".join(chunks), encoding="utf-8", newline="\n"
        )
    sidebar = (
        "# TestLookup\n\n"
        + "\n".join(f"- [{slug.replace('-', ' ')}]({slug})" for slug in names.values())
        + "\n"
    )
    (output / "_Sidebar.md").write_text(sidebar, encoding="utf-8", newline="\n")
    (output / "_Footer.md").write_text(
        f"Source baseline `{SOURCE_SHA}` · Documentation `{docs_ref}`\n",
        encoding="utf-8",
        newline="\n",
    )
    # Prove every rewritten local wiki page target resolves in the export.
    targets = set(names.values())
    for page in output.glob("*.md"):
        for target in re.findall(
            r"\[[^\]\n]*\]\(([^)\n]+)\)", page.read_text(encoding="utf-8")
        ):
            parsed = urlsplit(target)
            if not parsed.scheme and parsed.path and parsed.path not in targets:
                raise RuntimeError(f"Unresolved wiki link in {page.name}: {target}")
    print(
        f"Exported {len(names)} pages plus sidebar/footer to {output}; all local wiki targets resolve."
    )


if __name__ == "__main__":
    main()
