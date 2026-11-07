"""The source probe reads one MediaMarkt page and never follows redirects."""

import unittest
from unittest.mock import patch

from hermes.providers.mediamarkt_diagnostic import HOME, inspect_source


class MediaMarktDiagnosticTests(unittest.TestCase):
    def test_rejects_foreign_hosts_before_opening_session(self):
        with patch("hermes.providers.mediamarkt_diagnostic.requests.Session") as session:
            self.assertIn("error", inspect_source("https://www.mediamarkt.com.tr.example.com/tr/product/test"))
            session.assert_not_called()

    def test_only_reports_product_links_from_the_turkish_site(self):
        with patch("hermes.providers.mediamarkt_diagnostic.requests.Session") as session, \
                patch("hermes.providers.mediamarkt_diagnostic.cleaned_html", return_value="""
                <a href="/tr/product/_urun-123.html?tracking=1">Ürün</a>
                <a href="https://example.com/tr/product/_other.html">Diğer</a>
                """):
            get = session.return_value.__enter__.return_value.get
            get.return_value.status_code = 200
            self.assertEqual(inspect_source("")["products"], ["/tr/product/_urun-123.html"])
            self.assertEqual(get.call_args.args, (HOME,))
            self.assertFalse(get.call_args.kwargs["allow_redirects"])
