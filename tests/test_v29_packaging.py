from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def test_dockerfile_contains_runtime_packages_and_product_entrypoint():
    docker = (ROOT / "gateway" / "Dockerfile").read_text()
    assert "COPY notifications ./notifications" in docker
    assert "COPY dashboard_app.py ./dashboard_app.py" in docker
    assert "COPY scheduler.py ./scheduler.py" in docker
    assert 'CMD ["./docker-entrypoint.sh"]' in docker

def test_docker_entrypoint_runs_dashboard_and_worker():
    entry = (ROOT / "docker-entrypoint.sh").read_text()
    assert "python -m worker.runner &" in entry
    assert "exec python dashboard_app.py" in entry

def test_dashboard_bind_host_is_configurable_for_containers():
    text = (ROOT / "dashboard_app.py").read_text()
    assert "DASHBOARD_HOST" in text

def test_old_dead_notification_scheduler_is_removed():
    assert not (ROOT / "notifications" / "scheduler.py").exists()
