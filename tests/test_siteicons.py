"""Site card icons: link discovery, background removal, readability, sizing and refresh."""

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

import support  # noqa: F401 - puts the add-on app on the import path
from hermes import siteicons
from hermes.web.pages import site_avatar


def png(image) -> bytes:
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


def logo_on(background, logo, size=64, box=(16, 16, 48, 48)):
    image = Image.new("RGBA", (size, size), background)
    image.paste(Image.new("RGBA", (box[2] - box[0], box[3] - box[1]), logo), box[:2])
    return image


class FakeResponse:
    def __init__(self, status, content):
        self.status_code, self.content = status, content


class FakeSession:
    def __init__(self, answers):
        self.answers, self.calls = answers, []

    def get(self, url, **_kwargs):
        self.calls.append(url)
        return FakeResponse(*self.answers.get(url, (404, b"")))


class SiteIconTests(unittest.TestCase):
    def test_candidates_put_the_largest_icon_first_and_skip_svg(self):
        html = ("<link rel='icon' href='/f16.png' sizes='16x16'><link rel='apple-touch-icon' href='/t.png' sizes='180x180'>"
                "<link rel='icon' href='/logo.svg'><link rel='stylesheet' href='/a.css'>")
        self.assertEqual(siteicons.icon_candidates(html, "https://x.test/tr/"),
                         ["https://x.test/t.png", "https://x.test/f16.png", "https://x.test/favicon.ico"])

    def test_white_background_is_removed_and_bright_logo_keeps_its_colour(self):
        data = siteicons.clean_icon(logo_on((255, 255, 255, 255), (0, 200, 230, 255)), "#19d3d3")
        image = Image.open(io.BytesIO(data))
        self.assertEqual(image.size, (siteicons.CANVAS, siteicons.CANVAS))
        self.assertEqual(image.getpixel((2, 2))[3], 0)
        self.assertEqual(image.getpixel((78, 78))[:3], (0, 200, 230))

    def test_dark_logo_is_painted_in_the_site_colour(self):
        data = siteicons.clean_icon(logo_on((255, 255, 255, 255), (0, 0, 0, 255)), "#19d3d3")
        self.assertEqual(Image.open(io.BytesIO(data)).getpixel((78, 78))[:3], (0x19, 0xd3, 0xd3))

    def test_wide_and_square_logos_get_the_same_visual_area(self):
        square = Image.open(io.BytesIO(siteicons.clean_icon(logo_on((255, 255, 255, 255), (250, 200, 0, 255)), "#000000")))
        wide = Image.open(io.BytesIO(siteicons.clean_icon(
            logo_on((255, 255, 255, 255), (250, 200, 0, 255), size=200, box=(20, 68, 180, 132)), "#000000")))
        area = lambda image: sum(1 for p in image.getdata() if p[3] > 128)  # noqa: E731
        self.assertLess(abs(area(square) - area(wide)) / area(square), 0.15)

    def test_refresh_reads_the_page_then_the_largest_icon_and_stores_it(self):
        big = png(logo_on((255, 255, 255, 255), (230, 120, 0, 255), size=180, box=(40, 40, 140, 140)))
        session = FakeSession({"https://x.test/": (200, b"<link rel='apple-touch-icon' href='/t.png'>"),
                               "https://x.test/t.png": (200, big)})
        with tempfile.TemporaryDirectory() as folder:
            saved = siteicons.refresh("network", "https://x.test/", session, 5, Path(folder),
                                      fallback=lambda *_a: (403, b""), now=1000)
            self.assertTrue(saved)
            self.assertEqual(session.calls, ["https://x.test/", "https://x.test/t.png"])
            self.assertTrue((Path(folder) / "network.png").exists())
            state = json.loads((Path(folder) / "state.json").read_text())
            self.assertTrue(state["network"]["ok"])
            self.assertFalse(siteicons.is_due("network", now=1000 + 3600, directory=Path(folder)))
            self.assertTrue(siteicons.is_due("network", now=1000 + siteicons.REFRESH_SECONDS, directory=Path(folder)))

    def test_refused_site_uses_its_site_client_and_a_failure_retries_next_day(self):
        icon = png(logo_on((255, 255, 255, 255), (230, 120, 0, 255)))
        session = FakeSession({})
        used = []

        def site_client(url, _timeout):
            used.append(url)
            return (200, icon) if url.endswith("favicon.ico") else (403, b"")

        with tempfile.TemporaryDirectory() as folder:
            self.assertTrue(siteicons.refresh("zara", "https://z.test/", session, 5, Path(folder), fallback=site_client, now=0))
            self.assertIn("https://z.test/favicon.ico", used)
            self.assertFalse(siteicons.refresh("hm", "https://h.test/", session, 5, Path(folder),
                                               fallback=lambda *_a: (403, b"<html>"), now=0))
            self.assertFalse(siteicons.is_due("hm", now=3600, directory=Path(folder)))
            self.assertTrue(siteicons.is_due("hm", now=siteicons.RETRY_SECONDS, directory=Path(folder)))

    def test_html_answer_is_never_taken_as_an_icon(self):
        self.assertIsNone(siteicons.decode_icon(b"<!DOCTYPE html><html></html>"))

    def test_card_shows_letter_until_an_icon_exists(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(siteicons, "ICON_DIR", Path(folder)):
            self.assertIn(">A</div>", site_avatar("Amazon Depo"))
            (Path(folder) / "amazon.png").write_bytes(b"x")
            self.assertIn("src='site-icon/amazon.png?v=", site_avatar("Amazon Depo"))
            self.assertEqual(siteicons.icon_png("amazon"), b"x")
            self.assertIsNone(siteicons.icon_png("../state"))


if __name__ == "__main__":
    unittest.main()
