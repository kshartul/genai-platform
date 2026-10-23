import os

from fastapi.testclient import TestClient

from genai_platform.cli import main
from genai_platform.evals import gate


def test_eval_gate_logic():
    assert gate({"faithfulness": 0.9, "latency_p95_ms": 900}, {"faithfulness": 0.85, "latency_p95_ms": 2500}) == []
    fails = gate({"faithfulness": 0.7, "latency_p95_ms": 3000}, {"faithfulness": 0.85, "latency_p95_ms": 2500,
                                                                  "hit_rate@5": 0.8})
    assert len(fails) == 3


def test_cli_eval_gate_passes_on_local_profile(tmp_path):
    assert main(["eval", "--report", str(tmp_path / "eval.json")]) == 0


def test_api_end_to_end():
    os.environ["GENAI_PROFILE"] = "profiles/knowledge-assistant.local.yaml"
    from services.api.main import app

    c = TestClient(app)
    assert c.get("/healthz").json()["chunks"] > 0
    r = c.post("/answer", json={"question": "How long are customer transaction records retained?"}).json()
    assert "8 years" in r["answer"] and r["citations"]
    hits = c.post("/retrieve", json={"query": "SEV1 acknowledge"}, headers={"X-Groups": "engineering"}).json()
    assert any("incident-runbook" in h["source"] for h in hits)
    assert c.post("/ingest", json={}).status_code == 403
    run = c.post("/agent/run", json={"question": "Open a ticket for a broken laptop"},
                 headers={"X-Scopes": "kb.read,tickets.write", "X-User": "asha"}).json()
    assert run["status"] == "awaiting_approval"
    done = c.post(f"/agent/{run['thread_id']}/approve", json={"approved": True}).json()
    assert done["observations"][0]["result"]["requested_by"] == "asha"
