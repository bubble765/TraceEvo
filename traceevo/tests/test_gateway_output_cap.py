import importlib.util
import json
from pathlib import Path
import unittest


GATEWAY_SERVER = (Path.home() / ".local" / "share" /
                  "claude-prefix-proxy" / "server.py")


def load_gateway_module():
    if not GATEWAY_SERVER.is_file():
        return None
    spec = importlib.util.spec_from_file_location(
        "claude_prefix_proxy_test", GATEWAY_SERVER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class GatewayOutputCapTests(unittest.TestCase):
    def test_opt_in_deepseek_recovery_request_is_reasoning_bounded(self):
        proxy = load_gateway_module()
        if proxy is None:
            self.skipTest("local claude-prefix-proxy is not installed")
        payload = {"model": "deepseek-v4-flash", "max_tokens": 4096,
                   "messages": [{"role": "user", "content": "test"}]}
        bounded = json.loads(proxy.normalize_payload(
            json.dumps(payload).encode(), "none"))
        self.assertEqual(bounded["reasoning_effort"], "none")
        self.assertEqual(bounded["max_tokens"], 2048)

    def test_non_opt_in_request_is_not_given_a_new_cap_or_effort(self):
        proxy = load_gateway_module()
        if proxy is None:
            self.skipTest("local claude-prefix-proxy is not installed")
        payload = {"model": "deepseek-v4-flash", "max_tokens": 4096,
                   "messages": [{"role": "user", "content": "test"}]}
        normalized = json.loads(proxy.normalize_payload(json.dumps(payload).encode()))
        self.assertEqual(normalized["max_tokens"], 4096)
        self.assertNotIn("reasoning_effort", normalized)


if __name__ == "__main__":
    unittest.main()
