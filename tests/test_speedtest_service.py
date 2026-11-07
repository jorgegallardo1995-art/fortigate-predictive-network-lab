"""Hourly speed tests: Ookla JSON parsing, storage of successes *and* failures,
no concurrent tests, and the API that feeds the /speedtests page."""

from __future__ import annotations

import json
import subprocess
from datetime import timedelta

import pytest
from app.core.timeutils import utc_now
from app.database.models import DATA_SOURCE_MOCK, DATA_SOURCE_REAL, SpeedTest
from app.services import speedtest_service

# Trimmed real `speedtest --format=json` output (Ookla CLI 1.2.0).
OOKLA_RESULT = {
    "type": "result",
    "timestamp": "2026-09-28T03:30:05Z",
    "ping": {"jitter": 0.412, "latency": 6.873, "low": 6.2, "high": 7.5},
    "download": {"bandwidth": 23_750_000, "bytes": 260_000_000, "elapsed": 10_000,
                 "latency": {"iqm": 48.2, "low": 7.1, "high": 210.0, "jitter": 9.1}},
    "upload": {"bandwidth": 11_875_000, "bytes": 130_000_000, "elapsed": 10_000,
               "latency": {"iqm": 95.4, "low": 7.0, "high": 300.0, "jitter": 20.3}},
    "packetLoss": 0.0,
    "isp": "Example ISP",
    "server": {"id": 12345, "host": "st.example.cl", "name": "Example Server",
               "location": "Santiago", "country": "Chile"},
    "result": {"id": "abc", "url": "https://www.speedtest.net/result/c/abc", "persisted": True},
}


@pytest.fixture
def real_mode(monkeypatch):
    from app.core import config as config_module

    settings = config_module.get_settings()
    monkeypatch.setattr(settings, "mock_mode", False)
    monkeypatch.setattr(speedtest_service.shutil, "which", lambda _b: "/usr/local/bin/speedtest")


def _fake_run(stdout: str = "", stderr: str = "", returncode: int = 0):
    def run(cmd, **_kw):
        assert "--accept-license" in cmd and "--format=json" in cmd
        return subprocess.CompletedProcess(cmd, returncode, stdout=stdout, stderr=stderr)
    return run


def test_parse_result_converts_bandwidth_to_mbps() -> None:
    parsed = speedtest_service.parse_result(OOKLA_RESULT)
    assert parsed["download_mbps"] == 190.0  # 23.75 MB/s * 8
    assert parsed["upload_mbps"] == 95.0
    assert parsed["ping_ms"] == 6.873
    assert parsed["download_latency_ms"] == 48.2
    assert parsed["server_id"] == 12345
    assert parsed["server_location"] == "Santiago, Chile"
    assert parsed["result_url"].endswith("/abc")


def test_real_run_stores_parsed_result(session, real_mode, monkeypatch) -> None:
    stdout = '{"type":"log","message":"x"}\n' + json.dumps(OOKLA_RESULT) + "\n"
    monkeypatch.setattr(speedtest_service.subprocess, "run", _fake_run(stdout))

    out = speedtest_service.run_speedtest(trigger="manual")

    assert out["success"] is True
    assert out["download_mbps"] == 190.0
    row = session.query(SpeedTest).one()
    assert row.data_source == DATA_SOURCE_REAL
    assert row.trigger == "manual"
    assert row.finished_at >= row.started_at
    assert row.raw_json["server"]["id"] == 12345


def test_cli_error_is_stored_as_failed_test(session, real_mode, monkeypatch) -> None:
    monkeypatch.setattr(
        speedtest_service.subprocess, "run",
        _fake_run(stderr="[error] Cannot open socket: Timeout occurred", returncode=2),
    )

    out = speedtest_service.run_speedtest()

    assert out["success"] is False
    assert "Timeout occurred" in out["error"]
    assert session.query(SpeedTest).one().download_mbps is None


def test_missing_binary_is_reported(session, real_mode, monkeypatch) -> None:
    monkeypatch.setattr(speedtest_service.shutil, "which", lambda _b: None)
    out = speedtest_service.run_speedtest()
    assert out["success"] is False
    assert "install_speedtest.sh" in out["error"]


def test_timeout_is_stored_as_failed_test(session, real_mode, monkeypatch) -> None:
    def run(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, kw["timeout"])
    monkeypatch.setattr(speedtest_service.subprocess, "run", run)

    assert speedtest_service.run_speedtest()["error"] == "timeout"


def test_mock_mode_generates_no_traffic(session, monkeypatch) -> None:
    def never(*_a, **_kw):
        raise AssertionError("MOCK mode must not run the CLI")
    monkeypatch.setattr(speedtest_service.subprocess, "run", never)

    out = speedtest_service.run_speedtest()

    assert out["success"] is True
    assert out["data_source"] == DATA_SOURCE_MOCK


def test_concurrent_run_is_skipped(session) -> None:
    speedtest_service._lock.acquire()
    try:
        assert speedtest_service.run_speedtest() == {"skipped": True, "reason": "already-running"}
    finally:
        speedtest_service._lock.release()
    assert session.query(SpeedTest).count() == 0


def test_api_lists_and_summarizes(session) -> None:
    from app.web import routes_api

    speedtest_service.run_speedtest()
    speedtest_service.run_speedtest(trigger="manual")
    now = utc_now()
    session.add(SpeedTest(  # a failure and an old test outside the window
        started_at=now, finished_at=now, data_source=DATA_SOURCE_MOCK,
        trigger="scheduled", success=False, error="timeout",
    ))
    session.add(SpeedTest(
        started_at=now - timedelta(days=30), finished_at=now - timedelta(days=30),
        data_source=DATA_SOURCE_MOCK, trigger="scheduled", success=True, download_mbps=1.0,
    ))
    session.commit()

    rows = routes_api.get_speedtests(hours=24)
    assert len(rows) == 3
    assert rows[0]["started_at"] >= rows[-1]["started_at"]  # newest first

    s = routes_api.get_speedtests_summary(hours=24)
    assert (s["total"], s["successful"], s["failed"]) == (3, 2, 1)
    assert 150 <= s["download_mbps"]["min"] <= s["download_mbps"]["max"] <= 200
    assert s["schedule"]["enabled"] is True
