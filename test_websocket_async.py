import sys
import types
import unittest
from unittest.mock import patch


fetchers_stub = sys.modules.setdefault("scrapling.fetchers", types.ModuleType("scrapling.fetchers"))
fetchers_stub.StealthyFetcher = object
fetchers_stub.StealthySession = object

import main


class AsyncExtractionContractTests(unittest.TestCase):
    def setUp(self):
        with main.async_tasks_lock:
            main.async_tasks.clear()
        with main.websocket_connections_lock:
            main.websocket_connections.clear()

    def test_extract_async_returns_request_id_and_queued_status(self):
        req = main.ExtractRequest(url="https://example.com/produto", escalation_level=2)

        with patch("main._enqueue_extraction", return_value="abc123") as enqueue:
            response = main.extract_async(req)

        self.assertEqual(response, {"request_id": "abc123", "status": "queued"})
        enqueue.assert_called_once_with(
            "https://example.com/produto",
            2,
            async_mode=True,
        )

    def test_task_events_include_started_and_completed_payload(self):
        main._create_async_task_record("abc123", "https://example.com/produto", 1)

        self.assertTrue(main._mark_async_task("abc123", "started", started_at=10.0, worker_id=0))
        self.assertTrue(
            main._mark_async_task(
                "abc123",
                "completed",
                images=["https://cdn.example.com/p.jpg"],
                completed_at=12.0,
                elapsed_seconds=2.0,
            )
        )

        with main.async_tasks_lock:
            payload = main._task_event_from_record(main.async_tasks["abc123"])

        self.assertEqual(payload["status"], "completed")
        self.assertEqual(payload["image_count"], 1)
        self.assertEqual(payload["images"], ["https://cdn.example.com/p.jpg"])
        self.assertEqual(payload["elapsed_seconds"], 2.0)

    def test_task_failed_payload_includes_error(self):
        main._create_async_task_record("abc123", "https://example.com/produto", 1)
        self.assertTrue(
            main._mark_async_task(
                "abc123",
                "failed",
                completed_at=12.0,
                elapsed_seconds=2.0,
                error="boom",
            )
        )

        with main.async_tasks_lock:
            payload = main._task_event_from_record(main.async_tasks["abc123"])

        self.assertEqual(payload["status"], "failed")
        self.assertEqual(payload["error"], "boom")

    def test_websocket_not_found_returns_event_and_closes(self):
        from fastapi.testclient import TestClient

        startup_handlers = list(main.app.router.on_startup)
        main.app.router.on_startup.clear()
        try:
            with TestClient(main.app) as client:
                with client.websocket_connect("/ws/extract/missing") as websocket:
                    payload = websocket.receive_json()
        finally:
            main.app.router.on_startup[:] = startup_handlers

        self.assertEqual(payload["event"], "not_found")
        self.assertEqual(payload["request_id"], "missing")
        self.assertEqual(payload["status"], "not_found")

    def test_websocket_late_completed_task_receives_final_state(self):
        from fastapi.testclient import TestClient

        main._create_async_task_record("abc123", "https://example.com/produto", 1)
        main._mark_async_task(
            "abc123",
            "completed",
            images=["https://cdn.example.com/p.jpg"],
            completed_at=12.0,
            elapsed_seconds=2.0,
        )

        startup_handlers = list(main.app.router.on_startup)
        main.app.router.on_startup.clear()
        try:
            with TestClient(main.app) as client:
                with client.websocket_connect("/ws/extract/abc123") as websocket:
                    payload = websocket.receive_json()
        finally:
            main.app.router.on_startup[:] = startup_handlers

        self.assertEqual(payload["status"], "completed")
        self.assertEqual(payload["images"], ["https://cdn.example.com/p.jpg"])


if __name__ == "__main__":
    unittest.main()
