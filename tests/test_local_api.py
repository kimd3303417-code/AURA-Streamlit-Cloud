from unittest import TestCase
from unittest.mock import patch
from uuid import uuid4

from fastapi import HTTPException

import local_api


class LocalApiTests(TestCase):
    def test_history_is_scoped_to_verified_user(self):
        with patch('local_api.server.current_user', return_value='verified-user') as auth, \
             patch('local_api.server.history', return_value=[]) as history:
            self.assertEqual(local_api.call('/api/history', 'GET', None, 'user-jwt'), [])
        auth.assert_called_once_with('Bearer user-jwt')
        history.assert_called_once_with('verified-user')

    def test_report_download_uses_verified_owner(self):
        report_id = uuid4()
        with patch('local_api.server.current_user', return_value='verified-user'), \
             patch('local_api.server.download_report', return_value={'url': 'signed'}) as download:
            local_api.call(f'/api/reports/{report_id}/download', 'GET', None, 'user-jwt')
        download.assert_called_once_with(report_id, 'verified-user')

    def test_invalid_session_cannot_read_history(self):
        with patch('local_api.server.current_user', side_effect=HTTPException(401, 'Invalid session')), \
             patch('local_api.server.history') as history:
            with self.assertRaisesRegex(RuntimeError, 'Invalid session'):
                local_api.call('/api/history', 'GET', None, 'bad-token')
        history.assert_not_called()

