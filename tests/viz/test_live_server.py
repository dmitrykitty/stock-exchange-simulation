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
    assert log.meta["config"]["n_sessions"] == log.meta["live"]["stopped_at_session"] < 40
    kinds = log["events"]["kind"].tolist()
    assert kinds.count("shock_external") == 1 and kinds.count("set_herding") == 1


def test_page_template_takes_the_replay_styles():
    page = render_page()
    assert "--node-up" in page and 'prefers-color-scheme: dark' in page
    assert "/api/control" in page and "shock_valuation" in page and "set_herding" in page
