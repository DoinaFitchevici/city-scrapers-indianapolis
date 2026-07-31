from datetime import datetime
from os.path import dirname, join

from city_scrapers_core.constants import BOARD, TENTATIVE
from city_scrapers_core.utils import file_response
from freezegun import freeze_time

from city_scrapers.spiders.ind_indygo_bod import IndIndygoFinanceSpider

test_response = file_response(
    join(dirname(__file__), "files", "ind_indygo.html"),
    url="https://www.indygo.net/about-indygo/board-of-directors/",
)
spider = IndIndygoFinanceSpider()


def _resolve_fixture_response(url):
    if "board-meeting-media-archives" in url:
        filename = "ind_indygo_video_archive.html"
    elif "onboardmeetings.com" in url:
        filename = "ind_indygo_finance_listings.html"
    else:
        filename = "ind_indygo.html"

    return file_response(join(dirname(__file__), "files", filename), url=url)


freezer = freeze_time("2025-08-10")
freezer.start()

queue = list(spider.parse(test_response))
parsed_items = []
while queue:
    result = queue.pop(0)
    if hasattr(result, "callback"):
        fixture_response = _resolve_fixture_response(result.url)
        queue.extend(result.callback(fixture_response, **result.cb_kwargs))
    else:
        parsed_items.append(result)

freezer.stop()


def test_first_item():
    item = parsed_items[0]
    assert item["title"] == "IndyGo Finance Committee"
    assert item["description"] == ""
    assert item["start"] == datetime(2026, 2, 19, 15, 0)
    assert item["end"] is None
    assert (
        item["time_notes"] == "Check meeting attachments for a more accurate location."
    )
    assert item["id"] == "ind_indygo_finance/202602191500/x/indygo_finance_committee"
    assert item["status"] == TENTATIVE
    assert item["location"] == {
        "name": "Boardroom - 'B' building",
        "address": "9503 E 33rd St, Indianapolis, IN 46235",
    }
    assert item["source"] == "https://www.indygo.net/about-indygo/board-of-directors/"
    assert item["links"] == [
        {
            "href": "https://public.onboardmeetings.com/Meeting/HrdLpC4rmFdYrgplGJZm82TtkS14OCvw7QLcFFPpPrIA/bLBQjii2mMe%2FDWZk5%2FDIQy8pt00bmyB8O7SzSFaFXtMA?ReturnUrl=%2FGroup%2FHrdLpC4rmFdYrgplGJZm82TtkS14OCvw7QLcFFPpPrIA%2FPBtWHdxtJt6XgVphYPHNTSsJFC992FZbLhKOoPeFrjsA",  # noqa
            "title": "Meeting Listings",
        },
        {
            "href": "https://youtu.be/Kay6uWEK7oo",
            "title": "Video",
        },
    ]
    assert item["classification"] == BOARD


def test_all_day():
    assert all(item["all_day"] is False for item in parsed_items)


def test_meeting_count():
    assert len(parsed_items) == 3


def test_meeting_listings_specific_link():
    # July's meeting is on OnBoard's listing, but the archive has no video
    # for Finance that month (only Board and Gov Audit do).
    item = next(i for i in parsed_items if i["start"] == datetime(2026, 7, 16, 15, 0))
    assert item["links"] == [
        {
            "href": "https://public.onboardmeetings.com/Meeting/HrdLpC4rmFdYrgplGJZm82TtkS14OCvw7QLcFFPpPrIA/fc5SjM7560AUWb2h%2FLlJ0sTLU%2FoP%2FnnmlQ3mCh%2F1S2MA?ReturnUrl=%2FGroup%2FHrdLpC4rmFdYrgplGJZm82TtkS14OCvw7QLcFFPpPrIA%2FPBtWHdxtJt6XgVphYPHNTSsJFC992FZbLhKOoPeFrjsA",  # noqa
            "title": "Meeting Listings",
        },
    ]


def test_no_meeting_listings_link_until_published():
    # December's meeting isn't on OnBoard's listing yet, and there's no
    # video either.
    item = next(i for i in parsed_items if i["start"] == datetime(2026, 12, 17, 15, 0))
    assert item["links"] == []


def test_unique_ids():
    ids = [item["id"] for item in parsed_items]
    assert len(ids) == len(set(ids))
