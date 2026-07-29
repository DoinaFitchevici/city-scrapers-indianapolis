from datetime import datetime
from os.path import dirname, join

from city_scrapers_core.constants import BOARD, TENTATIVE
from city_scrapers_core.utils import file_response
from freezegun import freeze_time

from city_scrapers.spiders.ind_indygo_bod import IndIndygoSpider

test_response = file_response(
    join(dirname(__file__), "files", "ind_indygo.html"),
    url="https://www.indygo.net/about-indygo/board-of-directors/",
)
spider = IndIndygoSpider()

freezer = freeze_time("2025-08-10")
freezer.start()

parsed_items = list(spider.parse(test_response))

freezer.stop()


def test_first_item():
    item = parsed_items[0]
    assert item["title"] == "IndyGo Board"
    assert item["description"] == ""
    assert item["start"] == datetime(2026, 1, 15, 16, 0)
    assert item["end"] is None
    assert (
        item["time_notes"] == "Check meeting attachments for a more accurate location."
    )
    assert item["id"] == "ind_indygo/202601151600/x/indygo_board"
    assert item["status"] == TENTATIVE
    assert item["location"] == {
        "name": "'B' Building",
        "address": "9503 E 33rd St, Indianapolis, IN 46235",
    }
    assert item["source"] == "https://www.indygo.net/about-indygo/board-of-directors/"
    assert item["links"] == [
        {
            "href": "https://www.indygo.net/wp-content/uploads/2026/01/January_Board_of_Directors__Annual_Board_of_Finance_Meeting_Book.pdf",  # noqa
            "title": "Board Reports",
        },
    ]
    assert item["classification"] == BOARD


def test_all_day():
    assert all(item["all_day"] is False for item in parsed_items)


def test_meeting_count():
    assert len(parsed_items) == 13


def test_board_reports_specific_link():
    # February's report is published, so it resolves to that month's PDF.
    item = next(i for i in parsed_items if i["start"] == datetime(2026, 2, 19, 16, 0))
    assert item["links"] == [
        {
            "href": "https://www.indygo.net/wp-content/uploads/2026/02/February_Board_of_Directors_Book-10.pdf",  # noqa
            "title": "Board Reports",
        },
    ]


def test_no_board_reports_link_until_published():
    # August's report isn't published yet.
    item = next(i for i in parsed_items if i["start"] == datetime(2026, 8, 20, 16, 0))
    assert item["links"] == []


def test_unique_ids():
    ids = [item["id"] for item in parsed_items]
    assert len(ids) == len(set(ids))
