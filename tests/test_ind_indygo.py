from datetime import datetime
from os.path import dirname, join

from city_scrapers_core.constants import BOARD, TENTATIVE
from city_scrapers_core.utils import file_response
from freezegun import freeze_time
from scrapy.http import HtmlResponse

from city_scrapers.spiders.ind_indygo_bod import IndIndygoSpider

test_response = file_response(
    join(dirname(__file__), "files", "ind_indygo.html"),
    url="https://www.indygo.net/about-indygo/board-of-directors/",
)
spider = IndIndygoSpider()


def _resolve_fixture_response(url):
    if "board-meeting-media-archives" in url:
        filename = "ind_indygo_video_archive.html"
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
    assert item["title"] == "IndyGo Board"
    assert item["description"] == ""
    assert item["start"] == datetime(2026, 1, 15, 16, 0)
    assert item["end"] is None
    assert item["time_notes"] == ""
    assert item["id"] == "ind_indygo/202601151600/x/indygo_board"
    assert item["status"] == TENTATIVE
    assert item["location"] == {
        "name": "Boardroom - 'B' building",
        "address": "9503 E 33rd St, Indianapolis, IN 46235",
    }
    assert item["source"] == "https://www.indygo.net/about-indygo/board-of-directors/"
    assert item["links"] == [
        {
            "href": "https://www.youtube.com/@iptcIndyGo/streams",
            "title": "Live Stream",
        },
        {
            "href": "https://www.indygo.net/wp-content/uploads/2026/01/January_Board_of_Directors__Annual_Board_of_Finance_Meeting_Book.pdf",  # noqa
            "title": "Board Report",
        },
        {
            "href": "https://www.youtube.com/watch?v=2x0UmxgGei8&t=52s",
            "title": "Video",
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
    assert {"title": "Board Report", "href": item["links"][1]["href"]} in item["links"]
    assert (
        item["links"][1]["href"]
        == "https://www.indygo.net/wp-content/uploads/2026/02/February_Board_of_Directors_Book-10.pdf"  # noqa
    )


def test_no_board_reports_link_until_published():
    # August's report isn't published yet, and no video is archived either,
    # so only the Live Stream link and the date's own Agenda link remain.
    item = next(i for i in parsed_items if i["start"] == datetime(2026, 8, 20, 16, 0))
    assert item["links"] == [
        {
            "href": "https://www.youtube.com/@iptcIndyGo/streams",
            "title": "Live Stream",
        },
        {
            "href": "https://www.indygo.net/wp-content/uploads/2026/08/August-2026-Board-Agenda.docx",  # noqa
            "title": "Agenda",
        },
    ]


def test_date_link_becomes_agenda_attachment():
    # Aug. 20's date itself links to a document, so it's attached as an
    # Agenda link.
    august_20 = next(
        i for i in parsed_items if i["start"] == datetime(2026, 8, 20, 16, 0)
    )
    assert {
        "title": "Agenda",
        "href": "https://www.indygo.net/wp-content/uploads/2026/08/August-2026-Board-Agenda.docx",  # noqa
    } in august_20["links"]

    # Other dates aren't links, so they get no Agenda attachment.
    january_15 = next(
        i for i in parsed_items if i["start"] == datetime(2026, 1, 15, 16, 0)
    )
    assert all(link["title"] != "Agenda" for link in january_15["links"])


def test_unique_ids():
    ids = [item["id"] for item in parsed_items]
    assert len(ids) == len(set(ids))


def test_title_includes_dash_separated_description():
    item = next(i for i in parsed_items if i["start"] == datetime(2026, 7, 16, 16, 0))
    assert item["title"] == "IndyGo Board – Budget 2027 Introduced"


def test_video_link_goes_to_earliest_meeting_in_month():
    # July has two Board meetings (16th and 30th); the archive only has one
    # July video, so the earlier meeting (the 16th) claims it...
    july_16 = next(
        i for i in parsed_items if i["start"] == datetime(2026, 7, 16, 16, 0)
    )
    assert {
        "title": "Video",
        "href": "https://www.youtube.com/watch?v=9LhfUDi0i68",
    } in [
        {"title": link["title"], "href": link["href"]}
        for link in july_16["links"]
        if link["title"] == "Video"
    ]

    # ...and the 30th gets no video link.
    july_30 = next(
        i for i in parsed_items if i["start"] == datetime(2026, 7, 30, 16, 0)
    )
    assert all(link["title"] != "Video" for link in july_30["links"])


def test_board_report_link_goes_to_earliest_meeting_in_month():
    # July has two Board meetings (16th and 30th) but only one published
    # report, so the earlier meeting (the 16th) claims it
    july_16 = next(
        i for i in parsed_items if i["start"] == datetime(2026, 7, 16, 16, 0)
    )
    assert {
        "title": "Board Report",
        "href": "https://www.indygo.net/wp-content/uploads/2026/07/July_Board_of_Directors_-_Intro_to_2027_Budget_Book.pdf",  # noqa
    } in [
        {"title": link["title"], "href": link["href"]}
        for link in july_16["links"]
        if link["title"] == "Board Report"
    ]

    # the 30th gets no board report link.
    july_30 = next(
        i for i in parsed_items if i["start"] == datetime(2026, 7, 30, 16, 0)
    )
    assert all(link["title"] != "Board Report" for link in july_30["links"])


def test_live_stream_link_on_every_meeting():
    assert all(
        any(link["title"] == "Live Stream" for link in item["links"])
        for item in parsed_items
    )


def test_location_fallback_when_address_not_found():
    html_without_address = b"""
    <html><body>
        <h2>Attend a Board Meeting</h2>
        <p>Call the office for meeting details.</p>
    </body></html>
    """
    response = HtmlResponse(
        url="https://www.indygo.net/about-indygo/board-of-directors/",
        body=html_without_address,
    )

    location, time_notes = spider._parse_location_and_time_notes(response)

    assert location == {"name": "", "address": ""}
    assert time_notes == "Check meeting attachments for a more accurate location."
