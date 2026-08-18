from datetime import datetime
from os.path import dirname, join

import pytest
import scrapy
from city_scrapers_core.constants import BOARD
from city_scrapers_core.utils import file_response
from freezegun import freeze_time

from city_scrapers.spiders.ind_public_library import IndPublicLibrarySpider

MAIN_URL = "https://www.indypl.org/about-the-library/board-meeting-times-committees"
DOCUMENTS_URL = "https://www.indypl.org/about-the-library/board-documents-archives"

# The spider follows each current-year meeting's location link to pull
# its street address off that location's own page (unless the text
# already overrides the location).

LOCATION_FIXTURES = {
    "https://www.indypl.org/locations/library-services-center": (
        "ind_public_library_library_services_center.html"
    ),
}
DEFAULT_LOCATION_FIXTURE = "ind_public_library_library_services_center.html"


def _location_response(url):
    filename = LOCATION_FIXTURES.get(url, DEFAULT_LOCATION_FIXTURE)
    return file_response(join(dirname(__file__), "files", filename), url=url)


def _resolve(results):
    """
    spider.parse() yields a mix of Meeting items and scrapy.Request
    objects (meetings whose location isn't overridden in the text have
    to fetch their own address page).
    """
    items = []
    for result in results:
        if isinstance(result, scrapy.Request):
            location_response = _location_response(result.url)
            for meeting in result.callback(location_response, **result.cb_kwargs):
                items.append(meeting)
        else:
            items.append(result)
    return items


@pytest.fixture(scope="module")
def spider():
    return IndPublicLibrarySpider()


@pytest.fixture(scope="module")
def documents_data():
    return file_response(
        join(dirname(__file__), "files", "ind_public_library_documents_archives.html"),
        url=DOCUMENTS_URL,
    )


@pytest.fixture(scope="module")
def meetings_data():
    return file_response(
        join(dirname(__file__), "files", "ind_public_library.html"),
        url=MAIN_URL,
    )


@pytest.fixture(scope="module")
def parsed_items(spider, documents_data, meetings_data):
    with freeze_time("2026-02-01"):
        # Mirrors start_requests() -> _parse_documents() in a real
        # crawl: populate spider.attachments before parsing the main
        # page, since meeting documents are matched by date + kind.
        list(spider._parse_documents(documents_data))
        items = _resolve(spider.parse(meetings_data))
    return items


def test_count(parsed_items):
    assert len(parsed_items) == 42


def test_title(parsed_items):
    assert (
        parsed_items[0]["title"] == "Indianapolis Public Library Board of Trustees"
    )  # noqa


def test_description(parsed_items):
    assert parsed_items[0]["description"] == ""


def test_start(parsed_items):
    assert parsed_items[1]["start"] == datetime(2026, 1, 30, 18, 30)


def test_end(parsed_items):
    assert parsed_items[1]["end"] is None


def test_time_notes(parsed_items):
    assert parsed_items[1]["time_notes"] == (
        "Meetings are usually held at 6:30pm on the fourth Monday "
        "of the month (third Monday in May/December)."
        "Please refer to the source page for more accurate "
        "meeting time and location."
    )


def test_id(parsed_items):
    assert (
        parsed_items[1]["id"]
        == "ind_public_library/202601301830/x/indianapolis_public_library_board_of_trustees"  # noqa
    )


def test_status(parsed_items):
    assert parsed_items[1]["status"] == "passed"


def test_location(parsed_items):
    assert parsed_items[1]["location"] == {
        "name": "Library Services Center",
        "address": "2450 North Meridian Street, Indianapolis, IN 46208",
    }


def test_source(parsed_items):
    assert parsed_items[1]["source"] == MAIN_URL


def test_links(parsed_items):
    assert parsed_items[1]["links"] == [
        {"href": "https://youtube.com/live/hA1OCeOykp8", "title": "Video"},
        {
            "href": "https://spirit.indypl.org/boardMeetingAdminDev/files/Minutes_-_Regular_Meeting,_January_30,_2026_%28Rescheduled%29_-_For_Posting_20260224115501.pdf",  # noqa
            "title": "(Rescheduled) Board Meeting Minutes",
        },
    ]


def test_classification(parsed_items):
    assert parsed_items[1]["classification"] == BOARD


def test_all_day(parsed_items):
    for item in parsed_items:
        assert item["all_day"] is False


def test_future_meeting_without_video_link(parsed_items):
    assert parsed_items[11]["status"] == "tentative"
    assert not any(link["title"] == "Video" for link in parsed_items[11]["links"])
