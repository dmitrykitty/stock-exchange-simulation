"""Live mode of the F9 page: the network market runs now, in a separate process, and the page changes it.

    python -m marketsim.viz.live                                  # configs/f9_live.yaml, seed 101
    python -m marketsim.viz.live --config configs/f6_e5_short.yaml --seed 3006 --speed 2

The simulation process (``marketsim.sim.live.LiveSession``) takes one sampling step (``sample_dt``) at a time and
sends a frame after each: prices, best quotes, R, mean valuations, external levels, new trades. A local web
server (standard library only) streams the frames to the page as server-sent events and passes the buttons
back: an external shock (oil +-30%), a valuation shock to one company and a new herding strength h, which are
control events of the simulation written to its ``events`` table, and pause, resume and display speed, which
only decide how fast steps are taken and never change the result. At the configured end, or on Stop, the run
log is written to ``runs/live/<run_id>``; ``python -m marketsim.viz.replay`` turns it into a replay page and
``marketsim.sim.live.run_with_controls`` reproduces it from ``meta.controls``.
"""

from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import queue
import threading
import time
import webbrowser
from dataclasses import asdict, replace
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from importlib.util import find_spec
from itertools import count
from pathlib import Path

from marketsim import paths
from marketsim.experiments.config import ConfigError, RunConfig, load_config
from marketsim.viz import replay

DEFAULT_CONFIG = paths.PROJECT_ROOT / "configs" / "f9_live.yaml"
# Display speeds in sessions per second; 0 = as fast as the simulation goes.
SPEEDS = (0.25, 0.5, 1, 2, 5, 10, 0)


# ----- simulation process -------------------------------------------------------------------------------------
def _worker(config: RunConfig, seed: int, out_dir: str, speed: float, commands: mp.Queue, out: mp.Queue) -> None:
    """Runs in its own process: steps the simulation, paced by the wall clock, and obeys the page's commands."""
    from marketsim.sim.live import LiveSession  # imported here: the server process never runs the simulation

    try:
        session = LiveSession(config, seed)
    except Exception as exc:  # noqa: BLE001 - reported to the page and the terminal
        out.put({"type": "error", "message": f"{type(exc).__name__}: {exc}"})
        return
    sim = session.sim
    hs = sim.settings.herding
    out.put({
        "type": "ready", "assets": session.assets, "start": sim.start_ticks, "externals": session.external_names,
        "n_sessions": config.n_sessions, "sample_dt": config.sample_dt, "seed": seed,
        "config": asdict(config), "investors": sim.investors, "followers": len(sim.herd_followers),
        "h": hs.h, "h_std": hs.h_std, "frame": session.frame(),
    })
    paused, interval, next_at = True, 0.0, time.perf_counter()

    def set_speed(value: float) -> float:
        return config.sample_dt / value if value > 0 else 0.0

    interval = set_speed(speed)

    def close() -> None:
        session.stop()
        out.put({"type": "frame", "frame": session.frame()})
        try:
            path = session.write(out_dir)
            out.put({"type": "end", "path": str(path), "session": session.end, "controls": sim.applied_controls})
        except Exception as exc:  # noqa: BLE001
            out.put({"type": "error", "message": f"could not write the log: {exc}"})

    while True:
        running = not paused and not session.done
        wait = max(0.0, next_at - time.perf_counter()) if running else 0.5
        try:
            command = commands.get(timeout=wait) if wait > 0 else commands.get_nowait()
        except queue.Empty:
            command = None
        if command is not None:
            name = command.get("cmd")
            if name == "quit":
                break
            if name == "pause":
                paused = True
            elif name == "resume":
                paused, next_at = False, time.perf_counter()
            elif name == "speed":
                interval = set_speed(float(command["value"]))
                next_at = time.perf_counter()
            elif name == "control":
                try:
                    control = session.control(command["kind"], **command.get("params", {}))
                    out.put({"type": "control", "id": command.get("id"), "control": control})
                except (ConfigError, TypeError) as exc:
                    out.put({"type": "control_error", "id": command.get("id"), "message": str(exc)})
            elif name == "stop" and not session.done:
                close()
            out.put({"type": "status", "paused": paused, "done": session.done})
            continue
        if not running:
            continue
        session.step()
        out.put({"type": "frame", "frame": session.frame()})
        if session.done:
            close()
            out.put({"type": "status", "paused": paused, "done": True})
        next_at = max(next_at + interval, time.perf_counter()) if interval else time.perf_counter()


# ----- hub in the server process ------------------------------------------------------------------------------
class Hub:
    """Owns the simulation process, keeps the frames seen so far and fans them out to every open page."""

    def __init__(self, config: RunConfig, seed: int, out_dir: Path, speed: float = 1.0, autostart: bool = True):
        self.config, self.seed, self.speed, self.autostart = config, seed, speed, autostart
        context = mp.get_context("spawn")
        self._commands, self._out = context.Queue(), context.Queue()
        self._process = context.Process(
            target=_worker, args=(config, seed, str(out_dir), speed, self._commands, self._out), daemon=True)
        self._lock = threading.Lock()
        self._clients: list[queue.Queue] = []
        self._replies: dict[int, tuple[threading.Event, list]] = {}
        self._ids = count(1)
        self.static: dict | None = None
        self.frames: list[dict] = []
        self.controls: list[dict] = []
        self.status = {"paused": True, "done": False, "speed": speed, "log": None, "error": None}
        self.ready = threading.Event()
        self.ended = threading.Event()
        self._started_once = False

    def start(self) -> None:
        self._process.start()
        threading.Thread(target=self._read, daemon=True).start()

    def close(self) -> None:
        self._commands.put({"cmd": "quit"})
        self._process.join(timeout=5)
        if self._process.is_alive():
            self._process.terminate()

    # messages from the simulation
    def _read(self) -> None:
        while True:
            try:
                message = self._out.get(timeout=1.0)
            except queue.Empty:
                if not self._process.is_alive() and not self.ended.is_set():
                    self._on_error("the simulation process stopped")
                    return
                continue
            except (EOFError, OSError):
                return
            kind = message["type"]
            with self._lock:
                if kind == "ready":
                    self.static = self._static(message)
                    self.frames.append(message["frame"])
                elif kind == "frame":
                    self.frames.append(message["frame"])
                elif kind == "control":
                    self.controls.append(message["control"])
                elif kind == "status":
                    self.status.update(paused=message["paused"], done=message["done"])
                elif kind == "end":
                    self.status.update(done=True, log=message["path"])
                elif kind == "error":
                    self.status["error"] = message["message"]
                if kind in ("control", "control_error") and message.get("id") in self._replies:
                    event, box = self._replies.pop(message["id"])
                    box.append(message)
                    event.set()
                outgoing = {k: v for k, v in message.items() if k != "id"}
                if kind == "ready":
                    outgoing = {"type": "init", **self.snapshot()}
                if kind != "control_error":
                    for client in self._clients:
                        client.put(outgoing)
            if kind == "ready":
                self.ready.set()
                if self._clients and self.autostart:
                    self._resume_once()
            elif kind == "end":
                self.ended.set()
            elif kind == "error":
                self._on_error(message["message"])

    def _on_error(self, text: str) -> None:
        with self._lock:
            self.status["error"] = text
            for client in self._clients:
                client.put({"type": "error", "message": text})
        print(f"live: {text}", flush=True)
        self.ready.set()
        self.ended.set()

    def _static(self, ready: dict) -> dict:
        """Everything the page needs once: graph layout, names, sectors, labels, start prices, settings."""
        assets, externals = ready["assets"], ready["externals"]
        graph = replay.load_graph(self.config.graph)
        if assets != graph.companies():
            from marketsim.sim.api import _subgraph  # the same subset the simulation trades

            graph = _subgraph(graph, tuple(assets))
        nodes, edges = replay.graph_elements(graph)
        try:
            names = replay.data_api.load_companies().set_index("ticker")["name"].to_dict()
        except FileNotFoundError:
            names = {}
        agents = ready["config"].get("agents", {})
        return {
            "graph": self.config.graph, "scenario": self.config.external, "name": self.config.name,
            "seed": ready["seed"], "backend": self.config.exchange_backend, "n_sessions": ready["n_sessions"],
            "sample_dt": ready["sample_dt"], "investors": ready["investors"], "followers": ready["followers"],
            "composition": agents.get("composition", "custom"), "h": ready["h"], "h_std": ready["h_std"],
            "assets": [{"ticker": t, "name": names.get(t, t), "sector": replay.SECTORS.get(t, ""), "start": s}
                       for t, s in zip(assets, ready["start"])],
            "externals": {"names": externals, "labels": [replay.EXTERNAL_LABELS.get(n, n) for n in externals]},
            "nodes": nodes, "edges": edges, "speeds": list(SPEEDS),
        }

    def snapshot(self) -> dict:
        return {"static": self.static, "frames": list(self.frames), "controls": list(self.controls),
                "status": dict(self.status)}

    # page side
    def subscribe(self) -> tuple[queue.Queue, dict | None]:
        client: queue.Queue = queue.Queue()
        with self._lock:
            self._clients.append(client)
            init = {"type": "init", **self.snapshot()} if self.static else None
            if init is None and self.status["error"]:
                init = {"type": "error", "message": self.status["error"]}
        if init and init["type"] == "init" and self.autostart:
            self._resume_once()
        return client, init

    def unsubscribe(self, client: queue.Queue) -> None:
        with self._lock:
            if client in self._clients:
                self._clients.remove(client)

    def _resume_once(self) -> None:
        # The run starts when the first page is open, so its beginning is not spent before anyone watches.
        with self._lock:
            if self._started_once:
                return
            self._started_once = True
        self.command({"cmd": "resume"})

    def command(self, command: dict) -> None:
        if command.get("cmd") == "speed":
            self.status["speed"] = float(command["value"])
        if command.get("cmd") == "resume":
            self._started_once = True
        self._commands.put(command)

    def control(self, kind: str, params: dict, timeout: float = 30.0) -> dict:
        """Schedule a control in the simulation and wait for the time it got (or its error)."""
        request_id = next(self._ids)
        event, box = threading.Event(), []
        with self._lock:
            self._replies[request_id] = (event, box)
        self._commands.put({"cmd": "control", "id": request_id, "kind": kind, "params": params})
        if not event.wait(timeout):
            with self._lock:
                self._replies.pop(request_id, None)
            return {"type": "control_error", "message": "the simulation did not answer"}
        return box[0]


# ----- web server ---------------------------------------------------------------------------------------------
def render_page() -> str:
    """The live page: its own template with the replay page's styles."""
    files = resources.files("marketsim.viz")
    styles = files.joinpath("replay_template.html").read_text(encoding="utf-8")
    styles = styles.split("<style>", 1)[1].split("</style>", 1)[0]
    template = files.joinpath("live_template.html").read_text(encoding="utf-8")
    return template.replace("/*REPLAY_STYLES*/", styles)


class _Handler(BaseHTTPRequestHandler):
    server: LiveServer

    def log_message(self, format, *args) -> None:  # quiet: the terminal shows the run, not requests
        pass

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, data: dict, status: int = 200) -> None:
        self._send(status, json.dumps(data, allow_nan=False).encode(), "application/json")

    def do_GET(self) -> None:
        hub = self.server.hub
        route = self.path.split("?", 1)[0]
        if route == "/":
            self._send(200, self.server.page, "text/html; charset=utf-8")
        elif route == "/plotly.js":
            self._send(200, self.server.plotly(), "application/javascript; charset=utf-8")
        elif route == "/api/state":
            with hub._lock:
                self._json(hub.snapshot())
        elif route == "/events":
            self._stream(hub)
        else:
            self._send(404, b"not found", "text/plain")

    def _stream(self, hub: Hub) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        client, init = hub.subscribe()
        try:
            if init:
                self._event(init)
            while not self.server.closing:
                try:
                    self._event(client.get(timeout=10))
                except queue.Empty:
                    self.wfile.write(b": keep-alive\n\n")
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            hub.unsubscribe(client)

    def _event(self, message: dict) -> None:
        self.wfile.write(b"data: " + json.dumps(message, allow_nan=False, separators=(",", ":")).encode() + b"\n\n")
        self.wfile.flush()

    def do_POST(self) -> None:
        hub = self.server.hub
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError):
            self._json({"error": "body must be JSON"}, HTTPStatus.BAD_REQUEST)
            return
        route = self.path.split("?", 1)[0]
        if route == "/api/control":
            reply = hub.control(str(body.get("kind")), dict(body.get("params") or {}))
            if reply["type"] == "control":
                self._json({"control": reply["control"]})
            else:
                self._json({"error": reply["message"]}, HTTPStatus.BAD_REQUEST)
        elif route in ("/api/pause", "/api/resume", "/api/stop"):
            hub.command({"cmd": route.rsplit("/", 1)[1]})
            self._json({"ok": True})
        elif route == "/api/speed":
            try:
                value = float(body["value"])
            except (KeyError, TypeError, ValueError):
                self._json({"error": "speed needs a number"}, HTTPStatus.BAD_REQUEST)
                return
            hub.command({"cmd": "speed", "value": max(0.0, value)})
            self._json({"ok": True})
        else:
            self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)


class LiveServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, hub: Hub, host: str = "127.0.0.1", port: int = 8765) -> None:
        super().__init__((host, port), _Handler)
        self.hub = hub
        self.page = render_page().encode()
        self.closing = False
        self._plotly: bytes | None = None

    def plotly(self) -> bytes:
        if self._plotly is None:
            from plotly.offline import get_plotlyjs  # optional dependency: pip install -e ".[viz]"

            self._plotly = get_plotlyjs().encode()
        return self._plotly

    @property
    def url(self) -> str:
        host, port = self.server_address[:2]
        return f"http://{host}:{port}/"


def _backend(config: RunConfig, requested: str | None) -> RunConfig:
    backend = requested or config.exchange_backend
    if backend == "cpp" and find_spec("lob_core") is None:
        if requested == "cpp":
            raise SystemExit("--backend cpp: lob_core is not built (pip install -e cpp/)")
        print("live: lob_core is not built, using the Python exchange (slower, same results)", flush=True)
        backend = "python"
    return replace(config, exchange_backend=backend)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run the network market live and change it from a web page.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="experiment YAML (its base config is run)")
    parser.add_argument("--seed", type=int, default=None, help="run seed (default: the first seed of the config)")
    parser.add_argument("--sessions", type=int, default=None, help="number of sessions (default: from the config)")
    parser.add_argument("--backend", choices=("python", "cpp"), default=None, help="exchange backend")
    parser.add_argument("--speed", type=float, default=1.0, help="display speed, sessions per second (0 = max)")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--out", default=None, help="where the run log goes (default: runs/live)")
    parser.add_argument("--no-browser", action="store_true", help="do not open the page in a browser")
    args = parser.parse_args(argv)

    experiment = load_config(args.config)
    config = _backend(experiment.base, args.backend)
    if args.sessions:
        config = replace(config, n_sessions=args.sessions,
                         warmup_sessions=min(config.warmup_sessions, args.sessions - 1)).validate()
    seed = experiment.seeds[0] if args.seed is None else args.seed
    out_dir = Path(args.out) if args.out else paths.runs_dir() / "live"
    hub = Hub(config, seed, out_dir, speed=args.speed)
    server = LiveServer(hub, port=args.port)
    hub.start()
    print(f"live: {config.name} on {config.graph}, seed {seed}, {config.n_sessions} sessions, "
          f"{config.exchange_backend} exchange", flush=True)
    print(f"live: open {server.url}  (Stop on the page or Ctrl+C here writes the run log)", flush=True)
    if not args.no_browser:
        threading.Timer(0.5, lambda: webbrowser.open(server.url)).start()
    try:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        while not hub.ended.wait(0.5):
            pass
        if hub.status.get("log"):
            print(f"live: run log written to {hub.status['log']}")
        print("live: the run is over; the page stays open until Ctrl+C")
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        if not hub.ended.is_set() and hub.ready.is_set():
            print("\nlive: finishing the current session and writing the log ...")
            hub.command({"cmd": "stop"})
            hub.ended.wait(120)
            if hub.status.get("log"):
                print(f"live: run log written to {hub.status['log']}")
    finally:
        server.closing = True
        server.shutdown()
        hub.close()


if __name__ == "__main__":
    main()
