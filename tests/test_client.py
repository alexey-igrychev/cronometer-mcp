"""Tests for the Cronometer client (mocked, no credentials needed)."""

import pytest
from unittest.mock import patch, MagicMock
from datetime import date

from cronometer_mcp.client import CronometerClient, EXPORT_TYPES


@pytest.fixture
def client():
    """Create a client with dummy credentials."""
    return CronometerClient(username="test@example.com", password="testpass")


class TestClientInit:
    def test_creates_with_explicit_creds(self):
        c = CronometerClient(username="a@b.com", password="pw")
        assert c.username == "a@b.com"
        assert c.password == "pw"
        assert not c._authenticated

    def test_raises_without_creds(self):
        with patch.dict("os.environ", {}, clear=True):
            with pytest.raises(ValueError, match="credentials required"):
                CronometerClient()

    def test_reads_env_vars(self):
        env = {"CRONOMETER_USERNAME": "env@test.com", "CRONOMETER_PASSWORD": "envpw"}
        with patch.dict("os.environ", env, clear=True):
            c = CronometerClient()
            assert c.username == "env@test.com"
            assert c.password == "envpw"

    def test_custom_gwt_values(self):
        c = CronometerClient(
            username="a@b.com", password="pw",
            gwt_permutation="CUSTOM_PERM",
            gwt_header="CUSTOM_HDR",
        )
        assert c.gwt_permutation == "CUSTOM_PERM"
        assert c.gwt_header == "CUSTOM_HDR"


class TestAuthentication:
    def test_get_anticsrf(self, client):
        mock_resp = MagicMock()
        mock_resp.text = '<input name="anticsrf" value="token123">'
        client.session.get = MagicMock(return_value=mock_resp)

        token = client._get_anticsrf()
        assert token == "token123"

    def test_get_anticsrf_missing(self, client):
        mock_resp = MagicMock()
        mock_resp.text = "<html>no token here</html>"
        client.session.get = MagicMock(return_value=mock_resp)

        with pytest.raises(RuntimeError, match="anti-CSRF"):
            client._get_anticsrf()

    def test_login_success_redirect(self, client):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"redirect": "https://cronometer.com/"}
        client.session.post = MagicMock(return_value=mock_resp)
        client.session.cookies = MagicMock()
        client.session.cookies.get = MagicMock(return_value="nonce123")

        client._login("csrf_token")
        assert client.nonce == "nonce123"

    def test_login_success_flag(self, client):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"success": True}
        client.session.post = MagicMock(return_value=mock_resp)
        client.session.cookies = MagicMock()
        client.session.cookies.get = MagicMock(return_value="nonce456")

        client._login("csrf_token")
        assert client.nonce == "nonce456"

    def test_login_error(self, client):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"error": "Invalid credentials"}
        client.session.post = MagicMock(return_value=mock_resp)

        with pytest.raises(RuntimeError, match="Invalid credentials"):
            client._login("csrf_token")

    def test_login_no_nonce(self, client):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"redirect": "https://cronometer.com/"}
        client.session.post = MagicMock(return_value=mock_resp)
        client.session.cookies = MagicMock()
        client.session.cookies.get = MagicMock(return_value=None)

        with pytest.raises(RuntimeError, match="sesnonce"):
            client._login("csrf_token")

    def test_gwt_authenticate(self, client):
        mock_resp = MagicMock()
        mock_resp.text = "//OK[12345,1,['some','data'],0,7]"
        client.session.post = MagicMock(return_value=mock_resp)
        client.session.cookies = MagicMock()
        client.session.cookies.get = MagicMock(return_value="new_nonce")

        client._gwt_authenticate()
        assert client.user_id == "12345"
        assert client.nonce == "new_nonce"

    def test_gwt_authenticate_failure(self, client):
        mock_resp = MagicMock()
        mock_resp.text = "//EX[something went wrong]"
        client.session.post = MagicMock(return_value=mock_resp)

        with pytest.raises(RuntimeError, match="GWT authenticate failed"):
            client._gwt_authenticate()

    def test_generate_auth_token(self, client):
        client.nonce = "test_nonce"
        client.user_id = "12345"

        mock_resp = MagicMock()
        mock_resp.text = '//OK["abc-token-123",0,7]'
        client.session.post = MagicMock(return_value=mock_resp)

        token = client._generate_auth_token()
        assert token == "abc-token-123"

    def test_authenticate_full_flow(self, client):
        with patch.object(client, "_get_anticsrf", return_value="csrf") as m1, \
             patch.object(client, "_login") as m2, \
             patch.object(client, "_gwt_authenticate") as m3:
            client.authenticate()
            m1.assert_called_once()
            m2.assert_called_once_with("csrf")
            m3.assert_called_once()
            assert client._authenticated

    def test_authenticate_skips_if_already_done(self, client):
        client._authenticated = True
        with patch.object(client, "_get_anticsrf") as m:
            client.authenticate()
            m.assert_not_called()


class TestExports:
    def test_export_types_mapping(self):
        assert EXPORT_TYPES["servings"] == "servings"
        assert EXPORT_TYPES["daily_summary"] == "dailySummary"
        assert EXPORT_TYPES["exercises"] == "exercises"
        assert EXPORT_TYPES["biometrics"] == "biometrics"
        assert EXPORT_TYPES["notes"] == "notes"

    def test_export_parsed(self, client):
        csv_data = "Day,Food Name,Amount\n2026-01-01,Eggs,2.00 large\n"
        with patch.object(client, "export_raw", return_value=csv_data):
            rows = client.export_parsed("servings", date(2026, 1, 1))
            assert len(rows) == 1
            assert rows[0]["Food Name"] == "Eggs"
            assert rows[0]["Amount"] == "2.00 large"
