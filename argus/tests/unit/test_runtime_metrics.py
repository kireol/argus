"""Parse /proc + gfxinfo snapshots used for in-run metrics."""

from argus.adapters.runtime_metrics import (
    ProcMetricsState,
    linux_snapshot_script,
    parse_gfxinfo,
    parse_linux_metrics,
    parse_proc_stat_starttime,
    parse_proc_stat_times,
    parse_profiledata_fps,
    parse_ps_etime,
    parse_sf_vsync_hz,
    safe_identifier,
)

_GFXINFO = """
** Graphics info for pid 1234 [com.example.app] **
  Stats since: 1000000ns
  Total frames rendered: 1
  Janky frames: 1 (100.00%)
  50th percentile: 97ms
  Number Missed Vsync: 0
  Number Slow UI thread: 0
  Number Frame deadline missed: 0

** Graphics info for pid 1234 [com.example.app] **
  Stats since: 2000000000ns
  Total frames rendered: 120
  Janky frames: 6 (5.00%)
  50th percentile: 8ms
  90th percentile: 12ms
  95th percentile: 16ms
  99th percentile: 24ms
  Number Missed Vsync: 2
  Number Slow UI thread: 3
  Number Frame deadline missed: 1
"""

_STAT = "4321 (app:ui) S 1 1 1 0 0 0 0 0 0 0 50 25 0 0 20 0 12 0 1000"


def test_safe_identifier_rejects_shell_metacharacters() -> None:
    assert safe_identifier("com.example.app") == "com.example.app"
    assert safe_identifier("com.example.app; reboot") is None
    assert safe_identifier(None) is None


def test_parse_proc_stat_times_handles_spaces_in_comm() -> None:
    assert parse_proc_stat_times(_STAT) == (50, 25)
    assert parse_proc_stat_starttime(_STAT) == 1000


def test_parse_ps_etime() -> None:
    assert parse_ps_etime("42") == 42.0
    assert parse_ps_etime("01:02") == 62.0
    assert parse_ps_etime("1:02:03") == 3723.0
    assert parse_ps_etime("2-01:02:03") == 176523.0
    assert parse_ps_etime("") is None


def test_system_uptime_without_process() -> None:
    sample = parse_linux_metrics(
        "LOAD=0.10 0.10 0.10 1/200 9\nUPTIME=42.5\nCLK=100\n",
        ProcMetricsState(),
        now=1.0,
    )
    assert sample["system_uptime_s"] == 42.5
    assert "app_uptime_s" not in sample


def test_parse_gfxinfo_counters() -> None:
    gfx = parse_gfxinfo(_GFXINFO)
    assert gfx["frames"] == 120
    assert gfx["janky"] == 6
    assert gfx["p50_ms"] == 8
    assert gfx["p99_ms"] == 24
    assert gfx["deadline_missed"] == 1


def _profiledata(frames: int = 60, interval_ns: int = 16_666_667, flags: int = 0) -> str:
    t0 = 5_000_000_000
    lines = ["Flags,IntendedVsync,Vsync"]
    for i in range(frames):
        ts = t0 + i * interval_ns
        lines.append(f"{flags},{ts},{ts}")
    body = "\n".join(lines)
    return f"---PROFILEDATA---\n{body}\n---PROFILEDATA---"


def test_profiledata_fps_is_60() -> None:
    fps = parse_profiledata_fps(_profiledata())
    assert fps is not None
    assert abs(fps - 60.0) < 0.5


def test_profiledata_does_not_stretch_old_frames_into_two_fps() -> None:
    """30 HWUI submits over ~14s used to report ~2.07 fps via (n-1)/span."""
    fps = parse_profiledata_fps(_profiledata(frames=30, interval_ns=500_000_000))
    assert fps is not None
    assert 2 <= fps <= 4


def test_sf_vsync_period_is_60hz() -> None:
    hz = parse_sf_vsync_hz("SF_LATENCY_PERIOD=16666666")
    assert hz is not None
    assert abs(hz - 60.0) < 0.01


def test_fps_uses_display_refresh_when_hwui_is_idle() -> None:
    """Compose BLAST windows often submit ~2 frames/s; the panel is still 60 Hz."""
    blob = """
LOAD=0.10 0.10 0.10 1/200 9
MEM=2048000 1024000
PID=4321
STATUS_RSS=81920
STATUS_SIZE=512000
THREADS=12
UPTIME=11.0
STAT=4321 (app) S 1 1 1 0 0 0 0 0 0 0 100 0 0 0 20 0 12 0 1000
CLK=100
SF_LATENCY_PERIOD=16666666
GFXINFO_BEGIN
** Graphics info for pid 4321 [com.example.app] **
Total frames rendered: 102
Janky frames: 2
GFXINFO_END
"""
    state = ProcMetricsState()
    parse_linux_metrics(
        blob.replace("UPTIME=11.0", "UPTIME=10.0").replace(
            "Total frames rendered: 102", "Total frames rendered: 100"
        ),
        state,
        now=10.0,
    )
    sample = parse_linux_metrics(blob, state, now=11.0)
    assert abs(sample["fps"] - 60.0) < 0.01
    assert abs(sample["app_fps"] - 2.0) < 0.01


def test_fps_ignores_one_frame_visibility_section() -> None:
    """A 1-frame overlay plus ~1s dumpsys dt used to report 0.94 fps."""
    overlay = _profiledata(frames=1, flags=1)
    ui = _profiledata(frames=60)
    blob = f"""
LOAD=0.10 0.10 0.10 1/200 9
MEM=2048000 1024000
PID=4321
STATUS_RSS=81920
STATUS_SIZE=512000
THREADS=12
UPTIME=10.0
STAT=4321 (app) S 1 1 1 0 0 0 0 0 0 0 100 0 0 0 20 0 12 0 1000
CLK=100
GFXINFO_BEGIN
** Graphics info for pid 4321 [com.example.app] **
Total frames rendered: 1
Janky frames: 1
{overlay}
** Graphics info for pid 4321 [com.example.app] **
Total frames rendered: 4000
Janky frames: 20
{ui}
GFXINFO_END
"""
    sample = parse_linux_metrics(blob, ProcMetricsState(), now=10.0)
    assert sample["fps"] > 50
    assert abs(sample["fps"] - 60.0) < 0.5


def test_parse_linux_metrics_deltas() -> None:
    script = linux_snapshot_script(package="com.example.app", gfxinfo=True)
    assert "dumpsys gfxinfo" in script
    assert "framestats" not in script
    assert "SurfaceFlinger --latency" in script
    first = """
LOAD=0.50 0.40 0.30 1/200 9
MEM=2048000 1024000
PID=4321
STATUS_RSS=81920
STATUS_SIZE=512000
THREADS=12
UPTIME=10.0
STAT=4321 (app) S 1 1 1 0 0 0 0 0 0 0 100 0 0 0 20 0 12 0 1000
CLK=100
GFXINFO_BEGIN
Total frames rendered: 100
Janky frames: 5
Number Missed Vsync: 1
Number Slow UI thread: 2
Number Frame deadline missed: 0
50th percentile: 8ms
GFXINFO_END
"""
    second = first.replace("UPTIME=10.0", "UPTIME=11.0").replace(
        "STAT=4321 (app) S 1 1 1 0 0 0 0 0 0 0 100 0 0 0 20 0 12 0 1000",
        "STAT=4321 (app) S 1 1 1 0 0 0 0 0 0 0 150 0 0 0 20 0 12 0 1000",
    ).replace("Total frames rendered: 100", "Total frames rendered: 160").replace(
        "Janky frames: 5", "Janky frames: 8"
    )
    state = ProcMetricsState()
    a = parse_linux_metrics(first, state, now=10.0)
    assert a["app_rss_mb"] == 80.0
    assert a["system_load_1m"] == 0.5
    assert a["system_uptime_s"] == 10.0
    assert a["app_uptime_s"] == 0.0
    assert "fps" not in a  # first gfx sample is the baseline
    b = parse_linux_metrics(second, state, now=11.0)
    assert b["fps"] == 60.0
    assert b["app_fps"] == 60.0
    assert b["system_uptime_s"] == 11.0
    assert b["app_uptime_s"] == 1.0
    assert b["jank_percent"] == 5.0
    assert abs(b["app_cpu_percent"] - 50.0) < 0.01
