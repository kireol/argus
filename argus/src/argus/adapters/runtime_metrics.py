"""Parse cheap /proc + dumpsys gfxinfo snapshots used by Linux-like devices."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

_SAFE_NAME = re.compile(r"^[A-Za-z0-9._-]+$")

_GFX_FRAMES = re.compile(r"Total frames rendered:\s*(\d+)")
_GFX_JANKY = re.compile(r"Janky frames:\s*(\d+)")
_GFX_MISSED = re.compile(r"Number Missed Vsync:\s*(\d+)")
_GFX_SLOW_UI = re.compile(r"Number Slow UI thread:\s*(\d+)")
_GFX_DEADLINE = re.compile(r"Number Frame deadline missed:\s*(\d+)")
_GFX_P50 = re.compile(r"50th percentile:\s*(\d+)\s*ms")
_GFX_P90 = re.compile(r"90th percentile:\s*(\d+)\s*ms")
_GFX_P95 = re.compile(r"95th percentile:\s*(\d+)\s*ms")
_GFX_P99 = re.compile(r"99th percentile:\s*(\d+)\s*ms")
_GFX_SECTION = re.compile(r"(?:\*\*\s*)?Graphics info for pid\s+\d+", re.IGNORECASE)
_PROFILEDATA = re.compile(r"---PROFILEDATA---\s*\n(.*?)---PROFILEDATA---", re.DOTALL)
_SKIPPED_FRAME = 1 << 3  # FrameInfoFlags::SkippedFrame
_FPS_WINDOW_NS = 1_000_000_000  # last 1s of vsyncs
_MIN_FPS_DT = 0.05


@dataclass
class ProcMetricsState:
    """Delta counters carried between samples on one adapter instance."""

    utime: int | None = None
    stime: int | None = None
    uptime: float | None = None
    clk_tck: float = 100.0
    frames: int | None = None
    janky: int | None = None
    missed_vsync: int | None = None
    slow_ui: int | None = None
    deadline_missed: int | None = None
    sample_mono: float | None = None
    extras: dict[str, float] = field(default_factory=dict)


def safe_identifier(value: str | None) -> str | None:
    if not value:
        return None
    return value if _SAFE_NAME.fullmatch(value) else None


def linux_snapshot_script(
    *,
    package: str | None = None,
    process: str | None = None,
    gfxinfo: bool = False,
) -> str:
    """One-shot shell script: load, memory, app RSS/CPU, optional gfxinfo."""
    pkg = safe_identifier(package) or ""
    proc = safe_identifier(process) or ""
    gfx = "1" if gfxinfo and pkg else ""
    # Toybox/BusyBox: pidof, awk, getconf. Avoid paste/head pipelines.
    return f"""
echo LOAD=$(cat /proc/loadavg 2>/dev/null)
echo MEM=$(awk '/^MemTotal:/{{t=$2}} /^MemAvailable:/{{a=$2}} END{{print t+0, a+0}}' \
  /proc/meminfo 2>/dev/null)
PID=""
PKG="{pkg}"
PROC="{proc}"
if [ -n "$PKG" ]; then
  PID=$(pidof "$PKG" 2>/dev/null | awk '{{print $1}}')
fi
if [ -z "$PID" ] && [ -n "$PROC" ]; then
  PID=$(pidof "$PROC" 2>/dev/null | awk '{{print $1}}')
  if [ -z "$PID" ]; then
    PID=$(pgrep -n "$PROC" 2>/dev/null)
  fi
fi
echo PID=$PID
echo UPTIME=$(awk '{{print $1}}' /proc/uptime 2>/dev/null)
if [ -n "$PID" ]; then
  echo STATUS_RSS=$(awk '/^VmRSS:/{{print $2}}' /proc/$PID/status 2>/dev/null)
  echo STATUS_SIZE=$(awk '/^VmSize:/{{print $2}}' /proc/$PID/status 2>/dev/null)
  echo THREADS=$(awk '/^Threads:/{{print $2}}' /proc/$PID/status 2>/dev/null)
  echo STAT=$(cat /proc/$PID/stat 2>/dev/null)
fi
echo CLK=$(getconf CLK_TCK 2>/dev/null || echo 100)
if [ -n "{gfx}" ] && [ -n "$PKG" ]; then
  echo GFXINFO_BEGIN
  dumpsys gfxinfo "$PKG" 2>/dev/null
  echo GFXINFO_END
  echo SF_LATENCY_PERIOD=$(dumpsys SurfaceFlinger --latency 2>/dev/null \
    | awk 'NR==1 {{print $1; exit}}')
fi
"""


def _kv_line(blob: str, key: str) -> str:
    prefix = key + "="
    for line in blob.splitlines():
        if line.startswith(prefix):
            return line[len(prefix) :].strip()
    return ""


def _float(text: str) -> float | None:
    try:
        return float(text.strip())
    except (TypeError, ValueError):
        return None


def parse_proc_stat_times(stat_line: str) -> tuple[int, int] | None:
    """utime, stime from ``/proc/<pid>/stat`` (fields 14 and 15)."""
    parsed = _proc_stat_fields(stat_line)
    if parsed is None or len(parsed) < 13:
        return None
    try:
        return int(parsed[11]), int(parsed[12])
    except ValueError:
        return None


def parse_proc_stat_starttime(stat_line: str) -> int | None:
    """starttime clock ticks from ``/proc/<pid>/stat`` (field 22)."""
    parsed = _proc_stat_fields(stat_line)
    if parsed is None or len(parsed) < 20:
        return None
    try:
        return int(parsed[19])
    except ValueError:
        return None


def _proc_stat_fields(stat_line: str) -> list[str] | None:
    close = stat_line.rfind(")")
    if close < 0:
        return None
    return stat_line[close + 2 :].split()


def parse_ps_etime(text: str) -> float | None:
    """Elapsed seconds from ``ps -o etime=`` (``[[dd-]hh:]mm:ss``)."""
    raw = text.strip()
    if not raw:
        return None
    try:
        if raw.isdigit():
            return float(raw)
        days = 0
        if "-" in raw:
            day_part, raw = raw.split("-", 1)
            days = int(day_part)
        parts = [int(p) for p in raw.split(":")]
    except ValueError:
        return None
    if len(parts) == 3:
        hours, minutes, seconds = parts
    elif len(parts) == 2:
        hours, minutes, seconds = 0, parts[0], parts[1]
    elif len(parts) == 1:
        hours, minutes, seconds = 0, 0, parts[0]
    else:
        return None
    return float(days * 86400 + hours * 3600 + minutes * 60 + seconds)


def parse_gfxinfo(text: str) -> dict[str, int]:
    """Stats from the busiest renderer, not the first (often a 1-frame overlay)."""
    section = _busiest_gfx_section(text)
    found: dict[str, int] = {}
    patterns = {
        "frames": _GFX_FRAMES,
        "janky": _GFX_JANKY,
        "missed_vsync": _GFX_MISSED,
        "slow_ui": _GFX_SLOW_UI,
        "deadline_missed": _GFX_DEADLINE,
        "p50_ms": _GFX_P50,
        "p90_ms": _GFX_P90,
        "p95_ms": _GFX_P95,
        "p99_ms": _GFX_P99,
    }
    for name, pattern in patterns.items():
        match = pattern.search(section)
        if match:
            found[name] = int(match.group(1))
    return found


def _gfx_sections(text: str) -> list[str]:
    matches = list(_GFX_SECTION.finditer(text))
    if not matches:
        return [text]
    sections = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        sections.append(text[match.start() : end])
    return sections


def _busiest_gfx_section(text: str) -> str:
    best = text
    best_frames = -1
    for section in _gfx_sections(text):
        match = _GFX_FRAMES.search(section)
        frames = int(match.group(1)) if match else -1
        if frames > best_frames:
            best_frames = frames
            best = section
    return best


def parse_sf_vsync_hz(blob: str) -> float | None:
    """Display refresh from ``dumpsys SurfaceFlinger --latency`` period (ns)."""
    raw = _kv_line(blob, "SF_LATENCY_PERIOD")
    try:
        period_ns = float(raw)
    except (TypeError, ValueError):
        return None
    if period_ns <= 0:
        return None
    hz = 1_000_000_000.0 / period_ns
    if hz < 1.0 or hz > 250.0:
        return None
    return hz


def parse_profiledata_fps(text: str) -> float | None:
    """Unique frames in the last ~1s of PROFILEDATA (not cadence over the ring).

    ``dumpsys gfxinfo`` prints one PROFILEDATA block per view root. Stretching
    the last 30 timestamps across many seconds reports ~2 fps for a 60 Hz
    Compose app that only invalidates a few times a second. Count frames
    whose IntendedVsync falls in the last 1s of that block instead.
    """
    best_fps: float | None = None
    best_n = 0
    for block in _PROFILEDATA.finditer(text):
        lines = [ln.strip() for ln in block.group(1).splitlines() if ln.strip()]
        vsync_col = 1
        if lines and lines[0].lower().startswith("flags"):
            names = [p.strip().lower() for p in lines[0].split(",")]
            if "intendedvsync" in names:
                vsync_col = names.index("intendedvsync")
            elif "frametimelinevsyncid" in names:
                vsync_col = 2
            lines = lines[1:]
        times: list[int] = []
        for stripped in lines:
            cols = stripped.split(",")
            if len(cols) <= vsync_col:
                continue
            try:
                flags = int(cols[0])
                intended = int(cols[vsync_col])
            except ValueError:
                continue
            if flags & _SKIPPED_FRAME or intended <= 0:
                continue
            times.append(intended)
        if len(times) < 2:
            continue
        times.sort()
        cutoff = times[-1] - _FPS_WINDOW_NS
        window = [t for t in times if t >= cutoff]
        if len(window) < 2:
            continue
        fps = float(len(window))
        if fps < 1.0 or fps > 250.0:
            continue
        if len(window) >= best_n:
            best_n = len(window)
            best_fps = fps
    return best_fps


def parse_linux_metrics(
    blob: str, state: ProcMetricsState, *, now: float | None = None
) -> dict[str, float]:
    """Turn a ``linux_snapshot_script`` dump into metric values; updates ``state``."""
    sample: dict[str, float] = {}
    mono = time.monotonic() if now is None else now
    prev_mono = state.sample_mono

    load = _kv_line(blob, "LOAD").split()
    if len(load) >= 3:
        for index, name in enumerate(("system_load_1m", "system_load_5m", "system_load_15m")):
            value = _float(load[index])
            if value is not None:
                sample[name] = value

    mem = _kv_line(blob, "MEM").split()
    if len(mem) >= 2:
        total_kb = _float(mem[0])
        avail_kb = _float(mem[1])
        if avail_kb is not None:
            sample["system_mem_available_mb"] = avail_kb / 1024.0
        if total_kb and avail_kb is not None and total_kb > 0:
            sample["system_mem_used_percent"] = max(
                0.0, min(100.0, 100.0 * (1.0 - avail_kb / total_kb))
            )

    rss_kb = _float(_kv_line(blob, "STATUS_RSS"))
    if rss_kb is not None:
        sample["app_rss_mb"] = rss_kb / 1024.0
    vsize_kb = _float(_kv_line(blob, "STATUS_SIZE"))
    if vsize_kb is not None:
        sample["app_vsize_mb"] = vsize_kb / 1024.0
    threads = _float(_kv_line(blob, "THREADS"))
    if threads is not None:
        sample["app_threads"] = threads

    clk = _float(_kv_line(blob, "CLK"))
    if clk and clk > 0:
        state.clk_tck = clk

    uptime = _float(_kv_line(blob, "UPTIME"))
    if uptime is not None:
        sample["system_uptime_s"] = uptime
    starttime_ticks = parse_proc_stat_starttime(_kv_line(blob, "STAT"))
    if uptime is not None and starttime_ticks is not None and state.clk_tck > 0:
        app_uptime = uptime - starttime_ticks / state.clk_tck
        if app_uptime >= 0:
            sample["app_uptime_s"] = app_uptime
    prev_uptime = state.uptime
    prev_gfx_uptime = state.extras.get("gfx_uptime")
    times = parse_proc_stat_times(_kv_line(blob, "STAT"))
    if uptime is not None and times is not None:
        utime, stime = times
        if prev_uptime is not None and state.utime is not None and state.stime is not None:
            dt = uptime - prev_uptime
            dticks = (utime + stime) - (state.utime + state.stime)
            if dt > 0 and dticks >= 0:
                sample["app_cpu_percent"] = max(
                    0.0, 100.0 * (dticks / state.clk_tck) / dt
                )
        state.utime, state.stime, state.uptime = utime, stime, uptime
    elif uptime is not None:
        state.uptime = uptime

    refresh_hz = parse_sf_vsync_hz(blob)
    if refresh_hz is not None:
        sample["fps"] = refresh_hz

    gfx_start = blob.find("GFXINFO_BEGIN")
    gfx_end = blob.find("GFXINFO_END")
    if gfx_start >= 0 and gfx_end > gfx_start:
        gfx_text = blob[gfx_start:gfx_end]
        gfx = parse_gfxinfo(gfx_text)
        for key, name in (
            ("p50_ms", "frame_p50_ms"),
            ("p90_ms", "frame_p90_ms"),
            ("p95_ms", "frame_p95_ms"),
            ("p99_ms", "frame_p99_ms"),
        ):
            if key in gfx:
                sample[name] = float(gfx[key])
        frames = gfx.get("frames")
        janky = gfx.get("janky")
        profile_fps = parse_profiledata_fps(gfx_text)
        app_fps: float | None = profile_fps
        if (
            app_fps is None
            and frames is not None
            and state.frames is not None
            and frames > state.frames
        ):
            dframes = frames - state.frames
            dt_uptime = 0.0
            if uptime is not None:
                baseline = prev_gfx_uptime if prev_gfx_uptime is not None else prev_uptime
                if baseline is not None:
                    dt_uptime = uptime - baseline
            dt_mono = (mono - prev_mono) if prev_mono is not None else 0.0
            dt = max(dt_uptime, dt_mono)
            if dt >= _MIN_FPS_DT:
                app_fps = dframes / dt
        if app_fps is not None:
            sample["app_fps"] = app_fps
            if "fps" not in sample:
                sample["fps"] = app_fps
        if (
            frames is not None
            and state.frames is not None
            and frames >= state.frames
        ):
            dframes = frames - state.frames
            if dframes > 0 and janky is not None and state.janky is not None:
                djanky = max(0, janky - state.janky)
                sample["jank_percent"] = 100.0 * djanky / dframes
            if state.missed_vsync is not None and "missed_vsync" in gfx:
                sample["missed_vsync"] = float(
                    max(0, gfx["missed_vsync"] - state.missed_vsync)
                )
            if state.slow_ui is not None and "slow_ui" in gfx:
                sample["slow_ui_frames"] = float(max(0, gfx["slow_ui"] - state.slow_ui))
            if state.deadline_missed is not None and "deadline_missed" in gfx:
                sample["deadline_missed"] = float(
                    max(0, gfx["deadline_missed"] - state.deadline_missed)
                )
        if frames is not None:
            state.frames = frames
        if janky is not None:
            state.janky = janky
        if "missed_vsync" in gfx:
            state.missed_vsync = gfx["missed_vsync"]
        if "slow_ui" in gfx:
            state.slow_ui = gfx["slow_ui"]
        if "deadline_missed" in gfx:
            state.deadline_missed = gfx["deadline_missed"]
        if uptime is not None:
            state.extras["gfx_uptime"] = uptime

    state.sample_mono = mono
    return sample
