"""Independent incident episodes, honest read accounting and bounded shutdown."""
import unittest
from datetime import datetime, timezone
from unittest.mock import Mock, patch

from support import TempData, config, monitor, notifier, watch
from hermes.diagnostics import Diagnostics, runtime_metrics
from hermes.history import Read, read_reads
from hermes.models import OfferResult
from decimal import Decimal
from hermes.logging_utils import ProblemLog
from hermes.monitor.cycle import ReadCancelled
from hermes.monitor.runner import MonitorService
from hermes.providers.nordbron import NordbronProvider
from hermes.web.dashboard import collect_errors, render_errors
from hermes.web.statistics import _percent, site_figures, render_problem_history


class IncidentTests(unittest.TestCase):
    def setUp(self):
        self.data = TempData()
        self.d = Diagnostics(self.data.files.database)

    def tearDown(self):
        self.d.db.close()
        self.data.cleanup()

    def report(self, *variants):
        self.d.incident('watch', 'partial', 'Fiyat belirsiz', context={'site': 'amazon', 'failures': [
            {'product_title': 'Apple iPhone 17 Pro: pazarlama açıklaması', 'variant': variant,
             'product_url': 'https://example.test/' + variant, 'reason': 'Fiyat belirsiz'} for variant in variants]})

    def test_runtime_proof_excludes_previous_boot_and_interrupted_jobs(self):
        for started, result in ((100, 'ok'), (300, 'partial'), (300, 'interrupted'), (300, 'timeout')):
            with patch('hermes.diagnostics.time.time', return_value=started):
                job = self.d.start('amazon', 'watch')
                self.d.finish(job, 'amazon', result, 0)
        with patch('hermes.diagnostics.PROCESS_STARTED_AT', datetime.fromtimestamp(200, timezone.utc)):
            metrics = runtime_metrics(self.data.files.database)
        self.assertEqual(metrics['completed_jobs'], 4)
        self.assertEqual(metrics['current_process_completed_jobs'], 2)
        self.assertEqual(metrics['current_process_successful_jobs'], 1)

    def test_variants_have_independent_repeats_and_recovery(self):
        self.report('silver', 'red')
        self.report('silver')
        active = self.d.active()
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]['count'], 2)
        self.assertEqual(active[0]['context']['failures'][0]['variant'], 'silver')
        history = self.d.recent()
        self.assertEqual(len(history), 2)
        self.assertEqual(sum(item['resolved'] is not None for item in history), 1)

    def test_recurrence_keeps_previous_episode_across_restart(self):
        self.report('silver')
        self.d.recover('watch', 'Düzeldi')
        self.report('silver')
        self.d.db.close()
        self.d = Diagnostics(self.data.files.database)
        self.assertEqual(len(self.d.recent()), 2)
        self.assertEqual(self.d.active()[0]['count'], 1)

    def test_duplicate_variant_in_one_read_counts_once(self):
        self.report('silver', 'silver')
        self.assertEqual(self.d.active()[0]['count'], 1)

    def test_blocked_incomplete_scan_does_not_resolve_unvisited_variant(self):
        self.report('silver', 'red')
        self.d.incident('watch', 'read', 'HTTP 503', context={'failures':[
            {'product_url':'https://example.test/silver', 'reason':'HTTP 503'}]})
        self.assertEqual(len(self.d.active()), 2)
        self.assertEqual(max(item['count'] for item in self.d.active()), 2)

    def test_partial_attention_requires_repetition_and_ten_minutes(self):
        item = {'component':'watch', 'kind':'partial', 'count':3, 'opened':1000, 'updated':1599, 'recovery':''}
        self.assertFalse(self.d.needs_attention(item))
        item['updated'] = 1600
        self.assertTrue(self.d.needs_attention(item))
        item['count'] = 2
        self.assertFalse(self.d.needs_attention(item))

    def test_history_displays_resolved_episode_and_repeat_count(self):
        self.report('silver')
        self.report('silver')
        self.d.recover('watch', 'Düzeldi')
        with patch('hermes.web.statistics.DATABASE_PATH', self.data.files.database), patch('hermes.web.statistics.STATE_PATH', self.data.files.state):
            from datetime import timedelta
            html = render_problem_history(timedelta(hours=24))
        self.assertIn('Düzeldi · 2 tekrar', html)
        self.assertIn('href=', html)


class ShutdownTests(unittest.TestCase):
    def test_one_deadline_for_all_workers_and_no_cleanup_with_active_writer(self):
        service = object.__new__(MonitorService)
        service.stop = Mock()
        service.monitor = Mock()
        first, second = Mock(), Mock()
        first.is_alive.return_value = False
        second.is_alive.return_value = True
        with patch('hermes.monitor.runner.time.monotonic', side_effect=[10,10,45]):
            service._shutdown([first, second], budget=60)
        first.join.assert_called_once_with(timeout=60)
        second.join.assert_called_once_with(timeout=25)
        service.monitor.close.assert_not_called()

    def test_partial_provider_result_is_persisted_as_partial(self):
        data = TempData()
        rule = watch('Ürün', 'https://nordbron.com/x')
        app = monitor(config([rule]), data, notifier())
        def read(rule, ctx, outcome):
            outcome.errors.append('Fiyat belirsiz')
            outcome.error_details.append({'product_url':rule.url, 'product_title':'Ürün', 'reason':'Fiyat belirsiz'})
            return [OfferResult('Ürün', Decimal('100'), url=rule.url)]
        try:
            with patch.object(NordbronProvider, 'read', side_effect=read):
                app.run_cycle()
            records = read_reads(data.files.database, datetime(2000,1,1,tzinfo=timezone.utc))
            self.assertEqual([row.outcome for row in records], ['partial'])
            self.assertEqual(app.diagnostics.active()[0]['kind'], 'partial')
        finally:
            app.close()
            data.cleanup()

    def test_stop_during_pacing_is_interrupted_without_error_or_price(self):
        data = TempData()
        rule = watch('Ürün', 'https://nordbron.com/x')
        app = monitor(config([rule]), data, notifier())
        stopped = [False]
        app.should_stop = lambda: stopped[0]
        app.pace = lambda label: stopped.__setitem__(0, True)
        try:
            with patch.object(NordbronProvider, 'read') as read:
                app.run_cycle()
            read.assert_not_called()
            self.assertEqual(app.diagnostics.active(), [])
            records = read_reads(data.files.database, datetime(2000,1,1,tzinfo=timezone.utc))
            self.assertEqual([row.outcome for row in records], ['interrupted'])
        finally:
            app.close(shutdown=True)
            data.cleanup()

    def test_shutdown_keeps_unsent_outbox(self):
        data = TempData()
        transport = notifier()
        app = monitor(config([]), data, transport)
        app.delivery.enqueue({'title':'Uyarı','message':'Test'}, 'shutdown-test')
        app.close(shutdown=True)
        d = Diagnostics(data.files.database)
        try:
            transport.send.assert_not_called()
            self.assertEqual(d.db.connect().execute('SELECT state FROM outbox').fetchone(), ('pending',))
        finally:
            d.db.close()
            data.cleanup()

    def test_cancellation_is_not_a_provider_failure(self):
        self.assertFalse(issubclass(ReadCancelled, Exception))


class CompactAndAccountingTests(unittest.TestCase):
    def test_partial_does_not_count_as_success(self):
        now = datetime.now(timezone.utc)
        figures = site_figures([Read(now, 'amazon', outcome, 100, 'w','cycle') for outcome in ('ok','partial','stock','empty')], [])[0]
        self.assertEqual((figures.ok, figures.partial, figures.errors), (3,1,0))
        self.assertEqual(_percent(9999,10000), '%99,9')
        self.assertEqual(_percent(10000,10000), '%100')

    def test_stock_absence_is_not_an_error_row(self):
        state = {'offer':{'last_error':'Ürün son okumada bulunamadı.', 'last_checked_at':datetime.now(timezone.utc).isoformat()}}
        self.assertEqual(collect_errors(state), [])

    def test_compact_row_has_own_short_model_variant_reason_link(self):
        html = render_errors([{'title':'Amazon: Apple iPhone 17 Pro: büyük pazarlama açıklaması',
            'variant':'256 GB / Gümüş Rengi', 'message':'Fiyat belirsiz; çok uzun inceleme açıklaması', 'url':'https://example.test/own'}])
        for value in ('iPhone 17 Pro','256 GB / Gümüş','Fiyat belirsiz',"href='https://example.test/own'"):
            self.assertIn(value, html)
        self.assertNotIn('pazarlama', html)
        self.assertNotIn('uzun inceleme', html)
        self.assertEqual(html.count('<li '), 1)

    def test_log_repeats_are_bounded_and_do_not_merge_siblings(self):
        lines = []
        clock = Mock(return_value=0)
        logger = ProblemLog(lines.append, clock, interval=300, limit=2)
        logger.failure('silver','Gümüş hata')
        logger.failure('silver','Gümüş hata')
        logger.failure('red','Kırmızı hata')
        self.assertEqual(lines, ['Gümüş hata','Kırmızı hata'])
        clock.return_value = 300
        logger.failure('silver','Gümüş hata')
        self.assertIn('2 tekrar / 300 sn', lines[-1])
        logger.recovered('silver')
        self.assertIn('Sorun düzeldi', lines[-1])
        for number in range(10):
            logger.failure(number, 'Başka')
        self.assertEqual(len(logger.entries), 2)
