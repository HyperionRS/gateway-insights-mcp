# SPDX-License-Identifier: Apache-2.0
"""Offline tests of the shared core. Fixtures are synthetic."""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gateway_insights_core import budget, config, dates, files, html, markdown, mask, mcp, numbers, report, theme  # noqa: E402
from gateway_insights_core.errors import AccessDenied, ApiError, ConfigError, InputError, NotSupported, ThemeError  # noqa: E402
from gateway_insights_core.http import ConsoleClient  # noqa: E402

TOKEN = "pat_" + "a1b2c3d4e5f6a7b8c9d0"


def profile(**kw) -> config.Profile:
    base = dict(name="test", base_url="https://console.example.com/api", token=TOKEN)
    base.update(kw)
    return config.Profile(**base)


class NumbersTest(unittest.TestCase):
    def test_display_strings_parse_to_decimal(self):
        self.assertEqual(numbers.to_decimal("1 234 567.89"), Decimal("1234567.89"))
        self.assertEqual(numbers.to_decimal("12\u00a0345"), Decimal("12345"))
        self.assertEqual(numbers.to_decimal("97.25%"), Decimal("0.9725"))
        self.assertEqual(numbers.to_decimal(3), Decimal(3))
        self.assertIsNone(numbers.to_decimal(None))
        self.assertIsNone(numbers.to_decimal(""))
        # a Java BigDecimal printed in scientific notation, as the console sends a zero margin
        self.assertEqual(numbers.to_decimal("0E-8"), Decimal(0))
        self.assertEqual(numbers.to_decimal("1.5E+3"), Decimal(1500))
        self.assertEqual(numbers.to_decimal("-12.50"), Decimal("-12.50"))

    def test_non_numbers_are_refused_not_guessed(self):
        for bad in ("abc", "1,234.5", "12 34x", True):
            if bad is True:
                self.assertIsNone(numbers.to_decimal(bad))
                continue
            with self.assertRaises(ValueError):
                numbers.to_decimal(bad)

    def test_formatting(self):
        self.assertEqual(numbers.fmt_money(Decimal("1234567.891"), "EUR"), "1,234,567.89 EUR")
        self.assertEqual(numbers.fmt_rate(Decimal("0.9725")), "97.25%")
        self.assertEqual(numbers.fmt_pp(Decimal("0.0123")), "+1.23 pp")
        self.assertEqual(numbers.fmt_pp(Decimal("-0.5")), "-50.00 pp")
        self.assertEqual(numbers.fmt_count(Decimal("1000")), "1,000")
        self.assertEqual(numbers.fmt_money(None), "—")

    def test_ratio_by_zero_is_none(self):
        self.assertIsNone(numbers.ratio(Decimal(1), Decimal(0)))
        self.assertEqual(numbers.ratio(Decimal(1), Decimal(4)), Decimal("0.25"))


class MaskTest(unittest.TestCase):
    def test_luhn_card_numbers_are_masked(self):
        self.assertEqual(mask.mask_text("card 4111 1111 1111 1111 ok"), "card 411111***1111 ok")

    def test_non_luhn_digits_are_kept(self):
        self.assertEqual(mask.mask_text("order 1234567890123"), "order 1234567890123")

    def test_secret_keys_are_masked(self):
        out = mask.mask_json({"apiToken": "x", "name": "Shop", "nested": [{"password": "p"}]})
        self.assertEqual(out, {"apiToken": "***", "name": "Shop", "nested": [{"password": "***"}]})


class MarkdownTest(unittest.TestCase):
    def test_cells_are_escaped(self):
        out = markdown.md_table(["A"], [["x | y *bold*\n[link](u)"]])
        self.assertIn("x \\| y \\*bold\\* \\[link\\](u)", out)   # «[» escaped: no link can form
        self.assertNotIn("[link](", out.replace("\\[link\\](", ""))
        self.assertNotIn("\n[link]", out)


class ConfigTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "config.json"
        self.env = mock.patch.dict(os.environ, {"GI_CONFIG": str(self.path)}, clear=False)
        self.env.start()
        for key in ("GI_PROFILE", "GI_BASE_URL", "GI_TOKEN", "GI_INSECURE", "GI_THEME"):
            os.environ.pop(key, None)

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def write(self, data, mode=0o600):
        self.path.write_text(json.dumps(data))
        os.chmod(self.path, mode)

    def test_profile_loads(self):
        self.write({"default_profile": "prod", "profiles": {"prod": {
            "base_url": "https://console.example.com/api/", "token": TOKEN, "currency_id": 978}}})
        p = config.load_profile()
        self.assertEqual(p.base_url, "https://console.example.com/api")
        self.assertEqual(p.currency_id, 978)
        self.assertTrue(p.verify_tls)

    @unittest.skipUnless(os.name == "posix", "file modes are POSIX")
    def test_world_readable_file_is_refused(self):
        self.write({"profiles": {"p": {"base_url": "https://c.example.com", "token": TOKEN}}}, mode=0o644)
        with self.assertRaises(ConfigError) as ctx:
            config.load_profile("p")
        self.assertIn("chmod 600", str(ctx.exception))

    def test_session_jwt_is_refused(self):
        self.write({"profiles": {"p": {"base_url": "https://c.example.com", "token": "aaa.bbb.ccc"}}})
        with self.assertRaises(ConfigError):
            config.load_profile("p")

    def test_plain_http_is_refused(self):
        self.write({"profiles": {"p": {"base_url": "http://c.example.com", "token": TOKEN}}})
        with self.assertRaises(ConfigError):
            config.load_profile("p")

    def test_environment_overrides_the_file(self):
        self.write({"profiles": {"p": {"base_url": "https://c.example.com", "token": TOKEN}}})
        with mock.patch.dict(os.environ, {"GI_BASE_URL": "https://other.example.com"}):
            self.assertEqual(config.load_profile("p").base_url, "https://other.example.com")

    def test_token_hint_hides_the_middle(self):
        hint = profile().token_hint
        self.assertNotIn(TOKEN[5:-4], hint)
        self.assertTrue(hint.endswith(TOKEN[-4:]))


class DatesTest(unittest.TestCase):
    today = date(2026, 10, 10)

    def test_default_window_is_seven_days_to_today(self):
        w = dates.window(None, None, self.today)
        self.assertEqual((w.start, w.end, w.days), (date(2026, 10, 4), self.today, 7))

    def test_too_long_window_is_refused(self):
        with self.assertRaises(InputError):
            dates.window("2023-01-01", "2026-01-01", self.today)

    def test_base_rules(self):
        cur = dates.window("2026-10-01", "2026-10-07", self.today)
        self.assertIsNone(dates.base_window(None, None, cur))
        with self.assertRaises(InputError):
            dates.base_window("2026-09-01", None, cur)
        with self.assertRaises(InputError):
            dates.base_window("2026-09-30", "2026-10-02", cur)
        self.assertEqual(dates.implied_base(cur), dates.Window(date(2026, 9, 24), date(2026, 9, 30)))


class BudgetTest(unittest.TestCase):
    def test_call_bound(self):
        b = budget.CallBudget()
        b.take(budget.MAX_CALLS)
        with self.assertRaises(InputError):
            b.take()

    def test_parallel_bound(self):
        active, peak, lock = [0], [0], threading.Lock()

        def job():
            with lock:
                active[0] += 1
                peak[0] = max(peak[0], active[0])
            time.sleep(0.05)
            with lock:
                active[0] -= 1
            return 1

        self.assertEqual(budget.CallBudget().run([job] * 6), [1] * 6)
        self.assertLessEqual(peak[0], budget.MAX_PARALLEL)


class FakeOpener:
    """Records requests; answers from a queue of (status, body) pairs."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.requests = []

    def __call__(self, request, timeout, context):
        self.requests.append(request)
        status, body = self.answers.pop(0)
        if status != 200:
            raise urllib.error.HTTPError(request.full_url, status, "err", {}, io.BytesIO(body.encode()))
        return body.encode()


class HttpTest(unittest.TestCase):
    def client(self, opener):
        return ConsoleClient(profile(), frozenset({"/a"}), opener=opener)

    def test_path_outside_allowlist_never_reaches_the_network(self):
        opener = FakeOpener()
        with self.assertRaises(ConfigError):
            self.client(opener).post("/b", {}, what="x")
        self.assertEqual(opener.requests, [])

    def test_numbers_parse_as_decimal_and_token_goes_in_header(self):
        opener = FakeOpener((200, '{"v": 0.1, "n": "Card 4111111111111111"}'))
        out = self.client(opener).post("/a", {"k": 1}, what="x")
        self.assertEqual(out["v"], Decimal("0.1"))
        self.assertEqual(out["n"], "Card 411111***1111")
        self.assertEqual(opener.requests[0].get_header("Authorization"), f"Bearer {TOKEN}")
        self.assertEqual(opener.requests[0].full_url, "https://console.example.com/api/a")

    def test_403_and_404_are_named(self):
        with self.assertRaises(AccessDenied) as ctx:
            self.client(FakeOpener((403, '{"title": "Forbidden", "detail": "needs right X"}'))).post("/a", {}, what="w")
        self.assertIn("needs right X", str(ctx.exception))
        self.assertNotIn(TOKEN, str(ctx.exception))
        with self.assertRaises(NotSupported):
            self.client(FakeOpener((404, ""))).post("/a", {}, what="w")

    def test_one_retry_on_5xx_only(self):
        with mock.patch("time.sleep"):
            out = self.client(FakeOpener((503, ""), (200, "{}"))).post("/a", {}, what="w")
            self.assertEqual(out, {})
            with self.assertRaises(ApiError):
                self.client(FakeOpener((503, ""), (503, ""))).post("/a", {}, what="w")
            opener = FakeOpener((400, '{"title": "Bad"}'))
            with self.assertRaises(ApiError):
                self.client(opener).post("/a", {}, what="w")
            self.assertEqual(len(opener.requests), 1)


class RedirectTest(unittest.TestCase):
    """The token must never follow a redirect to an address the console chose."""

    def test_the_default_opener_does_not_follow_a_redirect(self):
        import http.server
        import ssl
        import urllib.request
        from gateway_insights_core.http import _default_opener

        seen = []

        class Target(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                seen.append(self.headers.get("Authorization"))
                self.send_response(200)
                self.end_headers()

            do_GET = do_POST    # urllib turns a POST into a GET on 302

            def log_message(self, *args):
                pass

        target = http.server.HTTPServer(("127.0.0.1", 0), Target)

        class Redirect(Target):
            def do_POST(self):
                self.send_response(302)
                self.send_header("Location", f"http://127.0.0.1:{target.server_port}/stolen")
                self.end_headers()

        origin = http.server.HTTPServer(("127.0.0.1", 0), Redirect)
        for server in (target, origin):
            threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            request = urllib.request.Request(f"http://127.0.0.1:{origin.server_port}/a", data=b"{}",
                                             headers={"Authorization": f"Bearer {TOKEN}"}, method="POST")
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                _default_opener(request, 5, ssl.create_default_context())
            self.assertEqual(ctx.exception.code, 302)
            self.assertEqual(seen, [])
        finally:
            for server in (target, origin):
                server.shutdown()
                server.server_close()

    def test_a_redirect_is_an_error_that_says_why(self):
        with self.assertRaises(ApiError) as ctx:
            ConsoleClient(profile(), frozenset({"/a"}), opener=FakeOpener((302, ""))).post("/a", {}, what="w")
        self.assertIn("redirect", str(ctx.exception))
        self.assertNotIn(TOKEN, str(ctx.exception))


def sample_spec():
    spec = report.new_spec("Earnings <b>", tool="ceo_test", period={"current": "2026-10-01 .. 2026-10-07",
                                                                     "base": "2026-09-24 .. 2026-09-30"},
                           currency="EUR")
    spec["summary"].append("Margin grew <script>x</script>")
    spec["kpis"].append(report.kpi("Margin", Decimal("10.5"), "money", base=Decimal("9"), delta=Decimal("1.5"),
                                   direction=1))
    spec["kpis"].append(report.kpi("Bank cost", report.HIDDEN, "money"))
    spec["sections"].append({
        "title": "By merchant",
        "chart": {"type": "bar", "categories": ["A <i>", "B"],
                  "series": [{"label": "now", "values": ["5", "3"]}]},
        "table": report.table([{"key": "name", "label": "Merchant", "unit": "text"},
                               {"key": "margin", "label": "Margin", "unit": "money"}],
                              [{"name": "Shop | <img src=x onerror=1>", "margin": report.HIDDEN},
                               {"name": "B", "margin": Decimal("3")}]),
    })
    spec["caveats"].append("Margins are summed over all statuses.")
    spec["provenance"].append({"tool": "exec_top", "path": "/exec-dashboard/top", "params": "metric=TOTAL_MARGIN"})
    return spec


class ReportTest(unittest.TestCase):
    def test_hidden_values_read_no_access_never_zero(self):
        text = report.to_markdown(sample_spec())
        self.assertIn(markdown.NO_ACCESS, text)
        self.assertNotIn("0.00 EUR", text)
        self.assertIn(markdown.UNTRUSTED_NOTE, text)

    def test_contract_violations_are_caught(self):
        spec = sample_spec()
        spec["kpis"][0]["unit"] = "furlongs"
        with self.assertRaises(report.SpecError):
            report.validate(spec)
        spec = sample_spec()
        spec["sections"][0]["chart"]["series"][0]["values"] = ["1"]
        with self.assertRaises(report.SpecError):
            report.validate(spec)


def write_theme(root: Path, **overrides) -> Path:
    data = json.loads((theme.NEUTRAL_DIR / "theme.json").read_text())
    data.update(overrides)
    root.mkdir(parents=True, exist_ok=True)
    (root / "theme.json").write_text(json.dumps(data))
    return root


class ThemeAndHtmlTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_neutral_theme_loads(self):
        t = theme.load_theme(None)
        self.assertEqual(t.name, "neutral")
        self.assertIsNone(t.logo_data_uri)

    def test_css_that_loads_anything_is_refused(self):
        for css in ("@import url(x.css);", "a{background:url(https://example.com/x.png)}",
                    "a{background:url('//example.com/x')}", "a{b:expression(1)}"):
            pack = write_theme(self.root / f"t{abs(hash(css))}")
            (pack / "extra.css").write_text(css)
            with self.assertRaises(ThemeError):
                theme.load_theme(str(pack))

    def test_logo_outside_the_pack_is_refused(self):
        outside = self.root / "outside.svg"
        outside.write_text("<svg xmlns='http://www.w3.org/2000/svg'/>")
        pack = write_theme(self.root / "p", logo="../outside.svg")
        with self.assertRaises(ThemeError):
            theme.load_theme(str(pack))

    def test_fonts_are_embedded_with_their_range(self):
        pack = write_theme(self.root / "f", font_files=[
            {"family": "Brand Sans", "weight": "500 600", "file": "fonts/a.woff2", "unicode_range": "U+0000-00FF, U+20AC"}])
        (pack / "fonts").mkdir()
        (pack / "fonts" / "a.woff2").write_bytes(b"wOF2fake")
        t = theme.load_theme(str(pack))
        self.assertIn('font-family:"Brand Sans";font-weight:500 600', t.font_faces_css)
        self.assertIn("unicode-range:U+0000-00FF, U+20AC;", t.font_faces_css)
        self.assertIn("data:font/woff2;base64,", t.font_faces_css)

    def test_bad_font_range_or_weight_is_refused(self):
        for item in ({"family": "B", "weight": "500", "file": "fonts/a.woff2", "unicode_range": "U+00;}x{"},
                     {"family": "B", "weight": "bold", "file": "fonts/a.woff2"}):
            pack = write_theme(self.root / f"g{abs(hash(str(item)))}", font_files=[item])
            (pack / "fonts").mkdir()
            (pack / "fonts" / "a.woff2").write_bytes(b"x")
            with self.assertRaises(ThemeError):
                theme.load_theme(str(pack))

    def test_bad_colour_is_refused(self):
        pack = write_theme(self.root / "c", colors={**json.loads(
            (theme.NEUTRAL_DIR / "theme.json").read_text())["colors"], "accent": "red;}body{x"})
        with self.assertRaises(ThemeError):
            theme.load_theme(str(pack))

    def assert_inert(self, page: str):
        self.assertIn("Content-Security-Policy", page)
        self.assertNotIn("<script", page.lower())
        self.assertNotIn("<img src=x", page)   # the hostile name is present only escaped
        stripped = page.replace("http://www.w3.org/2000/svg", "")
        self.assertNotIn("http://", stripped)
        self.assertNotIn("https://", stripped)

    def test_same_spec_renders_in_two_themes_and_loads_nothing(self):
        pack = write_theme(self.root / "brand", product_name="Brand X", logo="logo.svg",
                           heading_uppercase=True)
        (pack / "logo.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'><script>x</script></svg>")
        neutral = html.render_html(sample_spec(), theme.load_theme(None))
        branded = html.render_html(sample_spec(), theme.load_theme(str(pack)))
        for page in (neutral, branded):
            self.assert_inert(page)
            self.assertIn("&lt;script&gt;", page)
            self.assertIn(markdown.NO_ACCESS, page)
        self.assertIn("data:image/svg+xml;base64,", branded)
        self.assertIn("Brand X", branded)
        self.assertNotIn("Brand X", neutral)

    def test_report_file_name_carries_no_console_text(self):
        path = files.write_report("<html></html>", "ceo_briefing", str(self.root / "r"), datetime(2026, 10, 10, 9, 0))
        self.assertEqual(path.name, "ceo_briefing-20261010-090000.html")
        with self.assertRaises(InputError):
            files.write_report("", "../x", str(self.root / "r"), datetime(2026, 10, 10))

    @unittest.skipUnless(os.name == "posix", "file modes are POSIX")
    def test_existing_report_folder_keeps_its_mode(self):
        folder = self.root / "shared"
        folder.mkdir()
        os.chmod(folder, 0o755)
        files.write_report("x", "ceo_x", str(folder), datetime(2026, 10, 10))
        self.assertEqual(folder.stat().st_mode & 0o777, 0o755)


class McpTest(unittest.TestCase):
    def server(self, handler=None):
        s = mcp.Server(name="t", version="0")
        s.add(mcp.Tool("t_one", "d", mcp.schema({}), handler or (lambda a: mcp.ToolResult("ok", {"v": Decimal("1.5")}))))
        return s

    def init(self, s, version):
        return s.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": version}})

    def test_version_negotiation_and_structured_content(self):
        s = self.server()
        self.assertEqual(self.init(s, "2025-06-18")["result"]["protocolVersion"], "2025-06-18")
        res = s.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "t_one"}})["result"]
        self.assertEqual(res["structuredContent"], {"v": "1.5"})
        self.assertIn(markdown.UNTRUSTED_NOTE, res["content"][0]["text"])
        s2 = self.server()
        self.assertEqual(self.init(s2, "2024-11-05")["result"]["protocolVersion"], "2024-11-05")
        res2 = s2.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "t_one"}})["result"]
        self.assertNotIn("structuredContent", res2)

    def test_tools_are_read_only(self):
        tools = self.server().handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})["result"]["tools"]
        self.assertTrue(all(t["annotations"]["readOnlyHint"] for t in tools))

    def test_errors_become_messages(self):
        def boom(a):
            raise RuntimeError("card 4111111111111111")
        err = io.StringIO()
        with mock.patch("sys.stderr", err):
            res = self.server(boom).handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                            "params": {"name": "t_one"}})["result"]
        self.assertNotIn("4111111111111111", err.getvalue())
        self.assertIn("411111***1111", err.getvalue())
        self.assertTrue(res["isError"])
        self.assertNotIn("4111111111111111", res["content"][0]["text"])
        unknown = self.server().handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                        "params": {"name": "nope"}})["result"]
        self.assertTrue(unknown["isError"])
        self.assertIsNone(self.server().handle({"jsonrpc": "2.0", "method": "notifications/initialized"}))


if __name__ == "__main__":
    unittest.main()
