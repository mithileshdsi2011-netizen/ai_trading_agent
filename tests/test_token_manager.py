"""Regression: every KiteConnect construction must set an explicit timeout.

A stalled broker socket inside run_once froze the Swing scheduler for
10+ hours (the SMTP-class defect). KiteConnect defaults to 7s, but the
timeout is now explicit at construction so the contract can't silently
depend on an SDK default.
"""
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch


class TestKiteConnectConstruction(unittest.TestCase):

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self._patcher = patch(
            'token_manager.data_path',
            lambda name: os.path.join(self._dir.name, name))
        self._patcher.start()
        from token_manager import TokenManager
        self.mgr = TokenManager()

    def tearDown(self):
        self._patcher.stop()
        self._dir.cleanup()

    def test_initialize_kite_uses_explicit_timeout(self):
        """The live session every broker API call flows through."""
        with patch('token_manager.KiteConnect') as KC:
            inst = KC.return_value
            inst.profile.return_value = {'user_name': 'u'}
            self.mgr.get_access_token = MagicMock(return_value='TOK')
            self.mgr.initialize_kite()
            KC.assert_called_once()
            self.assertEqual(KC.call_args.kwargs.get('timeout'), 15)
            inst.set_access_token.assert_called_once_with('TOK')

    def test_refresh_token_uses_explicit_timeout(self):
        """The OAuth exchange call is on the same network path."""
        with patch('token_manager.KiteConnect') as KC:
            KC.return_value.generate_session.return_value = {
                'access_token': 'NEW_TOK'}
            self.mgr._refresh_token('REQUEST_TOKEN')
            KC.assert_called_once()
            self.assertEqual(KC.call_args.kwargs.get('timeout'), 15)


if __name__ == '__main__':
    unittest.main()
