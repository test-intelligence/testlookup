"""H01 deployment entry points must perform the one-time protocol cutover."""

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]


def test_cutover_disables_hpa_before_scaling_legacy_backend():
    script = (REPO_ROOT / "scripts/prepare-live-fanout-cutover.sh").read_text(encoding="utf-8")

    delete_hpa = script.index('delete hpa "$hpa"')
    scale_zero = script.index('scale deployment "$deployment" --replicas=0')
    wait_for_delete = script.index("wait --for=delete pod")
    assert delete_hpa < scale_zero < wait_for_delete
    assert 'KCLI="${KCLI:-kubectl}"' in script
    assert "pod_protocols=" in script


def test_every_supported_kubernetes_apply_path_invokes_cutover():
    expected = {
        "Makefile": 4,
        "homelabsetup/deploy-homelab.sh": 1,
        "openshiftsetup/deploy-openshift-artifactory.sh": 1,
        "scripts/deploy-k8s.sh": 1,
        ".github/workflows/ci.yml": 1,
        ".github/workflows/deploy-eks.yml": 1,
        "scripts/release/import-bundle.sh": 2,
        "scripts/release/offline-bundle.sh": 1,
    }
    for relative_path, minimum_count in expected.items():
        text = (REPO_ROOT / relative_path).read_text(encoding="utf-8")
        assert text.count("prepare-live-fanout-cutover.sh") >= minimum_count, relative_path
