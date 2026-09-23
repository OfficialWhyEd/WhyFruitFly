from __future__ import annotations

import unittest

from fastapi.testclient import TestClient

from fruitfly_lab.server import create_app


class ServerTests(unittest.TestCase):
    def test_join_sets_cookie_and_status_requires_it(self) -> None:
        app = create_app(start_worker=False, session_token="test-token")
        with TestClient(app) as client:
            self.assertEqual(client.get("/api/status").status_code, 401)
            response = client.get("/join/test-token", follow_redirects=False)
            self.assertEqual(response.status_code, 303)
            self.assertIn("fruitfly_session", response.cookies)
            client.cookies.update(response.cookies)
            status_response = client.get("/api/status")
            self.assertEqual(status_response.status_code, 200)
            self.assertEqual(status_response.json()["engine"], "test")

    def test_wrong_join_token_does_not_set_cookie(self) -> None:
        app = create_app(start_worker=False, session_token="test-token")
        with TestClient(app) as client:
            response = client.get("/join/wrong", follow_redirects=False)
            self.assertEqual(response.status_code, 303)
            self.assertNotIn("fruitfly_session", response.cookies)


if __name__ == "__main__":
    unittest.main()
