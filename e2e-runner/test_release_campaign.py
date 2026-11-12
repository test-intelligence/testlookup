"""Exercise campaign sequencing without starting containers or downloading builds."""

import pytest

import release_campaign


@pytest.mark.parametrize(
    "resolver, operator, suite", [(0, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, 1)]
)
def test_ordinary_suite_and_evidence_merge_always_run(
    tmp_path, monkeypatch, resolver, operator, suite
):
    calls = []
    merged = []
    monkeypatch.setenv("GH_TOKEN", "synthetic")

    def tool(name, *args, env):
        calls.append(name)
        if name == "launch.py":
            assert "--defer-upgrade" in args and "GH_TOKEN" not in env
            return suite
        assert env["GH_TOKEN"] == "synthetic"
        return resolver if name == "upgrade_baseline.py" else operator

    def merge(out, upgrade, candidate, baseline, platform, suite_exit, upgrade_exit):
        assert platform == "podman" and upgrade.parent.parent == baseline.parent
        merged.append((suite_exit, upgrade_exit, baseline.parent))
        return int(bool(suite_exit or upgrade_exit))

    monkeypatch.setattr(release_campaign, "run_tool", tool)
    monkeypatch.setattr(release_campaign, "merge", merge)
    code = release_campaign.campaign(
        tmp_path / "manifest", "regression", tmp_path / "out", "example/project"
    )
    expected = (
        ["upgrade_baseline.py"] + ([] if resolver else ["upgrade.py"]) + ["launch.py"]
    )
    assert calls == expected
    assert code == int(bool(resolver or operator or suite))
    assert merged[0][:2] == (suite, resolver or operator)
    assert not merged[0][2].exists()


def test_smoke_uses_existing_suite_without_operator_or_github_credentials(
    tmp_path, monkeypatch
):
    def smoke(name, *args, env):
        assert name == "launch.py" and "--defer-upgrade" not in args
        assert "GH_TOKEN" not in env
        return 1

    monkeypatch.setattr(release_campaign, "run_tool", smoke)
    assert (
        release_campaign.campaign(
            tmp_path / "manifest", "smoke", tmp_path / "out", "example/project"
        )
        == 1
    )


def test_prior_report_directory_is_preserved(tmp_path):
    with pytest.raises(ValueError, match="fresh directory"):
        release_campaign.campaign(
            tmp_path / "manifest", "full", tmp_path, "example/project"
        )
