# SPDX-License-Identifier: Apache-2.0
"""Offline tests of the actions server: classes, preview first, who runs what, secrets, order search."""

from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gateway_insights_actions import api, classes, journal  # noqa: E402
from gateway_insights_actions.server import build_server  # noqa: E402
from gateway_insights_actions.tools import Context  # noqa: E402
from gateway_insights_core import orders, secrets  # noqa: E402
from gateway_insights_core.config import Profile  # noqa: E402
from gateway_insights_core.errors import ConfigError, InputError  # noqa: E402
from gateway_insights_core.http import CallLog, ConsoleClient  # noqa: E402

TODAY = date(2026, 10, 11)
SECRET_VALUE = "Zq8" + "vN2kLp" + "4Rt7Xw1"
TYPES = [
    {"taskType": "ENTITY_ENABLE", "displayName": "Switch", "objectType": "REQUESTOR", "sources": ["IDS"],
     "sensitive": False, "maxItems": 5000, "payloadSchema": [{"key": "value", "type": "select"}]},
    {"taskType": "ENTITY_RATE_PLAN", "displayName": "Tariff", "objectType": "ENTITY", "sources": ["CSV"],
     "sensitive": False, "maxItems": 5000, "csvHeader": "level;entity_id;field;new_value;old_value"},
    {"taskType": "ORDER_REVERSAL", "displayName": "Reversal", "objectType": "ORDER", "sources": ["IDS"],
     "sensitive": False, "maxItems": 5000},
    {"taskType": "ORDER_QUERY_STATUS", "displayName": "Status", "objectType": "ORDER", "sources": ["IDS"],
     "sensitive": False, "maxItems": 5000},
    {"taskType": "BANK_TERMINAL_PSP_SECRET", "displayName": "PSP password", "objectType": "BANK_TERMINAL",
     "sources": ["IDS"], "sensitive": True, "maxItems": 1000},
    {"taskType": "BANK_TERMINAL_ROUTING_STATUS", "displayName": "Routing", "objectType": "BANK_TERMINAL",
     "sources": ["CSV"], "sensitive": False, "maxItems": 1000},
    {"taskType": "ROUTING_CRITERION_REPLACE_VALUE", "displayName": "Criterion", "objectType": "BLUEPRINT",
     "sources": ["CSV"], "sensitive": False, "maxItems": 500},
    {"taskType": "RESTRICTION_PARAM", "displayName": "Restriction", "objectType": "RESTRICTION_TARGET",
     "sources": ["CSV"], "sensitive": False, "maxItems": 2000},
    {"taskType": "BANK_TERMINAL_PROPERTY", "displayName": "Properties", "objectType": "BANK_TERMINAL",
     "sources": ["IDS"], "sensitive": False, "maxItems": 1000, "payloadSchema": [{"key": "changes", "type": "changes"}]},
    {"taskType": "BANK_TERMINAL_PROPERTY_CSV", "displayName": "Properties CSV", "objectType": "BANK_TERMINAL",
     "sources": ["CSV"], "sensitive": False, "maxItems": 1000},
    {"taskType": "MERCHANT_GENERATOR", "displayName": "Generator", "objectType": "MERCHANT", "sources": ["IDS"],
     "sensitive": False, "maxItems": 2000, "payloadSchema": [{"key": "changes", "type": "changes"}]},
    {"taskType": "REQUESTOR_CONFIG", "displayName": "Configs", "objectType": "REQUESTOR", "sources": ["IDS"],
     "sensitive": False, "maxItems": 5000, "payloadSchema": [{"key": "changes", "type": "changes"}]},
]
# Items as the console reports a run of each type (read from a stand console).
RUN_ITEMS = {
    "ROUTING_CRITERION_REPLACE_VALUE": [{"objectId": "3001", "status": "DONE", "changes": [
        {"field": "transaction_type", "beforeValue": "19", "afterValue": "18", "changed": True}]}],
    "RESTRICTION_PARAM": [{"objectId": "BLUEPRINT:3004", "subKey": "12/skip unapproved transactions", "status": "DONE",
                           "changes": [{"field": "skip unapproved transactions", "beforeValue": "N", "afterValue": "Y",
                                        "changed": True}]},
                          {"objectId": "BLUEPRINT:3002", "subKey": "13/34", "status": "DONE", "changes": [
                              {"field": "restriction", "beforeValue": "not attached", "afterValue": "attached disabled",
                               "changed": True}]}],
    "REQUESTOR_CONFIG": [
        {"objectId": "4001", "status": "DONE", "inputRef": "4001;secret-ish", "changes": [
            {"field": "generate_card_holder_enabled", "beforeValue": "Y", "afterValue": "N", "changed": True}]},
        {"objectId": "4002", "status": "DONE", "changes": [
            {"field": "generate_card_holder_enabled", "beforeValue": "Y", "afterValue": "N", "changed": True}]},
        {"objectId": "4003", "status": "DONE", "changes": [
            {"field": "generate_card_holder_enabled", "beforeValue": None, "afterValue": "N", "changed": True}]}],
}


class Console:
    """A synthetic console: answers every allowlisted path, keeps tasks, records every call."""

    def __init__(self, items=5, done=5, preview_status="PREVIEWED"):
        self.calls = []
        self.tasks = {}
        self.next_id = 100
        self.items, self.done, self.preview_status = items, done, preview_status

    def __call__(self, request, timeout, context):
        path = request.full_url.split("/api", 1)[1]
        body = json.loads(request.data)
        self.calls.append((path, body))
        return json.dumps(self.answer(path, body)).encode()

    def _task(self, **kw):
        self.next_id += 1
        task = {"taskId": self.next_id, "counts": {"items": self.items, "done": self.done, "failed": 0, "skipped":
                self.items - self.done, "attention": 0, "pending": 0}, **kw}
        self.tasks[task["taskId"]] = task
        return task

    def answer(self, path, body):
        if path == api.TYPES:
            return {"types": TYPES}
        if path == api.REGISTER:
            t = next(t for t in TYPES if t["taskType"] == body["taskType"])
            return {"task": self._task(taskType=body["taskType"], displayName=t["displayName"], mode="PREVIEW",
                                       status=self.preview_status, objectType=t["objectType"]), "duplicate": False}
        if path == api.VIEW:
            return self.tasks[body["taskId"]]
        if path == api.ITEMS_PAGE:
            task = self.tasks[body["taskId"]]
            if task["taskType"] in RUN_ITEMS:
                return {"items": RUN_ITEMS[task["taskType"]]}
            if task["taskType"] == "BANK_TERMINAL_ROUTING_STATUS":     # as the console reports it
                return {"items": [{"objectId": "5001", "status": "DONE", "changes": [
                    {"field": "routing", "beforeValue": "enabled", "afterValue": "disabled", "changed": True},
                    {"field": "group", "beforeValue": None, "afterValue": "no.enabled", "changed": False}]}]}
            if task["taskType"] == "ENTITY_ENABLE":
                return {"items": [{"objectId": "4004", "status": "DONE", "changes": [
                    {"field": "status_name", "beforeValue": "E", "afterValue": "D", "changed": True}]}]}
            if task["taskType"] == "ENTITY_RATE_PLAN":
                return {"items": [{"objectId": "BLUEPRINT:3003", "subKey": "mrtp", "status": "DONE", "changes": [
                    {"field": "mrtp", "beforeValue": "71:Default plan", "afterValue": "72:Gold", "changed": True}]}]}
            return {"items": [{"objectId": "7", "status": "DONE", "changes": [
                {"field": "status", "beforeValue": "ENABLED", "afterValue": "DISABLED", "changed": True}]}],
                "hasMore": False}
        if path == api.REASONS:
            return {"reasons": [{"status": "SKIPPED", "errorCode": "value.same", "itemCount": 1}]}
        if path == api.RUN_PREVIEW:
            src = self.tasks[body["previewTaskId"]]
            return {"task": self._task(taskType=src["taskType"], displayName=src["displayName"], mode="RUN",
                                       status="DONE", objectType=src["objectType"],
                                       fromPreviewTaskId=src["taskId"]), "duplicate": False}
        if path == api.CANCEL:
            return dict(self.tasks[body["taskId"]], status="CANCELLED")
        if path == api.LIST_COUNTS:
            return {"all": 3, "previewed": 1}
        if path == orders.ORDER_LIST:
            n = body["size"]
            return {"orders": [{"orderId": 1000 + i, "orderStatus": {"status": "UNKNOWN", "type": "SALE"},
                                "customerName": "Jane Roe", "browserIp": "203.0.113.9",
                                "card": {"panMasked": "411111XXXX1111", "cardholder": "JANE ROE"}}
                               for i in range(min(n, 3))], "hasMore": False}
        raise AssertionError(f"unexpected call {path}")


class Setup(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        secrets_dir = Path(self.dir.name, "secrets")
        secrets_dir.mkdir()
        (secrets_dir / "psp").write_text(SECRET_VALUE + "\n")
        os.chmod(secrets_dir / "psp", 0o600)
        self.profile = Profile(name="stand", base_url="https://console.example.com/api", token="pat_" + "x" * 24,
                               report_dir=self.dir.name, console_ui_url="https://console.example.com",
                               secret_sources={"files": {"type": "dir", "path": str(secrets_dir)}})
        self.console = Console()
        self.answers = []

    def tearDown(self):
        self.dir.cleanup()

    def server(self, console=None, can_ask=True):
        console = console or self.console

        def ask(message, props, required=None):
            if not can_ask:
                return None
            return self.answers.pop(0) if self.answers else {"run": True}

        def context(name):
            client = ConsoleClient(self.profile, api.ALLOWED_PATHS, opener=console, log=CallLog())
            return Context(api.ActionsApi(client, sleep=lambda s: None), TODAY, ask)
        return build_server(context, lambda: TODAY)

    def call(self, name, **arguments):
        server = self.server()
        server.handle({"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}})
        return server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                              "params": {"name": name, "arguments": arguments}})["result"]

    def paths(self):
        return [p for p, _ in self.console.calls]

    def journal_lines(self):
        path = journal.journal_path(self.dir.name)
        return [json.loads(x) for x in path.read_text().splitlines()] if path.exists() else []


class LinkTest(unittest.TestCase):
    def test_console_address(self):
        from gateway_insights_actions.tools import task_link
        p = Profile(name="p", base_url="https://c.example.com/console-api/api", token="pat_" + "x" * 20)
        self.assertEqual(task_link(p, 5), "https://c.example.com/console/batch-operations/tasks/5")
        p = Profile(name="p", base_url="https://c.example.com/api", token="pat_" + "x" * 20)
        self.assertEqual(task_link(p, 5), "https://c.example.com/batch-operations/tasks/5")
        p = Profile(name="p", base_url="https://c.example.com/api", token="pat_" + "x" * 20,
                    console_ui_url="https://ui.example.com/admin")
        self.assertEqual(task_link(p, 5), "https://ui.example.com/admin/batch-operations/tasks/5")


class ClassesTest(unittest.TestCase):
    def test_types(self):
        self.assertEqual(classes.task_class("ENTITY_ENABLE"), 1)
        self.assertEqual(classes.task_class("ORDER_REVERSAL"), 2)
        self.assertEqual(classes.task_class("BANK_TERMINAL_PSP_SECRET"), 3)
        self.assertEqual(classes.task_class("SOMETHING_NEW"), 2)      # unknown: prepared, run in the console
        self.assertEqual(classes.task_class("ENTITY_NAME", sensitive=True), 3)

    def test_routing_on_raises_to_money(self):
        self.assertEqual(classes.task_class("BANK_TERMINAL_ROUTING_STATUS", payload={"row_status": "DISABLED"}), 1)
        self.assertEqual(classes.task_class("BANK_TERMINAL_ROUTING_STATUS",
                                            csv_text="bank_terminal_id;status\n1;ENABLED\n"), 2)
        self.assertEqual(classes.task_class("BANK_TERMINAL_CLONE_EXTEND", payload={"enabled": "DISABLED"}), 1)
        self.assertEqual(classes.task_class("BANK_TERMINAL_CLONE_EXTEND", payload={"enabled": "ENABLED"}), 2)

    def test_credentials_raise_to_secret(self):
        csv_text = "bank_terminal_id;provider_id;name;descriptor;api_password\n1;;;;x\n"
        self.assertEqual(classes.task_class("BANK_TERMINAL_CLONE_EXTEND", csv_text=csv_text), 3)
        changes = json.dumps([{"param": "merchant_secret_key", "value": "x"}])
        self.assertEqual(classes.task_class("BANK_TERMINAL_PROPERTY", payload={"changes": changes}), 3)
        changes = json.dumps([{"param": "terminal_location", "value": "x"}])
        self.assertEqual(classes.task_class("BANK_TERMINAL_PROPERTY", payload={"changes": changes}), 1)

    def test_every_registry_type_has_a_class(self):
        self.assertEqual(len(classes.TYPE_CLASS), 38)

    def test_every_registry_type_has_a_guide(self):
        from gateway_insights_actions import guides
        self.assertEqual(guides.missing(list(classes.TYPE_CLASS)), [])
        self.assertIn("sub_key", guides.guide_text("MERCHANT_GENERATOR"))


class ServerShapeTest(Setup):
    def test_writing_tools_say_so(self):
        tools = self.server().handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})["result"]["tools"]
        by_name = {t["name"]: t for t in tools}
        self.assertTrue(by_name["batch_run"]["description"].startswith("WRITES:"))
        self.assertTrue(by_name["batch_run"]["annotations"]["destructiveHint"])
        for name, t in by_name.items():
            prepares = name.startswith("plan_") or name in ("batch_preview", "batch_cancel")
            if prepares:
                self.assertTrue(t["description"].startswith("PREPARES:"), name)
                self.assertFalse(t["annotations"]["readOnlyHint"], name)
            elif name != "batch_run":
                self.assertTrue(t["annotations"]["readOnlyHint"], name)
        self.assertIn("CHANGES DATA", self.server().instructions)

    def test_allowlist_has_no_single_operation_or_webhook_path(self):
        writes = ("/transaction-operation", "/order-operation/enqueue", "/order-operation/resend",
                  "/order-operation/send", "/order-operation/stop")
        for path in api.ALLOWED_PATHS:
            self.assertFalse(path.startswith(writes), path)
            if path.startswith("/order-operation"):
                self.assertIn("/reasons-", path)          # only the reason dictionaries
        self.assertNotIn("/merchant/view", api.ALLOWED_PATHS)


class PreviewTest(Setup):
    def test_preview_sends_a_key_and_changes_nothing(self):
        res = self.call("plan_disable", requestor_ids=[7, 8])
        self.assertFalse(res["isError"], res["content"][0]["text"])
        register = [b for p, b in self.console.calls if p == api.REGISTER][0]
        self.assertEqual(register["mode"], "PREVIEW")
        self.assertTrue(register["idempotencyKey"].startswith("gi-preview-"))
        self.assertNotIn(api.RUN_PREVIEW, self.paths())
        text = res["content"][0]["text"]
        self.assertIn("PREPARED — nothing changed yet", text)
        self.assertIn("batch_run task_id=", text)
        self.assertFalse(res["structuredContent"]["changed"])
        self.assertEqual(self.journal_lines()[-1]["event"], "preview")
        if os.name == "posix":
            mode = journal.journal_path(self.dir.name).stat().st_mode
            self.assertFalse(mode & (stat.S_IRWXG | stat.S_IRWXO))

    def test_same_request_same_key(self):
        from gateway_insights_actions import guard
        a = guard.operation_key("preview", {"x": 1, "y": [1, 2]}, "20261011103")
        b = guard.operation_key("preview", {"y": [1, 2], "x": 1}, "20261011103")
        self.assertEqual(a, b)
        self.assertNotEqual(a, guard.operation_key("preview", {"x": 2, "y": [1, 2]}, "20261011103"))
        self.assertNotEqual(a, guard.operation_key("preview", {"x": 1, "y": [1, 2]}, "20261011104"))

    def test_money_is_prepared_with_a_console_link_only(self):
        res = self.call("plan_order_action", task_type="ORDER_REVERSAL", order_ids=[1, 2],
                        payload={"comment": "refund"})
        text = res["content"][0]["text"]
        self.assertIn("run it in the console", text)
        self.assertIn("https://console.example.com/batch-operations/tasks/", text)
        self.assertNotIn("batch_run task_id", text)
        self.assertFalse(res["structuredContent"]["runs_here"])

    def test_threshold_warning(self):
        self.console.items = self.console.done = 150
        res = self.call("plan_order_action", task_type="ORDER_REVERSAL", order_ids=[1])
        self.assertIn("above this profile's warning threshold of 100", res["content"][0]["text"])


class RunTest(Setup):
    def prepared(self, tool="plan_disable", **args):
        res = self.call(tool, **(args or {"requestor_ids": [7]}))
        return res["structuredContent"]["task"]["taskId"]

    def test_runs_class_one_after_asking(self):
        task_id = self.prepared()
        res = self.call("batch_run", task_id=task_id)
        self.assertIn("CHANGED", res["content"][0]["text"])
        run = [b for p, b in self.console.calls if p == api.RUN_PREVIEW]
        self.assertEqual(run, [{"previewTaskId": task_id, "idempotencyKey": f"gi-run-{task_id}"}])
        self.assertEqual(self.journal_lines()[-1]["event"], "run")

    def test_declined_runs_nothing(self):
        task_id = self.prepared()
        self.answers = [{"run": False}]
        res = self.call("batch_run", task_id=task_id)
        self.assertIn("declined", res["content"][0]["text"])
        self.assertNotIn(api.RUN_PREVIEW, self.paths())

    def test_without_asking_runs_with_a_warning(self):
        task_id = self.prepared()
        server = self.server(can_ask=False)
        res = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                             "params": {"name": "batch_run", "arguments": {"task_id": task_id}}})["result"]
        self.assertIn("without it", res["content"][0]["text"])

    def test_never_runs_money(self):
        task_id = self.prepared("plan_order_action", task_type="ORDER_REVERSAL", order_ids=[1])
        res = self.call("batch_run", task_id=task_id)
        self.assertIn("Not run", res["content"][0]["text"])
        self.assertNotIn(api.RUN_PREVIEW, self.paths())

    def test_never_runs_a_routing_task_prepared_elsewhere(self):
        task = self.console._task(taskType="BANK_TERMINAL_ROUTING_STATUS", displayName="Routing",
                                  mode="PREVIEW", status="PREVIEWED", objectType="BANK_TERMINAL")
        res = self.call("batch_run", task_id=task["taskId"])
        self.assertIn("Not run", res["content"][0]["text"])
        self.assertNotIn(api.RUN_PREVIEW, self.paths())

    def test_routing_on_is_not_run_here(self):
        task_id = self.prepared("plan_route_status", bank_terminal_ids=[5], enable=True)
        res = self.call("batch_run", task_id=task_id)
        self.assertIn("Not run — class 2", res["content"][0]["text"])

    def test_only_a_finished_preview_runs(self):
        self.console.preview_status = "FAILED"
        task_id = self.prepared()
        res = self.call("batch_run", task_id=task_id)
        self.assertTrue(res["isError"])


class SecretTest(Setup):
    def test_secret_by_reference_never_leaves_the_server(self):
        res = self.call("plan_secret", bank_terminal_ids=[5], param="psp_password", secret_ref="files:psp")
        register = [b for p, b in self.console.calls if p == api.REGISTER][0]
        self.assertEqual(register["payload"]["secret"], SECRET_VALUE)        # the console gets it
        everything = json.dumps(res) + json.dumps(self.journal_lines())
        self.assertNotIn(SECRET_VALUE, everything)                             # nothing else does
        self.assertIn("…" + SECRET_VALUE[-4:], res["content"][0]["text"])
        self.assertIn("run it in the console", res["content"][0]["text"])

    def test_literal_secret_is_accepted_with_a_warning(self):
        res = self.call("plan_secret", bank_terminal_ids=[5], param="psp_password", secret=SECRET_VALUE)
        self.assertFalse(res["isError"])
        self.assertIn("treat it as exposed", res["content"][0]["text"])
        self.assertNotIn(SECRET_VALUE, json.dumps(res) + json.dumps(self.journal_lines()))

    def test_sources(self):
        sources = {"files": {"type": "dir", "path": str(Path(self.dir.name, "secrets"))},
                   "env": {"type": "env", "prefix": "GI_SECRET_"},
                   "cmd": {"type": "command", "argv": [sys.executable, "-c", "import sys; print('v-' + sys.argv[1])",
                                                       "{name}"]}}
        self.assertEqual(secrets.resolve("files:psp", sources).value, SECRET_VALUE)
        with self.assertRaises(InputError):
            secrets.resolve("files:../x", sources)
        with mock.patch.dict(os.environ, {"GI_SECRET_PSP_ACME": "abc"}):
            self.assertEqual(secrets.resolve("env:psp/acme", sources).value, "abc")
        self.assertEqual(secrets.resolve("cmd:a.b", sources).value, "v-a.b")
        with self.assertRaises(InputError):
            secrets.resolve("cmd:a;rm -rf /", sources)
        with self.assertRaises(ConfigError):
            secrets.resolve("nope:x", sources)
        if os.name == "posix":
            os.chmod(Path(self.dir.name, "secrets", "psp"), 0o644)
            with self.assertRaises(ConfigError):
                secrets.resolve("files:psp", sources)

    def test_looks_like_secret(self):
        self.assertTrue(secrets.looks_like_secret(SECRET_VALUE))
        self.assertTrue(secrets.looks_like_secret("sk_live_" + "a" * 20))
        for plain in ("refund", "a.example.com, b.example.com", "files:psp", "2026-10-11", "ENABLED", "72"):
            self.assertFalse(secrets.looks_like_secret(plain), plain)


class RevertTest(Setup):
    def test_tariff_way_back_swaps_values(self):
        res = self.call("plan_rate_plan", rows=[{"level": "BLUEPRINT", "entity_id": 3003, "field": "mrtp",
                                                 "new_value": 72, "old_value": 71}])
        preview_id = res["structuredContent"]["task"]["taskId"]
        run = self.call("batch_run", task_id=preview_id)
        run_id = run["structuredContent"]["task"]["taskId"]
        back = self.call("plan_revert", task_id=run_id)
        self.assertFalse(back["isError"], back["content"][0]["text"])
        csv_text = [b for p, b in self.console.calls if p == api.REGISTER][-1]["csv"]
        self.assertIn("BLUEPRINT;3003;mrtp;71;72", csv_text)


class RevertFormatsTest(Setup):
    def run_and_revert(self, tool, **args):
        preview_id = self.call(tool, **args)["structuredContent"]["task"]["taskId"]
        run_id = self.call("batch_run", task_id=preview_id)["structuredContent"]["task"]["taskId"]
        back = self.call("plan_revert", task_id=run_id)
        self.assertFalse(back["isError"], back["content"][0]["text"])
        return [b for p, b in self.console.calls if p == api.REGISTER][-1]

    def test_requestor_switch_back_reads_e_and_d(self):
        body = self.run_and_revert("plan_disable", requestor_ids=[4004])
        self.assertEqual(body["payload"], {"value": "ENABLED"})
        self.assertEqual(body["objects"], [{"objectId": "4004"}])

    def test_routing_back_reads_enabled_and_ignores_unchanged_fields(self):
        body = self.run_and_revert("plan_route_status", bank_terminal_ids=[5001])
        self.assertEqual(body["csv"], "bank_terminal_id;status\n5001;ENABLED\n")


class RevertMoreTest(Setup):
    def run_task(self, task_type):
        task = self.console._task(taskType=task_type, displayName=task_type, mode="RUN", status="DONE",
                                  objectType="X")
        return task["taskId"]

    def test_criterion_replacement_back(self):
        res = self.call("plan_revert", task_id=self.run_task("ROUTING_CRITERION_REPLACE_VALUE"))
        self.assertFalse(res["isError"], res["content"][0]["text"])
        csv_text = [b for p, b in self.console.calls if p == api.REGISTER][-1]["csv"]
        self.assertIn("3001;TRANSACTION_TYPE;18;19", csv_text)

    def test_restriction_parameter_back_and_attach_warned(self):
        res = self.call("plan_revert", task_id=self.run_task("RESTRICTION_PARAM"))
        csv_text = [b for p, b in self.console.calls if p == api.REGISTER][-1]["csv"]
        self.assertIn("BLUEPRINT;3004;12;skip unapproved transactions;Y;N", csv_text)
        self.assertNotIn("3002", csv_text)
        self.assertIn("does not detach", res["content"][0]["text"])

    def test_changes_back_groups_objects_and_lists_inherited(self):
        res = self.call("plan_revert", task_id=self.run_task("REQUESTOR_CONFIG"))
        body = [b for p, b in self.console.calls if p == api.REGISTER][-1]
        self.assertEqual(body["objects"], [{"objectId": "4001"}, {"objectId": "4002"}])
        self.assertEqual(json.loads(body["payload"]["changes"]),
                         [{"param": "generate_card_holder_enabled", "value": "Y"}])
        self.assertIn("4003", res["content"][0]["text"])        # inherited before: not set back, said so

    def test_credential_values_masked_even_unflagged(self):
        RUN_ITEMS["BANK_TERMINAL_PROPERTY"] = [{"objectId": "5002", "status": "DONE", "changes": [
            {"field": "api_merchant_password", "beforeValue": "Plain-Old-Pass1", "afterValue": "x", "changed": True},
            {"field": "terminal_location", "beforeValue": "PREMISES", "afterValue": "EMPTY", "changed": True}]}]
        try:
            res = self.call("batch_items", task_id=self.run_task("BANK_TERMINAL_PROPERTY"))
        finally:
            RUN_ITEMS.pop("BANK_TERMINAL_PROPERTY")
        dumped = json.dumps(res)
        self.assertNotIn("Plain-Old-Pass1", dumped)
        self.assertIn("******", dumped)
        self.assertIn("PREMISES", dumped)                   # ordinary fields stay readable

    def test_generator_back_keeps_manager_and_form_values(self):
        RUN_ITEMS["MERCHANT_GENERATOR"] = [{"objectId": "6001", "subKey": "61", "status": "DONE", "changes": [
            {"field": "generator_customer_email_generation_enabled_status", "beforeValue": "D", "afterValue": "F",
             "changed": True}]}]
        try:
            self.call("plan_revert", task_id=self.run_task("MERCHANT_GENERATOR"))
        finally:
            RUN_ITEMS.pop("MERCHANT_GENERATOR")
        body = [b for p, b in self.console.calls if p == api.REGISTER][-1]
        self.assertEqual(body["objects"], [{"objectId": "6001", "subKey": "61"}])
        self.assertEqual(json.loads(body["payload"]["changes"]),
                         [{"param": "generator_customer_email_generation_enabled_status", "value": "DISABLED"}])

    def test_input_ref_never_leaves(self):
        task_id = self.run_task("REQUESTOR_CONFIG")
        res = self.call("batch_items", task_id=task_id)
        self.assertNotIn("secret-ish", json.dumps(res))

    def test_malformed_csv_line_does_not_crash(self):
        self.assertEqual(classes.task_class("BANK_TERMINAL_PROPERTY_CSV",
                                            csv_text="bank_terminal_id;terminal_location\n840;PREMISES;X\n"), 1)

    def test_property_csv_back(self):
        RUN_ITEMS["BANK_TERMINAL_PROPERTY_CSV"] = [{"objectId": "5003", "status": "DONE", "changes": [
            {"field": "terminal_location", "beforeValue": "PREMISES", "afterValue": "EMPTY", "changed": True}]}]
        try:
            self.call("plan_revert", task_id=self.run_task("BANK_TERMINAL_PROPERTY_CSV"))
        finally:
            RUN_ITEMS.pop("BANK_TERMINAL_PROPERTY_CSV")
        self.assertEqual([b for p, b in self.console.calls if p == api.REGISTER][-1]["csv"],
                         "bank_terminal_id;terminal_location\n5003;PREMISES\n")

    def test_criterion_add_is_not_reversible(self):
        self.assertEqual(classes.task_class("ROUTING_CRITERION_ADD_VALUE"), 2)


class FieldsAndProvenanceTest(Setup):
    def test_type_without_changes_is_not_asked_for_attributes(self):
        res = self.call("batch_fields", task_type="ENTITY_ENABLE")
        self.assertFalse(res["isError"], res["content"][0]["text"])
        self.assertIn("How to fill it", res["content"][0]["text"])
        self.assertNotIn(api.FIELDS, self.paths())

    def test_polls_are_one_line(self):
        self.console.preview_status = "NEW"
        original = self.console.answer

        def answer(path, body):
            data = original(path, body)
            if path == api.VIEW:
                seen = sum(1 for p, _ in self.console.calls if p == api.VIEW)
                data = dict(data, status="PREVIEWED" if seen >= 3 else "NEW")
            return data
        self.console.answer = answer
        text = self.call("plan_disable", requestor_ids=[7])["content"][0]["text"]
        self.assertIn("×3", text)


class OrderSearchTest(Setup):
    def q(self, **args):
        return orders.build_query(args, TODAY)

    def test_date_types_and_filters(self):
        with self.assertRaises(InputError) as e:
            self.q(date_type="CREATED", provider_ids=[1])
        self.assertIn("CHANGED or TRANSACTION_CREATED", str(e.exception))
        with self.assertRaises(InputError):
            self.q(date_type="TRANSACTION_CREATED", order_statuses=[1])
        with self.assertRaises(InputError):
            self.q(date_type="CREATED", search_type="orderId", search_text="1")
        with self.assertRaises(InputError):
            self.q(order_statuses=[1], transaction_statuses=[2])
        body, _ = self.q(date_type="TRANSACTION_CREATED", transaction_statuses=[2], provider_ids=[9])
        self.assertEqual(body["period"], {"startDate": "2026-10-09", "endDate": "2026-10-11"})

    def test_default_periods_follow_the_screen(self):
        self.assertNotIn("period", self.q(search_type="orderId", search_text="1 2,3")[0])
        self.assertEqual(self.q(search_type="sourceRRN", search_text="123")[0]["period"]["startDate"], "2025-10-11")
        self.assertEqual(self.q(merchant_ids=[1])[0]["period"]["startDate"], "2026-09-11")
        self.assertEqual(self.q()[0]["period"]["startDate"], "2026-10-09")
        body, _ = self.q(date_from="2026-10-10 17:00", date_to="2026-10-10 18:00")
        self.assertEqual(body["period"], {"startDate": "2026-10-10 17:00:00", "endDate": "2026-10-10 18:00:59"})

    def test_formats(self):
        body, warnings = self.q(search_type="card6and4", search_text="4111 1111 1111 1111")
        self.assertEqual(body["searchText"], "411111*1111")
        self.assertTrue(any("full card number" in w for w in warnings))
        with self.assertRaises(InputError):
            self.q(search_type="orderId", search_text=" ".join(str(i) for i in range(501)))
        with self.assertRaises(InputError):
            self.q(search_type="sourceBIN", search_text="41x111")
        _, warnings = self.q(search_type="customerEMail", search_text="a@example.com")
        self.assertTrue(any("model provider" in w for w in warnings))

    def test_customer_data_hidden_by_default(self):
        res = self.call("order_search", search_type="orderId", search_text="1000")
        self.assertNotIn("Jane Roe", json.dumps(res))
        self.assertNotIn("203.0.113.9", json.dumps(res))
        self.assertEqual(res["structuredContent"]["order_ids"], [1000, 1001, 1002])

    def test_status_check_from_a_search(self):
        res = self.call("plan_status_check", search={"order_statuses": [5]})
        register = [b for p, b in self.console.calls if p == api.REGISTER][0]
        self.assertEqual([o["objectId"] for o in register["objects"]], ["1000", "1001", "1002"])
        self.assertIn("batch_run task_id=", res["content"][0]["text"])


class AskTest(unittest.TestCase):
    def test_elicitation_round_trip(self):
        from gateway_insights_core.mcp import Server
        server = Server(name="t", version="0")
        sent, inbox = [], [{"jsonrpc": "2.0", "method": "notifications/progress"},
                           {"jsonrpc": "2.0", "id": "ask-1", "result": {"action": "accept", "content": {"run": True}}}]
        server._send, server._receive = sent.append, lambda: inbox.pop(0) if inbox else None
        self.assertIsNone(server.ask("q", {"run": {"type": "boolean"}}))            # client did not say it can
        server.handle({"jsonrpc": "2.0", "id": 0, "method": "initialize",
                       "params": {"protocolVersion": "2025-06-18", "capabilities": {"elicitation": {}}}})
        self.assertEqual(server.ask("q", {"run": {"type": "boolean"}}), {"run": True})
        self.assertEqual(sent[0]["method"], "elicitation/create")
        self.assertEqual(len(server._pending), 1)                                     # handled after the tool


if __name__ == "__main__":
    unittest.main()
