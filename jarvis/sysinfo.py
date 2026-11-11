"""System telemetry for the HUD and the diagnostics ability."""
import os
import socket
import time

import psutil

from . import plat

_last_net = None


def snapshot() -> dict:
    global _last_net
    now = time.time()
    net = psutil.net_io_counters()
    up = down = 0.0
    if _last_net:
        dt = max(now - _last_net[0], 1e-3)
        up = (net.bytes_sent - _last_net[1].bytes_sent) / dt
        down = (net.bytes_recv - _last_net[1].bytes_recv) / dt
    _last_net = (now, net)
    battery = psutil.sensors_battery()
    vm = psutil.virtual_memory()
    disk = psutil.disk_usage(os.path.abspath(os.sep))  # "/" or the Windows system drive
    return {
        "cpu": psutil.cpu_percent(interval=None),
        "cores": psutil.cpu_percent(interval=None, percpu=True),
        "mem": vm.percent,
        "mem_used_gb": round(vm.used / 2**30, 1),
        "mem_total_gb": round(vm.total / 2**30, 1),
        "disk": disk.percent,
        "disk_free_gb": round(disk.free / 2**30, 1),
        "battery": None if battery is None else round(battery.percent),
        "charging": None if battery is None else bool(battery.power_plugged),
        "net_up_kbs": round(up / 1024, 1),
        "net_down_kbs": round(down / 1024, 1),
        "uptime_h": round((now - psutil.boot_time()) / 3600, 1),
    }


_names: tuple[float, set] = (0.0, set())


def recent_process_names(max_age: float) -> set | None:
    """Names of running processes from the last top_processes() call, if it was recent."""
    at, names = _names
    return names if names and time.time() - at <= max_age else None


def top_processes(n: int = 4) -> list[dict]:
    """Busiest processes since the previous call (psutil keeps per-process CPU counters between calls)."""
    global _names
    me = psutil.Process().pid
    procs, names = [], set()
    for p in psutil.process_iter(["pid", "name", "cpu_percent", "memory_percent"]):
        i = p.info
        if i["name"]:
            names.add(i["name"])
        if i["pid"] in (0, me) or not i["name"]:
            continue
        procs.append({"name": i["name"], "cpu": round(i["cpu_percent"] or 0, 1), "mem": round(i["memory_percent"] or 0, 1)})
    _names = (time.time(), names)
    return sorted(procs, key=lambda x: x["cpu"], reverse=True)[:n]


def diagnostics() -> dict:
    snap = snapshot()
    for p in psutil.process_iter(["cpu_percent"]):  # prime per-process CPU counters
        pass
    time.sleep(0.4)
    procs = []
    for p in psutil.process_iter(["pid", "name", "cpu_percent", "memory_percent"]):
        procs.append(p.info)
    top_cpu = sorted(procs, key=lambda p: p.get("cpu_percent") or 0, reverse=True)[:6]
    top_mem = sorted(procs, key=lambda p: p.get("memory_percent") or 0, reverse=True)[:6]
    snap.update({
        "host": socket.gethostname(),
        "os": plat.description(),
        "load_avg": [round(x, 2) for x in psutil.getloadavg()],
        "top_cpu": [{"name": p["name"], "pid": p["pid"], "cpu": round(p.get("cpu_percent") or 0, 1)} for p in top_cpu],
        "top_mem": [{"name": p["name"], "pid": p["pid"], "mem": round(p.get("memory_percent") or 0, 1)} for p in top_mem],
    })
    return snap
