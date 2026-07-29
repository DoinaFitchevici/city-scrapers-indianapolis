from datetime import datetime
from os.path import dirname, join

from city_scrapers_core.constants import BOARD, TENTATIVE
from city_scrapers_core.utils import file_response
from freezegun import freeze_time

from city_scrapers.spiders.ind_indygo_bod import IndIndygoGovAuditSpider

test_response = file_response(
    join(dirname(__file__), "files", "ind_indygo.html"),
    url="https://www.indygo.net/about-indygo/board-of-directors/",
)
spider = IndIndygoGovAuditSpider()

freezer = freeze_time("2025-08-10")
freezer.start()

listings_request = next(spider.parse(test_response))
listings_response = file_response(
    join(dirname(__file__), "files", "ind_indygo_gov_audit_listings.html"),
    url=listings_request.url,
)
parsed_items = list(
    listings_request.callback(listings_response, **listings_request.cb_kwargs)
)

freezer.stop()


def test_first_item():
    item = parsed_items[0]
    assert item["title"] == "IndyGo Governance and Audit Committee"
    assert item["description"] == ""
    assert item["start"] == datetime(2026, 1, 8, 10, 0)
    assert item["end"] is None
    assert (
        item["time_notes"] == "Check meeting attachments for a more accurate location."
    )
    assert (
        item["id"]
        == "ind_indygo_gov_audit/202601081000/x/indygo_governance_and_audit_committee"
    )
    assert item["status"] == TENTATIVE
    assert item["location"] == {
        "name": "'B' Building",
        "address": "9503 E 33rd St, Indianapolis, IN 46235",
    }
    assert item["source"] == "https://www.indygo.net/about-indygo/board-of-directors/"
    assert item["links"] == [
        {
            "href": "https://public.onboardmeetings.com/Meeting/HrdLpC4rmFdYrgplGJZm82TtkS14OCvw7QLcFFPpPrIA/K7BGS1xvfy2D4MSwsXC6Zy9fI3wz6CG3SqeUSuSYEsoA?ReturnUrl=%2FGroup%2FHrdLpC4rmFdYrgplGJZm82TtkS14OCvw7QLcFFPpPrIA%2FTQhh8dVF3IFGmqPt9KPGgkEFwuhy9cHXFm5%252FFdjTKHsA",  # noqa
            "title": "Meeting Listings",
        },
    ]
    assert item["classification"] == BOARD


def test_all_day():
    assert all(item["all_day"] is False for item in parsed_items)


def test_meeting_count():
    assert len(parsed_items) == 4


def test_meeting_listings_specific_link():
    # April's meeting is on OnBoard's listing.
    item = next(i for i in parsed_items if i["start"] == datetime(2026, 4, 9, 10, 0))
    assert item["links"] == [
        {
            "href": "https://public.onboardmeetings.com/Meeting/HrdLpC4rmFdYrgplGJZm82TtkS14OCvw7QLcFFPpPrIA/S4zXVqWxdNtjkwR4I0wDQmDY9m7Pnb34viQApNRQ7J8A?ReturnUrl=%2FGroup%2FHrdLpC4rmFdYrgplGJZm82TtkS14OCvw7QLcFFPpPrIA%2FTQhh8dVF3IFGmqPt9KPGgkEFwuhy9cHXFm5%252FFdjTKHsA",  # noqa
            "title": "Meeting Listings",
        },
    ]


def test_no_meeting_listings_link_until_published():
    # October's meeting isn't on OnBoard's listing yet.
    item = next(i for i in parsed_items if i["start"] == datetime(2026, 10, 8, 10, 0))
    assert item["links"] == []


def test_unique_ids():
    ids = [item["id"] for item in parsed_items]
    assert len(ids) == len(set(ids))
