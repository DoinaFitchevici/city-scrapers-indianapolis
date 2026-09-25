import re

import scrapy
from city_scrapers_core.constants import BOARD
from city_scrapers_core.items import Meeting
from city_scrapers_core.spiders import CityScrapersSpider
from dateutil.parser import parser


class IndIndygoBodSpiderMeta(type):
    """
    Metaclass that enforces required static variables on generated spiders.
    """

    def __init__(cls, name, bases, namespace):
        if name == "IndIndygoBodSpiderMixin":
            super().__init__(name, bases, namespace)
            return

        if any(
            getattr(base, "__name__", "") == "IndIndygoBodSpiderMixin" for base in bases
        ):
            required_static_vars = [
                "agency",
                "name",
                "title",
                "section_heading_match",
                "agency_name",
            ]

            missing_vars = [
                variable
                for variable in required_static_vars
                if variable not in namespace
            ]

            if missing_vars:
                missing_vars_str = ", ".join(missing_vars)

                raise NotImplementedError(
                    f"{name} must define the following static variable(s): "
                    f"{missing_vars_str}."
                )

        super().__init__(name, bases, namespace)


class IndIndygoBodSpiderMixin(
    CityScrapersSpider,
    metaclass=IndIndygoBodSpiderMeta,
):
    """
    Shared implementation for the IndyGo Board and committee spiders.
    """

    name = None
    agency = None
    title = None
    classification = BOARD
    section_heading_match = None
    links = []
    location = {"name": "", "address": ""}
    time_notes = ""

    _FALLBACK_TIME_NOTES = "Check meeting attachments for a more accurate location."

    _LOCATION_RE = re.compile(r"held at (?P<address>.+?) in the (?P<name>.+?)\.")

    _WAYBACK_PREFIX_RE = re.compile(r"^https?://web\.archive\.org/web/[^/]+/")

    #: Facebook/fb.watch videos require a login to play, so skip them.
    _FACEBOOK_LINK_RE = re.compile(r"^https?://(www\.)?(facebook\.com|fb\.watch)/")

    #: Catches broken `href`s where an editor pasted the link text itself
    #: (e.g. "http://Finance Committee Meeting") instead of a real URL --
    #: no real URL contains a literal space.
    _MALFORMED_HREF_RE = re.compile(r"\s")

    board_reports_container_selector = None

    video_archive_url = "https://www.indygo.net/board-meeting-media-archives/"

    video_archive_pattern = None

    _MONTH_NAMES = (
        "January",
        "February",
        "March",
        "April",
        "May",
        "June",
        "July",
        "August",
        "September",
        "October",
        "November",
        "December",
    )

    schedule_container_selector = ".rc-layout-content.rc-text-lg"

    #: Extra `?year=<meeting_year + offset>` requests against a committee's
    #: OnBoard listings page. Each such response always includes the
    #: requested year plus the year before it, so offset -1 sweeps up
    #: `meeting_year - 1` and `- 2`, and offset +1 sweeps up
    #: `meeting_year + 1` -- net effect: 2 years back, 1 forward.
    _EXTRA_LISTING_YEAR_OFFSETS = (-1, 1)

    #: Wayback Machine snapshots backfilling years the live board page no
    #: longer shows (it only ever displays the current year). Only the
    #: Board spider uses this -- committees backfill via OnBoard's
    #: `?year=` instead (see `_EXTRA_LISTING_YEAR_OFFSETS`). One-time
    #: backfill for 2024-2025; 2027+ will come from the live page.
    historical_snapshots = []

    timezone = "America/Detroit"

    start_urls = ["https://www.indygo.net/about-indygo/board-of-directors/"]

    custom_settings = {"ROBOTSTXT_OBEY": False, "FEED_EXPORT_ENCODING": "utf-8"}

    def parse(self, response):
        """Parse this spider's section, then backfill any `historical_snapshots`."""
        yield from self._parse_schedule_page(response, self.schedule_container_selector)

        for snapshot in self.historical_snapshots:
            if snapshot.get("design") == "old":
                yield scrapy.Request(
                    snapshot["url"],
                    callback=self._parse_old_design_schedule_snapshot,
                    cb_kwargs={"reports_url": snapshot.get("reports_url")},
                )
            else:
                yield scrapy.Request(
                    snapshot["url"],
                    callback=self._parse_historical_snapshot,
                    cb_kwargs={
                        "container_selector": snapshot.get("container_selector")
                    },
                )

    def _parse_schedule_page(self, response, container_selector):
        """Parse meetings from a board page's schedule section."""
        self.location, self.time_notes = self._parse_location_and_time_notes(response)

        container = response.css(container_selector)

        if not container:
            self.logger.warning(
                "Could not find the schedule container using selector %s",
                container_selector,
            )
            return

        meeting_year, raw_meeting_time, dates_list, listings_href = self._parse_section(
            container[0]
        )

        if meeting_year is None:
            self.logger.warning(
                "Could not find a section heading matching %r for %s",
                self.section_heading_match,
                self.agency,
            )
            return

        if not raw_meeting_time:
            self.logger.warning(
                "Could not find a meeting time for %s",
                self.agency,
            )
            return

        if dates_list is None:
            self.logger.warning(
                "Could not find meeting dates for %s",
                self.agency,
            )
            return

        meeting_time = self._parse_meeting_time(raw_meeting_time)

        starts = [
            (
                self._parse_start(date_item, meeting_year, meeting_time),
                self._parse_title(date_item),
            )
            for date_item in dates_list.css("li")
        ]

        board_reports_by_month = (
            self._parse_board_reports(response)
            if self.board_reports_container_selector
            else None
        )

        if self.video_archive_pattern:
            yield scrapy.Request(
                self.video_archive_url,
                callback=self._parse_video_archive_and_continue,
                cb_kwargs={
                    "starts": starts,
                    "source": response.url,
                    "board_reports_by_month": board_reports_by_month,
                    "listings_href": listings_href,
                    "meeting_year": meeting_year,
                    "meeting_time": meeting_time,
                },
            )
        else:
            yield from self._continue_parsing(
                starts,
                response.url,
                listings_href,
                meeting_year,
                meeting_time,
                board_reports_by_month,
            )

    def _parse_historical_snapshot(self, response, container_selector=None):
        """Default historical-snapshot handler, for a same-design page."""
        yield from self._parse_schedule_page(
            response, container_selector or self.schedule_container_selector
        )

    def _strip_wayback(self, url):
        """Unwrap a snapshot's `web.archive.org/web/<ts>/<url>` links to the live URL."""
        return self._WAYBACK_PREFIX_RE.sub("", url)

    def _parse_old_design_section(self, response):
        """
        Find this spider's section on an old (pre-Oct-2025) page design,
        where each section is its own `<div class="content-section">`
        instead of h2/p/ul siblings (see `_parse_section`).
        """
        for section in response.css("div.content-section"):
            heading = section.css("h2::text").get()
            if not heading or self.section_heading_match not in heading:
                continue

            meeting_year = self._parse_meeting_year(heading)

            raw_meeting_time = None
            listings_href = None
            for paragraph in section.css("p"):
                strong_text = paragraph.css("strong::text").get()
                if strong_text and "meeting time" in strong_text.lower():
                    raw_meeting_time = strong_text
                    continue

                link_text = paragraph.css("a::text").get()
                if link_text and "click here" in link_text.lower():
                    href = paragraph.css("a::attr(href)").get()
                    if href:
                        listings_href = self._strip_wayback(response.urljoin(href))

            return meeting_year, raw_meeting_time, section.css("ul"), listings_href

        return None, None, None, None

    def _parse_old_design_schedule_snapshot(self, response, reports_url=None):
        """
        Parse an old-design snapshot's schedule section. Board Reports come
        from this same response, unless `reports_url` points to a later
        snapshot with a more complete Reports accordion.
        """
        self.location, self.time_notes = self._parse_location_and_time_notes(response)

        meeting_year, raw_meeting_time, dates_list, listings_href = (
            self._parse_old_design_section(response)
        )

        if meeting_year is None or not raw_meeting_time or not dates_list:
            self.logger.warning("Could not parse historical snapshot %s", response.url)
            return

        meeting_time = self._parse_meeting_time(raw_meeting_time)

        starts = [
            (
                self._parse_start(date_item, meeting_year, meeting_time),
                self._parse_title(date_item),
            )
            for date_item in dates_list.css("li")
        ]

        if reports_url:
            yield scrapy.Request(
                reports_url,
                callback=self._parse_old_design_reports_and_continue,
                cb_kwargs={
                    "starts": starts,
                    "source": response.url,
                    "listings_href": listings_href,
                    "meeting_year": meeting_year,
                    "meeting_time": meeting_time,
                },
            )
        else:
            board_reports_by_month = self._parse_old_design_board_reports(
                response, meeting_year
            )
            yield from self._continue_parsing(
                starts,
                response.url,
                listings_href,
                meeting_year,
                meeting_time,
                board_reports_by_month,
            )

    def _parse_old_design_reports_and_continue(
        self, response, starts, source, listings_href, meeting_year, meeting_time
    ):
        board_reports_by_month = self._parse_old_design_board_reports(
            response, meeting_year
        )
        yield from self._continue_parsing(
            starts,
            source,
            listings_href,
            meeting_year,
            meeting_time,
            board_reports_by_month,
        )

    def _parse_old_design_board_reports(self, response, year):
        """Board Reports accordion on an old-design board page snapshot."""
        reports = {}

        for card in response.css("#accordion-1 .card"):
            heading = "".join(card.css(".card-header button ::text").getall()).strip()
            if not heading.startswith(year):
                continue

            for link in card.css(".card-body a"):
                text = "".join(link.css("::text").getall()).strip()
                match = re.match(rf"^([A-Za-z]+)\s+{year}\b", text)
                if not match:
                    continue

                href = link.attrib.get("href")
                if not href:
                    continue

                reports[(year, match.group(1))] = self._strip_wayback(
                    response.urljoin(href)
                )

        return reports

    def _parse_location_and_time_notes(self, response):
        location = self._parse_location(response)
        if location:
            return location, ""

        return {"name": "", "address": ""}, self._FALLBACK_TIME_NOTES

    def _parse_location(self, response):
        headings = response.xpath(
            '//h2[normalize-space(text())="Attend a Board Meeting"]'
        )
        if not headings:
            return None

        paragraphs = headings[0].xpath("following-sibling::p[1]")
        if not paragraphs:
            return None

        paragraph_text = "".join(paragraphs.css("::text").getall()).strip()

        match = self._LOCATION_RE.search(paragraph_text)
        if not match:
            return None

        address = match.group("address").replace(".", "").strip()
        name = match.group("name")
        name = name.replace("located in our", "-")
        name = name.replace("“", "'").replace("”", "'").replace('"', "'")
        name = re.sub(r"\s+", " ", name).strip()

        if not address or not name:
            return None

        return {"name": name, "address": f"{address}, Indianapolis, IN 46235"}

    def _parse_video_archive_and_continue(
        self,
        response,
        starts,
        source,
        board_reports_by_month,
        listings_href,
        meeting_year,
        meeting_time,
    ):
        video_link_by_month = self._parse_video_archive(response)
        yield from self._continue_parsing(
            starts,
            source,
            listings_href,
            meeting_year,
            meeting_time,
            board_reports_by_month,
            video_link_by_month,
        )

    def _continue_parsing(
        self,
        starts,
        source,
        listings_href,
        meeting_year,
        meeting_time,
        board_reports_by_month=None,
        video_link_by_month=None,
    ):
        if listings_href:
            yield scrapy.Request(
                listings_href,
                callback=self._parse_meeting_listings_and_build,
                cb_kwargs={
                    "starts": starts,
                    "source": source,
                    "listings_href": listings_href,
                    "meeting_year": meeting_year,
                    "meeting_time": meeting_time,
                    "board_reports_by_month": board_reports_by_month,
                    "video_link_by_month": video_link_by_month,
                },
            )
        else:
            for start, title in starts:
                links = self._resolve_links(
                    start,
                    board_reports_by_month=board_reports_by_month,
                    video_link_by_month=video_link_by_month,
                )
                yield self._build_meeting(start, title, links, source)

    def _build_meeting(self, start, title, links, source):
        meeting = Meeting(
            title=title,
            description="",
            classification=self.classification,
            start=start,
            end=None,
            all_day=False,
            time_notes=self.time_notes,
            location=self.location,
            links=links,
            source=source,
        )

        meeting["status"] = self._get_status(meeting)
        meeting["id"] = self._get_id(meeting)

        return meeting

    def _parse_meeting_listings_and_build(
        self,
        response,
        starts,
        source,
        listings_href,
        meeting_year,
        meeting_time,
        board_reports_by_month=None,
        video_link_by_month=None,
    ):
        """Match current-year meetings to the OnBoard listing, then fetch extra years."""
        meeting_link_by_date, _past_dates_by_year = self._parse_meeting_listings(
            response, meeting_year
        )

        yield from self._fetch_extra_listing_years(
            list(self._EXTRA_LISTING_YEAR_OFFSETS),
            starts=starts,
            source=source,
            listings_href=listings_href,
            meeting_year=meeting_year,
            meeting_time=meeting_time,
            meeting_link_by_date=meeting_link_by_date,
            extra_dates_by_year={},
            board_reports_by_month=board_reports_by_month,
            video_link_by_month=video_link_by_month,
        )

    def _fetch_extra_listing_years(
        self,
        remaining_offsets,
        starts,
        source,
        listings_href,
        meeting_year,
        meeting_time,
        meeting_link_by_date,
        extra_dates_by_year,
        board_reports_by_month=None,
        video_link_by_month=None,
    ):
        if not remaining_offsets:
            all_starts = starts + self._parse_past_starts(
                extra_dates_by_year, meeting_time
            )
            for start, title in all_starts:
                links = self._resolve_links(
                    start,
                    listings_href=listings_href,
                    meeting_link_by_date=meeting_link_by_date,
                    board_reports_by_month=board_reports_by_month,
                    video_link_by_month=video_link_by_month,
                )
                yield self._build_meeting(start, title, links, source)
            return

        offset, remaining_offsets = remaining_offsets[0], remaining_offsets[1:]
        separator = "&" if "?" in listings_href else "?"
        year_url = f"{listings_href}{separator}year={int(meeting_year) + offset}"

        yield scrapy.Request(
            year_url,
            callback=self._parse_extra_listing_year_and_continue,
            cb_kwargs={
                "remaining_offsets": remaining_offsets,
                "starts": starts,
                "source": source,
                "listings_href": listings_href,
                "meeting_year": meeting_year,
                "meeting_time": meeting_time,
                "meeting_link_by_date": meeting_link_by_date,
                "extra_dates_by_year": extra_dates_by_year,
                "board_reports_by_month": board_reports_by_month,
                "video_link_by_month": video_link_by_month,
            },
        )

    def _parse_extra_listing_year_and_continue(
        self,
        response,
        remaining_offsets,
        starts,
        source,
        listings_href,
        meeting_year,
        meeting_time,
        meeting_link_by_date,
        extra_dates_by_year,
        board_reports_by_month=None,
        video_link_by_month=None,
    ):
        new_link_by_date, new_dates_by_year = self._parse_meeting_listings(
            response, meeting_year
        )

        meeting_link_by_date = {**meeting_link_by_date, **new_link_by_date}

        extra_dates_by_year = dict(extra_dates_by_year)
        for year, dates in new_dates_by_year.items():
            # First offset to find a year wins -- guards against OnBoard's
            # fallback view re-reporting a year we already have.
            extra_dates_by_year.setdefault(year, dates)

        yield from self._fetch_extra_listing_years(
            remaining_offsets,
            starts=starts,
            source=source,
            listings_href=listings_href,
            meeting_year=meeting_year,
            meeting_time=meeting_time,
            meeting_link_by_date=meeting_link_by_date,
            extra_dates_by_year=extra_dates_by_year,
            board_reports_by_month=board_reports_by_month,
            video_link_by_month=video_link_by_month,
        )

    def _resolve_links(
        self,
        start,
        listings_href=None,
        meeting_link_by_date=None,
        board_reports_by_month=None,
        video_link_by_month=None,
    ):
        """Add a link only once a document for this exact meeting exists."""
        links = [dict(link) for link in self.links]

        if listings_href:
            date_key = (str(start.year), start.strftime("%b"), start.day)
            self._append_link(links, meeting_link_by_date, date_key, "Meeting Listings")

        if board_reports_by_month:
            month_key = (str(start.year), start.strftime("%B"))
            self._append_link(links, board_reports_by_month, month_key, "Board Report")

        if video_link_by_month:
            month_key = (str(start.year), start.strftime("%B"))
            href = video_link_by_month.pop(month_key, None)
            if href:
                links.append({"href": href, "title": "Video"})

        return links

    @staticmethod
    def _append_link(links, mapping, key, title):
        href = (mapping or {}).get(key)
        if href:
            links.append({"href": href, "title": title})

    def _is_facebook_link(self, href):
        return bool(self._FACEBOOK_LINK_RE.match(href))

    def _is_malformed_href(self, href):
        return bool(self._MALFORMED_HREF_RE.search(href))

    def _parse_board_reports(self, response):
        """
        Walk the "Board Reports" accordion and return a
        `{(year, month_name): report_url}` mapping of each month's report.
        """
        board_reports_by_month = {}
        month_pattern = "|".join(self._MONTH_NAMES)

        accordion_items = response.css(
            f"{self.board_reports_container_selector} .rc-accordion-item"
        )

        for item in accordion_items:
            heading_text = item.css(".rc-accordion-button-text::text").get()
            if not heading_text:
                continue

            year_match = re.search(r"\b\d{4}\b", heading_text)
            if not year_match:
                continue

            year = year_match.group()

            for link in item.css(".rc-accordion-body ul.wp-block-list > li > a"):
                link_text = "".join(link.css("::text").getall()).strip()
                month_match = re.match(
                    rf"({month_pattern})\s+{year}\s+Board\s+(?:Meeting|Report)\b",
                    link_text,
                )
                if not month_match:
                    continue

                href = link.attrib.get("href")
                if not href:
                    continue

                board_reports_by_month[(year, month_match.group(1))] = (
                    self._strip_wayback(response.urljoin(href))
                )

        return board_reports_by_month

    def _parse_video_archive(self, response):
        video_link_by_month = {}

        for year_item in response.css(".rc-block--accordion .rc-accordion-item"):
            year = year_item.css(".rc-accordion-button-text::text").get()
            if not year:
                continue

            current_month = None

            for child in year_item.css(".rc-accordion-content").xpath("./*"):
                tag = child.root.tag

                if tag == "p":
                    current_month = "".join(child.css("::text").getall()).strip()
                    continue

                if tag != "ul" or not current_month:
                    continue

                for link in child.css("li > a"):
                    link_text = "".join(link.css("::text").getall()).strip()
                    if not re.match(
                        self.video_archive_pattern, link_text, re.IGNORECASE
                    ):
                        continue

                    href = link.attrib.get("href")
                    if (
                        not href
                        or self._is_facebook_link(href)
                        or self._is_malformed_href(href)
                    ):
                        continue

                    video_link_by_month[(year, current_month)] = response.urljoin(href)

        return video_link_by_month

    def _parse_meeting_listings(self, response, meeting_year):
        """
        Return `{(year, month_abbr, day): meeting_url}` for every listed
        meeting, plus `{year: [(month_abbr, day), ...]}` for any other
        years also shown on the page.
        """
        meeting_link_by_date = {}
        past_dates_by_year = {}
        current_year = None

        nodes = response.xpath(
            '//div[contains(concat(" ", normalize-space(@class), " "), '
            '" year-text ") or '
            'contains(concat(" ", normalize-space(@class), " "), '
            '" year-meetings ")]'
        )

        for node in nodes:
            classes = node.attrib.get("class", "").split()

            if "year-text" in classes:
                current_year = node.css("h3::text").get()
                continue

            if current_year is None:
                continue

            for day_item in node.css(".meeting-day-item"):
                day_label = day_item.css(".day-label::text").get()
                if not day_label:
                    continue

                month_abbr, _, day_str = day_label.strip().partition(" ")
                if not day_str.isdigit():
                    continue

                href = day_item.css("a.meeting-link::attr(href)").get()
                if not href:
                    continue

                date_key = (current_year, month_abbr, int(day_str))
                meeting_link_by_date[date_key] = response.urljoin(href)

                if current_year != meeting_year:
                    past_dates_by_year.setdefault(current_year, []).append(
                        (month_abbr, int(day_str))
                    )

        return meeting_link_by_date, past_dates_by_year

    def _parse_past_starts(self, past_dates_by_year, meeting_time):
        """
        Build `(start, title)` tuples for meetings found only on the OnBoard
        listing, using the same recurring meeting time as the current year.
        """
        past_starts = []

        for year, dates in past_dates_by_year.items():
            for month_abbr, day in dates:
                start = parser().parse(f"{month_abbr} {day} {year} {meeting_time}")
                past_starts.append((start, self.title))

        return sorted(past_starts, key=lambda item: item[0])

    def _parse_section(self, container):
        in_section = False
        meeting_year = None
        raw_meeting_time = None
        dates_list = None
        listings_href = None

        for child in container.xpath("./*"):
            tag = child.root.tag

            if tag == "h2":
                if in_section:
                    break

                heading_text = "".join(child.css("::text").getall()).strip()
                if self.section_heading_match in heading_text:
                    in_section = True
                    meeting_year = self._parse_meeting_year(heading_text)
                continue

            if not in_section:
                continue

            if tag == "p":
                strong_text = child.css("strong::text").get()
                if strong_text and "meeting time" in strong_text.lower():
                    raw_meeting_time = strong_text
                else:
                    link_text = child.css("a::text").get()
                    if link_text and "click here" in link_text.lower():
                        href = child.css("a::attr(href)").get()
                        if href:
                            listings_href = href
            elif tag == "ul":
                dates_list = child

        return meeting_year, raw_meeting_time, dates_list, listings_href

    def _parse_meeting_year(self, section_title):
        """Parse the four-digit year from a section heading like "2026 ...Meetings"."""
        year_match = re.search(r"\b\d{4}\b", section_title)

        if not year_match:
            raise ValueError(
                f"Could not find meeting year in section title: " f"{section_title!r}"
            )

        return year_match.group()

    def _parse_meeting_time(self, raw_time):
        """Strip the field label and trailing timezone abbreviation."""
        if ":" in raw_time:
            time_string = raw_time.split(":", 1)[1]
        else:
            time_string = raw_time

        return re.sub(
            r"\s*(?:EST|EDT)\s*$",
            "",
            time_string,
            flags=re.IGNORECASE,
        ).strip()

    #: Matches the leading "Weekday, Month Day[st|nd|rd|th]" part of a
    #: schedule item, ignoring any annotation after it (dash, colon, etc).
    _DATE_PREFIX_RE = re.compile(r"^[A-Za-z]+,\s*[A-Za-z.]+\s*\d{1,2}(?:st|nd|rd|th)?")

    def _parse_start(self, date_item, meeting_year, meeting_time):
        """
        Parse the meeting start as a naive datetime object.

        E.g. "Thursday, Jan. 15" or "Thursday, July 16 – Budget Introduced".
        """
        raw_date = " ".join(date_item.css("::text").getall()).strip()

        date_match = self._DATE_PREFIX_RE.match(raw_date)
        if not date_match:
            raise ValueError(f"Could not parse a date from: {raw_date!r}")

        meeting_date = date_match.group()

        return parser().parse(f"{meeting_date} {meeting_year} {meeting_time}")

    def _parse_title(self, date_item):
        raw_date = " ".join(date_item.css("::text").getall()).strip()

        date_match = self._DATE_PREFIX_RE.match(raw_date)
        remainder = raw_date[date_match.end() :] if date_match else raw_date

        description_match = re.match(r"\s*[-–]\s*(.+)", remainder)
        if description_match:
            return f"{self.title} – {description_match.group(1).strip()}"

        return self.title
