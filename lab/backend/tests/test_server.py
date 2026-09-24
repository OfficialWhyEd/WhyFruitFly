from __future__ import annotations

import unittest
from unittest import mock

from fastapi.testclient import TestClient

from fruitfly_lab import server
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


class LanIpTests(unittest.TestCase):
    def test_only_private_non_loopback_ipv4_is_usable(self) -> None:
        for ip in ("192.168.1.20", "10.0.0.5", "172.16.0.1", "172.31.255.1"):
            self.assertTrue(server._usable_lan_ip(ip), ip)
        for ip in ("127.0.0.1", "127.0.1.1", "169.254.3.4", "172.200.1.1", "8.8.8.8", "::1", "nope"):
            self.assertFalse(server._usable_lan_ip(ip), ip)

    def test_falls_back_to_hostname_then_loopback(self) -> None:
        with mock.patch.object(server.socket, "socket", side_effect=OSError), mock.patch.object(
            server.socket, "gethostbyname_ex", return_value=("pc", [], ["127.0.1.1", "192.168.1.7"])
        ):
            self.assertEqual(server.discover_lan_ip(), "192.168.1.7")
        with mock.patch.object(server.socket, "socket", side_effect=OSError), mock.patch.object(
            server.socket, "gethostbyname_ex", side_effect=OSError
        ):
            self.assertEqual(server.discover_lan_ip(), "127.0.0.1")


if __name__ == "__main__":
    unittest.main()
