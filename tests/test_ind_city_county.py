import json
from datetime import datetime
from os.path import dirname, join

import pytest
import scrapy
from city_scrapers_core.constants import CANCELLED, CITY_COUNCIL, COMMITTEE, TENTATIVE
from city_scrapers_core.utils import file_response
from freezegun import freeze_time

from city_scrapers.spiders.ind_city_county import IndCityCountySpider


@pytest.fixture(scope="module")
def test_response():
    return file_response(
        join(dirname(__file__), "files", "ind_city_county.jsonp"),
        url="https://calendar.indy.gov/handlers/query.ashx?get=eventlist&page=0&pageSize=-1&total=-1&view=list.xslt",  # noqa
    )


@pytest.fixture(scope="module")
def empty_page_response():
    return file_response(
        join(dirname(__file__), "files", "ind_city_county_page_empty.jsonp"),
        url="https://calendar.indy.gov/handlers/query.ashx?get=eventlist&page=2&pageSize=125&total=125&view=list.xslt",  # noqa
    )


# Fixtures for the three GraphQL "Activity" pages the spider prefetches in
# start_requests() before parsing the calendar listing.
@pytest.fixture(scope="module")
def committee_agendas_response():
    return file_response(
        join(dirname(__file__), "files", "ind_city_county_committee_agendas.json"),
        url=IndCityCountySpider.GRAPHQL_URL,
    )


@pytest.fixture(scope="module")
def full_council_agendas_response():
    return file_response(
        join(dirname(__file__), "files", "ind_city_county_full_council_agendas.json"),
        url=IndCityCountySpider.GRAPHQL_URL,
    )


@pytest.fixture(scope="module")
def committees_directory_response():
    return file_response(
        join(dirname(__file__), "files", "ind_city_county_committees_directory.json"),
        url=IndCityCountySpider.GRAPHQL_URL,
    )


# Fixtures for the per-committee "Meeting Minutes" GraphQL requests kicked
# off after the committee directory - real ones for the committees used in
# test_minutes_fallback_when_no_agenda and
# test_minutes_match_when_calendar_title_has_extra_meeting_word below, and a
# generic empty one for every other committee in the directory.
@pytest.fixture(scope="module")
def rules_minutes_response():
    return file_response(
        join(
            dirname(__file__), "files", "ind_city_county_committee_minutes_rules.json"
        ),
        url=IndCityCountySpider.GRAPHQL_URL,
    )


@pytest.fixture(scope="module")
def admin_finance_minutes_response():
    return file_response(
        join(dirname(__file__), "files", "ind_city_county_minutes_admin_finance.json"),
        url=IndCityCountySpider.GRAPHQL_URL,
    )


@pytest.fixture(scope="module")
def empty_minutes_response():
    return file_response(
        join(
            dirname(__file__), "files", "ind_city_county_committee_minutes_empty.json"
        ),
        url=IndCityCountySpider.GRAPHQL_URL,
    )


@pytest.fixture(scope="module")
def spider(
    committee_agendas_response,
    full_council_agendas_response,
    committees_directory_response,
    rules_minutes_response,
    admin_finance_minutes_response,
    empty_minutes_response,
):
    """
    A spider with its GraphQL-backed lookups (committee_attachments,
    full_council_attachments, committee_minutes, weekly_notices,
    known_committees) already populated, mirroring the real chain of
    callbacks kicked off by start_requests() in a live crawl.
    """
    spider = IndCityCountySpider()
    with freeze_time("2026-09-02"):
        list(spider.start_requests())
        list(spider._parse_committee_agendas(committee_agendas_response))
        list(spider._parse_full_council_agendas(full_council_agendas_response))

        # _parse_committee_directory kicks off one GraphQL request per
        # committee (for its Meeting Minutes archive), one at a time - walk
        # that chain to completion, feeding the real fixtures for
        # "rules-and-public-policy-committee" and
        # "administration-and-finance-committee" and an empty one for every
        # other committee slug.
        minutes_responses_by_slug = {
            "rules-and-public-policy-committee": rules_minutes_response,
            "administration-and-finance-committee": admin_finance_minutes_response,
        }
        request = list(
            spider._parse_committee_directory(committees_directory_response)
        )[0]
        while request.url == IndCityCountySpider.GRAPHQL_URL:
            slug = json.loads(request.body)["variables"]["slug"]
            response = minutes_responses_by_slug.get(slug, empty_minutes_response)
            request = list(request.callback(response, **request.cb_kwargs))[0]
    return spider


@pytest.fixture(scope="module")
def parsed_items(spider, test_response):
    with freeze_time("2026-09-02"):
        return list(spider.parse(test_response))


def test_count(parsed_items):
    assert len(parsed_items) == 53


def test_title(parsed_items):
    assert parsed_items[0]["title"] == "Parks and Recreation Committee"


def test_description(parsed_items):
    assert (
        parsed_items[0]["description"]
        == "Monthly meeting of the Parks and Recreation Committee of the City-County Council to consider pending legislation"  # noqa
    )


def test_start(parsed_items):
    assert parsed_items[0]["start"] == datetime(2026, 9, 3, 18, 30)


def test_end(parsed_items):
    assert parsed_items[0]["end"] is None


def test_time_notes(parsed_items):
    assert parsed_items[0]["time_notes"] == ""


def test_id(parsed_items):
    assert (
        parsed_items[0]["id"]
        == "ind_city_county/202609031830/x/parks_and_recreation_committee"
    )


def test_status(parsed_items):
    assert parsed_items[0]["status"] == TENTATIVE


def test_location(parsed_items):
    assert parsed_items[0]["location"] == {
        "name": "City-County Building, Meeting Room 260",
        "address": "200 East Washington Street, Indianapolis, IN, 46204",
    }


def test_source(parsed_items):
    """source now points at the meeting's own Event Details page rather
    than the calendar listing endpoint."""
    assert (
        parsed_items[0]["source"]
        == "https://calendar.indy.gov/event/parks-and-recreation-committee-14/"
    )


def test_links(parsed_items):
    """links now carries the real agenda/notice attachment matched from
    the council-committee-agendas GraphQL data instead of a duplicate of
    the Event Details page."""
    assert parsed_items[0]["links"] == [
        {
            "href": "https://us-east-1-indy.graphassets.com/parks-notice",
            "title": "Parks and Recreation Committee Meeting Notice",
        }
    ]


def test_classification(parsed_items):
    assert parsed_items[0]["classification"] == COMMITTEE


def test_all_day(parsed_items):
    assert parsed_items[0]["all_day"] is False


def test_committee_attachment_match_by_name_and_date(parsed_items):
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


def test_cancelled_committee_meeting(parsed_items):
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


def test_full_council_attachment_match_by_date(parsed_items):
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
            "title": "Full Council Meeting Agenda for September 28, 2026",
        }
    ]


def test_no_attachment_when_not_in_api(parsed_items):
    """A committee meeting not present in the API's "next occurrence" list
    (here, deliberately left out of the fixture) gets no attachment rather
    than a fabricated one."""
    meeting = parsed_items[13]
    assert meeting["title"] == "Municipal Corporations Committee"
    assert meeting["classification"] == COMMITTEE
    assert meeting["links"] == []


def test_minutes_fallback_when_no_agenda(parsed_items):
    """A committee meeting with no agenda match (past its "next occurrence"
    window) falls back to that committee's own Meeting Minutes archive."""
    meeting = parsed_items[20]
    assert meeting["title"] == "Rules and Public Policy Committee"
    assert meeting["start"] == datetime(2026, 10, 13, 17, 30)
    assert meeting["status"] == TENTATIVE
    assert meeting["links"] == [
        {
            "href": "https://us-east-1-indy.graphassets.com/rules-minutes-mar",
            "title": "Rules and Public Policy Committee Minutes for October 13, 2026",
        }
    ]


def test_minutes_match_when_calendar_title_has_extra_meeting_word(parsed_items):
    """The calendar sometimes titles a committee meeting "X Committee
    Meeting" while every other source (the committees directory, the
    committee's own Meeting Minutes archive) only ever calls it "X
    Committee" - the lookup key must normalize that difference away, while
    the meeting's own displayed title stays exactly as the calendar has
    it."""
    meeting = parsed_items[30]
    assert meeting["title"] == "Administration and Finance Committee Meeting"
    assert meeting["start"] == datetime(2026, 11, 10, 16, 30)
    assert meeting["classification"] == COMMITTEE
    assert meeting["links"] == [
        {
            "href": "https://us-east-1-indy.graphassets.com/admin-finance-minutes-nov",
            "title": "Administration and Finance Committee Minutes for November 10, 2026",  # noqa
        }
    ]


def test_weekly_notice_fallback_when_no_agenda_or_minutes(parsed_items):
    """A committee meeting with neither an agenda nor minutes match falls
    back to the bundled weekly notice PDF for that meeting's week."""
    meeting = parsed_items[16]
    assert meeting["title"] == "Administration and Finance Committee"
    assert meeting["start"] == datetime(2026, 10, 6, 17, 30)
    assert meeting["links"] == [
        {
            "href": "https://us-east-1-indy.graphassets.com/weekly-notice-mar11",
            "title": "NOTICE OF COMMITTEES OF THE COUNCIL FOR THE WEEK OF October 5-9, 2026",  # noqa
        }
    ]


def test_paginated_page_requests_next_page_when_nonempty(
    spider, test_response, parsed_items
):
    """A page with events yields its meetings and a request for the next
    page, so the 2-year search range gets fully paginated through."""
    with freeze_time("2026-09-02"):
        results = list(spider._parse_paginated_page(test_response, page=0))

    requests = [r for r in results if isinstance(r, scrapy.Request)]
    meetings = [r for r in results if not isinstance(r, scrapy.Request)]

    assert len(meetings) == len(parsed_items)
    assert len(requests) == 1
    assert "page=1" in requests[0].url


def test_paginated_page_stops_on_empty_page(spider, empty_page_response):
    """An empty page (no raw events at all) yields nothing further,
    terminating the pagination loop."""
    with freeze_time("2026-09-02"):
        results = list(spider._parse_paginated_page(empty_page_response, page=2))

    assert results == []
