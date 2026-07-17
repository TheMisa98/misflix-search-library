from __future__ import annotations

import sqlite3

from misflix.infra import browser_cookies


def _make_cookie_db(path, rows: list[tuple[str, str, str, int]]) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE moz_cookies (
            id INTEGER PRIMARY KEY,
            name TEXT,
            value TEXT,
            host TEXT,
            path TEXT,
            expiry INTEGER
        )
        """
    )
    conn.executemany(
        "INSERT INTO moz_cookies (name, value, host, path, expiry) VALUES (?, ?, ?, ?, ?)",
        [(name, value, host, "/", expiry) for name, value, host, expiry in rows],
    )
    conn.commit()
    conn.close()


def test_load_domain_cookies_picks_freshest_value_per_name(tmp_path, monkeypatch):
    db_path = tmp_path / "cookies.sqlite"
    _make_cookie_db(
        db_path,
        [
            ("cf_clearance", "old-value", ".example.com", 1_700_000_000),
            ("cf_clearance", "new-value", ".example.com", 1_800_000_000),
            ("other_site_cookie", "irrelevant", ".other.com", 1_900_000_000),
        ],
    )
    monkeypatch.setattr(browser_cookies, "_candidate_cookie_dbs", lambda: [db_path])

    cookies = browser_cookies.load_domain_cookies("example.com")

    assert cookies == {"cf_clearance": "new-value"}


def test_load_domain_cookies_normalizes_millisecond_expiry(tmp_path, monkeypatch):
    db_path = tmp_path / "cookies.sqlite"
    _make_cookie_db(
        db_path,
        [
            ("cf_clearance", "seconds-value", ".example.com", 1_700_000_000),
            ("cf_clearance", "ms-value", ".example.com", 1_800_000_000_000),
        ],
    )
    monkeypatch.setattr(browser_cookies, "_candidate_cookie_dbs", lambda: [db_path])

    cookies = browser_cookies.load_domain_cookies("example.com")

    assert cookies == {"cf_clearance": "ms-value"}


def test_load_domain_cookies_returns_empty_when_no_profiles_found(monkeypatch):
    monkeypatch.setattr(browser_cookies, "_candidate_cookie_dbs", lambda: [])

    assert browser_cookies.load_domain_cookies("example.com") == {}
