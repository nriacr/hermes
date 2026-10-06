"""Provider read flows: Amazon variants/search and the other sites' fetchers."""

import time
import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock, patch

import requests

from support import LOG_LINES, watch

from hermes.errors import BotProtectionHermesError, EmptySearchResultsHermesError, HermesError, OutOfStockHermesError
from hermes.constants import AMAZON_PRIORITY_INTERVAL_SECONDS, AMAZON_SLOWDOWN_RECOVER_SECONDS, AMAZON_SWEEP_INTERVAL_SECONDS
from hermes.models import OfferResult, SearchResultItem
from hermes.providers import amazon as amazon_reader
from hermes.providers.amazon import AmazonProvider
from hermes.providers.amazon import parser as amazon_parser
from hermes.providers.amazon.access import MAIN_LANE
from hermes.providers.amazon.client import AmazonClient
from hermes.providers.amazon.search import AmazonSearchCandidate
from hermes.providers.base import DEPO_LANE, ReadContext, WatchRead
from hermes.providers.bengurme import fetch_bengurme_page
from hermes.providers.beymenclub import fetch_beymenclub_page, fetch_beymenclub_size_summary
from hermes.providers.hepsiburada import HepsiburadaProvider, is_challenge_page as hepsiburada_challenge
from hermes.providers.nordbron import is_challenge_page as nordbron_challenge
from hermes.providers.zara import is_interstitial as zara_interstitial
from hermes.utils import extract_asin_from_url

ROOT = "https://www.amazon.com.tr/dp/B000000001"
CHILD = "https://www.amazon.com.tr/dp/B000000002"
UNAVAILABLE = '<span id="productTitle">iPhone</span><div id="availability">Şu anda mevcut değil.</div>'


def site_block(access):
    access.request_finished(True, "B000000001")


def priced(price: str = "100,00", title: str = "iPhone") -> str:
    return (f'<span id="productTitle">{title}</span><div id="corePrice_feature_div"><span class="a-price">'
            f'<span class="a-offscreen">{price} TL</span></span></div>')


def context(watch_names=None, lane: str = "") -> ReadContext:
    return ReadContext(timeout=10, session=requests.Session(), pace=Mock(), watch_names=watch_names or {}, lane=lane)


class AmazonTestCase(unittest.TestCase):
    def setUp(self):
        self.client = AmazonClient()
        self.provider = AmazonProvider(self.client)
        self.fetched = []

    def tearDown(self):
        self.provider.close()

    def serve(self, pages):
        """Answer fetches from a dict (or callable) of URL → HTML."""

        def fetch(url, _ctx, expect_search=False):
            self.fetched.append(url)
            page = pages(url) if callable(pages) else pages[url]
            if isinstance(page, Exception):
                raise page
            return page

        return patch.object(self.provider, "fetch", side_effect=fetch)

    def read(self, rule, outcome=None, ctx=None, fresh=True, lane=""):
        """One read; by default as the first read of a watch (a full sweep, nothing remembered).

        `fresh=False` is the next cycle: page caches are dropped, the rhythm memory stays.
        """
        if fresh:
            self.provider.rhythms.clear()
        else:
            self.provider.begin_cycle()
        return list(self.provider.read(rule, ctx or context(lane=lane), outcome or WatchRead()))


class AmazonRhythmTests(AmazonTestCase):
    """The configured page every 100 s, the variant family every 270 s."""

    def family(self, pages):
        variations = [amazon_parser.AmazonProductVariation("Gümüş", ROOT), amazon_parser.AmazonProductVariation("Turuncu", CHILD)]
        return (self.serve(pages), patch.object(amazon_parser, "extract_product_variations", return_value=variations))

    def test_a_watch_is_due_until_it_was_read_then_after_its_interval(self):
        product = watch(url=ROOT, include_variations=True)
        search = watch("Hue", "https://www.amazon.com.tr/s?k=hue")
        self.assertTrue(self.provider.read_due(product))
        with self.serve({ROOT: priced()}), patch.object(amazon_reader.time, "monotonic", return_value=1000):
            self.read(product)
            self.assertFalse(self.provider.read_due(product))
        for seconds, due in ((59, False), (60, True)):
            with patch.object(amazon_reader.time, "monotonic", return_value=1000 + seconds):
                self.assertEqual(self.provider.read_due(product), due)
        with patch.object(amazon_reader.time, "monotonic", return_value=1000):
            self.provider.rhythms[self.provider._rhythm_key(search)] = amazon_reader.WatchRhythm(main_at=1000)
        for seconds, due in ((269, False), (270, True)):
            with patch.object(amazon_reader.time, "monotonic", return_value=1000 + seconds):
                self.assertEqual(self.provider.read_due(search), due)

    def test_the_depo_lane_fetches_the_main_page_again_inside_the_same_cycle(self):
        rule = watch(url=ROOT, target="100000", include_variations=True)
        fetched = []

        def http_read(url, _timeout, _expect_search):
            fetched.append(url)
            return priced("100,00", "iPhone Gümüş")

        with patch.object(self.client, "_http_read", side_effect=http_read), \
                patch.object(amazon_reader.time, "monotonic", return_value=1000):
            list(self.provider.read(rule, context(), WatchRead()))  # the sweep
            first = len(fetched)
        with patch.object(self.client, "_http_read", side_effect=http_read), \
                patch.object(amazon_reader.time, "monotonic", return_value=1100):
            # The same cycle (no begin_cycle): a cached page of the sweep must not answer the Depo lane.
            list(self.provider.read(rule, context(lane=DEPO_LANE), WatchRead()))
        self.assertEqual(len(fetched) - first, 1)
        self.assertEqual(self.client.lane, "")
        self.assertIsNone(getattr(self.provider._lane_local, "caches", None))

    def test_a_watch_is_not_due_for_the_other_lane_while_it_is_being_read(self):
        rule = watch(url=ROOT, include_variations=True)
        with self.serve({ROOT: priced()}), patch.object(amazon_reader.time, "monotonic", return_value=1000):
            reads = self.provider.read(rule, context(), WatchRead())
            # Marked busy from the moment the read starts until it ends.
            self.assertTrue(self.provider._is_busy(self.provider._rhythm_key(rule)))
            self.assertFalse(self.provider.read_due(rule))
            list(reads)
            self.assertFalse(self.provider._is_busy(self.provider._rhythm_key(rule)))

    def test_a_failed_search_read_does_not_leave_the_watch_busy(self):
        rule = watch("Hue", "https://www.amazon.com.tr/s?k=hue")
        with patch.object(self.provider, "read_search", side_effect=HermesError("x")):
            with self.assertRaises(HermesError):
                self.provider.read(rule, context(), WatchRead())
        self.assertFalse(self.provider._is_busy(self.provider._rhythm_key(rule)))

    def test_the_depo_lane_may_read_any_remembered_family_and_every_product_without_variants(self):
        family = watch(url=ROOT, include_variations=True)
        single = watch("Tek", url=CHILD)
        search = watch("Hue", "https://www.amazon.com.tr/s?k=hue")
        self.assertFalse(self.provider.next_read_is_main(family))  # nothing remembered yet
        self.assertTrue(self.provider.needs_sweep(family))
        with self.serve({ROOT: priced()}), patch.object(amazon_reader.time, "monotonic", return_value=1000):
            self.read(family)
            self.assertTrue(self.provider.next_read_is_main(family))
            self.assertFalse(self.provider.needs_sweep(family))
        with patch.object(amazon_reader.time, "monotonic", return_value=1270):
            # The sweep is due, but the Depo lane may still read the main page.
            self.assertTrue(self.provider.next_read_is_main(family))
            self.assertTrue(self.provider.needs_sweep(family))
        with patch.object(amazon_reader.time, "monotonic", return_value=99999):
            self.assertTrue(self.provider.next_read_is_main(family))
        self.assertTrue(self.provider.next_read_is_main(single))
        self.assertFalse(self.provider.needs_sweep(single))  # nothing to sweep: the Depo lane reads its whole page
        self.assertFalse(self.provider.next_read_is_main(search))
        self.assertTrue(self.provider.needs_sweep(search))
        self.assertTrue(amazon_reader.AmazonProvider.has_depo_lane)

    def test_the_depo_lane_reads_only_the_main_page_even_when_the_sweep_is_overdue(self):
        rule = watch(url=ROOT, target="100000", include_variations=True)
        pages = {ROOT: priced("100,00", "iPhone Gümüş"), CHILD: priced("200,00", "iPhone Turuncu")}
        serving, variations = self.family(pages)
        with serving, variations:
            with patch.object(amazon_reader.time, "monotonic", return_value=1000):
                self.read(rule)
            self.assertEqual(self.fetched, [ROOT, CHILD])
            pages[ROOT] = priced("90,00", "iPhone Gümüş")
            pages[CHILD] = priced("150,00", "iPhone Turuncu")
            # 30 minutes later (the sweep queue has not got to this family): the Depo lane still reads one page.
            with patch.object(amazon_reader.time, "monotonic", return_value=1000 + 1800):
                offers = self.read(rule, fresh=False, lane=DEPO_LANE)
            self.assertEqual(self.fetched, [ROOT, CHILD, ROOT])
            by_url = {offer.url: offer for offer in offers}
            self.assertEqual(by_url[ROOT].price, Decimal("90"))
            self.assertEqual(by_url[CHILD].price, Decimal("200"))  # the remembered sweep price
            self.assertIsNotNone(by_url[CHILD].checked_at)
            # The sweep queue (no lane) reads the whole family once its sweep is due.
            with patch.object(amazon_reader.time, "monotonic", return_value=1000 + 1900):
                offers = self.read(rule, fresh=False)
            self.assertEqual({offer.price for offer in offers}, {Decimal("90"), Decimal("150")})

    def test_the_depo_lane_reads_a_product_without_variants_in_full_in_its_lane(self):
        rule = watch("Tek", url=ROOT, target="100000")
        lanes = []

        def fetch(url, _ctx, expect_search=False):
            lanes.append(self.client.lane)
            return priced("100,00")

        with patch.object(self.provider, "fetch", side_effect=fetch):
            offers = self.read(rule, lane=DEPO_LANE)
        self.assertEqual(len(offers), 1)
        self.assertEqual(lanes, [MAIN_LANE])

    def test_the_gap_between_main_page_reads_goes_to_the_measurement_line(self):
        rule = watch(url=ROOT, include_variations=True)
        with self.serve({ROOT: priced()}):
            for moment in (1000, 1100, 1210):
                with patch.object(amazon_reader.time, "monotonic", return_value=moment):
                    self.read(rule, fresh=False)
        self.assertEqual([round(gap) for _at, gap in self.provider.main_gaps], [100, 110])
        LOG_LINES.clear()
        self.client.access._last_stats_log -= 601
        with patch.object(amazon_reader.time, "monotonic", return_value=1300):
            self.provider.begin_cycle()
        line = next(line for line in LOG_LINES if "Amazon ölçüm:" in line)
        self.assertIn("ana sayfa aralığı medyan/p90=110/110 sn (n=2)", line)
        self.assertIn("istek: depo şeridi=", line)

    def test_main_reads_rank_before_sweeps(self):
        rule = watch(url=ROOT, include_variations=True)
        self.assertEqual(self.provider.read_rank(rule), 1)  # nothing remembered: a sweep is coming
        with self.serve({ROOT: priced()}), patch.object(amazon_reader.time, "monotonic", return_value=1000):
            self.read(rule)
            self.assertEqual(self.provider.read_rank(rule), 0)  # next read is a main read
        with patch.object(amazon_reader.time, "monotonic", return_value=1270):
            self.assertEqual(self.provider.read_rank(rule), 1)  # the sweep is due again
        self.assertEqual(self.provider.read_rank(watch("Hue", "https://www.amazon.com.tr/s?k=hue")), 1)

    def test_main_read_fetches_only_the_configured_page_and_replays_the_other_variants(self):
        rule = watch(url=ROOT, target="100000", include_variations=True)
        pages = {ROOT: priced("100,00", "iPhone Gümüş"), CHILD: priced("200,00", "iPhone Turuncu")}
        serving, variations = self.family(pages)
        with serving, variations, patch.object(amazon_reader.time, "monotonic", return_value=1000):
            first = self.read(rule)
            self.assertEqual(self.fetched, [ROOT, CHILD])
            self.assertTrue(all(offer.checked_at is None for offer in first))
        pages[ROOT] = priced("90,00", "iPhone Gümüş")
        pages[CHILD] = priced("150,00", "iPhone Turuncu")
        with serving, variations, patch.object(amazon_reader.time, "monotonic", return_value=1100):
            second = self.read(rule, fresh=False)
        self.assertEqual(self.fetched, [ROOT, CHILD, ROOT])
        by_url = {offer.url: offer for offer in second}
        self.assertEqual(by_url[ROOT].price, Decimal("90"))
        self.assertIsNone(by_url[ROOT].checked_at)
        # The other variant is the sweep's price (200), not the new page (150), with the time it was read.
        self.assertEqual(by_url[CHILD].price, Decimal("200"))
        self.assertIsNotNone(by_url[CHILD].checked_at)

    def test_the_variant_sweep_runs_again_after_270_seconds(self):
        rule = watch(url=ROOT, target="100000", include_variations=True)
        pages = {ROOT: priced("100,00", "iPhone Gümüş"), CHILD: priced("200,00", "iPhone Turuncu")}
        serving, variations = self.family(pages)
        with serving, variations:
            with patch.object(amazon_reader.time, "monotonic", return_value=1000):
                self.read(rule)
            pages[CHILD] = priced("150,00", "iPhone Turuncu")
            with patch.object(amazon_reader.time, "monotonic", return_value=1269):
                self.assertEqual({o.price for o in self.read(rule, fresh=False)}, {Decimal("100"), Decimal("200")})
            with patch.object(amazon_reader.time, "monotonic", return_value=1270):
                self.assertEqual({o.price for o in self.read(rule, fresh=False)}, {Decimal("100"), Decimal("150")})
        self.assertEqual(self.fetched, [ROOT, CHILD, ROOT, ROOT, CHILD])

    def test_a_sold_out_main_page_drops_its_own_offer_but_not_the_others(self):
        rule = watch(url=ROOT, target="100000", include_variations=True)
        pages = {ROOT: priced("100,00", "iPhone Gümüş"), CHILD: priced("200,00", "iPhone Turuncu")}
        serving, variations = self.family(pages)
        with serving, variations:
            with patch.object(amazon_reader.time, "monotonic", return_value=1000):
                self.read(rule)
            pages[ROOT] = UNAVAILABLE
            outcome = WatchRead()
            with patch.object(amazon_reader.time, "monotonic", return_value=1100):
                offers = self.read(rule, outcome, fresh=False)
            self.assertEqual([offer.url for offer in offers], [CHILD])
            self.assertEqual(outcome.unavailable[0]["product_url"], ROOT)
            # And the next main read does not bring the sold-out offer back from memory.
            with patch.object(amazon_reader.time, "monotonic", return_value=1200):
                self.assertEqual([offer.url for offer in self.read(rule, fresh=False)], [CHILD])

    def test_a_block_in_a_main_read_keeps_the_others_visible_and_reports_the_block(self):
        rule = watch(url=ROOT, target="100000", include_variations=True)
        pages = {ROOT: priced("100,00", "iPhone Gümüş"), CHILD: priced("200,00", "iPhone Turuncu")}
        serving, variations = self.family(pages)
        with serving, variations:
            with patch.object(amazon_reader.time, "monotonic", return_value=1000):
                self.read(rule)
            pages[ROOT] = BotProtectionHermesError("Amazon captcha")
            outcome = WatchRead()
            with patch.object(amazon_reader.time, "monotonic", return_value=1100):
                offers = self.read(rule, outcome, fresh=False)
        self.assertEqual([offer.url for offer in offers], [CHILD])
        self.assertIsInstance(outcome.blocked, BotProtectionHermesError)

    def test_a_block_with_nothing_remembered_raises(self):
        rule = watch(url=ROOT, target="100000", include_variations=True)
        with self.serve({ROOT: BotProtectionHermesError("Amazon captcha")}):
            with self.assertRaises(BotProtectionHermesError):
                self.read(rule)

    def test_a_watch_without_variants_is_read_in_full_every_time(self):
        rule = watch(url=ROOT, target="100000")
        with self.serve({ROOT: priced("100,00")}), patch.object(amazon_reader.time, "monotonic", return_value=1000):
            self.read(rule)
            self.read(rule, fresh=False)
        self.assertEqual(self.fetched, [ROOT, ROOT])

    def test_changed_settings_forget_the_memory(self):
        rule = watch(url=ROOT, include_variations=True)
        other = watch(url=ROOT, include_variations=True, excluded_terms=["1 TB"])
        self.assertNotEqual(self.provider._rhythm_key(rule), self.provider._rhythm_key(other))

    def test_depot_checks_are_counted(self):
        listing = '<a href="/gp/offer-listing/B000000001?condition=used">Kullanılmış teklifler</a>'
        depot = OfferResult("iPhone", Decimal("90"), "Amazon Depo", ROOT, True)
        with (self.serve(lambda url: "depot-listing"),
              patch.object(amazon_parser, "extract_verified_warehouse_offers_from_listing", side_effect=[[], [depot]])):
            self.provider.page_offers(ROOT, UNAVAILABLE + listing, context(), WatchRead())
        counters = self.client.access.counters
        self.assertEqual((counters["depo_sayfa"], counters["depo_liste"], counters["depo_dogrulanan"]), (1, 1, 1))

    def test_measurements_are_logged_every_ten_minutes(self):
        LOG_LINES.clear()
        self.client.access._last_stats_log -= 601
        self.provider.begin_cycle()
        self.provider.begin_cycle()
        lines = [line for line in LOG_LINES if "Amazon ölçüm:" in line]
        self.assertEqual(len(lines), 1)
        for expected in ("pencere=", "eşik=", "depo: sayfa kontrolü=", "ana sayfa okuması", "varyant taraması"):
            self.assertIn(expected, lines[0])


    def test_measurement_line_is_written_during_a_long_sweep(self):
        """Not only at a cycle start: every fetch may write it once ten minutes have passed."""
        LOG_LINES.clear()
        with patch.object(self.client, "fetch", return_value=priced()):
            self.provider.fetch(ROOT, context())
            self.assertEqual(sum("Amazon ölçüm:" in line for line in LOG_LINES), 0)
            self.client.access._last_stats_log -= 601
            self.provider.fetch(ROOT, context())
            self.provider.fetch(ROOT, context())
        self.assertEqual(sum("Amazon ölçüm:" in line for line in LOG_LINES), 1)

    def test_a_failed_fetch_still_writes_the_measurement_line(self):
        LOG_LINES.clear()
        self.client.access._last_stats_log -= 601
        with patch.object(self.client, "fetch", side_effect=BotProtectionHermesError("captcha")):
            with self.assertRaises(BotProtectionHermesError):
                self.provider.fetch(ROOT, context())
        self.assertEqual(sum("Amazon ölçüm:" in line for line in LOG_LINES), 1)


class AmazonCategoryRhythmTests(AmazonTestCase):
    """3.7.0: the priority category alone decides the reading interval: red 60 s, yellow hourly, green 3 hours."""

    def due_after(self, rule, seconds):
        with patch.object(amazon_reader.time, "monotonic", return_value=1000 + seconds):
            return self.provider.read_due(rule)

    def first_read(self, rule, price):
        with self.serve({ROOT: priced(price)}), patch.object(amazon_reader.time, "monotonic", return_value=1000):
            self.read(rule)

    def test_each_category_has_its_own_interval_whatever_the_price(self):
        for priority, interval in (("high", 60), ("medium", 3600), ("low", 3 * 3600)):
            with self.subTest(priority=priority):
                rule = watch(url=ROOT, target="1000", priority=priority)
                self.first_read(rule, "9.000,00")  # far above the target: the price changes nothing
                self.assertFalse(self.due_after(rule, interval - 1))
                self.assertTrue(self.due_after(rule, interval))

    def test_a_red_watch_far_above_its_target_is_still_read_every_minute(self):
        rule = watch(url=ROOT, target="3000", priority="high")
        self.first_read(rule, "7.000,00")
        self.assertTrue(self.due_after(rule, AMAZON_PRIORITY_INTERVAL_SECONDS["high"]))

    def test_a_block_wave_stretches_every_interval_and_they_return_by_themselves(self):
        rule = watch(url=ROOT, priority="high")
        self.first_read(rule, "100,00")
        access = self.client.access
        site_block(access)  # x2
        self.assertEqual(self.provider.main_interval(rule), 120)
        self.assertFalse(self.due_after(rule, 119))
        self.assertTrue(self.due_after(rule, 120))
        access.slowdown_since -= AMAZON_SLOWDOWN_RECOVER_SECONDS  # a clean stretch passes
        self.assertEqual(self.provider.main_interval(rule), 60)

    def test_a_yellow_or_green_family_is_swept_at_its_own_longer_interval(self):
        red, yellow = (watch(url=ROOT, include_variations=True, priority=p) for p in ("high", "medium"))
        self.assertEqual(self.provider.sweep_interval(red), AMAZON_SWEEP_INTERVAL_SECONDS)
        self.assertEqual(self.provider.sweep_interval(yellow), 3600)

    def test_a_watch_never_read_is_due_at_once(self):
        self.assertTrue(self.provider.read_due(watch(url=ROOT)))

class AmazonLaneTests(AmazonTestCase):
    def test_the_main_page_lane_is_set_only_while_it_reads(self):
        rule = watch(url=ROOT, target="100000", include_variations=True)
        lanes = []
        pages = {ROOT: priced("100,00"), CHILD: priced("200,00")}
        variations = [amazon_parser.AmazonProductVariation("Gümüş", ROOT), amazon_parser.AmazonProductVariation("Turuncu", CHILD)]

        def fetch(url, _ctx, expect_search=False):
            lanes.append(self.client.lane)
            return pages[url]

        with (patch.object(self.provider, "fetch", side_effect=fetch),
              patch.object(amazon_parser, "extract_product_variations", return_value=variations),
              patch.object(amazon_reader.time, "monotonic", return_value=1000)):
            self.read(rule)
            self.read(rule, fresh=False, lane=DEPO_LANE)
        self.assertEqual(lanes, ["", "", MAIN_LANE])
        self.assertEqual(self.client.lane, "")


class AmazonProductTests(AmazonTestCase):
    def test_missing_root_price_still_visits_all_discovered_variants(self):
        rule = watch(url=ROOT, include_variations=True)
        variations = [amazon_parser.AmazonProductVariation("Gümüş", ROOT), amazon_parser.AmazonProductVariation("Turuncu", CHILD)]
        outcome = WatchRead()
        with (self.serve({ROOT: UNAVAILABLE, CHILD: priced()}),
              patch.object(amazon_parser, "extract_product_variations", return_value=variations)):
            offers = self.read(rule, outcome)
        self.assertEqual(self.fetched, [ROOT, CHILD])
        self.assertEqual([offer.url for offer in offers], [CHILD])
        self.assertEqual(outcome.unavailable[0]["product_url"], ROOT)

    def test_depot_listing_is_read_without_a_new_product_price(self):
        html = UNAVAILABLE + '<a href="/gp/offer-listing/B000000001?condition=used">Kullanılmış teklifler</a>'
        depot = OfferResult("iPhone", Decimal("90"), "Amazon Depo", ROOT, True)
        with (self.serve(lambda url: "depot-listing"),
              patch.object(amazon_parser, "extract_verified_warehouse_offers_from_listing", side_effect=[[], [depot]])):
            offers = self.provider.page_offers(ROOT, html, context(), WatchRead())
        self.assertEqual(len(self.fetched), 1)
        self.assertEqual(offers, [depot])

    def test_no_offer_probe_is_bounded_without_hiding_priced_siblings(self):
        rule = watch(url=ROOT, include_variations=True)
        pages = {ROOT: UNAVAILABLE, CHILD: priced("100,00")}
        variations = [amazon_parser.AmazonProductVariation("Gümüş", ROOT), amazon_parser.AmazonProductVariation("Turuncu", CHILD)]
        with (self.serve(pages), patch.object(amazon_parser, "extract_product_variations", return_value=variations),
              patch.object(amazon_reader.time, "monotonic", return_value=100)):
            for price in ("100,00", "101,00"):
                pages[CHILD] = priced(price)
                self.provider.begin_cycle()
                offers = self.read(rule)
            self.assertEqual(self.fetched, [ROOT, CHILD, CHILD])
            self.assertEqual(offers[0].price, Decimal("101"))
            # The expiry is fixed: looking at the cached absence never postpones its probe.
            pages[ROOT] = pages[CHILD]
            with patch.object(amazon_reader.time, "monotonic", return_value=401):
                self.provider.begin_cycle()
                offers = self.read(rule)
        self.assertEqual(self.fetched, [ROOT, CHILD, CHILD, ROOT, CHILD])
        self.assertEqual({offer.url for offer in offers}, {ROOT, CHILD})
        self.assertFalse(self.client.unavailable_product_pages)

    def test_missing_price_probe_is_bounded_but_never_becomes_fake_stock(self):
        rule = watch(url=ROOT)
        with self.serve({ROOT: '<span id="productTitle">iPhone</span>'}):
            for _ in range(2):
                self.provider.begin_cycle()
                with self.assertRaises(HermesError) as caught:
                    self.read(rule)
                self.assertNotIsInstance(caught.exception, OutOfStockHermesError)
        self.assertEqual(self.fetched, [ROOT])
        self.assertEqual(len(self.client.unavailable_product_pages), 1)

    def test_access_failure_never_enters_the_no_offer_cache(self):
        with self.serve({ROOT: BotProtectionHermesError("Amazon captcha")}):
            with self.assertRaisesRegex(HermesError, "captcha"):
                self.read(watch(url=ROOT))
        self.assertFalse(self.client.unavailable_product_pages)

    def test_all_unavailable_family_reports_its_next_useful_probe(self):
        outcome = WatchRead()
        with self.serve({ROOT: UNAVAILABLE}):
            with self.assertRaises(OutOfStockHermesError):
                self.read(watch(url=ROOT), outcome)
        self.assertIsNotNone(outcome.retry_after)

    def test_walks_color_capacity_graph_and_yields_depot_immediately(self):
        rule = watch(url=ROOT, target="100000", include_variations=True)

        def page_for(url):
            index = int(extract_asin_from_url(url)[-1]) - 1
            color, capacity = divmod(index, 3)
            neighbors = {color * 3 + n for n in range(3)} | {n * 3 + capacity for n in range(3)}
            swatches = "".join(f'<li data-asin="B00000000{n + 1}" class="swatchUnavailable">Option {n}</li>' for n in sorted(neighbors))
            return (f'<span id="productTitle">iPhone color {color} capacity {capacity}</span>'
                    f'<div id="variation_size_name"><ul>{swatches}</ul></div>'
                    f'<div id="corePriceDisplay_desktop_feature_div"><span class="a-price"><span class="a-offscreen">{120000 + index},00 TL</span></span></div>'
                    f'<div id="usedBuySection">Kullanılmış ve yeni gibi Satıcı: Amazon Depo'
                    f'<span class="a-price"><span class="a-offscreen">{90000 + index},87 TL</span></span></div>')

        with (self.serve(page_for),
              patch.object(amazon_parser, "extract_product_variations", wraps=amazon_parser.extract_product_variations) as discover):
            stream = iter(self.provider.read(rule, context(), WatchRead()))
            first = next(stream)
            self.assertTrue(first.is_warehouse)
            self.assertEqual(len(self.fetched), 1)
            offers = [first, *stream]
        self.assertEqual(len(set(self.fetched)), 9)
        self.assertEqual(discover.call_count, 9)
        self.assertEqual(len(offers), 18)
        for offer in offers:
            index = int(extract_asin_from_url(offer.url)[-1]) - 1
            expected = Decimal(90000 + index) + Decimal(".87") if offer.is_warehouse else Decimal(120000 + index)
            self.assertEqual(offer.price, expected)

    def test_remembered_exclusions_lose_no_variant_of_a_color_capacity_grid(self):
        rule = watch(url=ROOT, target="100000", include_variations=True, excluded_terms=["capacity 2"])

        def page_for(url):
            index = int(extract_asin_from_url(url)[-1]) - 1
            color, capacity = divmod(index, 3)
            neighbors = {color * 3 + n for n in range(3)} | {n * 3 + capacity for n in range(3)}
            swatches = "".join(f'<li data-asin="B00000000{n + 1}" class="swatchUnavailable">Option {n}</li>' for n in sorted(neighbors))
            return (f'<span id="productTitle">iPhone color {color} capacity {capacity}</span>'
                    f'<div id="variation_size_name"><ul>{swatches}</ul></div>'
                    f'<div id="corePriceDisplay_desktop_feature_div"><span class="a-price"><span class="a-offscreen">{120000 + index},00 TL</span></span></div>')

        with self.serve(page_for):
            first = {offer.url for offer in self.read(rule)}
            first_fetches = len(self.fetched)
            self.provider.begin_cycle()
            second = {offer.url for offer in self.read(rule)}
        self.assertEqual(first_fetches, 9)
        # Capacity 2 is excluded: 3 of 9 pages, read once for their neighbours, then taken from memory.
        self.assertEqual(len(first), 6)
        self.assertEqual(second, first)
        self.assertEqual(len(self.fetched) - first_fetches, 6)
        self.assertEqual(self.client.access.counters["hariç_atlanan"], 3)

    def test_exclusions_skip_offer_read_but_still_expand_and_can_upgrade_cache(self):
        urls = [f"https://www.amazon.com.tr/dp/B00000000{number}" for number in range(1, 4)]
        labels = {urls[0]: "256 GB", urls[1]: "1 TB", urls[2]: "512 GB"}
        variations = [amazon_parser.AmazonProductVariation(labels[item], item) for item in urls]
        filtered = watch(url=ROOT, target="100000", include_variations=True, excluded_terms=["1 TB"])
        unfiltered = watch("iPhone full", url=ROOT, target="100000", include_variations=True)

        def offers_for(page_url, _html, _ctx, _outcome, soup=None):
            return [OfferResult(f"Apple iPhone {labels[page_url]}", Decimal("90000"))]

        LOG_LINES.clear()
        with (self.serve(lambda url: f'<span id="productTitle">Apple iPhone {labels[url]}</span>'),
              patch.object(amazon_parser, "extract_product_variations", return_value=variations) as discover,
              patch.object(self.provider, "page_offers", side_effect=offers_for) as offer_reader):
            self.assertEqual([offer.url for offer in self.read(filtered)], [urls[0], urls[2]])
            self.assertEqual(len(self.fetched), 3)
            self.assertEqual(offer_reader.call_count, 2)
            self.assertEqual([offer.url for offer in self.read(filtered)], [urls[0], urls[2]])
            self.assertEqual(len(self.fetched), 3)
            unfiltered_offers = self.read(unfiltered)
        self.assertEqual([offer.url for offer in unfiltered_offers], urls)
        self.assertEqual(self.fetched, [*urls, urls[1]])
        self.assertEqual(offer_reader.call_count, 3)
        self.assertEqual(discover.call_count, 4)
        self.assertTrue(any("hariç tut filtresi: 1 TB" in line for line in LOG_LINES))
        self.assertIn("hariç_nedeniyle_fiyat_okuması_atlandı=1",
                      next(line for line in LOG_LINES if "Amazon varyasyon taraması:" in line))

    def test_every_variant_page_is_inspected_even_with_a_collapsed_family_list(self):
        rule = watch(url=ROOT, target="100000", include_variations=True)
        urls = [f"https://www.amazon.com.tr/dp/B00000000{number}" for number in range(1, 4)]
        variations = [amazon_parser.AmazonProductVariation(str(number), url) for number, url in enumerate(urls)]
        html = ('<script type="a-state" data-a-state=\'{"key":"twister-plus-desktop-inline-twister-collapse-view-asins-data"}\'>'
                '{"asinsInCollapsedView":["B000000002","B000000003"]}</script>')
        with (self.serve(lambda _url: html),
              patch.object(amazon_parser, "extract_product_variations", return_value=variations) as discover,
              patch.object(amazon_parser, "selected_variation_label", return_value="Gümüş"),
              patch.object(self.provider, "page_offers", side_effect=lambda url, *_a, **_k: [OfferResult("iPhone", Decimal("90000"))]) as reader):
            offers = self.read(rule)
        self.assertEqual(self.fetched, urls)
        self.assertEqual(discover.call_count, 3)
        self.assertEqual(reader.call_count, 3)
        self.assertEqual([offer.url for offer in offers], urls)

    def test_excluded_variants_are_requested_once_then_remembered_with_their_neighbours(self):
        rule = watch(url=ROOT, target="100000", include_variations=True, excluded_terms=["1 TB"])
        variants = {"B000000001": "256 GB", "B000000002": "1 TB", "B000000003": "512 GB"}

        def page_for(url):
            asin = extract_asin_from_url(url)
            swatches = "".join(f'<li data-asin="{item}" class="swatchUnavailable">{label}</li>' for item, label in variants.items())
            return (f'<div id="variation_size_name"><ul>{swatches}</ul><span class="selection">{variants[asin]}</span></div>'
                    f'<span id="productTitle">iPhone {variants[asin]}</span>'
                    '<div id="corePriceDisplay_desktop_feature_div"><span class="a-price"><span class="a-offscreen">100.000,00 TL</span></span></div>')

        with self.serve(page_for):
            for _ in range(2):
                self.provider.begin_cycle()
                self.assertEqual(len(self.read(rule)), 2)
        asins = [extract_asin_from_url(url) for url in self.fetched]
        # The excluded 1 TB page is read in the first cycle only (for its neighbours); the second cycle skips it.
        self.assertEqual(asins, ["B000000001", "B000000002", "B000000003", "B000000001", "B000000003"])
        self.assertEqual(self.client.access.counters["hariç_atlanan"], 1)

    def test_remembered_exclusion_expires_and_follows_the_watchs_terms(self):
        rule = watch(url=ROOT, include_variations=True, excluded_terms=["1 TB"])
        self.provider._remember_exclusion(CHILD, rule, [])
        self.assertIsNotNone(self.provider._remembered_exclusion(CHILD, rule))
        changed = watch(url=ROOT, include_variations=True, excluded_terms=["2 TB"])
        self.assertIsNone(self.provider._remembered_exclusion(CHILD, changed))
        self.provider._remember_exclusion(CHILD, rule, [])
        entry = self.client.excluded_pages[CHILD]
        self.assertGreater(entry["refresh_at"], time.monotonic() + 15 * 60 - 1)
        self.assertLess(entry["refresh_at"], time.monotonic() + 45 * 60 + 1)
        with patch.object(amazon_reader.time, "monotonic", return_value=entry["refresh_at"] + 1):
            self.assertIsNone(self.provider._remembered_exclusion(CHILD, rule))

    def test_parsed_product_page_is_shared_between_watches_in_one_cycle(self):
        rules = [watch(url=ROOT, target="100000", include_variations=True),
                 watch("Telefon fırsatı", url=ROOT, target="95000", include_variations=True)]
        offer = OfferResult("iPhone Gümüş", Decimal("90000"), is_warehouse=True)
        with (self.serve(lambda _url: "html"),
              patch.object(amazon_parser, "parse_product_page", return_value=object()) as parse,
              patch.object(amazon_parser, "extract_product_variations", return_value=[]) as variations,
              patch.object(amazon_parser, "selected_variation_label", return_value="Gümüş"),
              patch.object(amazon_parser, "extract_title", return_value="iPhone"),
              patch.object(self.provider, "page_offers", return_value=[offer]) as extract):
            results = [self.read(rule) for rule in rules]
        self.assertEqual(len(self.fetched), 1)
        self.assertEqual((parse.call_count, variations.call_count, extract.call_count), (1, 1, 1))
        self.assertEqual([len(result) for result in results], [1, 1])

    def test_all_enabled_color_variations_are_read_with_their_labels(self):
        rule = watch("Tablet", url="https://www.amazon.com.tr/dp/B000000001?th=1", target="20000", include_variations=True)
        urls = [rule.url, "https://www.amazon.com.tr/dp/B000000002?psc=1", "https://www.amazon.com.tr/dp/B000000003?psc=1"]
        variations = [SimpleNamespace(label=label, url=url) for label, url in zip(("Antrasit", "Mavi", "Pembe"), urls)]
        with (self.serve(lambda url: url), patch.object(amazon_parser, "extract_product_variations", return_value=variations),
              patch.object(amazon_parser, "extract_offers",
                           side_effect=lambda html, source_url, soup=None: [OfferResult("Örnek tablet", Decimal("18999"), "Amazon", source_url)])):
            offers = self.read(rule)
        self.assertEqual([offer.url for offer in offers], urls)
        self.assertEqual([offer.title for offer in offers],
                         ["Örnek tablet / Antrasit", "Örnek tablet / Mavi", "Örnek tablet / Pembe"])
        self.assertEqual(self.fetched[0], rule.url)
        self.assertEqual(len(self.fetched), 3)

    def test_variant_scan_stops_after_a_protection_page(self):
        with self.serve({ROOT: BotProtectionHermesError("Amazon captcha")}):
            with self.assertRaisesRegex(HermesError, "captcha"):
                self.read(watch(url=ROOT, include_variations=True))
        self.assertEqual(len(self.fetched), 1)

    def test_partial_family_keeps_yielded_offers_and_reports_the_block(self):
        variations = [amazon_parser.AmazonProductVariation("Gümüş", ROOT), amazon_parser.AmazonProductVariation("Turuncu", CHILD)]
        outcome = WatchRead()
        with (self.serve({ROOT: priced(), CHILD: BotProtectionHermesError("Amazon captcha")}),
              patch.object(amazon_parser, "extract_product_variations", return_value=variations)):
            offers = self.read(watch(url=ROOT, include_variations=True), outcome)
        self.assertEqual(len(offers), 1)
        self.assertIsInstance(outcome.blocked, BotProtectionHermesError)

    def test_variations_are_only_followed_when_enabled(self):
        variations = [amazon_parser.AmazonProductVariation("Gümüş", ROOT), amazon_parser.AmazonProductVariation("Turuncu", CHILD)]
        with self.serve({ROOT: priced(), CHILD: priced()}), \
                patch.object(amazon_parser, "extract_product_variations", return_value=variations):
            self.read(watch(url=ROOT))
        self.assertEqual(self.fetched, [ROOT])


class AmazonSearchTests(AmazonTestCase):
    def test_depot_no_results_notice_ignores_all_category_fallback(self):
        html = ('<div id="search"><h2>Tüm Kategoriler içindeki sonuçlar gösteriliyor</h2><h3>Amazon Depo içinde <b>juo 240w</b> '
                'için sonuç bulunamadı</h3><div class="s-main-slot"><div data-component-type="s-search-result" data-asin="B000000001">'
                '<h2><a href="/dp/B000000001"><span>Juo 240W</span></a></h2><span class="a-price"><span class="a-offscreen">100,00 TL</span>'
                '</span><span>Kullanılmış Amazon Depo</span></div></div></div>')
        rule = watch("Juo 240W", "https://www.amazon.com.tr/s?k=juo+240w&i=warehouse-deals")
        with self.serve({rule.url: html}), patch.object(self.provider, "_detail_offers") as detail:
            with self.assertRaises(EmptySearchResultsHermesError) as caught:
                self.read(rule)
        self.assertTrue(caught.exception.no_results_notice)
        detail.assert_not_called()

    def test_excluded_cards_are_skipped_before_detail_requests(self):
        rule = watch(url="https://www.amazon.com.tr/s?k=iphone", target="100000", excluded_terms=["1 TB"])
        candidates = [AmazonSearchCandidate("iPhone 1 TB", ROOT, Decimal("100000")),
                      AmazonSearchCandidate("iPhone 256 GB", CHILD, Decimal("90000"))]
        with (self.serve(lambda _url: "html"), patch.object(amazon_reader, "extract_result_candidates", return_value=candidates),
              patch.object(self.provider, "_detail_offers", return_value=[]) as detail):
            offers = self.read(rule)
        self.assertEqual([offer.title for offer in offers], ["iPhone 256 GB"])
        self.assertEqual(detail.call_count, 1)
        self.assertEqual(detail.call_args.args[0].title, "iPhone 256 GB")

    def test_read_card_is_kept_but_details_stop_on_captcha(self):
        rule = watch(url="https://www.amazon.com.tr/s?k=iphone", target="100000")
        candidates = [AmazonSearchCandidate("iPhone 256 GB", ROOT, Decimal("90000")),
                      AmazonSearchCandidate("iPhone 512 GB", CHILD, Decimal("120000"))]
        outcome = WatchRead()
        with (self.serve(lambda _url: "html"), patch.object(amazon_reader, "extract_result_candidates", return_value=candidates),
              patch.object(self.provider, "_detail_offers", side_effect=BotProtectionHermesError("Amazon captcha")) as detail):
            offers = self.read(rule, outcome)
        self.assertEqual([offer.title for offer in offers], ["iPhone 256 GB"])
        detail.assert_called_once()
        self.assertIsInstance(outcome.blocked, BotProtectionHermesError)

    def test_deep_scan_always_adds_a_verified_used_price(self):
        search_url = "https://www.amazon.com.tr/s?k=edifier+m60"
        search_html = ('<div class="s-main-slot"><div data-component-type="s-search-result" data-asin="B0D95QG8W4">'
                       '<h2><a href="/dp/B0D95QG8W4"><span>Edifier M60 Compact Masa Hoparlörü - Siyah</span></a></h2>'
                       '<span class="a-price"><span class="a-offscreen">8.899,00 TL</span></span></div></div>')
        detail_html = ('<html><head><title>Edifier M60 Compact Masa Hoparlörü - Siyah</title></head><body>'
                       '<div id="corePriceDisplay_desktop_feature_div"><span class="a-price"><span class="a-offscreen">8.899,00 TL</span></span></div>'
                       '<a href="/gp/offer-listing/B0D95QG8W4?condition=used">Yeni & İkinci El Ürün</a></body></html>')
        listing_html = ('<html><head><title>Edifier M60 Compact Masa Hoparlörü - Siyah</title></head><body><div class="aod-offer">'
                        '<span>İkinci El - Çok İyi</span><a>Amazon Depo</a><span class="a-price"><span class="a-offscreen">8.787,77 TL</span></span>'
                        '</div></body></html>')
        rule = watch("Edifier M60", search_url, target="9000")

        def pages(url):
            return search_html if url == search_url else listing_html if "offer-listing" in url else detail_html

        with self.serve(pages):
            offers = self.read(rule)
        self.assertEqual([offer.price for offer in offers], [Decimal("8899.00"), Decimal("8787.77")])
        self.assertEqual([offer.is_warehouse for offer in offers], [False, True])

    def test_warehouse_search_keeps_only_used_results(self):
        rule = watch("Edifier M60", "https://www.amazon.com.tr/s?k=edifier+m60&i=warehouse-deals", target="9000")
        candidates = [AmazonSearchCandidate("Edifier M60", ROOT, Decimal("8787.77"), is_warehouse=True),
                      AmazonSearchCandidate("Edifier M60 yeni", CHILD, Decimal("8899"))]
        with self.serve(lambda _url: "html"), patch.object(amazon_reader, "extract_result_candidates", return_value=candidates):
            offers = self.read(rule)
        self.assertEqual([(offer.title, offer.is_warehouse) for offer in offers], [("Edifier M60", True)])

    def test_overlapping_models_go_to_the_most_specific_configured_card(self):
        rule = watch("Apple iPhone 17 Pro", "https://www.amazon.com.tr/s?k=iphone+17+pro")
        candidates = [AmazonSearchCandidate("Apple iPhone 17 Pro 256 GB", ROOT, Decimal("100")),
                      AmazonSearchCandidate("Apple iPhone 17 Pro Max 256 GB", CHILD, Decimal("110"))]
        ctx = context({"amazon": ["Apple iPhone 17 Pro", "Apple iPhone 17 Pro Max"]})
        with (self.serve(lambda _url: "html"), patch.object(amazon_reader, "extract_result_candidates", return_value=candidates),
              patch.object(self.provider, "_detail_offers", return_value=[])):
            offers = self.read(rule, ctx=ctx)
        self.assertEqual([offer.url for offer in offers], [ROOT])


class AmazonSellerFilterTests(unittest.TestCase):
    def test_own_seller_option_keeps_amazon_new_offers_and_every_depot_offer(self):
        provider = AmazonProvider.__new__(AmazonProvider)
        rule = watch(url=ROOT, target="100000", official_seller_only=True)
        offers = [OfferResult("Üçüncü taraf", Decimal("90000"), "Başka Satıcı", ROOT),
                  OfferResult("Bilinmeyen", Decimal("91000"), None, ROOT),
                  OfferResult("Amazon sıfır", Decimal("100000"), "Amazon.com.tr", ROOT),
                  OfferResult("Depo", Decimal("89000"), "Amazon Depo", ROOT, True)]
        self.assertEqual([offer.title for offer in offers if provider.keeps_offer(rule, offer)], ["Amazon sıfır", "Depo"])
        rule.official_seller_only = False
        self.assertEqual(len([offer for offer in offers if provider.keeps_offer(rule, offer)]), 4)

    def test_search_results_without_matches_are_a_normal_empty_result(self):
        with self.assertRaises(EmptySearchResultsHermesError):
            amazon_reader.offers_from_search_results([SearchResultItem("Başka ürün", ROOT, Decimal("1"))], "iPhone")


class OtherSiteTests(unittest.TestCase):
    def test_nordbron_product_page_is_not_misread_as_a_challenge(self):
        html = ('<div class="product-detail_price__hYyw9"><span>₺ 3,900.00</span></div>'
                '<script>{"customerSettings":{"requireCaptchaValidation":true},"label":"robot"}</script>')
        self.assertFalse(nordbron_challenge(html))
        self.assertTrue(nordbron_challenge("captcha robot"))

    def test_zara_product_page_is_not_misread_as_a_challenge(self):
        html = ('<script type="application/ld+json">{"@type":"Product","name":"Zara ürün"}</script>'
                '<script>{"customerSettings":{"requireCaptchaValidation":true},"label":"robot"}</script>')
        self.assertFalse(zara_interstitial(html))
        self.assertTrue(zara_interstitial("bm-verify _sec/verify"))

    def test_hepsiburada_security_page_is_a_challenge(self):
        self.assertTrue(hepsiburada_challenge("<html>HBBlockAndCaptcha</html>"))
        self.assertFalse(hepsiburada_challenge("<html>Sepete ekle</html>"))

    def test_hepsiburada_search_titles_are_completed_from_the_variant_page(self):
        page = ("<html><body><h1>Samsung Galaxy Tab S10 FE+ 8GB 128GB SM-X620</h1><span>Kapasite:</span><strong>128 GB</strong>"
                "<span>Renk:</span><strong>Mavi</strong><div>18.299,00 TL</div><section>Ürün Bilgileri</section></body></html>")
        provider = HepsiburadaProvider()
        offer = OfferResult("Samsung Galaxy Tab S10 FE+ 8GB 128GB SM-X620", Decimal("18049"), "Hepsiburada",
                            "https://www.hepsiburada.com/samsung-tablet-p-HBCV00008E1SXR")
        with patch.object(provider, "_page", return_value=page):
            enriched = provider._with_variant_titles(context(), [offer])
        self.assertEqual(enriched[0].title, "Samsung Galaxy Tab S10 FE+ 8GB 128GB SM-X620 / 128 GB / Mavi")

    def test_bengurme_fetch_prefers_shopify_variant_json(self):
        response = SimpleNamespace(status_code=200, headers={"content-type": "application/json"}, encoding="utf-8",
                                   text='{"title":"Kilis Karası Kan Üzümü","variants":[]}', raise_for_status=lambda: None)
        response.content = response.text.encode("utf-8")
        session = Mock()
        session.get.return_value = response
        result = fetch_bengurme_page(session, "https://bengurme.com/products/kilis-karasi-kan-uzumu", 10)
        self.assertIs(result, response)
        session.get.assert_called_once()
        self.assertEqual(session.get.call_args.args[0], "https://bengurme.com/products/kilis-karasi-kan-uzumu.js")
        self.assertIn("Chrome/124", session.get.call_args.kwargs["headers"]["User-Agent"])

    def test_beymenclub_fetch_uses_its_browser_shaped_headers(self):
        response = SimpleNamespace(status_code=200, headers={"content-type": "text/html; charset=utf-8"},
                                   text="<script>BEYMEN.productMain = {}</script>", raise_for_status=lambda: None)
        response.content = response.text.encode("utf-8")
        session = Mock()
        session.get.return_value = response
        self.assertIs(fetch_beymenclub_page(session, "https://www.beymenclub.com/tr/p_test", 10), response)
        headers = session.get.call_args.kwargs["headers"]
        self.assertIn("Chrome/124", headers["User-Agent"])
        self.assertEqual(headers["Accept-Language"], "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7")

    def test_beymenclub_size_summary_uses_browser_session_headers(self):
        response = SimpleNamespace(status_code=200, raise_for_status=lambda: None,
                                   json=lambda: {"result": {"sizes": [{"sizeName": "XL", "inStock": True}]}})
        session = Mock()
        session.post.return_value = response
        payload = fetch_beymenclub_size_summary(session, "https://www.beymenclub.com/tr/p_test", 1941298, 10)
        self.assertEqual(payload["result"]["sizes"][0]["sizeName"], "XL")
        url, kwargs = session.post.call_args.args[0], session.post.call_args.kwargs
        self.assertEqual(url, "https://www.beymenclub.com/sf-api/api/product/1941298/productsummary")
        self.assertEqual(kwargs["headers"]["Origin"], "https://www.beymenclub.com")
        self.assertEqual(kwargs["headers"]["Sec-Fetch-Site"], "same-origin")


if __name__ == "__main__":
    unittest.main()
