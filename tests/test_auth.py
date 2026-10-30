"""Tests for the request class build_service gives every Google API call."""

from __future__ import annotations

import ssl

import httplib2
import pytest
from googleapiclient.http import HttpMockSequence

from jean_claude.auth import RetryingReadRequest


class DropsFirstConnection(HttpMockSequence):
    """Fails the first request the way a dropped TLS connection does."""

    def __init__(self, responses):
        super().__init__(responses)
        self.attempts = 0

    def request(self, *args, **kwargs):
        self.attempts += 1
        if self.attempts == 1:
            raise ssl.SSLEOFError("EOF occurred in violation of protocol")
        return super().request(*args, **kwargs)


def make_request(http, method):
    return RetryingReadRequest(
        http,
        lambda resp, content: content,
        "https://gmail.googleapis.com/gmail/v1/users/me/messages",
        method=method,
    )


@pytest.fixture(autouse=True)
def no_backoff_sleep(monkeypatch):
    monkeypatch.setattr("googleapiclient.http.time.sleep", lambda seconds: None)


def test_read_survives_dropped_connection():
    http = DropsFirstConnection([({"status": "200"}, b"ok")])
    assert make_request(http, "GET").execute() == b"ok"
    assert http.attempts == 2


def test_write_is_not_retried():
    http = DropsFirstConnection([({"status": "200"}, b"ok")])
    with pytest.raises(ssl.SSLEOFError):
        make_request(http, "POST").execute()
    assert http.attempts == 1


def test_read_gives_up_after_retries():
    http = httplib2.Http()
    attempts = []

    def always_drop(*args, **kwargs):
        attempts.append(1)
        raise ssl.SSLEOFError("EOF occurred in violation of protocol")

    http.request = always_drop
    with pytest.raises(ssl.SSLEOFError):
        make_request(http, "GET").execute()
    assert len(attempts) == 4
