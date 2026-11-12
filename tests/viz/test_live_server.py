"""F9 live server: a real simulation process behind a local HTTP server, driven as the page drives it."""

import json
import threading
import time
import urllib.error
import urllib.request

import pytest

from marketsim.experiments.config import RunConfig
from marketsim.sim.log_schema import load_run
from marketsim.viz.live import Hub, LiveServer, render_page

CONFIG = RunConfig(
    name="live_test", exchange_backend="python", graph="graph_chain", external="flat", use_market_index=True,
    n_sessions=40, warmup_sessions=1, sample_dt=0.05,
    agents={"valuers_myopic": 8, "valuers_network": 8, "trend": 4, "noise": 4, "n_funds": 2,
            "total_cash_cents": 6_000_000, "total_shares": 2_400},
)


@pytest.fixture
def server(tmp_path):
    hub = Hub(CONFIG, 3, tmp_path, speed=20)
    server = LiveServer(hub, port=0)
    hub.start()
    threading.Thread(target=server.serve_forever, daemon=True).start()
    assert hub.ready.wait(60), "the simulation process did not start"
    yield server
    server.closing = True
    server.shutdown()
    hub.close()


def post(server, path, body=None):
    request = urllib.request.Request(server.url.rstrip("/") + path, json.dumps(body or {}).encode(),
                                     {"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read())


def get(server, path):
    with urllib.request.urlopen(server.url.rstrip("/") + path, timeout=60) as response:
        return response.read()


def test_page_streams_frames_takes_controls_and_writes_the_log(server):
    hub = server.hub
    page = get(server, "/").decode()
    assert "/*REPLAY_STYLES*/" not in page and "--page:" in page and "EventSource" in page
    # Opening the event stream starts the run; its first message carries the static part and the frames so far.
    stream = urllib.request.urlopen(server.url + "events", timeout=60)
    first = json.loads(stream.readline().decode().removeprefix("data: "))
    assert first["type"] == "init"
    static = first["static"]
    assert [a["ticker"] for a in static["assets"]] and static["nodes"] and static["edges"]
    assert first["frames"][0]["t"] == 0
    stream.close()

    oil = post(server, "/api/control", {"kind": "shock_external", "params": {"node": "wti", "change": 0.3}})
    assert oil["control"]["kind"] == "shock_external" and oil["control"]["t"] == int(oil["control"]["t"]) >= 1
    herd = post(server, "/api/control", {"kind": "set_herding", "params": {"h": 0.5}})["control"]
    with pytest.raises(urllib.error.HTTPError) as bad:
        post(server, "/api/control", {"kind": "shock_valuation", "params": {"asset": "XXX", "change": -0.2}})
    assert bad.value.code == 400 and "not an asset" in json.loads(bad.value.read())["error"]
    post(server, "/api/pause")
    post(server, "/api/speed", {"value": 0})
    post(server, "/api/resume")
    deadline = time.monotonic() + 120
    while hub.frames[-1]["t"] <= oil["control"]["t"] and time.monotonic() < deadline:  # both controls applied
        time.sleep(0.05)
    post(server, "/api/stop")
    assert hub.ended.wait(120)

    state = json.loads(get(server, "/api/state"))
    assert state["status"]["done"] and state["status"]["log"]
    times = [f["t"] for f in state["frames"]]
    assert times == sorted(times) and len(times) > 20
    log = load_run(state["status"]["log"])
    assert sorted(log.meta["controls"], key=lambda c: c["kind"]) == [herd, oil["control"]]
    # At speed 0 a fast machine may reach the end before Stop arrives; either way the logged config ends there.
    assert log.meta["config"]["n_sessions"] == log.meta["live"]["stopped_at_session"] <= 40
    kinds = log["events"]["kind"].tolist()
    assert kinds.count("shock_external") == 1 and kinds.count("set_herding") == 1


def test_page_template_takes_the_replay_styles():
    page = render_page()
    assert "--node-up" in page and 'prefers-color-scheme: dark' in page
    assert "/api/control" in page and "shock_valuation" in page and "set_herding" in page


def raw(server, path, body, headers):
    request = urllib.request.Request(server.url.rstrip("/") + path, body, headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def test_other_sites_and_malformed_requests_are_refused(server):
    hub = server.hub
    queued = []
    original = hub.command
    hub.command = lambda command: (queued.append(command), original(command))[1]
    body = json.dumps({"kind": "set_herding", "params": {"h": 0.9}}).encode()
    host = server.url.split("//", 1)[1].rstrip("/")
    # A page on another site: simple text/plain request, or JSON with its own Origin.
    assert raw(server, "/api/control", body, {"Content-Type": "text/plain", "Origin": "http://evil.example"})[0] == 403
    assert raw(server, "/api/control", body, {"Content-Type": "text/plain"})[0] == 415
    assert raw(server, "/api/speed", b'{"value": 0}', {"Content-Type": "application/json",
                                                       "Origin": "http://evil.example"})[0] == 403
    assert raw(server, "/api/stop", b"{}", {"Content-Type": "application/json", "Host": "evil.example"})[0] == 403
    assert raw(server, "/api/control", b"x" * 5000, {"Content-Type": "application/json"})[0] == 413
    json_headers = {"Content-Type": "application/json", "Origin": f"http://{host}"}
    for bad in (b"[]", b'{"kind": "set_herding", "params": 4}', b'{"kind": "set_herding", "params": {"h": "x"}}',
                b'{"kind": "shock_external", "params": {"node": 1, "change": 0.1}}', b'"text"', b"{bad"):
        status, reply = raw(server, "/api/control", bad, json_headers)
        assert status == 400 and reply["error"], bad
    for bad in (b'{"value": "Infinity"}', b'{"value": Infinity}', b'{"value": 1e309}', b'{"value": -1}', b"{}"):
        assert raw(server, "/api/speed", bad, json_headers)[0] == 400, bad
    status, reply = raw(server, "/api/control", json.dumps(
        {"kind": "shock_external", "params": {"node": "wti", "change": 1e308}}).encode(), json_headers)
    assert status == 400 and "factor" in reply["error"]
    assert not queued, "nothing reached the simulation"
    state = json.loads(get(server, "/api/state"))  # still serializable
    assert state["status"]["error"] is None and not state["controls"]


def test_ctrl_c_in_the_terminal_writes_the_log(tmp_path):
    import os
    import signal
    import subprocess
    import sys

    import yaml

    config = tmp_path / "live.yaml"
    from dataclasses import asdict

    config.write_text(yaml.safe_dump({"base": asdict(CONFIG), "seeds": [3], "grid": {}}))
    # As in a terminal, Ctrl+C raises KeyboardInterrupt in the server (a test runner may start children with SIGINT
    # ignored); the simulation process it spawns gets the server's own protection only.
    launcher = ("import runpy, signal, sys; signal.signal(signal.SIGINT, signal.default_int_handler); "
                "sys.argv = ['live'] + sys.argv[1:]; runpy.run_module('marketsim.viz.live', run_name='__main__')")
    process = subprocess.Popen(
        [sys.executable, "-c", launcher, "--config", str(config), "--port", "0", "--no-browser",
         "--out", str(tmp_path / "out"), "--sessions", "6"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, start_new_session=True)
    try:
        lines = []
        for line in process.stdout:  # wait until the server and the simulation process are up
            lines.append(line)
            if "live: open" in line:
                break
        time.sleep(3)
        os.killpg(process.pid, signal.SIGINT)  # what Ctrl+C does: the whole process group
        out, _ = process.communicate(timeout=120)
    finally:
        if process.poll() is None:
            process.kill()
    text = "".join(lines) + out
    assert "run log written to" in text, text
    logs = list((tmp_path / "out").iterdir())
    assert len(logs) == 1 and load_run(logs[0]).meta["live"]["stopped_at_session"] >= 1
