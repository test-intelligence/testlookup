"""GAP-B07: no uploaded XML can read a file or expand entities, in any parser.

Every report upload (a QA_ENGINEER or a CI key) reaches one of six XML
parsers -- xUnit, NUnit, TRX, Robot, TestNG and surefire/JUnit (the TestNG
module) -- and the test-case import reads ``.xlsx`` through openpyxl. All of
them are safe today only because they parse through ``defusedxml`` (openpyxl
does so while ``defusedxml`` is installed). No test carried an entity: swapping
one import to plain ``lxml.etree`` passed the suite, and ``ruff`` selects no
XML rules.

For each parser, the real producer output in ``e2e-runner/fixtures/native/``
(or a minimal document where none exists) gets a DOCTYPE and an entity
reference placed where the failure message is read. A control proves that
spot is a parsed field: plain text put there comes back in ``error_message``
or ``stack_trace``.
Then an external entity naming a temp file, an internal entity and a
ten-level expansion must each give an error or no tests, and the file's
text, the entity's text and the expansion never appear in what is parsed.
A static check keeps every XML parse in ``app/`` on ``defusedxml`` (or on an
lxml parser with ``resolve_entities=False``).
"""

from __future__ import annotations

import ast
import io
import re
import zipfile
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.services.nunit_parser import parse_nunit_xml_with_timings
from app.services.robot_parser import parse_robot_xml_with_timings
from app.services.testng_parser import parse_testng_xml_with_timings
from app.services.trx_parser import parse_trx_xml_with_timings
from app.services.xunit_parser import parse_xunit_xml_with_timings

BACKEND = Path(__file__).resolve().parents[1]
NATIVE = BACKEND.parent / "e2e-runner" / "fixtures" / "native"
FAILURE = (
    "release fixture expected failure"  # the failure message in every native fixture
)
PLAIN_MARKER = "ci-xml-plain-marker-test-only"
FILE_MARKER = "ci-xml-xxe-file-marker-test-only"
ENTITY_MARKER = "ci-xml-internal-entity-test-only"
LAUGH = "laugh"

#: A minimal surefire/JUnit and Robot document (no native fixture of either).
_JUNIT = (
    '<testsuite name="secb6" tests="1"><testcase classname="secb6.Case" name="t_one" time="0.1">'
    f'<failure message="boom" type="AssertionError">{FAILURE}</failure></testcase></testsuite>'
)
_ROBOT = (
    '<robot generator="Robot 7.0" generated="2026-11-11T10:00:00.000000"><suite id="s1" name="Secb6">'
    '<test id="s1-t1" name="t_one"><status status="FAIL" start="2026-11-11T10:00:00.000000" '
    f'elapsed="0.1">{FAILURE}</status></test>'
    '<status status="FAIL" start="2026-11-11T10:00:00.000000" elapsed="0.1"/></suite></robot>'
)


def _native(name: str):
    def load() -> str:
        path = NATIVE / name
        # A missing fixture fails (never skips): the repository root is part of every CI checkout.
        assert path.is_file(), f"missing native fixture {path}"
        return path.read_text(encoding="utf-8")

    return load


#: parser id -> (parse function, the clean document, how many tests it holds)
PARSERS = {
    "xunit": (parse_xunit_xml_with_timings, _native("xunit.xml"), 3),
    "nunit": (parse_nunit_xml_with_timings, _native("nunit.xml"), 3),
    "trx": (parse_trx_xml_with_timings, _native("report.trx"), 3),
    "testng": (parse_testng_xml_with_timings, _native("testng.xml"), 3),
    "junit": (parse_testng_xml_with_timings, lambda: _JUNIT, 1),
    "robot": (parse_robot_xml_with_timings, lambda: _ROBOT, 1),
}
IDS = sorted(PARSERS)


def _without_declaration(text: str) -> str:
    # A str with an encoding declaration is refused by some parsers before any
    # entity is seen; dropping it keeps the entity the thing under test.
    return re.sub(r"^\s*<\?xml[^>]*\?>\s*", "", text)


def _place(text: str, replacement: str) -> str:
    """Put ``replacement`` (text or an entity reference) where the failure message is."""
    cdata = f"<![CDATA[{FAILURE}]]>"
    if cdata in text:
        return text.replace(cdata, replacement, 1)
    assert FAILURE in text, "the fixture lost its failure message"
    return text.replace(FAILURE, replacement, 1)


def _with_entities(parser_id: str, declarations: str, reference: str) -> str:
    _parse, load, _count = PARSERS[parser_id]
    body = _place(_without_declaration(load()), reference)
    return f"<!DOCTYPE r [{declarations}]>{body}"


def _parse(parser_id: str, document: str) -> list[dict]:
    parse, _load, _count = PARSERS[parser_id]
    results, _timings = parse(document, "secb6-run")
    return results


@pytest.fixture
def marker_url(tmp_path):
    marker = tmp_path / "host-secret.txt"
    marker.write_text(FILE_MARKER, encoding="utf-8")
    return marker.resolve().as_uri()


# ── controls: the documents parse, and the chosen spot is a parsed field ────


@pytest.mark.parametrize("parser_id", IDS)
def test_the_clean_document_parses(parser_id):
    _parse_fn, load, count = PARSERS[parser_id]

    assert len(_parse(parser_id, load())) == count


@pytest.mark.parametrize("parser_id", IDS)
def test_text_at_the_injection_point_reaches_the_failure_text(parser_id):
    _parse_fn, load, _count = PARSERS[parser_id]
    document = _place(_without_declaration(load()), PLAIN_MARKER)

    failures = [
        f"{r.get('error_message') or ''}\n{r.get('stack_trace') or ''}"
        for r in _parse(parser_id, document)
    ]

    assert any(PLAIN_MARKER in text for text in failures), failures


# ── the attacks ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("parser_id", IDS)
def test_an_external_entity_never_reads_the_file(parser_id, marker_url):
    document = _with_entities(
        parser_id, f'<!ENTITY xxe SYSTEM "{marker_url}">', "&xxe;"
    )

    results = _parse(parser_id, document)

    assert FILE_MARKER not in repr(results)
    assert results == [], (
        f"{parser_id} parsed a document that declares entities: {results}"
    )


@pytest.mark.parametrize("parser_id", IDS)
def test_an_internal_entity_is_never_expanded(parser_id):
    document = _with_entities(parser_id, f'<!ENTITY who "{ENTITY_MARKER}">', "&who;")

    results = _parse(parser_id, document)

    assert ENTITY_MARKER not in repr(results)
    assert results == [], (
        f"{parser_id} parsed a document that declares entities: {results}"
    )


@pytest.mark.parametrize("parser_id", IDS)
def test_a_ten_level_entity_expansion_is_refused(parser_id):
    declarations = f'<!ENTITY e0 "{LAUGH}">' + "".join(
        f'<!ENTITY e{level} "{("&e" + str(level - 1) + ";") * 10}">'
        for level in range(1, 11)
    )
    document = _with_entities(parser_id, declarations, "&e10;")

    results = _parse(parser_id, document)

    assert LAUGH * 2 not in repr(results)
    assert results == [], (
        f"{parser_id} parsed a document that declares entities: {results}"
    )


# ── the .xlsx test-case import (openpyxl) ───────────────────────────────────


def test_openpyxl_parses_through_defusedxml():
    from openpyxl import xml as openpyxl_xml

    assert openpyxl_xml.DEFUSEDXML is True


def _workbook(title: str) -> bytes:
    from openpyxl import Workbook

    book = Workbook()
    sheet = book.active
    sheet.append(["Title", "Objective"])
    sheet.append([title, "secb6 objective"])
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


def _rewrite(content: bytes, member: str, edit) -> bytes:
    source, out = zipfile.ZipFile(io.BytesIO(content)), io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as target:
        for item in source.infolist():
            data = source.read(item.filename)
            if item.filename == member:
                data = edit(data.decode("utf-8")).encode("utf-8")
            target.writestr(item, data)
    return out.getvalue()


def test_a_clean_workbook_imports_its_title():
    from app.services.test_case_import_service import parse_upload

    parsed = parse_upload("cases.xlsx", _workbook(PLAIN_MARKER))

    assert [values.get("title") for _row, values in parsed.rows] == [PLAIN_MARKER]


@pytest.mark.parametrize("kind", ["external", "internal"])
def test_an_entity_in_a_workbook_is_refused(kind, marker_url):
    from app.services.test_case_import_service import IMPORT_UNREADABLE, parse_upload

    declaration = (
        f'<!ENTITY ent SYSTEM "{marker_url}">'
        if kind == "external"
        else f'<!ENTITY ent "{ENTITY_MARKER}">'
    )
    content = _workbook(PLAIN_MARKER)
    names = zipfile.ZipFile(io.BytesIO(content)).namelist()
    member = (
        "xl/sharedStrings.xml"
        if "xl/sharedStrings.xml" in names
        else "xl/worksheets/sheet1.xml"
    )

    def edit(xml: str) -> str:
        assert PLAIN_MARKER in xml, f"{member} does not hold the title"
        body = _without_declaration(xml).replace(PLAIN_MARKER, "&ent;", 1)
        return f"<!DOCTYPE r [{declaration}]>{body}"

    with pytest.raises(HTTPException) as refused:
        parse_upload("cases.xlsx", _rewrite(content, member, edit))

    assert refused.value.status_code == 422
    assert refused.value.detail == IMPORT_UNREADABLE


# ── static: every XML parse in app/ is entity-safe ──────────────────────────

#: Standard-library and lxml modules whose parse functions resolve or expand entities.
_UNSAFE_MODULES = {
    "xml.etree.ElementTree",
    "xml.etree.cElementTree",
    "xml.dom.minidom",
    "xml.dom.pulldom",
    "xml.dom.expatbuilder",
    "xml.sax",
    "xml.parsers.expat",
    "lxml.etree",
    "xmltodict",
}
_PARSE_FUNCTIONS = {
    "fromstring",
    "fromstringlist",
    "parse",
    "iterparse",
    "XML",
    "XMLParser",
    "XMLPullParser",
    "parseString",
    "make_parser",
    "ParserCreate",
    "XMLID",
}
#: Reviewed exceptions: (module, call) -> why it is safe.
_REVIEWED = {
    (
        "app/services/mock_generator.py",
        "minidom.parseString",
    ): "pretty-prints XML this function just built with ElementTree; never parses input",
}


def _unsafe_aliases(tree: ast.Module) -> dict[str, str]:
    """local name -> the unsafe module (or module.function) it is bound to."""
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for name in node.names:
                if name.name in _UNSAFE_MODULES:
                    aliases[name.asname or name.name] = name.name
        elif isinstance(node, ast.ImportFrom) and node.module:
            for name in node.names:
                full = f"{node.module}.{name.name}"
                if full in _UNSAFE_MODULES:
                    aliases[name.asname or name.name] = full
                elif node.module in _UNSAFE_MODULES and name.name in _PARSE_FUNCTIONS:
                    aliases[name.asname or name.name] = full
    return aliases


def _keyword(call: ast.Call, name: str):
    return next((kw.value for kw in call.keywords if kw.arg == name), None)


def xml_parse_violations(source: str, relpath: str) -> list[str]:
    """Every call that parses XML through an entity-resolving parser, as ``path:line call``."""
    tree = ast.parse(source)
    aliases = _unsafe_aliases(tree)
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            module, fn, label = (
                aliases.get(func.value.id),
                func.attr,
                f"{func.value.id}.{func.attr}",
            )
        elif isinstance(func, ast.Name) and "." in aliases.get(func.id, ""):
            module, _sep, fn = aliases[func.id].rpartition(".")
            label = func.id
        else:
            continue
        if (
            module is None
            or fn not in _PARSE_FUNCTIONS
            or (relpath, label) in _REVIEWED
        ):
            continue
        if module == "lxml.etree":
            if fn == "XMLParser":
                flag = _keyword(node, "resolve_entities")
                if isinstance(flag, ast.Constant) and flag.value is False:
                    continue
            elif _keyword(node, "parser") is not None:
                continue  # the parser passed must itself be an XMLParser(resolve_entities=False), checked above
        out.append(f"{relpath}:{node.lineno} {label}")
    return out


def test_every_xml_parse_in_app_is_entity_safe():
    violations, parsing_modules = [], set()
    for path in sorted((BACKEND / "app").rglob("*.py")):
        relpath = path.relative_to(BACKEND).as_posix()
        source = path.read_text(encoding="utf-8")
        violations += xml_parse_violations(source, relpath)
        if "defusedxml" in source:
            parsing_modules.add(relpath)

    assert not violations, (
        "parse XML through defusedxml (or lxml.etree.XMLParser(resolve_entities=False, no_network=True)), "
        "or review the call in _REVIEWED:\n  " + "\n  ".join(violations)
    )
    # The report parsers and SAML are the defusedxml users this check protects.
    assert {
        "app/services/xunit_parser.py",
        "app/services/nunit_parser.py",
        "app/services/trx_parser.py",
        "app/services/robot_parser.py",
        "app/services/testng_parser.py",
        "app/services/saml_slo.py",
        "app/services/sso_service.py",
    } <= parsing_modules


@pytest.mark.parametrize(
    "source, expected",
    [
        ("import lxml.etree as ET\nET.fromstring(x)\n", ["m.py:2 ET.fromstring"]),
        ("from lxml import etree\netree.fromstring(x)\n", ["m.py:2 etree.fromstring"]),
        (
            "from lxml import etree\np = etree.XMLParser()\netree.fromstring(x, parser=p)\n",
            ["m.py:2 etree.XMLParser"],
        ),
        (
            "from lxml import etree\np = etree.XMLParser(resolve_entities=False)\netree.fromstring(x, parser=p)\n",
            [],
        ),
        ("import xml.etree.ElementTree as ET\nET.parse(f)\n", ["m.py:2 ET.parse"]),
        (
            "from xml.etree.ElementTree import fromstring\nfromstring(x)\n",
            ["m.py:2 fromstring"],
        ),
        (
            "from xml.dom import minidom\nminidom.parseString(x)\n",
            ["m.py:2 minidom.parseString"],
        ),
        ("import defusedxml.ElementTree as ET\nET.fromstring(x)\n", []),
        ("from xml.etree import ElementTree\nraise ElementTree.ParseError()\n", []),
    ],
)
def test_the_static_check_tells_safe_from_unsafe(source, expected):
    assert xml_parse_violations(source, "m.py") == expected
