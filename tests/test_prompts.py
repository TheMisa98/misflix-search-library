from __future__ import annotations

from misflix.core.models import DownloadOption
from misflix.ui.prompts import _format_size, _parse_pasted_links, option_host


def test_option_host_extracts_host_and_drops_quality():
    option = DownloadOption(label="MEGA (1080p)", url="https://example.com/1")

    assert option_host(option) == "MEGA"


def test_option_host_uppercases_a_host_without_quality():
    option = DownloadOption(label="mediafire", url="https://example.com/1")

    assert option_host(option) == "MEDIAFIRE"


def test_option_host_falls_back_to_the_raw_label_when_it_does_not_match():
    option = DownloadOption(label="Servidor desconocido", url="https://example.com/1")

    assert option_host(option) == "SERVIDOR DESCONOCIDO"


def test_format_size_returns_dash_for_unknown_size():
    assert _format_size(None) == "-"


def test_format_size_formats_bytes():
    assert _format_size(500) == "500.0 B"


def test_format_size_formats_gigabytes():
    assert _format_size(1_500_000_000) == "1.4 GB"


def test_format_size_formats_petabytes_for_absurdly_large_values():
    assert _format_size(1024**5) == "1.0 PB"


def test_parse_pasted_links_extracts_urls_from_surrounding_text():
    raw = ["Mira este link:", "https://mediafire.com/file/abc123", "gracias!"]

    assert _parse_pasted_links(raw) == ["https://mediafire.com/file/abc123"]


def test_parse_pasted_links_strips_trailing_punctuation():
    raw = ["(https://mediafire.com/file/abc.rar), listo."]

    assert _parse_pasted_links(raw) == ["https://mediafire.com/file/abc.rar"]


def test_parse_pasted_links_deduplicates_keeping_first_occurrence():
    raw = ["https://mediafire.com/file/part1.rar", "https://mediafire.com/file/part1.rar"]

    assert _parse_pasted_links(raw) == ["https://mediafire.com/file/part1.rar"]


def test_parse_pasted_links_sorts_by_part_number():
    raw = [
        "https://mediafire.com/file.part3.rar",
        "https://mediafire.com/file.part1.rar",
        "https://mediafire.com/file.part2.rar",
    ]

    assert _parse_pasted_links(raw) == [
        "https://mediafire.com/file.part1.rar",
        "https://mediafire.com/file.part2.rar",
        "https://mediafire.com/file.part3.rar",
    ]


def test_parse_pasted_links_puts_links_without_part_number_last_in_paste_order():
    raw = [
        "https://mediafire.com/file.part2.rar",
        "https://mediafire.com/no-part.rar",
        "https://mediafire.com/also-no-part.rar",
    ]

    assert _parse_pasted_links(raw) == [
        "https://mediafire.com/file.part2.rar",
        "https://mediafire.com/no-part.rar",
        "https://mediafire.com/also-no-part.rar",
    ]


def test_parse_pasted_links_returns_empty_list_when_nothing_matches():
    assert _parse_pasted_links(["no hay ningun link aca"]) == []
