import json
from datetime import datetime
from os.path import dirname, join

import pytest  # noqa
import scrapy
from city_scrapers_core.constants import CANCELLED, CITY_COUNCIL, COMMITTEE, TENTATIVE
from city_scrapers_core.utils import file_response
from freezegun import freeze_time

from city_scrapers.spiders.ind_city_county import IndCityCountySpider

test_response = file_response(
    join(dirname(__file__), "files", "ind_city_county.jsonp"),
    url="https://calendar.indy.gov/handlers/query.ashx?get=eventlist&page=0&pageSize=-1&total=-1&view=list.xslt",  # noqa
)

empty_page_response = file_response(
    join(dirname(__file__), "files", "ind_city_county_page_empty.jsonp"),
    url="https://calendar.indy.gov/handlers/query.ashx?get=eventlist&page=2&pageSize=125&total=125&view=list.xslt",  # noqa
)

# Fixtures for the three GraphQL "Activity" pages the spider prefetches in
# start_requests() before parsing the calendar listing.
committee_agendas_response = file_response(
    join(dirname(__file__), "files", "ind_city_county_committee_agendas.json"),
    url=IndCityCountySpider.GRAPHQL_URL,
)
full_council_agendas_response = file_response(
    join(dirname(__file__), "files", "ind_city_county_full_council_agendas.json"),
    url=IndCityCountySpider.GRAPHQL_URL,
)
committees_directory_response = file_response(
    join(dirname(__file__), "files", "ind_city_county_committees_directory.json"),
    url=IndCityCountySpider.GRAPHQL_URL,
)

# Fixtures for the per-committee "Meeting Minutes" GraphQL requests kicked
# off after the committee directory - one real one (for the committee used
# in test_minutes_fallback_when_no_agenda below) and a generic empty one
# for every other committee in the directory, since only one is relevant.
rules_minutes_response = file_response(
    join(dirname(__file__), "files", "ind_city_county_committee_minutes_rules.json"),
    url=IndCityCountySpider.GRAPHQL_URL,
)
empty_minutes_response = file_response(
    join(dirname(__file__), "files", "ind_city_county_committee_minutes_empty.json"),
    url=IndCityCountySpider.GRAPHQL_URL,
)

spider = IndCityCountySpider()

freezer = freeze_time("2024-02-07")
freezer.start()

# Mirrors the real chain of callbacks kicked off by start_requests(): each
# GraphQL response populates a lookup used to match attachments/committee
# names into the meetings parsed from the calendar listing.
list(spider.start_requests())
list(spider._parse_committee_agendas(committee_agendas_response))
list(spider._parse_full_council_agendas(full_council_agendas_response))

# _parse_committee_directory kicks off one GraphQL request per committee
# (for its Meeting Minutes archive), one at a time - walk that chain to
# completion, feeding the real fixture for "rules-and-public-policy
# -committee" and an empty one for every other committee slug.
request = list(spider._parse_committee_directory(committees_directory_response))[0]
while request.url == IndCityCountySpider.GRAPHQL_URL:
    slug = json.loads(request.body)["variables"]["slug"]
    response = (
        rules_minutes_response
        if slug == "rules-and-public-policy-committee"
        else empty_minutes_response
    )
    request = list(request.callback(response, **request.cb_kwargs))[0]

parsed_items = [item for item in spider.parse(test_response)]

freezer.stop()


def test_count():
    assert len(parsed_items) == 53


def test_title():
    assert parsed_items[0]["title"] == "Parks and Recreation Committee"


def test_description():
    assert (
        parsed_items[0]["description"]
        == "Monthly meeting of the Parks and Recreation Committee of the City-County Council to consider pending legislation"  # noqa
    )


def test_start():
    assert parsed_items[0]["start"] == datetime(2024, 2, 8, 17, 30)


def test_end():
    assert parsed_items[0]["end"] is None


def test_time_notes():
    assert parsed_items[0]["time_notes"] == ""


def test_id():
    assert (
        parsed_items[0]["id"]
        == "ind_city_county/202402081730/x/parks_and_recreation_committee"
    )


def test_status():
    assert parsed_items[0]["status"] == TENTATIVE


def test_location():
    assert parsed_items[0]["location"] == {
        "name": "City-County Building, Meeting Room 260",
        "address": "200 East Washington Street, Indianapolis, IN, 46204",
    }


def test_source():
    """source now points at the meeting's own Event Details page rather
    than the calendar listing endpoint."""
    assert (
        parsed_items[0]["source"]
        == "https://calendar.indy.gov/event/parks-and-recreation-committee-14/"
    )


def test_links():
    """links now carries the real agenda/notice attachment matched from
    the council-committee-agendas GraphQL data instead of a duplicate of
    the Event Details page."""
    assert parsed_items[0]["links"] == [
        {
            "href": "https://us-east-1-indy.graphassets.com/parks-notice",
            "title": "Parks and Recreation Committee Meeting Notice",
        }
    ]


def test_classification():
    assert parsed_items[0]["classification"] == COMMITTEE


def test_all_day():
    assert parsed_items[0]["all_day"] is False


def test_committee_attachment_match_by_name_and_date():
    """A different committee's meeting still gets matched independently
    by (committee name, date)."""
    meeting = parsed_items[2]
    assert meeting["title"] == "Administration and Finance Committee"
    assert meeting["source"] == (
        "https://calendar.indy.gov/event/administration-and-finance-committee-1/"
    )
    assert meeting["links"] == [
        {
            "href": "https://us-east-1-indy.graphassets.com/admin-finance-notice",
            "title": "Administration and Finance Committee Meeting Notice",
        }
    ]


def test_cancelled_committee_meeting():
    """A committee flagged CANCELLED on council-committee-agendas gets a
    CANCELLED status, and keeps its attachment link."""
    meeting = parsed_items[4]
    assert meeting["title"] == "Rules and Public Policy Committee"
    assert meeting["status"] == CANCELLED
    assert meeting["links"] == [
        {
            "href": "https://us-east-1-indy.graphassets.com/rules-notice",
            "title": "Rules and Public Policy Committee Meeting Notice",
        }
    ]


def test_full_council_attachment_match_by_date():
    """Full Council meetings are matched by date against the
    council-meeting-agendas year archive."""
    meeting = parsed_items[10]
    assert meeting["title"] == "Full City-County Council Meeting"
    assert meeting["classification"] == CITY_COUNCIL
    assert meeting["source"] == (
        "https://calendar.indy.gov/event/full-city-county-council-meeting-29/"
    )
    assert meeting["links"] == [
        {
            "href": "https://us-east-1-indy.graphassets.com/full-council-march",
            "title": "Full Council Meeting Agenda for March 4, 2024",
        }
    ]


def test_no_attachment_when_not_in_api():
    """A committee meeting not present in the API's "next occurrence" list
    (here, deliberately left out of the fixture) gets no attachment rather
    than a fabricated one."""
    meeting = parsed_items[13]
    assert meeting["title"] == "Municipal Corporations Committee"
    assert meeting["classification"] == COMMITTEE
    assert meeting["links"] == []


def test_minutes_fallback_when_no_agenda():
    """A committee meeting with no agenda match (past its "next occurrence"
    window) falls back to that committee's own Meeting Minutes archive."""
    meeting = parsed_items[20]
    assert meeting["title"] == "Rules and Public Policy Committee"
    assert meeting["start"] == datetime(2024, 3, 19, 17, 30)
    assert meeting["status"] == TENTATIVE
    assert meeting["links"] == [
        {
            "href": "https://us-east-1-indy.graphassets.com/rules-minutes-mar",
            "title": "Rules and Public Policy Committee Minutes for March 19, 2024",
        }
    ]


def test_weekly_notice_fallback_when_no_agenda_or_minutes():
    """A committee meeting with neither an agenda nor minutes match falls
    back to the bundled weekly notice PDF for that meeting's week."""
    meeting = parsed_items[16]
    assert meeting["title"] == "Administration and Finance Committee"
    assert meeting["start"] == datetime(2024, 3, 12, 17, 30)
    assert meeting["links"] == [
        {
            "href": "https://us-east-1-indy.graphassets.com/weekly-notice-mar11",
            "title": "NOTICE OF COMMITTEES OF THE COUNCIL FOR THE WEEK OF March 11-15, 2024",  # noqa
        }
    ]


def test_paginated_page_requests_next_page_when_nonempty():
    """A page with events yields its meetings and a request for the next
    page, so the 2-year search range gets fully paginated through."""
    with freeze_time("2024-02-07"):
        results = list(spider._parse_paginated_page(test_response, page=0))

    requests = [r for r in results if isinstance(r, scrapy.Request)]
    meetings = [r for r in results if not isinstance(r, scrapy.Request)]

    assert len(meetings) == len(parsed_items)
    assert len(requests) == 1
    assert "page=1" in requests[0].url


def test_paginated_page_stops_on_empty_page():
    """An empty page (no raw events at all) yields nothing further,
    terminating the pagination loop."""
    with freeze_time("2024-02-07"):
        results = list(spider._parse_paginated_page(empty_page_response, page=2))

    assert results == []
