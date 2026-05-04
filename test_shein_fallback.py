import unittest
import sys
import types
from unittest.mock import patch


fetchers_stub = types.ModuleType("scrapling.fetchers")
fetchers_stub.StealthyFetcher = object
sys.modules.setdefault("scrapling.fetchers", fetchers_stub)

from scrapper import (
    extract_product_images,
    _TerminalAntiBotBlock,
    _extract_shein_images_from_html,
    _extract_shein_product_params,
    _is_shein_risk_page,
    _normalize_shein_image_url,
    _shein_manual_wait_seconds,
)


class SheinFallbackTests(unittest.TestCase):
    def test_extracts_goods_id_and_mall_code_from_product_url(self):
        url = (
            "https://br.shein.com/Sweetina-Vestido-Slip-Mini-com-Decote-V-"
            "Profundo-e-Decora%C3%A7%C3%A3o-Floral-p-69127643.html?mallCode=1"
        )

        self.assertEqual(_extract_shein_product_params(url), ("69127643", "1"))

    def test_normalizes_protocol_relative_product_image(self):
        url = "//img.ltwebstatic.com/images3_pi/2025/03/24/e9/example_thumbnail_405x552.jpg"

        self.assertEqual(
            _normalize_shein_image_url(url),
            "https://img.ltwebstatic.com/images3_pi/2025/03/24/e9/example_thumbnail_405x552.jpg",
        )

    def test_extracts_images_from_shein_html_json_fields(self):
        html = """
        <html>
          <head>
            <meta property="og:image" content="//img.ltwebstatic.com/images3_pi/2025/03/24/e9/main.jpg">
          </head>
          <body>
            <script>
              window.product = {
                "goods_img": "//img.ltwebstatic.com/images3_pi/2025/03/24/e9/goods.jpg",
                "detail_image": [
                  "//img.ltwebstatic.com/images3_pi/2025/03/24/e9/detail-a.jpg",
                  "//img.ltwebstatic.com/images3_spmp/2025/03/24/e9/detail-b.webp"
                ]
              };
            </script>
          </body>
        </html>
        """

        images = set(_extract_shein_images_from_html(html, page_url="https://br.shein.com/item-p-69127643.html"))

        self.assertEqual(
            images,
            {
                "https://img.ltwebstatic.com/images3_pi/2025/03/24/e9/main.jpg",
                "https://img.ltwebstatic.com/images3_pi/2025/03/24/e9/goods.jpg",
                "https://img.ltwebstatic.com/images3_pi/2025/03/24/e9/detail-a.jpg",
                "https://img.ltwebstatic.com/images3_spmp/2025/03/24/e9/detail-b.webp",
            },
        )

    def test_rejects_risk_page_with_ltwebstatic_layout_assets(self):
        html = """
        <html>
          <body>
            <a href="/risk/challenge?captcha_type=903">challenge</a>
            <img src="//img.ltwebstatic.com/image/ar/n-ar-pop_v4dfb2bf.png">
            <img src="//img.ltwebstatic.com/images3_pi/2025/03/24/e9/should-not-pass.jpg">
          </body>
        </html>
        """

        self.assertTrue(_is_shein_risk_page("https://br.shein.com/risk/challenge?captcha_type=903", html))
        self.assertEqual(
            _extract_shein_images_from_html(
                html,
                page_url="https://br.shein.com/risk/challenge?captcha_type=903",
            ),
            [],
        )

    def test_rejects_non_product_ltwebstatic_asset(self):
        self.assertIsNone(
            _normalize_shein_image_url("//img.ltwebstatic.com/image/ar/n-ar-pop_v4dfb2bf.png")
        )

    def test_shein_manual_wait_only_applies_to_shein_domains(self):
        with patch.dict("os.environ", {"SHEIN_MANUAL_WAIT_SECONDS": "90"}):
            self.assertEqual(_shein_manual_wait_seconds("br.shein.com"), 90)
            self.assertEqual(_shein_manual_wait_seconds("example.com"), 0)

    def test_invalid_shein_manual_wait_is_ignored(self):
        with patch.dict("os.environ", {"SHEIN_MANUAL_WAIT_SECONDS": "invalid"}):
            self.assertEqual(_shein_manual_wait_seconds("br.shein.com"), 0)


class AutoRetryTests(unittest.TestCase):
    def test_learns_level_two_when_second_attempt_finds_images(self):
        calls = []

        def fake_once(url, session=None, wait_idle=False, escalation_level=1):
            calls.append((escalation_level, wait_idle))
            return [] if len(calls) == 1 else ["https://cdn.example.com/p.jpg"]

        with patch("scrapper.database.get_profile", return_value={}), \
             patch("scrapper._extract_product_images_once", side_effect=fake_once), \
             patch("scrapper.mark_escalation_required") as learn:
            images = extract_product_images("https://example.com/produto")

        self.assertEqual(images, ["https://cdn.example.com/p.jpg"])
        self.assertEqual(calls, [(1, False), (2, True)])
        learn.assert_called_once_with("example.com", 2)

    def test_returns_empty_after_three_empty_attempts_without_learning(self):
        calls = []

        def fake_once(url, session=None, wait_idle=False, escalation_level=1):
            calls.append((escalation_level, wait_idle))
            return []

        with patch("scrapper.database.get_profile", return_value={}), \
             patch("scrapper._extract_product_images_once", side_effect=fake_once), \
             patch("scrapper._extract_via_managed_api", return_value=[]) as api_fallback, \
             patch("scrapper.mark_escalation_required") as learn:
            images = extract_product_images("https://example.com/produto")

        self.assertEqual(images, [])
        self.assertEqual(calls, [(1, False), (2, True), (3, True)])
        api_fallback.assert_called_once_with(
            "https://example.com/produto",
            "example.com",
            reason="local_returned_zero_images",
        )
        learn.assert_not_called()

    def test_saved_level_three_never_tries_lower_levels(self):
        calls = []

        def fake_once(url, session=None, wait_idle=False, escalation_level=1):
            calls.append((escalation_level, wait_idle))
            return ["https://cdn.example.com/p.jpg"]

        with patch("scrapper.database.get_profile", return_value={"escalation_level": 3, "wait_idle": True}), \
             patch("scrapper._extract_product_images_once", side_effect=fake_once), \
             patch("scrapper.mark_escalation_required") as learn:
            images = extract_product_images("https://example.com/produto", escalation_level=1)

        self.assertEqual(images, ["https://cdn.example.com/p.jpg"])
        self.assertEqual(calls, [(3, True)])
        learn.assert_not_called()

    def test_success_on_first_attempt_does_not_retry_or_learn(self):
        with patch("scrapper.database.get_profile", return_value={}), \
             patch("scrapper._extract_product_images_once", return_value=["https://cdn.example.com/p.jpg"]) as once, \
             patch("scrapper.mark_escalation_required") as learn:
            images = extract_product_images("https://example.com/produto")

        self.assertEqual(images, ["https://cdn.example.com/p.jpg"])
        once.assert_called_once()
        learn.assert_not_called()

    def test_terminal_antibot_block_stops_retries(self):
        with patch("scrapper.database.get_profile", return_value={}), \
             patch("scrapper._extract_product_images_once", side_effect=_TerminalAntiBotBlock("blocked")) as once, \
             patch("scrapper._extract_via_managed_api", return_value=[]) as api_fallback, \
             patch("scrapper.mark_escalation_required") as learn:
            images = extract_product_images("https://br.shein.com/produto-p-123.html")

        self.assertEqual(images, [])
        once.assert_called_once()
        api_fallback.assert_called_once_with(
            "https://br.shein.com/produto-p-123.html",
            "br.shein.com",
            reason="blocked",
        )
        learn.assert_not_called()

    def test_login_wall_detection_stops_local_retries_and_uses_api(self):
        class Page:
            url = "https://example.com/captcha"
            html = "<html><body>captcha</body></html>"

        class Session:
            def fetch(self, url, network_idle=False):
                return Page()

        with patch("scrapper.database.get_profile", return_value={}), \
             patch("scrapper._attempt_captcha_bypass", return_value=[]), \
             patch("scrapper._extract_via_googlebot", return_value=[]), \
             patch("scrapper._extract_via_managed_api", return_value=["https://cdn.example.com/api.jpg"]) as api_fallback:
            images = extract_product_images("https://example.com/produto", session=Session())

        self.assertEqual(images, ["https://cdn.example.com/api.jpg"])
        api_fallback.assert_called_once()


class ManagedApiFallbackTests(unittest.TestCase):
    def test_local_success_does_not_call_managed_api(self):
        with patch("scrapper.database.get_profile", return_value={}), \
             patch("scrapper._extract_product_images_once", return_value=["https://cdn.example.com/p.jpg"]), \
             patch("scrapper._extract_via_managed_api") as api_fallback:
            images = extract_product_images("https://example.com/produto")

        self.assertEqual(images, ["https://cdn.example.com/p.jpg"])
        api_fallback.assert_not_called()

    def test_empty_local_result_uses_managed_api_result(self):
        with patch("scrapper.database.get_profile", return_value={}), \
             patch("scrapper._extract_product_images_once", return_value=[]), \
             patch("scrapper._extract_via_managed_api", return_value=["https://cdn.example.com/api.jpg"]) as api_fallback:
            images = extract_product_images("https://example.com/produto")

        self.assertEqual(images, ["https://cdn.example.com/api.jpg"])
        api_fallback.assert_called_once_with(
            "https://example.com/produto",
            "example.com",
            reason="local_returned_zero_images",
        )

    def test_missing_scrapedo_token_returns_empty_without_http_call(self):
        with patch.dict("os.environ", {"SCRAPING_API_FALLBACK": "scrapedo", "SCRAPEDO_TOKEN": ""}, clear=False), \
             patch("scrapper.requests.get") as get:
            images = __import__("scrapper")._extract_via_managed_api(
                "https://example.com/produto",
                "example.com",
                reason="local_returned_zero_images",
            )

        self.assertEqual(images, [])
        get.assert_not_called()

    def test_scrapedo_html_is_parsed_with_existing_heuristics(self):
        class Response:
            status_code = 200
            url = "https://api.scrape.do/?url=https%3A%2F%2Fexample.com%2Fproduto"
            text = '<html><head><meta property="og:image" content="/image.jpg"></head></html>'

        with patch.dict("os.environ", {"SCRAPING_API_FALLBACK": "scrapedo", "SCRAPEDO_TOKEN": "token"}, clear=False), \
             patch("scrapper.requests.get", return_value=Response()) as get:
            images = __import__("scrapper")._extract_via_managed_api(
                "https://example.com/produto",
                "example.com",
                reason="local_returned_zero_images",
            )

        self.assertEqual(images, ["https://example.com/image.jpg"])
        get.assert_called_once()

    def test_scrapedo_http_failure_returns_empty(self):
        with patch.dict("os.environ", {"SCRAPING_API_FALLBACK": "scrapedo", "SCRAPEDO_TOKEN": "token"}, clear=False), \
             patch("scrapper.requests.get", side_effect=TimeoutError("timeout")):
            images = __import__("scrapper")._extract_via_managed_api(
                "https://example.com/produto",
                "example.com",
                reason="local_returned_zero_images",
            )

        self.assertEqual(images, [])


if __name__ == "__main__":
    unittest.main()
