"""Tests of the server agent: the schema-2 snapshot, a contract with the ops repository (README
"Agent contract" there), the order of its containers and the answers of its HTTP server.

Docker is a stub HTTP server and /proc a directory of fixture files, so nothing of the machine
is read. Standard library only, like the agent; from infra/agent, with Python 3.13 on Linux:
    python -m unittest -v
"""

import json
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import NoneType, SimpleNamespace
from unittest import mock

import agent

NOW = 1_790_400_000  # 2026-09-26T05:20:00Z


def container(project, service, version=None):
    """A container as the Docker API lists it, labelled by compose."""
    labels = {"com.docker.compose.project": project, "com.docker.compose.service": service}
    if version:
        labels["candlestack.version"] = version
    return {
        "Id": f"{project}-{service}".encode().hex(),
        "Names": [f"/{project}-{service}-1"],
        "State": "running",
        "Status": "Up 2 hours (healthy)",
        "Created": NOW - 7300,
        "Labels": labels,
    }


# In no particular order, as Docker lists them; the last one was not started by compose.
CONTAINERS = [
    container("agent", "socket-proxy"),
    container("prod", "redis"),
    container("stage", "backend", "main-0123abc"),
    container("edge", "caddy"),
    container("prod", "backend", "v0.2.0"),
    container("agent", "agent"),
    container("prod", "frontend", "v0.2.0"),
    {
        "Id": "c0ffee",
        "Names": ["/adhoc"],
        "State": "exited",
        "Status": "Exited (0) 2 days ago",
        "Created": NOW - 200_000,
        "Labels": {},
    },
]

stats_calls = Counter()  # per container id


class FakeDocker(BaseHTTPRequestHandler):
    """The read-only Docker API the agent uses: the container list, each container's start
    time and its one-shot stats, whose CPU counters grow by the same amount on every call."""

    def do_GET(self):
        parts = self.path.split("?")[0].strip("/").split("/")
        if parts == ["containers", "json"]:
            body = CONTAINERS
        elif len(parts) == 3 and parts[2] == "json":
            body = {"State": {"StartedAt": "2026-09-26T03:20:00.123456789Z"}}
        elif len(parts) == 3 and parts[2] == "stats":
            stats_calls[parts[1]] += 1
            calls = stats_calls[parts[1]]
            body = {
                "cpu_stats": {
                    "cpu_usage": {"total_usage": calls * 1_234_567_891},
                    "system_cpu_usage": calls * 10_000_000_000,
                    "online_cpus": 2,
                },
                "memory_stats": {"usage": 60_000_000, "stats": {"inactive_file": 10_000_000}},
            }
        else:
            self.send_error(404)
            return
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


class Server(ThreadingHTTPServer):
    # The agent's pool connects 8 at a time; the default backlog of 5 would drop some.
    request_queue_size = 32


class AgentTest(unittest.TestCase):
    def setUp(self):
        stats_calls.clear()
        docker_url = self.serve(FakeDocker)
        self.proc = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.write_stat(user=1000, idle=9000)
        (self.proc / "meminfo").write_text(
            "MemTotal:        1957236 kB\nMemFree:          200000 kB\n"
            "MemAvailable:     858856 kB\nBuffers:           10000 kB\n"
        )
        (self.proc / "uptime").write_text("71018.70 140000.00\n")
        (self.proc / "loadavg").write_text("0.25 0.20 0.15 1/180 4242\n")
        self.clock = [NOW]

        def path(name):  # /proc/<file> is read from the fixture directory
            return self.proc / Path(name).relative_to("/proc")

        for name, value in {
            "DOCKER_URL": docker_url,
            "VM_LABEL": "AWS t3.medium · eu-north-1",
            "Path": path,
            "time": SimpleNamespace(time=lambda: self.clock[0], monotonic=time.monotonic),
            "_prev_cpu": {},
            "_prev_host_cpu": None,
            "LATEST": None,
        }.items():
            self.enterContext(mock.patch.object(agent, name, value))
        disk = SimpleNamespace(f_blocks=6_303_968, f_bfree=5_269_346, f_frsize=4096)
        self.enterContext(mock.patch.object(agent.os, "statvfs", return_value=disk))
        self.enterContext(mock.patch.object(agent.os, "cpu_count", return_value=2))

    def serve(self, handler):
        """Serves ``handler`` on a free local port until the test ends; returns its URL."""
        server = Server(("127.0.0.1", 0), handler)
        threading.Thread(target=server.serve_forever, args=(0.01,), daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return f"http://127.0.0.1:{server.server_address[1]}"

    def write_stat(self, user, idle):
        """/proc/stat with these user and idle jiffies, the other fields fixed."""
        (self.proc / "stat").write_text(f"cpu  {user} 0 500 {idle} 100 0 50 0 0 0\ncpu0 1 2 3\n")

    def next_sample(self):
        """A sample one interval later, with 600 of 933 more jiffies busy."""
        self.write_stat(user=1600, idle=9333)
        self.clock[0] += agent.INTERVAL
        return agent.sample()

    def assert_shape(self, value, types):
        """``value`` has exactly the keys of ``types``, in that order, each of its type or
        types (exact types: a bool is no int, an int no float)."""
        self.assertEqual(list(value), list(types))
        for key, expected in types.items():
            kinds = expected if isinstance(expected, tuple) else (expected,)
            self.assertIn(type(value[key]), kinds, f"{key}: {value[key]!r}")

    def test_snapshot_has_the_keys_and_types_of_schema_2(self):
        first = agent.sample()  # no CPU shares yet: they need two samples
        second = self.next_sample()

        for snapshot in first, second:
            self.assert_shape(
                snapshot,
                {
                    "schema": int,
                    "sampled_at": int,
                    "host": dict,
                    "containers": list,
                },
            )
            self.assertEqual(snapshot["schema"], 2)
            self.assert_shape(
                snapshot["host"],
                {
                    "label": str,
                    "cpus": int,
                    "uptime": float,
                    "cpu": (float, NoneType),
                    "load": float,
                    "mem_used": int,
                    "mem_total": int,
                    "disk_used": int,
                    "disk_total": int,
                },
            )
            for row in snapshot["containers"]:
                self.assert_shape(
                    row,
                    {
                        "name": str,
                        "env": str,
                        "state": str,
                        "up": str,
                        "cpu": (float, NoneType),
                        "mem": (int, NoneType),
                        "created": (int, NoneType),
                        "started": (int, NoneType),
                        "version": (str, NoneType),
                    },
                )
        self.assertEqual((first["host"]["cpu"], type(second["host"]["cpu"])), (None, float))

    def test_snapshot_values(self):
        agent.sample()
        snapshot = self.next_sample()

        self.assertEqual(snapshot["sampled_at"], NOW + agent.INTERVAL)
        self.assertEqual(
            snapshot["host"],
            {
                "label": "AWS t3.medium · eu-north-1",
                "cpus": 2,
                "uptime": 71018.7,
                "cpu": 64.3,  # 600 of 933 jiffies busy, rounded
                "load": 0.25,
                "mem_used": (1957236 - 858856) * 1024,
                "mem_total": 1957236 * 1024,
                "disk_used": (6_303_968 - 5_269_346) * 4096,
                "disk_total": 6_303_968 * 4096,
            },
        )
        rows = {(row["env"], row["name"]): row for row in snapshot["containers"]}
        self.assertEqual(
            rows["prod", "backend"],
            {
                "name": "backend",
                "env": "prod",
                "state": "running",
                "up": "2 hours (healthy)",
                "cpu": 24.7,  # 12.35% of the system's time, on 2 CPUs, rounded
                "mem": 50_000_000,  # without the inactive page cache
                "created": NOW - 7300,
                "started": NOW - 7200,  # 2026-09-26T03:20:00Z
                "version": "v0.2.0",
            },
        )
        self.assertEqual(
            rows["", "adhoc"],
            {
                "name": "adhoc",
                "env": "",
                "state": "exited",
                "up": "Exited (0) 2 days ago",
                "cpu": 24.7,
                "mem": 50_000_000,
                "created": NOW - 200_000,
                "started": NOW - 7200,
                "version": None,
            },
        )

    def test_containers_are_ordered_environments_then_services(self):
        snapshot = agent.sample()

        self.assertEqual(
            [(row["env"], row["name"]) for row in snapshot["containers"]],
            [
                ("prod", "backend"),
                ("prod", "frontend"),
                ("prod", "redis"),
                ("stage", "backend"),
                ("edge", "caddy"),
                ("agent", "agent"),
                ("agent", "socket-proxy"),
                ("", "adhoc"),
            ],
        )

    def test_http_answers_starting_then_the_snapshot_then_stale(self):
        base = self.serve(agent.Handler)

        def get(path):
            """Status and body; the headers of the last answer in ``headers``."""
            nonlocal headers
            try:
                with urllib.request.urlopen(base + path, timeout=5) as response:
                    headers = response.headers
                    return response.status, response.read()
            except urllib.error.HTTPError as error:
                with error:
                    headers = error.headers
                    return error.code, error.read()

        headers = None
        self.assertEqual(get("/api/snapshot"), (503, b'{"status":"starting"}'))
        self.assertEqual(get("/api/health"), (503, b'{"status":"stale"}'))

        agent.LATEST = agent.sample()
        snapshot = json.dumps(agent.LATEST, separators=(",", ":")).encode()
        self.assertEqual(get("/api/snapshot?from=ops"), (200, snapshot))
        self.assertEqual(
            (headers["Content-Type"], headers["Cache-Control"]), ("application/json", "no-store")
        )
        self.assertEqual(get("/api/health"), (200, b'{"status":"ok"}'))

        self.clock[0] = NOW + 3 * agent.INTERVAL  # as old as a sample may be
        self.assertEqual(get("/api/health"), (200, b'{"status":"ok"}'))
        self.clock[0] += 1
        self.assertEqual(get("/api/health"), (503, b'{"status":"stale"}'))
        # The last sample is still served; ops shows it as stale data.
        self.assertEqual(get("/api/snapshot"), (200, snapshot))
        self.assertEqual(get("/metrics"), (404, b'{"status":"not found"}'))


if __name__ == "__main__":
    unittest.main()
