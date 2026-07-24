from __future__ import annotations

import unittest

import app as app_module


class PublicMinutesAccessTests(unittest.TestCase):
    def test_only_expected_read_routes_are_registered_publicly(self) -> None:
        expected_rules = {
            "/api/public/minutes/status",
            "/api/public/minutes/search",
            "/api/public/minutes/meetings",
            "/api/public/minutes/speakers",
            "/api/public/minutes/meetings/<int:meeting_id>",
            "/api/public/minutes/days/<int:day_id>",
        }
        public_rules = {
            rule.rule
            for rule in app_module.app.url_map.iter_rules()
            if rule.rule.startswith(app_module.PUBLIC_MINUTES_API_PREFIX)
        }

        self.assertEqual(expected_rules, public_rules)
        for rule in app_module.app.url_map.iter_rules():
            if rule.rule not in public_rules:
                continue
            self.assertIn("GET", rule.methods)
            self.assertNotIn("POST", rule.methods)
            self.assertNotIn("PUT", rule.methods)
            self.assertNotIn("DELETE", rule.methods)

    def test_public_allowlist_rejects_admin_paths_and_write_methods(self) -> None:
        allowed = [
            "/api/public/minutes/status",
            "/api/public/minutes/search",
            "/api/public/minutes/meetings",
            "/api/public/minutes/speakers",
            "/api/public/minutes/meetings/1",
            "/api/public/minutes/days/999",
        ]
        rejected = [
            "/api/public/minutes",
            "/api/public/minutes/sync",
            "/api/public/minutes/compile",
            "/api/public/minutes/retag",
            "/api/public/minutes/meetings/0",
            "/api/public/minutes/meetings/-1",
            "/api/public/minutes/meetings/1/extra",
            "/api/minutes/status",
        ]

        for path in allowed:
            with self.subTest(path=path):
                self.assertTrue(app_module.is_public_minutes_request("GET", path))
                self.assertTrue(app_module.is_public_minutes_request("HEAD", path))
                self.assertFalse(app_module.is_public_minutes_request("POST", path))

        for path in rejected:
            with self.subTest(path=path):
                self.assertFalse(app_module.is_public_minutes_request("GET", path))

    def test_rate_limit_key_prefers_nginx_verified_real_ip(self) -> None:
        with app_module.app.test_request_context(
            "/api/public/minutes/status",
            headers={
                "X-Real-IP": "203.0.113.10",
                "X-Forwarded-For": "198.51.100.99, 203.0.113.10",
            },
            environ_base={"REMOTE_ADDR": "127.0.0.1"},
        ):
            self.assertEqual("203.0.113.10", app_module.public_minutes_client_key())


if __name__ == "__main__":
    unittest.main()
