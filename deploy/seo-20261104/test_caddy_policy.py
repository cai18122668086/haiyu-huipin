import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("seo_caddy", Path(__file__).with_name("caddy_policy.py"))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class CaddyPolicyTests(unittest.TestCase):
    def test_public_policy_removes_only_worker_hosts_and_preserves_admin_function(self):
        original = '''(huipin_routes) {
    header {
        -Server
        Content-Security-Policy "default-src 'self'; connect-src 'self' http://localhost:8765/v1/enrollment http://127.0.0.1:8765/v1/enrollment; img-src 'self' data: blob:"
        X-Content-Type-Options "nosniff"
    }
    reverse_proxy frontend:3000
}
'''
        scoped = module.scope_worker_bridge(original)
        self.assertIn("@seo_public_csp not path /admin/accounts /admin/accounts/", scoped)
        public = next(line for line in scoped.splitlines() if "header @seo_public_csp " in line)
        admin = next(line for line in scoped.splitlines() if "header @seo_worker_bridge " in line)
        self.assertNotIn("localhost", public)
        self.assertNotIn("127.0.0.1", public)
        for source in module.BRIDGE_SOURCES:
            self.assertIn(source, admin)
        self.assertIn("img-src 'self' data: blob:", public)
        self.assertIn('X-Content-Type-Options "nosniff"', scoped)
        self.assertIn("reverse_proxy frontend:3000", scoped)
        with self.assertRaises(ValueError):
            module.scope_worker_bridge(scoped)

    def test_ambiguous_or_unexpected_baselines_are_rejected(self):
        for source in ["", 'header { Content-Security-Policy "default-src self" }']:
            with self.assertRaises(ValueError):
                module.scope_worker_bridge(source)


if __name__ == "__main__":
    unittest.main()
