"""W30 ADM-05 phase 2: the object key rule (contract §2.3 R-1) on crafted and real-shaped keys.

Only a run's files under its validated ``minio_prefix`` (``{project_id}/…``)
travel; every key the import writes is rebuilt under ``{new_project_id}/`` with
the suffix re-checked. Pure: no storage.
"""
from __future__ import annotations

import uuid

import pytest

from app.services import project_export_objects as o
from app.services.project_export_vocab import OBJECT_KEY_SUFFIX_MAX, SKIP_REASONS

_UP = ".."
_NUL = chr(0)


def _ids() -> tuple[str, str, str]:
    return str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())


@pytest.mark.parametrize("suffix", [
    _UP + "/x", "a/" + _UP + "/b", "a/./b", "./a", _UP, ".", "/abs", "a" + _NUL + "b", "a" + chr(92) + "b",
    "a" + chr(10) + "b", "a" + chr(9) + "b", "", "q" * (OBJECT_KEY_SUFFIX_MAX + 1), "a//" + _UP + "/b",
])
def test_a_crafted_suffix_is_refused(suffix):
    assert o.safe_suffix(suffix) is False


@pytest.mark.parametrize("suffix", [
    "allure/abc-result.json", "allure/screenshot 01.png", "testng/testng-results.xml", "upload_complete.json",
    "a/b/c.d/e-f_g~h@i+j=k,l(m)[n].txt", ".hidden-is-a-name", "x..y/z", "q" * OBJECT_KEY_SUFFIX_MAX,
])
def test_a_real_shaped_suffix_is_accepted(suffix):
    assert o.safe_suffix(suffix) is True


def test_split_key_keeps_the_suffix_under_the_run_root():
    pid, run, _ = _ids()
    prefix = f"{pid}/builds/build-2001/"
    for suffix in ("allure/abc-result.json", "allure/attachments/shot.png"):
        assert o.split_key(prefix + suffix, project_id=pid, run_id=run, minio_prefix=prefix) == (
            o.ROOT_MINIO_PREFIX, suffix,
        )
        assert o.refusal_reason(prefix + suffix, project_id=pid, run_id=run, minio_prefix=prefix) is None


def test_split_key_refuses_every_other_place_and_says_why():
    pid, run, other = _ids()
    prefix = f"{pid}/builds/build-2001/"
    cases = {
        f"uploads/{pid}/{run}/queued/abc123.json": "ingest_input",          # O-2: ingest input never travels
        f"{other}/builds/build-2001/allure/x.json": "refused_prefix",        # another project's tree
        f"{pid}/builds/build-2002/allure/x.json": "refused_prefix",          # not under THIS run's root
        f"{pid}/builds/build-2001/{_UP}/{_UP}/{other}/x.json": "unsafe_key",  # a parent segment
        f"{pid}/builds/build-2001/a/./b": "unsafe_key",
        f"{pid}/builds/build-2001//abs": "unsafe_key",                       # leading '/' after the root
        f"{pid}/builds/build-2001/a{_NUL}b": "unsafe_key",
        prefix + "q" * (OBJECT_KEY_SUFFIX_MAX + 1): "unsafe_key",
    }
    for key, reason in cases.items():
        assert o.split_key(key, project_id=pid, run_id=run, minio_prefix=prefix) is None, key
        assert o.refusal_reason(key, project_id=pid, run_id=run, minio_prefix=prefix) == reason, key
    assert set(o.REFUSALS) == {"unsafe_key", "ingest_input", "refused_prefix"}
    assert "unsafe_key" in SKIP_REASONS


@pytest.mark.parametrize("bad_prefix", [
    None, "", "uploads/{pid}/{run}/", "{other}/builds/b/", "{pid}/" + _UP + "/{other}/", "{pid}/", "{pid}",
    "{pid}/b", "/{pid}/b/", "{pid}/a/./b/", "{pid}x/b/",
])
def test_a_refused_minio_prefix_copies_nothing(bad_prefix):
    pid, run, other = _ids()
    prefix = bad_prefix.format(pid=pid, run=run, other=other) if bad_prefix is not None else None
    key = f"{pid}/b/allure/x.json"
    assert o.split_key(key, project_id=pid, run_id=run, minio_prefix=prefix) is None
    assert o.rebuild_prefix(prefix, source_project_id=pid, new_project_id=str(uuid.uuid4())) is None


def test_rebuild_prefix_moves_the_root_to_the_new_project():
    src, _, new = _ids()
    assert o.rebuild_prefix(f"{src}/rest/", source_project_id=src, new_project_id=new) == f"{new}/rest/"
    assert o.rebuild_prefix(f"{src}/builds/build-2001/allure/", source_project_id=src, new_project_id=new) == (
        f"{new}/builds/build-2001/allure/"
    )
    assert o.rebuild_prefix(f" {src}/rest/ ", source_project_id=src, new_project_id=new) == f"{new}/rest/"


def test_every_key_an_import_writes_starts_with_the_new_project_root():
    src, run, new = _ids()
    prefix = f"{src}/builds/build-2001/"
    new_root = o.rebuild_prefix(prefix, source_project_id=src, new_project_id=new)
    assert new_root is not None
    for key in (prefix + "allure/abc-result.json", prefix + "allure/shot.png"):
        split = o.split_key(key, project_id=src, run_id=run, minio_prefix=prefix)
        assert split is not None
        written = o.object_key(new_root, split[1])
        assert written is not None and written.startswith(f"{new}/") and written.endswith(split[1])
    assert o.object_key(new_root, f"{_UP}/{_UP}/elsewhere/x.json") is None   # an archive suffix is re-checked
    assert o.object_key(new_root.rstrip("/"), "x.json") is None


def test_every_object_class_has_a_sentence_and_only_minio_prefix_travels():
    assert set(o.OBJECT_CLASSES) == {"minio_prefix", "ingest_input", "pipeline_scratch", "knowledge_content",
                                     "compliance_pack"}
    for name, (klass, sentence) in o.OBJECT_CLASSES.items():
        assert klass in ("exported", "excluded") and sentence.strip().endswith("."), name
    assert [n for n, (k, _) in o.OBJECT_CLASSES.items() if k == "exported"] == [o.ROOT_MINIO_PREFIX]
    assert len(o.EXCLUDED_OBJECT_NOTES) == 2


def test_media_and_compressed_objects_are_stored():
    assert o.is_stored("a/shot.PNG") and o.is_stored("a/video.webm") and o.is_stored("a/report.pdf")
    assert not o.is_stored("a/result.json") and not o.is_stored("a/testng.xml")
    assert all(s.startswith(".") and s == s.lower() for s in o.STORED_SUFFIXES)
