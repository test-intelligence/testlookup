from pathlib import Path


def test_release_worker_subscribes_to_all_declared_default_queues():
    text = (Path(__file__).resolve().parents[2] / ".." / "docker-compose.release.yml").read_text()
    command = next(line for line in text.splitlines() if "celery -A app.worker.celery_app worker" in line)
    for queue in ["default", "critical", "ingestion", "ai_analysis", *[f"ingestion.shard.{i}" for i in range(8)]]:
        assert queue in command


def test_release_compose_keeps_children_consumer():
    text = (Path(__file__).resolve().parents[2] / ".." / "docker-compose.release.yml").read_text()
    assert "-Q agent_children" in text
