# SPDX-License-Identifier: Apache-2.0
"""Regressions from an outside review: run admission across profiles, secrets in every input, errors
from secret stores, complete ways back, operation identity, and the publication scan."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from gateway_insights_actions import api  # noqa: E402
from gateway_insights_core import secrets  # noqa: E402
from gateway_insights_core.config import Profile  # noqa: E402
from gateway_insights_core.errors import ConfigError  # noqa: E402
from test_actions import Console, Setup  # noqa: E402

SECRET = "Sup3r" + "-Secr3t-" + "Value9"


class AdmissionAcrossProfilesTest(Setup):
    def test_class_one_preview_in_one_console_does_not_admit_money_in_another(self):
        task_id = self.call("plan_disable", requestor_ids=[7])["structuredContent"]["task"]["taskId"]
        other = Console()
        other.next_id = task_id - 1      # the other console happens to give the same id to a reversal
        other._task(taskType="ORDER_REVERSAL", displayName="Reversal", mode="PREVIEW", status="PREVIEWED",
                    objectType="ORDER")
        self.profile = Profile(name="other", base_url="https://other.example.com/api", token="pat_" + "y" * 24,
                               report_dir=self.dir.name)
        server = self.server(other)
        res = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                             "params": {"name": "batch_run", "arguments": {"task_id": task_id}}})["result"]
        self.assertIn("Not run", res["content"][0]["text"])
        self.assertNotIn(api.RUN_PREVIEW, [p for p, _ in other.calls])

    def test_journal_never_lowers_the_class_of_the_task_as_it_is(self):
        task_id = self.call("plan_disable", requestor_ids=[7])["structuredContent"]["task"]["taskId"]
        self.console.tasks[task_id]["taskType"] = "ORDER_REVERSAL"     # same console, the id now names money
        res = self.call("batch_run", task_id=task_id)
        self.assertIn("Not run", res["content"][0]["text"])


class SecretsInEveryInputTest(Setup):
    def test_csv_credential_column_is_not_echoed_or_journaled(self):
        TYPES_ADD = {"taskType": "BANK_TERMINAL_CLONE_EXTEND", "displayName": "Clone", "objectType": "BANK_TERMINAL",
                     "sources": ["CSV"], "sensitive": False, "maxItems": 200}
        import test_actions
        test_actions.TYPES.append(TYPES_ADD)
        try:
            res = self.call("batch_preview", task_type="BANK_TERMINAL_CLONE_EXTEND",
                            csv=f"bank_terminal_id;provider_id;name;descriptor;api_password\n900;;;;{SECRET}\n")
        finally:
            test_actions.TYPES.remove(TYPES_ADD)
        self.assertFalse(res["isError"], res["content"][0]["text"])
        self.assertEqual(res["structuredContent"]["class"], 3)
        sent = [b for p, b in self.console.calls if p == api.REGISTER][0]
        self.assertIn(SECRET, sent["csv"])                                      # the console gets it
        self.assertNotIn(SECRET, json.dumps(res) + json.dumps(self.journal_lines()))

    def test_refusal_examples_keep_only_the_object(self):
        original = self.console.answer

        def answer(path, body):
            if path == api.REASONS:
                return {"reasons": [{"status": "FAILED", "errorCode": "property.not.defined", "itemCount": 2,
                                     "sampleRefs": f"5004;;;;{SECRET}, 5005;;;;{SECRET}x"}]}
            return original(path, body)
        self.console.answer = answer
        res = self.call("plan_disable", requestor_ids=[7])
        self.assertNotIn(SECRET, json.dumps(res))
        self.assertEqual(res["structuredContent"]["reasons"][0]["sampleRefs"], "5004, 5005")

    def test_object_params_credentials_are_not_echoed(self):
        res = self.call("batch_preview", task_type="ENTITY_ENABLE",
                        objects=[{"id": 7, "params": {"merchant_password": SECRET}}], payload={"value": "DISABLED"})
        self.assertNotIn(SECRET, json.dumps(res) + json.dumps(self.journal_lines()))

    def test_changes_inside_object_params_are_cleaned(self):
        changes = json.dumps([{"param": "api_password", "value": SECRET}])
        res = self.call("batch_preview", task_type="ENTITY_ENABLE",
                        objects=[{"id": 7, "params": {"changes": changes}}], payload={"value": "DISABLED"})
        self.assertNotIn(SECRET, json.dumps(res) + json.dumps(self.journal_lines()))

    def test_a_clean_request_is_not_called_a_leak(self):
        res = self.call("plan_route_status", bank_terminal_ids=[5])
        self.assertNotIn("treat it as exposed", res["content"][0]["text"])
        res = self.call("batch_preview", task_type="ENTITY_ENABLE", ids=[7], payload={"value": "DISABLED"})
        self.assertNotIn("treat it as exposed", res["content"][0]["text"])

    def test_two_literal_secrets_are_two_operations(self):
        self.call("plan_secret", bank_terminal_ids=[5], param="psp_password", secret=SECRET)
        self.call("plan_secret", bank_terminal_ids=[5], param="psp_password", secret=SECRET + "x")
        keys = [b["idempotencyKey"] for p, b in self.console.calls if p == api.REGISTER]
        self.assertEqual(len(set(keys)), 2)


class RedactionUnitTest(unittest.TestCase):
    def test_clean_requests_come_back_as_given(self):
        from gateway_insights_actions import guard
        for body in ({"csv": "bank_terminal_id;status\n5;DISABLED"}, {"objects": [{"objectId": "7"}]},
                     {"payload": {"value": "DISABLED"}, "objects": []},
                     {"payload": {"changes": json.dumps([{"param": "terminal_location", "value": "EMPTY"}])}}):
            shown, found = guard.redact_found(body)
            self.assertEqual(shown, body)
            self.assertFalse(found)

    def test_changes_cleaned_in_every_form(self):
        from gateway_insights_actions import guard
        changes = json.dumps([{"param": "api_password", "value": SECRET}, {"param": "x", "value": "1"}])
        for body in ({"payload": {"changes": changes}}, {"objects": [{"objectId": "7", "params": {"changes": changes}}]}):
            shown, found = guard.redact_found(body)
            self.assertTrue(found)
            self.assertNotIn(SECRET, json.dumps(shown))
            raw = shown["payload"]["changes"] if "payload" in shown else shown["objects"][0]["params"]["changes"]
            self.assertEqual(json.loads(raw)[1], {"param": "x", "value": "1"})      # the rest is kept


class SecretStoreErrorTest(unittest.TestCase):
    def test_store_output_never_in_the_error(self):
        sources = {"cmd": {"type": "command", "argv": [
            sys.executable, "-c", f"import sys; sys.stderr.write('{SECRET}\\n'); sys.exit(3)", "{name}"]}}
        with self.assertRaises(ConfigError) as ctx:
            secrets.resolve("cmd:x", sources)
        self.assertNotIn(SECRET, str(ctx.exception))
        self.assertIn("exit code 3", str(ctx.exception))


class CompleteWayBackTest(Setup):
    def test_way_back_reads_every_page(self):
        many = [{"objectId": str(1000 + i), "status": "DONE", "changes": [
            {"field": "status_name", "beforeValue": "E", "afterValue": "D", "changed": True}]} for i in range(150)]
        original = self.console.answer

        def answer(path, body):
            if path == api.ITEMS_PAGE and self.console.tasks[body["taskId"]]["taskType"] == "ENTITY_ENABLE" \
                    and self.console.tasks[body["taskId"]].get("mode") == "RUN":
                page = many[body["offset"]:body["offset"] + body["size"]]
                return {"items": page, "hasMore": body["offset"] + body["size"] < len(many)}
            return original(path, body)
        self.console.answer = answer
        run = self.console._task(taskType="ENTITY_ENABLE", displayName="Switch", mode="RUN", status="DONE",
                                 objectType="REQUESTOR")
        res = self.call("plan_revert", task_id=run["taskId"])
        self.assertFalse(res["isError"], res["content"][0]["text"])
        body = [b for p, b in self.console.calls if p == api.REGISTER][-1]
        self.assertEqual(len(body["objects"]), 150)


class SharedArgumentsTest(Setup):
    def test_negative_ids_refused_in_actions_too(self):
        res = self.call("entity_count", level="REQUESTOR", merchant_ids=[-1])
        self.assertTrue(res["isError"])
        self.assertIn("not negative", res["content"][0]["text"])
        self.assertEqual(self.console.calls, [])


class CallCapTest(unittest.TestCase):
    def test_client_refuses_beyond_its_cap(self):
        from gateway_insights_core.errors import InputError
        from gateway_insights_core.http import ConsoleClient
        profile = Profile(name="t", base_url="https://console.example.com/api", token="pat_" + "c" * 20)
        client = ConsoleClient(profile, frozenset({"/a"}), opener=lambda r, t, c: b"{}", max_calls=2)
        client.post("/a", {}, what="x")
        client.post("/a", {}, what="x")
        with self.assertRaises(InputError):
            client.post("/a", {}, what="x")
        self.assertEqual(len(client.log.calls), 2)

    def test_actions_server_has_a_cap(self):
        self.assertTrue(0 < api.MAX_CALLS <= 200)


class ScanNonAsciiNamesTest(unittest.TestCase):
    def test_cyrillic_file_name_is_found(self):
        script = Path(__file__).resolve().parents[1] / "scripts" / "scan_forbidden.py"
        with tempfile.TemporaryDirectory() as repo:
            for args in (["init", "-q"], ["config", "user.name", "T"], ["config", "user.email", "t@example.com"]):
                subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)
            Path(repo, "\u0442\u0435\u0441\u0442.txt").write_text("clean\n")
            subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-q", "-m", "x"], cwd=repo, check=True, capture_output=True)
            out = subprocess.run([sys.executable, str(script)], cwd=repo, capture_output=True, text=True,
                                 env={**os.environ, "FORBIDDEN_PATTERNS": "[\u0400-\u04ff]"})
        self.assertEqual(out.returncode, 1, out.stdout)
        self.assertIn("committed file name", out.stdout)


if __name__ == "__main__":
    unittest.main()
