"""Site card icons: the bundled app icons replace the letter on every card."""

import unittest

import support  # noqa: F401 - puts the add-on app on the import path
from hermes import siteicons
from hermes.providers.registry import PROVIDER_TYPES
from hermes.web.assets import APP_CSS
from hermes.web.pages import site_avatar


class SiteIconTests(unittest.TestCase):
    def test_every_site_ships_a_png_icon(self):
        for provider in PROVIDER_TYPES:
            data = siteicons.icon_png(provider.site)
            self.assertIsNotNone(data, provider.site)
            self.assertTrue(data.startswith(b"\x89PNG"), provider.site)

    def test_card_shows_the_app_icon_and_unknown_site_keeps_its_letter(self):
        self.assertIn("ov-logo-amazon", site_avatar("Amazon Depo"))
        self.assertIn("ov-logo-hm", site_avatar("H&M"))
        self.assertIn(">X</div>", site_avatar("Xshop"))

    def test_stylesheet_points_each_icon_next_to_itself(self):
        # Relative to app.css, so /public/<token>/app.css finds /public/<token>/site-icon/...
        for provider in PROVIDER_TYPES:
            self.assertIn(f".ov-av.ov-logo-{provider.site} {{ background-image:url(site-icon/{provider.site}.png?v=", APP_CSS)

    def test_only_plain_site_names_are_served(self):
        self.assertIsNone(siteicons.icon_png("../constants"))
        self.assertIsNone(siteicons.icon_png("yok"))


if __name__ == "__main__":
    unittest.main()
