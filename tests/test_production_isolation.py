"""Prove the test suite cannot touch production state, real SMTP, or real
broker order/session entry points.

These tests exist because a mocked `run_once` cycle once wrote a phantom
`peak_value` into production `data/peak_value.json`, and `test_system_monitor`
alerts reached the real inbox via SMTP.
"""
import json
import os
import smtplib

import pytest


def test_email_is_disabled_for_tests():
    """config/env must report email disabled so senders short-circuit."""
    from config import config
    assert config.EMAIL_ENABLED is False
    assert os.getenv("EMAIL_ENABLED") == "false"


def test_smtp_transport_is_blocked():
    """Instantiating smtplib.SMTP must not open a real connection."""
    server = smtplib.SMTP("smtp.gmail.com", 587)  # MagicMock — no socket
    server.login("u", "p")
    server.send_message("x")
    # Reaching here proves the transport was mocked, not real.


def test_kite_order_entry_points_blocked():
    """A real KiteConnect can never place/modify/cancel orders under pytest."""
    from kiteconnect import KiteConnect
    for method in ("place_order", "modify_order", "cancel_order",
                   "generate_session", "renew_access_token"):
        with pytest.raises(RuntimeError):
            getattr(KiteConnect, method)(None)


def test_data_dir_is_redirected(tmp_path):
    """config.data_path() resolves inside the per-test temp dir."""
    from config import data_dir, data_path
    assert data_dir() == str(tmp_path / "prod_data")
    assert str(tmp_path) in data_path("peak_value.json")


def test_peak_value_write_goes_to_temp(tmp_path):
    """Writing peak_value.json must not touch the production file."""
    from config import data_path
    p = data_path("peak_value.json")
    prod = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "data", "peak_value.json",
    )
    assert os.path.abspath(p) != os.path.abspath(prod)

    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as f:
        json.dump({"peak_value": 20000.0, "date": "test"}, f)
    # Production file was never opened; nothing to restore.
