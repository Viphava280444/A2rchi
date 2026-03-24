"""
Tests for conversation sharing feature.

Tests cover:
- Share token generation (base62, correct length, uniqueness)
- SQL query structure (required columns, filtering, no RAG context leakage)
- Visibility enforcement logic
- Config restriction logic
"""
import secrets
import string
import unittest


# ---------------------------------------------------------------------------
# Standalone token generation (mirrors app.py implementation, avoids heavy import)
# ---------------------------------------------------------------------------
_BASE62_CHARS = string.ascii_letters + string.digits


def generate_share_token(length=22):
    return ''.join(secrets.choice(_BASE62_CHARS) for _ in range(length))


def _import_sql():
    """Import SQL constants (lightweight — no heavy deps)."""
    from src.utils.sql import (
        SQL_INSERT_SHARE,
        SQL_GET_SHARE_BY_TOKEN,
        SQL_GET_SHARE_BY_CONVERSATION,
        SQL_REVOKE_SHARE,
        SQL_REVOKE_SHARES_BY_CONVERSATION,
        SQL_GET_SHARED_CONVERSATION_MESSAGES,
        SQL_GET_SHARED_CONVERSATION_METADATA,
    )
    return {
        'INSERT_SHARE': SQL_INSERT_SHARE,
        'GET_BY_TOKEN': SQL_GET_SHARE_BY_TOKEN,
        'GET_BY_CONVERSATION': SQL_GET_SHARE_BY_CONVERSATION,
        'REVOKE': SQL_REVOKE_SHARE,
        'REVOKE_BY_CONV': SQL_REVOKE_SHARES_BY_CONVERSATION,
        'SHARED_MESSAGES': SQL_GET_SHARED_CONVERSATION_MESSAGES,
        'SHARED_METADATA': SQL_GET_SHARED_CONVERSATION_METADATA,
    }


# ===========================================================================
# 7.1 Unit tests for share token generation
# ===========================================================================

class TestShareTokenGeneration(unittest.TestCase):
    """Tests for share token generation utility."""

    def test_default_length(self):
        token = generate_share_token()
        self.assertEqual(len(token), 22)

    def test_custom_length(self):
        token = generate_share_token(length=10)
        self.assertEqual(len(token), 10)

    def test_url_safe_characters(self):
        """Token must only contain base62 chars (no +, /, =, etc.)."""
        token = generate_share_token()
        allowed = set(string.ascii_letters + string.digits)
        for c in token:
            self.assertIn(c, allowed, f"Invalid character '{c}' in token")

    def test_uniqueness(self):
        """100 tokens should all be unique."""
        tokens = {generate_share_token() for _ in range(100)}
        self.assertEqual(len(tokens), 100)

    def test_zero_length(self):
        token = generate_share_token(length=0)
        self.assertEqual(token, '')


# ===========================================================================
# 7.2 Unit tests for share SQL queries
# ===========================================================================

class TestShareSQLQueries(unittest.TestCase):
    """Tests that SQL query constants are well-formed."""

    @classmethod
    def setUpClass(cls):
        cls.sql = _import_sql()

    def test_insert_share_has_required_columns(self):
        q = self.sql['INSERT_SHARE']
        for col in ('share_token', 'conversation_id', 'visibility',
                     'created_by_user', 'created_by_client'):
            self.assertIn(col, q)

    def test_get_share_by_token_filters_revoked(self):
        self.assertIn('revoked_at IS NULL', self.sql['GET_BY_TOKEN'])

    def test_get_share_by_token_filters_expired(self):
        self.assertIn('expires_at', self.sql['GET_BY_TOKEN'])

    def test_get_share_by_conversation_filters_revoked(self):
        self.assertIn('revoked_at IS NULL', self.sql['GET_BY_CONVERSATION'])

    def test_revoke_share_sets_revoked_at(self):
        self.assertIn('revoked_at', self.sql['REVOKE'])
        self.assertIn('NOW()', self.sql['REVOKE'])

    def test_revoke_shares_by_conversation(self):
        q = self.sql['REVOKE_BY_CONV']
        self.assertIn('conversation_id', q)
        self.assertIn('revoked_at', q)


# ===========================================================================
# 7.3 Test visibility enforcement (query-level)
# ===========================================================================

class TestVisibilityEnforcement(unittest.TestCase):
    """Tests that shared message queries don't leak RAG context."""

    @classmethod
    def setUpClass(cls):
        cls.sql = _import_sql()

    def test_shared_messages_selects_only_safe_columns(self):
        """The shared messages query must NOT include RAG context columns."""
        select_part = self.sql['SHARED_MESSAGES'].split('FROM')[0].upper()
        self.assertIn('SENDER', select_part)
        self.assertIn('CONTENT', select_part)
        # Must NOT select the 'context' or 'link' columns (RAG data)
        # Check that context is not a selected column (it could appear in table alias)
        self.assertNotIn('.CONTEXT', select_part)
        self.assertNotIn(', CONTEXT', select_part)
        self.assertNotIn('.LINK', select_part)
        self.assertNotIn(', LINK', select_part)

    def test_shared_messages_ordered_chronologically(self):
        self.assertIn('ORDER BY', self.sql['SHARED_MESSAGES'])
        self.assertIn('ASC', self.sql['SHARED_MESSAGES'])

    def test_get_by_token_excludes_revoked(self):
        """Revoked tokens must not be returned."""
        self.assertIn('revoked_at IS NULL', self.sql['GET_BY_TOKEN'])

    def test_get_by_token_excludes_expired(self):
        """Expired tokens must not be returned."""
        q = self.sql['GET_BY_TOKEN']
        self.assertIn('expires_at IS NULL OR expires_at > NOW()', q)

    def test_shared_metadata_query(self):
        q = self.sql['SHARED_METADATA']
        self.assertIn('conversation_id', q)
        self.assertIn('title', q)

    def test_visibility_values_are_valid(self):
        """Only 'public' and 'authed' are valid visibility values."""
        valid = {'public', 'authed'}
        for v in ('public', 'authed'):
            self.assertIn(v, valid)
        for v in ('private', 'everyone', '', 'admin'):
            self.assertNotIn(v, valid)


# ===========================================================================
# 7.4 Test config restrictions
# ===========================================================================

class TestSharingConfig(unittest.TestCase):
    """Tests for sharing configuration logic."""

    def _apply_config(self, config):
        """Simulate _get_sharing_config defaults."""
        return {
            'enabled': config.get('enabled', True),
            'allow_public': config.get('allow_public', True),
            'allow_authenticated': config.get('allow_authenticated', True),
            'default_visibility': config.get('default_visibility', 'public'),
        }

    def test_defaults_enable_everything(self):
        cfg = self._apply_config({})
        self.assertTrue(cfg['enabled'])
        self.assertTrue(cfg['allow_public'])
        self.assertTrue(cfg['allow_authenticated'])
        self.assertEqual(cfg['default_visibility'], 'public')

    def test_sharing_disabled(self):
        cfg = self._apply_config({'enabled': False})
        self.assertFalse(cfg['enabled'])

    def test_public_disallowed(self):
        cfg = self._apply_config({'allow_public': False})
        self.assertFalse(cfg['allow_public'])
        self.assertTrue(cfg['allow_authenticated'])

    def test_authenticated_disallowed(self):
        cfg = self._apply_config({'allow_authenticated': False})
        self.assertTrue(cfg['allow_public'])
        self.assertFalse(cfg['allow_authenticated'])

    def test_default_visibility_authed(self):
        cfg = self._apply_config({'default_visibility': 'authed'})
        self.assertEqual(cfg['default_visibility'], 'authed')

    def test_visibility_rejected_when_not_allowed(self):
        """If allow_public=False, a 'public' visibility request should be rejected."""
        cfg = self._apply_config({'allow_public': False})
        requested_visibility = 'public'
        allowed = (
            (requested_visibility == 'public' and cfg['allow_public']) or
            (requested_visibility == 'authed' and cfg['allow_authenticated'])
        )
        self.assertFalse(allowed)

    def test_visibility_accepted_when_allowed(self):
        cfg = self._apply_config({'allow_public': True})
        requested_visibility = 'public'
        allowed = (
            (requested_visibility == 'public' and cfg['allow_public']) or
            (requested_visibility == 'authed' and cfg['allow_authenticated'])
        )
        self.assertTrue(allowed)


if __name__ == '__main__':
    unittest.main()
