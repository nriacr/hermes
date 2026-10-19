import unittest
from unittest.mock import Mock

from hermes.notifier import Pushover


class PushoverQuietTest(unittest.TestCase):
    def sent_priority(self, **kwargs):
        session = Mock()
        session.post.return_value.json.return_value = {"status": 1}
        Pushover("u", "t", session=session).send("b", "m", **kwargs)
        return session.post.call_args.kwargs["data"]["priority"]

    def test_default_is_normal_and_quiet_is_silent(self):
        self.assertEqual(self.sent_priority(), "0")
        self.assertEqual(self.sent_priority(quiet=True), "-1")


if __name__ == "__main__":
    unittest.main()
