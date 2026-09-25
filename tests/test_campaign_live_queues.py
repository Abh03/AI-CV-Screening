"""Live, isolated Compose check for a prebuilt docker-control-worker image.

Run: pytest tests/test_campaign_live_queues.py --run-infrastructure
"""
import os
import gc
import socket
import subprocess
import time
from pathlib import Path
from uuid import uuid4

import pytest
from celery import Celery
from redis import Redis

from app.workers.celery_app import celery_app


pytestmark = [pytest.mark.infrastructure, pytest.mark.worker]
ROOT = Path(__file__).resolve().parents[1]


def _port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _compose(project, override, env, *args):
    cmd = ["docker", "compose", "-p", project, "-f", str(ROOT / "docker" / "docker-compose.yml"),
           "-f", str(override), *args]
    result = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True, text=True,
                            timeout=180, check=False)
    if result.returncode:
        raise AssertionError(f"Compose command failed: {' '.join(args)}\n{result.stderr[-3000:]}")
    return result.stdout.strip()


def _wait_for(predicate, seconds=60):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.5)
    raise AssertionError("Timed out waiting for live Celery worker")


def test_retrieval_backlog_does_not_block_control(tmp_path):
    """Exercise the actual task names and service subscriptions, not eager mode."""
    project = "cvphase4" + uuid4().hex[:10]
    port = _port()
    workspace = ROOT.as_posix()
    override = tmp_path / "phase4.compose.yml"
    override.write_text(
        "services:\n"
        "  redis:\n"
        f"    ports: ['127.0.0.1:{port}:6379']\n"
        + "".join(f"  {service}:\n"
                  "    image: docker-control-worker:latest\n"
                  f"    volumes: ['{workspace}:/app:ro']\n"
                  for service in ("migrate", "control-worker", "retrieval-worker")),
        encoding="utf-8")
    env = dict(os.environ)
    env.update({
        "POSTGRES_PASSWORD": "phase4-test-only",
        "DATABASE_URL": "postgresql+asyncpg://postgres:phase4-test-only@postgres:5432/cv_engine",
        "ENCRYPTION_SECRET_KEY": "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=",
        "API_TOKENS_JSON": '[{"id":"test","role":"recruiter","token":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}]',
        "LLM_PROVIDER": "groq", "GROQ_API_KEY": "test-only",
    })
    broker = f"redis://127.0.0.1:{port}/0"
    probe = Celery("phase4_live_probe", broker=broker, backend=broker)
    probe.conf.task_routes = celery_app.conf.task_routes
    backlog = []
    control = None
    try:
        _compose(project, override, env, "up", "-d", "postgres", "redis", "migrate", "control-worker")
        redis = Redis.from_url(broker)
        _wait_for(redis.ping)
        control_host = _compose(project, override, env, "exec", "-T", "control-worker", "hostname")
        def control_subscribed():
            queues = probe.control.inspect(timeout=2).active_queues() or {}
            names = {queue["name"] for queue in queues.get("celery@" + control_host, [])}
            return names == {"control"}
        _wait_for(control_subscribed)

        backlog = [probe.send_task("campaign.stage2_pair", args=[str(uuid4()), "probe"])
                   for _ in range(8)]
        assert _wait_for(lambda: redis.llen("retrieval") == len(backlog))
        control = probe.send_task("campaign.coordinate", args=[str(uuid4())])
        assert control.get(timeout=20) is None
        assert redis.llen("retrieval") == len(backlog)
        assert all(task.state == "PENDING" for task in backlog)

        _compose(project, override, env, "up", "-d", "retrieval-worker")
        retrieval_host = _compose(project, override, env, "exec", "-T", "retrieval-worker", "hostname")
        def retrieval_subscribed():
            queues = probe.control.inspect(timeout=2).active_queues() or {}
            names = {queue["name"] for queue in queues.get("celery@" + retrieval_host, [])}
            return names == {"retrieval"}
        _wait_for(retrieval_subscribed)
        assert [task.get(timeout=30) for task in backlog] == ["NOT_FOUND"] * len(backlog)
        assert redis.llen("retrieval") == 0
    finally:
        backlog.clear()
        control = None
        gc.collect()
        probe.backend.result_consumer.stop()
        probe.close()
        # The project name is unique to this test, so cleanup cannot touch the user's stack.
        _compose(project, override, env, "down", "--volumes", "--remove-orphans")
