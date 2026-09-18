"""Negative-path checks for handoff tooling; no application services are started."""

from __future__ import annotations

import ast
import contextlib
import importlib.util
import io
import re
import runpy
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True


class CheckerTests(unittest.TestCase):
    def check(self, relative: str | None = None, transform=None):
        original = Path.read_text

        def read(path, *args, **kwargs):
            content = original(path, *args, **kwargs)
            if relative and path.resolve() == (ROOT / relative).resolve():
                return transform(content)
            return content

        output = io.StringIO()
        with patch.object(Path, "read_text", read), contextlib.redirect_stdout(output):
            try:
                runpy.run_path(
                    str(ROOT / "scripts/check_handoff_docs.py"), run_name="__main__"
                )
            except (ValueError, RuntimeError, OSError) as error:
                return error, output.getvalue()
            except SystemExit as error:
                return error, output.getvalue()
        return None, output.getvalue()

    def test_current_documentation_passes(self):
        error, output = self.check()
        self.assertIsNone(error, output)

    def test_missing_heading_is_rejected(self):
        error, output = self.check(
            "docs/README.md", lambda s: s + "\n[Bad](#nonexistent-review-heading)\n"
        )
        self.assertIsInstance(error, SystemExit)
        self.assertIn("missing heading", output)

    def test_top_level_entrypoint_is_checked(self):
        error, output = self.check(
            "GETTING_STARTED.md", lambda s: s + "\n[Bad](does-not-exist-review.md)\n"
        )
        self.assertIsInstance(error, SystemExit)
        self.assertIn("GETTING_STARTED.md: missing link", output)

    def test_actual_request_example_is_validated(self):
        error, _ = self.check(
            "docs/api/README.md",
            lambda s: s.replace(
                '"status":"FAILED"', '"status":"INVALID_REVIEW_STATUS"'
            ),
        )
        self.assertIsNotNone(error)
        self.assertIn("INVALID_REVIEW_STATUS", str(error))

    def test_duplicate_domain_operation_is_rejected(self):
        error, output = self.check(
            "docs/reference/api/ingest.md",
            lambda s: (
                s
                + "\n"
                + re.search(r"^## POST `[^`]+`", s, re.MULTILINE).group(0)
                + "\n"
            ),
        )
        self.assertIsInstance(error, SystemExit)
        self.assertIn("domain operation multiplicity mismatch", output)


class GenerationTests(unittest.TestCase):
    def test_obsolete_and_unowned_outputs(self):
        # Load the real reconciliation function without importing FastAPI or
        # generating the entire reference. Exercise real temporary files.
        tree = ast.parse(
            (ROOT / "scripts/generate_handoff_reference.py").read_text(encoding="utf-8")
        )
        function = next(
            n
            for n in tree.body
            if isinstance(n, ast.FunctionDef) and n.name == "reconcile_outputs"
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            out = root / "docs/reference"
            out.mkdir(parents=True)
            current = out / "current.md"
            current.write_text("Current output")
            obsolete = out / "obsolete.md"
            obsolete.write_text(
                "Generated from tracked source by `scripts/generate_handoff_reference.py`."
            )
            user_file = out / "unowned.md"
            user_file.write_text("Keep this manual content")
            namespace = {
                "OUT": out,
                "ROOT": root,
                "GENERATED": {"current.md"},
                "CHECK": True,
                "ERRORS": [],
            }
            exec(  # noqa: S102 - trusted local function isolated from import side effects
                compile(
                    ast.Module(body=[function], type_ignores=[]), "reconcile", "exec"
                ),
                namespace,
            )
            namespace["reconcile_outputs"]()
            self.assertEqual(len(namespace["ERRORS"]), 2)
            self.assertTrue(obsolete.exists())
            namespace.update(CHECK=False, ERRORS=[])
            namespace["reconcile_outputs"]()
            self.assertFalse(obsolete.exists())
            self.assertTrue(current.exists())
            self.assertEqual(user_file.read_text(), "Keep this manual content")
            self.assertEqual(len(namespace["ERRORS"]), 1)


class WikiTests(unittest.TestCase):
    def export(self, directory, *, dirty=False, old_ref=False):
        spec = importlib.util.spec_from_file_location(
            "handoff_wiki_test", ROOT / "scripts/export_handoff_wiki.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        root = Path(directory)
        (root / "docs/wiki").mkdir(parents=True)
        (root / "architecture").mkdir()
        (root / "backend").mkdir()
        (root / "docs/README.md").write_text(
            "[Schema](../architecture/DATABASE_SCHEMA.md)\n[Code](../backend/main.py)\n"
        )
        (root / "docs/wiki/Home.md").write_text("[Docs](../README.md)\n")
        (root / "architecture/DATABASE_SCHEMA.md").write_text("# Current schema\n")
        (root / "backend/main.py").write_text("# unchanged\n")
        module.ROOT = root
        head = "a" * 40

        def git(arguments, **_kwargs):
            if arguments[1:3] == ["rev-parse", "--verify"]:
                return ("b" * 40 if old_ref else head) + "\n"
            if arguments[1] == "rev-parse":
                return head + "\n"
            if arguments[1] == "status":
                return " M docs/README.md\n" if dirty else ""
            if arguments[1] == "diff":
                return "docs/README.md\narchitecture/DATABASE_SCHEMA.md\n"
            raise AssertionError(arguments)

        output = root / "export"
        with (
            patch.object(sys, "argv", ["export", "--output", str(output)]),
            patch.object(module.subprocess, "check_output", git),
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            module.main()
        return output, module.SOURCE_SHA

    def test_changed_legacy_document_uses_documentation_commit(self):
        with tempfile.TemporaryDirectory() as directory:
            output, source = self.export(directory)
            content = (output / "Documentation.md").read_text()
            self.assertIn(f"/blob/{'a' * 40}/architecture/DATABASE_SCHEMA.md", content)
            self.assertIn(f"/blob/{source}/backend/main.py", content)

    def test_dirty_tree_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory, self.assertRaises(SystemExit):
            self.export(directory, dirty=True)

    def test_mismatched_commit_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory, self.assertRaises(SystemExit):
            self.export(directory, old_ref=True)


if __name__ == "__main__":
    unittest.main()
