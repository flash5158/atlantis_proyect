# ruff: noqa: E701
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from nexo.atlantis_hub import create_app


class AtlantisHubTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "workspace"
        self.root.mkdir()
        self.client = TestClient(create_app(self.root, Path(self.tmp.name) / "data", "owner-secret"))
        self.auth = {"Authorization": "Bearer owner-secret"}

    def tearDown(self):
        self.tmp.cleanup()

    def test_auth_idempotency_and_revision_conflict(self):
        self.assertEqual(self.client.get("/v2/state").status_code, 401)
        body = {"client_id": "message-1", "channel": "team", "text": "hola"}
        first = self.client.post("/v2/messages", headers=self.auth, json=body).json()
        duplicate = self.client.post("/v2/messages", headers=self.auth, json=body).json()
        self.assertEqual(first["id"], duplicate["id"])
        saved = self.client.post("/v2/files/write", headers=self.auth, json={"path": "src/a.txt", "text": "uno"}).json()
        self.assertEqual(
            self.client.post(
                "/v2/files/write",
                headers=self.auth,
                json={"path": "src/a.txt", "text": "dos", "expected_revision": "stale"},
            ).status_code,
            409,
        )
        updated = self.client.post(
            "/v2/files/write",
            headers=self.auth,
            json={"path": "src/a.txt", "text": "dos", "expected_revision": saved["revision"]},
        )
        self.assertEqual(updated.status_code, 200)

    def test_paths_are_contained_and_invites_create_identity(self):
        self.assertEqual(
            self.client.get("/v2/files/read", headers=self.auth, params={"path": "../outside"}).status_code, 403
        )
        self.assertEqual(
            self.client.post("/v2/files/write", headers=self.auth, json={"path": ".env", "text": "x"}).status_code, 403
        )
        invite = self.client.post("/v2/invites", headers=self.auth, json={"name": "Amigo", "role": "worker"}).json()
        self.assertEqual(invite["member"]["role"], "worker")
        worker_auth = {"Authorization": "Bearer " + invite["token"]}
        state = self.client.get("/v2/state", headers=worker_auth).json()
        self.assertEqual(state["self"]["name"], "Amigo")

    def test_worker_task_claim_and_event_replay(self):
        invite = self.client.post("/v2/invites", headers=self.auth, json={"name": "Hermes", "role": "worker"}).json()
        worker = {"Authorization": "Bearer " + invite["token"]}
        task = self.client.post(
            "/v2/tasks",
            headers=self.auth,
            json={
                "client_id": "t1",
                "title": "test",
                "description": "run",
                "assignee_id": invite["member"]["id"],
                "paths": [],
            },
        ).json()
        claimed = self.client.post("/v2/worker/claim", headers=worker).json()["task"]
        self.assertEqual(claimed["id"], task["id"])
        done = self.client.post(
            f"/v2/worker/tasks/{task['id']}/complete",
            headers=worker,
            json={"run_id": claimed["lease_id"], "status": "completed", "summary": "ok", "exit_code": 0},
        ).json()
        self.assertTrue(done["ok"])
        events = self.client.app.state.hub.events_since(0)
        self.assertGreaterEqual(len(events), 3)
        self.assertEqual([e["seq"] for e in events], sorted(e["seq"] for e in events))


if __name__ == "__main__":
    unittest.main()
