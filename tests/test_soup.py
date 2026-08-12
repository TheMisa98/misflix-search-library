from __future__ import annotations

from bs4 import BeautifulSoup

from misflix.infra.soup import attr


def test_attr_reads_a_single_valued_attribute():
    tag = BeautifulSoup('<a href="https://example.com"></a>', "lxml").a

    assert attr(tag, "href") == "https://example.com"


def test_attr_joins_a_multi_valued_attribute_into_one_string():
    tag = BeautifulSoup('<a class="a b"></a>', "lxml").a

    assert attr(tag, "class") == "ab"
