from __future__ import annotations

from misflix.infra.process_lock import exclusive_lock


def test_second_holder_does_not_get_the_lock_until_the_first_releases_it(tmp_path):
    path = tmp_path / "state" / "subs.lock"

    with exclusive_lock(path) as first:
        with exclusive_lock(path) as second:
            assert first is True
            assert second is False

    with exclusive_lock(path) as again:
        assert again is True
