import json
import re
from datetime import datetime, timedelta

import pytz
import scrapy
from city_scrapers_core.constants import (
    BOARD,
    CITY_COUNCIL,
    COMMISSION,
    COMMITTEE,
    NOT_CLASSIFIED,
)
from city_scrapers_core.items import Meeting
from city_scrapers_core.spiders import CityScrapersSpider
from dateutil import parser
from dateutil.relativedelta import relativedelta
from scrapy.selector import Selector


class IndCityCountySpider(CityScrapersSpider):
    name = "ind_city_county"
    agency = "Indianapolis City-County Council"
    timezone = "America/Detroit"
    # COOKIES_ENABLED is off project-wide, but the search-postback flow
    # below (see SEARCH_URL) only works if the session cookie set by that
    # POST is sent back on the eventlist requests that follow it.
    custom_settings = {"ROBOTSTXT_OBEY": False, "COOKIES_ENABLED": True}

    # calendar.indy.gov is a Dude Solutions "Active Data" ASP.NET calendar.
    # Its plain eventlist endpoint only ever returns today through ~6 months
    # out. Submitting its search form first (a real ASP.NET postback) sets
    # server-side session state for a date range, after which the same
    # eventlist endpoint returns events from that range instead - that's
    # the only way to reach past meetings.
    SEARCH_URL = "https://calendar.indy.gov/?view=list&search=y"
    SEARCH_FORM_ID = "frmPublicMaster"
    START_DATE_FIELD = (
        "ctl01$ctl00$ctl00$publicBody$siteBody$UCEventSearch$UCEventSearch$txtStartDate"  # noqa: E501
    )
    SEARCH_BUTTON_FIELD = (
        "ctl01$ctl00$ctl00$publicBody$siteBody$UCEventSearch$UCEventSearch$btnSearch"  # noqa: E501
    )
    SEARCH_YEARS_BACK = 2
    EVENTLIST_URL = (
        "https://calendar.indy.gov/handlers/query.ashx?get=eventlist&view=list.xslt"
    )
    PAGE_SIZE = 125

    GRAPHQL_URL = "https://api-us-east-1-indy.graphcms.com/v2/ckp3xrh1i657g01xp53az2mv4/master"  # noqa: E501
    GRAPHQL_QUERY = """
        query ($slug: String) {
          activity(where: {slug: $slug}) {
            description {
              markdown
              html
            }
            accordions {
              title
              items {
                description {
                  html
                }
              }
            }
          }
        }
    """

    NOTICE_SUFFIX_RE = re.compile(r"\s*Meeting Notice\s*$")
    WEEK_OF_RE = re.compile(r"Week of ([A-Za-z]+ \d{1,2},?\s*\d{4})")

    async def start(self):
        """Scrapy 2.13+ dropped automatic support for the old-style sync
        start_requests() below as the crawl entry point - it's now only
        ever called directly (e.g. by this test suite), so bridge it here
        for a real crawl to actually start."""
        for request in self.start_requests():
            yield request

    def start_requests(self):
        """
        Prefetch the three indy.gov GraphQL "Activity" pages that carry real
        agenda attachments and the authoritative committee list before
        parsing the calendar's meeting listing, so each meeting can be
        matched against them by (committee name, date) or plain date.
        """
        self.committee_attachments = {}
        self.full_council_attachments = {}
        self.committee_minutes = {}
        self.weekly_notices = {}
        self.known_committees = set()
        yield self._graphql_request(
            "council-committee-agendas", self._parse_committee_agendas
        )

    def _graphql_request(self, slug, callback, cb_kwargs=None):
        payload = {"variables": {"slug": slug}, "query": self.GRAPHQL_QUERY}
        return scrapy.Request(
            self.GRAPHQL_URL,
            method="POST",
            body=json.dumps(payload),
            headers={"Content-Type": "application/json"},
            callback=callback,
            cb_kwargs=cb_kwargs or {},
        )

    def _activity(self, response):
        return response.json()["data"]["activity"] or {}

    def _parse_committee_agendas(self, response):
        """
        The "Upcoming Committee Agendas" list on this page is the only
        place a specific committee meeting's agenda/notice link and
        cancellation status are published, and only for the single next
        occurrence of each committee.
        """
        activity = self._activity(response)
        html = (activity.get("description") or {}).get("html", "")
        sel = Selector(text=html)
        items = sel.xpath(
            '//h2[contains(text(), "Upcoming Committee Agendas")]'
            "/following-sibling::ul[1]/li"
        )
        for li in items:
            link = li.css("a")
            if not link:
                continue
            href = link.attrib.get("href")
            title_attr = link.attrib.get("title", "").strip()
            name = self.NOTICE_SUFFIX_RE.sub("", title_attr).strip()
            date_text = link.css("::text").get(default="").strip()
            if not name or not href or not date_text:
                continue
            try:
                meeting_date = parser.parse(date_text).date()
            except (ValueError, OverflowError):
                continue
            full_text = " ".join(li.css("::text").getall())
            cancelled = "cancel" in full_text.lower()
            self.committee_attachments[(name, meeting_date)] = {
                "href": href,
                "title": title_attr,
                "cancelled": cancelled,
            }
        self._parse_weekly_notices(activity)
        yield self._graphql_request(
            "council-meeting-agendas", self._parse_full_council_agendas
        )

    def _parse_weekly_notices(self, activity):
        """
        The same page's "Weekly Notices" accordion bundles every committee
        meeting that week into a single PDF, keyed by the Monday of that
        week - a last-resort, imprecise fallback (the PDF isn't guaranteed
        to actually contain any given committee's agenda) for meetings with
        neither an agenda nor minutes match.
        """
        for accordion in activity.get("accordions") or []:
            if "Weekly Notices" not in (accordion.get("title") or ""):
                continue
            for year_item in accordion.get("items") or []:
                html = (year_item.get("description") or {}).get("html", "")
                sel = Selector(text=html)
                for li in sel.css("li"):
                    link = li.css("a")
                    full_text = " ".join(li.css("::text").getall())
                    week_match = self.WEEK_OF_RE.search(full_text)
                    if not link or not week_match:
                        continue
                    href = link.attrib.get("href")
                    if not href:
                        continue
                    try:
                        week_start = parser.parse(week_match.group(1)).date()
                    except (ValueError, OverflowError):
                        continue
                    title_attr = link.attrib.get("title", "").strip()
                    if not title_attr or title_attr.startswith("http"):
                        title_attr = f"Committee Notices for the {week_match.group(0)}"
                    self.weekly_notices[week_start] = {
                        "href": href,
                        "title": title_attr,
                    }

    def _parse_full_council_agendas(self, response):
        """
        Full Council agendas are archived by year, with one entry per
        specific meeting date going back to 2019, so past and future
        Full Council meetings can both get a real attachment.
        """
        activity = self._activity(response)
        for accordion in activity.get("accordions") or []:
            for year_item in accordion.get("items") or []:
                html = (year_item.get("description") or {}).get("html", "")
                sel = Selector(text=html)
                for li in sel.css("li"):
                    link = li.css("a")
                    if not link:
                        continue
                    href = link.attrib.get("href")
                    title_attr = link.attrib.get("title", "").strip()
                    date_text = li.xpath("string(.)").get(default="").split(":")[0]
                    if not href or not date_text:
                        continue
                    try:
                        meeting_date = parser.parse(date_text.strip()).date()
                    except (ValueError, OverflowError):
                        continue
                    self.full_council_attachments[meeting_date] = {
                        "href": href,
                        "title": title_attr,
                    }
        yield self._graphql_request(
            "committees-of-the-council", self._parse_committee_directory
        )

    COMMITTEE_LIST_ITEM_RE = re.compile(
        r"^-\s+\[([^\]]+)\]\(https://www\.indy\.gov/activity/([a-z0-9-]+)",
        re.MULTILINE,
    )

    def _parse_committee_directory(self, response):
        """
        The full, current list of standing committees (and their own page
        slugs), read dynamically so a committee being renamed/added/removed
        is picked up automatically rather than relying on a hardcoded list.
        Each committee's own page is queried next for its Meeting Minutes
        archive, which covers past meetings the "next occurrence only"
        council-committee-agendas page doesn't.
        """
        activity = self._activity(response)
        markdown = (activity.get("description") or {}).get("markdown", "")
        committees = []
        for match in self.COMMITTEE_LIST_ITEM_RE.finditer(markdown):
            name, slug = match.group(1).strip(), match.group(2).strip()
            self.known_committees.add(name)
            committees.append((name, slug))
        yield self._next_committee_minutes_request(committees)

    def _next_committee_minutes_request(self, remaining):
        """Walk the committee list one at a time, since each needs its own
        GraphQL request; once exhausted, move on to the calendar search."""
        if not remaining:
            return scrapy.Request(self.SEARCH_URL, callback=self._parse_search_page)
        name, slug = remaining[0]
        return self._graphql_request(
            slug,
            self._parse_committee_minutes,
            cb_kwargs={"committee_name": name, "remaining": remaining[1:]},
        )

    def _parse_committee_minutes(self, response, committee_name, remaining):
        """
        Each committee's own page has a "Meeting Minutes" accordion with
        one item per year, each listing individual meeting dates - the
        same per-date-archive shape as Full Council's agendas, just for
        minutes instead. Matched by (committee_name, date), used only when
        no agenda exists for that meeting (see _parse_attachment).
        """
        activity = self._activity(response)
        for accordion in activity.get("accordions") or []:
            for year_item in accordion.get("items") or []:
                html = (year_item.get("description") or {}).get("html", "")
                sel = Selector(text=html)
                for link in sel.css("a"):
                    href = link.attrib.get("href")
                    date_text = link.css("::text").get(default="").strip()
                    if not href or not date_text:
                        continue
                    try:
                        meeting_date = parser.parse(date_text).date()
                    except (ValueError, OverflowError):
                        # not every link's text is a date (e.g. "Exhibit A"
                        # attachments alongside a dated minutes link)
                        continue
                    self.committee_minutes[(committee_name, meeting_date)] = {
                        "href": href,
                        "title": f"{committee_name} Minutes for {date_text}",
                    }
        yield self._next_committee_minutes_request(remaining)

    def _search_start_date_str(self):
        """Format as the site's own "M/D/YYYY" (no zero-padding)."""
        start_date = datetime.now().date() - relativedelta(
            years=self.SEARCH_YEARS_BACK
        )
        return f"{start_date.month}/{start_date.day}/{start_date.year}"

    def _parse_search_page(self, response):
        """
        Submit the calendar's search form with an extended Start Date,
        leaving Category/Keyword/End Date untouched so the set of event
        types and the forward-looking window stay exactly what they are
        today - only the past coverage widens.
        """
        yield scrapy.FormRequest.from_response(
            response,
            formid=self.SEARCH_FORM_ID,
            formdata={self.START_DATE_FIELD: self._search_start_date_str()},
            clickdata={"name": self.SEARCH_BUTTON_FIELD},
            callback=self._parse_search_submitted,
        )

    def _parse_search_submitted(self, response):
        """The search postback response is just the reloaded shell page;
        the matching events are only available from the eventlist endpoint
        once this response's session cookies are sent along with it."""
        yield self._event_list_request(page=0)

    def _event_list_request(self, page):
        url = f"{self.EVENTLIST_URL}&page={page}&pageSize={self.PAGE_SIZE}&total={self.PAGE_SIZE}"  # noqa: E501
        return scrapy.Request(
            url, callback=self._parse_paginated_page, cb_kwargs={"page": page}
        )

    def _parse_paginated_page(self, response, page):
        """
        Build meetings from this page, then request the next page as long
        as this one had any raw events at all - a page can be full of
        non-council events (0 classified meetings) while later pages still
        hold real ones, so pagination can't stop on the classified count.
        """
        items = self._page_items(response)
        for item in items:
            meeting = self._build_meeting(item, response)
            if meeting:
                yield meeting
        if items:
            yield self._event_list_request(page + 1)

    def parse(self, response):
        """
        Parse the HTML content from a JSONP response. This agency includes
        many events that are not meetings, so we avoid parsing any meeting
        with an unknown classification.
        """
        for item in self._page_items(response):
            meeting = self._build_meeting(item, response)
            if meeting:
                yield meeting

    def _page_items(self, response):
        json_response = self.parse_jsonp(response.text)
        html_content = json_response["html"]
        sel = Selector(text=html_content)
        return sel.css("article.list-event")

    def _build_meeting(self, item, response):
        title = self._parse_title(item)
        all_day, start, end = self._parse_datetimes(item)
        classification = self._parse_classification(title)
        if classification == NOT_CLASSIFIED:
            return None
        links, cancelled = self._parse_attachment(title, start, classification)
        meeting = Meeting(
            title=title,
            description=self._parse_description(item),
            classification=classification,
            start=start,
            end=end,
            all_day=all_day,
            time_notes="",
            location=self._parse_location(item),
            links=links,
            source=self._parse_source(item, response),
        )
        status_text = "CANCELLED" if cancelled else ""
        meeting["status"] = self._get_status(meeting, status_text)
        meeting["id"] = self._get_id(meeting)
        return meeting

    def parse_jsonp(self, jsonp_str):
        """Decode a JSONP string and returns a Dict."""
        start = jsonp_str.find("(") + 1
        end = jsonp_str.rfind(")")
        json_str = jsonp_str[start:end]
        return json.loads(json_str)

    def _parse_title(self, item):
        title = item.css('h3[itemprop="name"] a::text').get()
        return title.strip() if title else ""

    def _parse_description(self, item):
        description = item.css('p[itemprop="description"]::text').get()
        return description.strip() if description else ""

    def _parse_classification(self, title):
        """Generates classification from title, preferring an exact match
        against the dynamically-fetched committee list before falling back
        to keyword matching for departments the GraphQL API doesn't cover
        (e.g. boards, commissions)."""
        clean_title = title.lower()
        if any(name.lower() == clean_title for name in self.known_committees):
            return COMMITTEE
        if "committee" in clean_title:
            return COMMITTEE
        elif "council" in clean_title:
            return CITY_COUNCIL
        elif "commission" in clean_title:
            return COMMISSION
        elif "board" in clean_title:
            return BOARD
        return NOT_CLASSIFIED

    def _parse_attachment(self, title, start, classification):
        """
        Look up this meeting's real agenda/notice link from the GraphQL
        data prefetched in start_requests, matched by (committee name,
        date) for committees or by date alone for Full Council. Returns a
        tuple of (links, cancelled).
        """
        if classification == CITY_COUNCIL:
            entry = self.full_council_attachments.get(start.date())
            if entry:
                return [{"href": entry["href"], "title": entry["title"]}], False
            return [], False
        if classification == COMMITTEE:
            key = (title, start.date())
            entry = self.committee_attachments.get(key)
            if entry:
                links = [{"href": entry["href"], "title": entry["title"]}]
                return links, entry["cancelled"]
            # No agenda (only ever published for the next occurrence) -
            # fall back to that meeting's own minutes archive, if any.
            minutes_entry = self.committee_minutes.get(key)
            if minutes_entry:
                minutes_title = minutes_entry["title"]
                return [{"href": minutes_entry["href"], "title": minutes_title}], False
            # Still nothing - last resort: the bundled weekly notice PDF for
            # that meeting's week, best-effort only (shared across every
            # committee that met that week, not specific to this one).
            week_start = start.date() - timedelta(days=start.weekday())
            weekly_entry = self.weekly_notices.get(week_start)
            if weekly_entry:
                weekly_title = weekly_entry["title"]
                return [{"href": weekly_entry["href"], "title": weekly_title}], False
            return [], False
        return [], False

    def _parse_datetimes(self, item):
        """
        Parse the start and end datetimes from the HTML. Values are
        located in time tags in "datetime" attrib. The presence of
        "startDate" and "endDate" in the same "itemprop" attrib
        indicate that the event is all day.

        Returns a tuple of three values:
        - all_day: a boolean indicating whether the event is all day
        - start: the start datetime of the event
        - end: the end datetime of the event or None
        """
        all_day = bool(
            item.css('time[itemprop="startDate endDate"]::attr(datetime)').get()
        )
        start_datetime_str = item.css(
            'time[itemprop*="startDate"]::attr(datetime)'
        ).get()

        # all day – only parse start
        # only a date string should be present as the attribute value string
        # so we don't need to convert timezones.
        if all_day:
            event_datetime = parser.parse(start_datetime_str)
            return all_day, event_datetime, event_datetime

        # not all day – parse start and end
        # A datetime string should be present and includes tz info that must
        # be converted. An end datetime is not guaranteed to be present.
        end_datetime_str = item.css('time[itemprop*="endDate"]::attr(datetime)').get()
        end_datetime = (
            self._parse_datetime(end_datetime_str) if end_datetime_str else None
        )
        start_datetime = self._parse_datetime(start_datetime_str)
        return all_day, start_datetime, end_datetime

    def _parse_datetime(self, datetime_str):
        """Convert the datetime string to the local timezone and
        return a naive datetime object."""
        start_datetime = parser.parse(datetime_str)
        desired_tz = pytz.timezone(self.timezone)
        start_datetime_aware = start_datetime.astimezone(desired_tz)
        return start_datetime_aware.replace(tzinfo=None)

    def _parse_location(self, item):
        """
        Parse the location from the HTML. Address details
        are generally contained in a single span tag, or
        contained in multiple span tags.
        """
        # handle compact location
        compact_location = (
            item.css('span[itemprop="name address"]::text').get(default="").strip()
        )
        if compact_location:
            return {"name": "", "address": compact_location}

        # handle detailed location
        location_name = item.css('span[itemprop="name"]::text').get(default="").strip()
        street_address = item.css('span[itemprop="streetAddress"]::text').get(
            default=""
        )
        additional_info = (
            item.xpath("normalize-space(following-sibling::text()[1])")
            .get(default="")
            .strip()
        )
        locality = item.css('span[itemprop="addressLocality"]::text').get(default="")
        region = item.css('span[itemprop="addressRegion"]::text').get(default="")
        postal_code = item.css('span[itemprop="postalCode"]::text').get(default="")
        address_components = [
            street_address,
            additional_info,
            locality,
            region,
            postal_code,
        ]
        address = ", ".join(filter(None, address_components))

        if not location_name and not address:
            return {"name": "TBD", "address": ""}

        return {"name": location_name, "address": address}

    def _parse_source(self, item, response):
        event_link = item.css("section.list-event-link a::attr(href)").get()
        return event_link or response.url
