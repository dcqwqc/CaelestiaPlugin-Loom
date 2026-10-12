"""Regression: OAuth redirects can close a Firefox BiDi browsing context."""
import unittest
from unittest.mock import Mock
from tabby.bidi import BidiSession, BidiError


def fake_session(sequence):
    session = object.__new__(BidiSession)
    session.context = "gone-tab"
    session.command = Mock(side_effect=sequence)
    return session


class BidiContextRecoveryTests(unittest.TestCase):
    def test_state_read_rebinds_one_replacement_tab(self):
        session = fake_session([
            BidiError("script.evaluate: no such frame: Browsing Context with id gone-tab not found"),
            {"contexts": [{"context": "new-tab"}]},
            {"result": {"value": '{"url":"https://chatgpt.com/"}'}},
        ])
        self.assertEqual(session.evaluate_json("JSON.stringify({url:location.href})"),
                         {"url": "https://chatgpt.com/"})
        self.assertEqual(session.context, "new-tab")
        self.assertEqual([c.args[0] for c in session.command.call_args_list],
                         ["script.evaluate", "browsingContext.getTree", "script.evaluate"])
        self.assertEqual(session.command.call_args_list[-1].args[1]["target"]["context"], "new-tab")

    def test_navigation_rebinds_after_login_tab_replacement(self):
        session = fake_session([
            BidiError("browsingContext.navigate: no such context"),
            {"contexts": [{"context": "replacement"}]},
            {"url": "https://example.org/"},
        ])
        self.assertEqual(session.navigate("https://example.org/")["url"], "https://example.org/")
        self.assertEqual(session.command.call_args_list[-1].args[1]["context"], "replacement")

    def test_ambiguous_tab_replacement_fails_closed(self):
        session = fake_session([
            BidiError("script.evaluate: no such frame"),
            {"contexts": [{"context": "a"}, {"context": "b"}]},
        ])
        with self.assertRaisesRegex(BidiError, "cannot safely select"):
            session.evaluate_json("1")
        self.assertEqual(session.command.call_count, 2)
        self.assertEqual(session.context, "gone-tab")

    def test_unrelated_errors_not_retried(self):
        session = fake_session([BidiError("script.evaluate: javascript error")])
        with self.assertRaisesRegex(BidiError, "javascript error"):
            session.evaluate_json("1")
        self.assertEqual(session.command.call_count, 1)

    def test_still_missing_same_tab_fails_without_retry_loop(self):
        session = fake_session([
            BidiError("script.evaluate: no such frame"),
            {"contexts": [{"context": "gone-tab"}]},
        ])
        with self.assertRaises(BidiError):
            session.evaluate_json("1")
        self.assertEqual(session.command.call_count, 2)


if __name__ == "__main__":
    unittest.main()
