"""The HTML→text conversion that feeds RAG, never executed.

``ConfluenceKnowledgeConnector._soup_to_text`` walks a BeautifulSoup tree and
produces the structured text that becomes a knowledge chunk — which is then
embedded, retrieved, and handed to the LLM as grounding. It had **one executed
line, the ``def``**, across ~17 tag branches.

That placement is what makes it worth covering. A defect here does not raise
and does not show up as an error anywhere: it produces a *slightly wrong
document*, which is indexed, retrieved, and cited. Losing the ``href`` from
links, or flattening a table into an unseparated run of words, degrades every
answer grounded on that page — and the only symptom is an AI that is vaguely
less useful.

``_regex_strip_html`` is the fallback used when BeautifulSoup is unavailable.
It is the offline/air-gapped path, so it is the one most likely to be running
somewhere nobody is watching, and it was equally unrun.

Every expected value below was read off the real implementation first rather
than assumed.
"""
from __future__ import annotations

import pytest

from app.services.connectors.confluence_connector import ConfluenceKnowledgeConnector

pytestmark = pytest.mark.regression

bs4 = pytest.importorskip("bs4")


@pytest.fixture(scope="module")
def convert():
    """The connector is documented as stateless, so a bare instance is enough."""
    from bs4 import BeautifulSoup

    connector = ConfluenceKnowledgeConnector()

    def _convert(html: str) -> str:
        return connector._soup_to_text(BeautifulSoup(html, "html.parser"))

    return _convert


class TestStructureSurvivesConversion:
    """Headings and lists are how a retrieved chunk keeps its shape. Flattened,
    a page becomes one undifferentiated blob and the retriever loses the
    section boundaries it ranks on."""

    @pytest.mark.parametrize(
        "tag,hashes",
        [("h1", "#"), ("h2", "##"), ("h3", "###"),
         ("h4", "####"), ("h5", "#####"), ("h6", "######")],
    )
    def test_every_heading_level_maps_to_its_own_depth(self, convert, tag, hashes):
        assert convert(f"<{tag}>Title</{tag}>").strip() == f"{hashes} Title"

    def test_a_paragraph_is_separated_by_a_blank_line(self, convert):
        """Without the blank line, consecutive paragraphs merge into one and a
        chunker splitting on blank lines cuts in the wrong places."""
        assert convert("<p>Hello</p>") == "Hello\n\n"

    def test_list_items_become_bullets_one_per_line(self, convert):
        assert convert("<ul><li>a</li><li>b</li></ul>") == "\n- a\n- b\n"

    def test_ordered_lists_are_handled_like_unordered_ones(self, convert):
        assert convert("<ol><li>a</li></ol>") == "\n- a\n"

    def test_a_line_break_becomes_a_newline(self, convert):
        assert convert("<br/>") == "\n"


class TestInlineFormatting:
    @pytest.mark.parametrize("tag", ["strong", "b"])
    def test_bold_variants_both_produce_markdown_bold(self, convert, tag):
        assert convert(f"<{tag}>x</{tag}>") == "**x**"

    @pytest.mark.parametrize("tag", ["em", "i"])
    def test_italic_variants_both_produce_markdown_italics(self, convert, tag):
        assert convert(f"<{tag}>y</{tag}>") == "*y*"

    def test_inline_code_is_fenced_with_backticks(self, convert):
        assert convert("<code>z</code>") == "`z`"

    def test_a_pre_block_becomes_a_fenced_block(self, convert):
        """A code block that loses its fence gets chunked and embedded as
        prose, which is how a config snippet ends up cited as a sentence."""
        assert convert("<pre>block</pre>") == "```\nblock\n```\n"

    def test_inline_formatting_survives_inside_a_paragraph(self, convert):
        assert convert("<p>see <strong>this</strong> now</p>") == "see **this** now\n\n"


class TestLinks:
    def test_a_link_keeps_both_its_text_and_its_target(self, convert):
        """The href is the citation. Dropping it leaves an answer that
        references a page it can no longer point to."""
        assert convert('<a href="/spaces/DOC/page">link</a>') == "[link](/spaces/DOC/page)"

    def test_a_link_without_an_href_still_keeps_its_text(self, convert):
        """Confluence emits anchors with no href for internal artefacts. The
        text must survive rather than the whole node being dropped."""
        assert convert("<a>no href</a>") == "[no href]()"


class TestTables:
    def test_cells_are_separated_so_a_row_does_not_become_one_word(self, convert):
        """The failure this prevents: `c1c2` embedded as a single token. Cells
        are pipe-separated and the row is newline-terminated."""
        out = convert("<table><tr><td>c1</td><td>c2</td></tr></table>")
        assert out == "\n| c1 | c2 | \n\n"

    def test_header_cells_are_treated_like_data_cells(self, convert):
        out = convert("<table><tr><th>H1</th><th>H2</th></tr></table>")
        assert "H1 | H2" in out

    def test_multiple_rows_each_get_their_own_line(self, convert):
        out = convert("<table><tr><td>a</td></tr><tr><td>b</td></tr></table>")
        assert out.count("\n| ") == 2


class TestUnknownAndConfluenceSpecificTags:
    def test_an_unknown_tag_passes_its_children_through(self, convert):
        """The catch-all branch. A tag nobody mapped must not delete the text
        inside it — silent content loss is the worst outcome for an ingester."""
        assert convert("<div>plain</div>") == "plain"
        assert convert("<span>inline</span>") == "inline"

    def test_a_confluence_macro_yields_its_body(self, convert):
        """`ac:structured-macro` wraps info panels, code blocks and notes. The
        wrapper is noise; the body is the knowledge."""
        html = '<ac:structured-macro ac:name="info"><p>Important</p></ac:structured-macro>'
        assert "Important" in convert(html)

    def test_deeply_nested_content_is_still_reached(self, convert):
        """The walk is recursive; a depth limit or a missed branch would
        silently truncate long pages."""
        html = "<div><section><ul><li><strong>deep</strong></li></ul></section></div>"
        assert "**deep**" in convert(html)

    def test_an_empty_document_yields_empty_text(self, convert):
        assert convert("") == ""

    @pytest.mark.parametrize("node", [None, 42, object()], ids=["none", "int", "object"])
    def test_a_node_that_is_neither_tag_nor_string_yields_empty_text(self, node):
        """The defensive branch. bs4 can hand back node types this walker does
        not model, and an ingester that raises on one unexpected node loses the
        whole page rather than one element."""
        assert ConfluenceKnowledgeConnector()._soup_to_text(node) == ""


class TestTheRegexFallback:
    """Used when BeautifulSoup is unavailable — the air-gapped path, and so the
    one most likely to be running where nobody is watching."""

    @staticmethod
    def strip(html: str) -> str:
        return ConfluenceKnowledgeConnector._regex_strip_html(html)

    def test_block_tags_become_newlines_rather_than_running_together(self):
        assert self.strip("<p>a</p><p>b</p>") == "a\nb"
        assert self.strip("<div>a</div><div>b</div>") == "a\nb"
        assert self.strip("<li>one</li><li>two</li>") == "one\ntwo"

    @pytest.mark.parametrize("br", ["<br/>", "<br>", "<br />"])
    def test_every_line_break_spelling_is_handled(self, br):
        assert self.strip(f"x{br}y") == "x\ny"

    def test_remaining_tags_are_stripped_but_their_text_kept(self):
        assert self.strip("<b>keep</b> text") == "keep text"

    def test_runs_of_blank_lines_are_collapsed(self):
        """Confluence exports are full of empty block tags; without this an
        embedded chunk is mostly whitespace."""
        assert self.strip("a<p></p><p></p><p></p>b") == "a\n\nb"

    def test_the_result_is_trimmed(self):
        assert self.strip("  <p>pad</p>  ") == "pad"

    def test_it_never_leaves_angle_brackets_behind(self):
        """The point of the fallback: whatever it cannot interpret, it must not
        emit raw markup into an embedding."""
        out = self.strip('<table><tr><td colspan="2">x</td></tr></table>')
        assert "<" not in out and ">" not in out
