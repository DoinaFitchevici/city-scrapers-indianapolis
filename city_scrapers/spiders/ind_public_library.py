import re
from collections import defaultdict
from urllib.parse import quote, unquote

import scrapy
from city_scrapers_core.constants import BOARD
from city_scrapers_core.items import Meeting
from city_scrapers_core.spiders import CityScrapersSpider
from dateutil.parser import parse


class IndPublicLibrarySpider(CityScrapersSpider):
    name = "ind_public_library"
    agency = "Indianapolis Public Library Board"
    timezone = "America/Indiana/Indianapolis"
    start_urls = [
        "https://www.indypl.org/about-the-library/board-meeting-times-committees"
    ]
    DOCUMENTS_URL = "https://www.indypl.org/about-the-library/board-documents-archives"

    CUSTOM_SETTINGS = {
        "ROBOTSTXT_OBEY": False,
    }

    DATE_RE = re.compile(r"[A-Z][a-z]+ \d{1,2},? \d{4}")
    MONTH_DAY_RE = re.compile(r"[A-Z][a-z]+ \d{1,2}")
    YEAR_RE = re.compile(r"\d{4}")
    LOCATION_OVERRIDE_RE = re.compile(
        r"to be held at (?:the )?([^,]+),\s*([^-]+?)(?:-|$)", re.I
    )
    ATTACHMENT_DATE_RE = re.compile(r"\s*(?:-\s*|on\s+)?" + DATE_RE.pattern, re.I)

    def start_requests(self):
        # Fetch the documents archive first so we have a date -> PDF
        # link map ready before we build any Meeting items.
        yield scrapy.Request(self.DOCUMENTS_URL, callback=self._parse_documents)

    EXCLUDED_DOC_RE = re.compile(r"exec(?:utive)?\s*session", re.I)

    def _is_board_meeting_document(self, text):
        """Committee notices and executive-session agendas/minutes
        aren't documents for the Board meeting itself, even though
        they share its date - only keep genuine Board/Annual/Special
        meeting documents and their minutes."""
        if self.EXCLUDED_DOC_RE.search(text):
            return False
        return True

    def _strip_date(self, text):
        """Remove the date (and its connector word/dash) from a
        document's display text, since the meeting's own `start`
        already carries that information, e.g.:
        "AGENDA - Executive Session on November 25, 2024" ->
        "AGENDA - Executive Session"
        """
        cleaned = self.ATTACHMENT_DATE_RE.sub("", text, count=1)
        return cleaned.strip(" -,")

    def _parse_documents(self, response):
        """
        Build a {(date, kind): [attachment hrefs]} map from the board
        documents archive page. That page is split into year
        accordions.
        Keying on (date, kind) instead of just date keeps a Regular
        meeting's documents separate from an Annual/Special meeting's
        documents that happen to fall on the same date.
        """
        self.attachments = defaultdict(list)
        for details in response.css("details"):
            summary_text = " ".join(details.css("summary ::text").getall())
            summary_text = " ".join(summary_text.split())
            year_match = self.YEAR_RE.search(summary_text)
            if not year_match:
                continue
            year = year_match.group(0)

            for link in details.css("li a"):
                href = link.attrib.get("href")
                text = " ".join(link.css("::text").getall()).strip()
                if not href or not self._is_board_meeting_document(text):
                    continue
                month_day_match = self.MONTH_DAY_RE.search(text)
                if not month_day_match:
                    continue
                date_key = parse(f"{month_day_match.group(0)}, {year}").date()
                kind = self._meeting_kind(text)
                self.attachments[(date_key, kind)].append(
                    (quote(unquote(href), safe=":/,"), self._strip_date(text))
                )

        yield response.follow(self.start_urls[0], callback=self.parse)

    def _attachment_links(self, start, kind):
        """Any board-document PDFs whose listed date and meeting kind
        (Regular/Special/Annual) match this meeting."""
        docs = getattr(self, "attachments", {}).get((start.date(), kind), [])
        return [{"href": href, "title": title} for href, title in docs]

    def parse(self, response):
        """
        Each accordion is a <details> block. We tell them apart by the
        text of their <summary>: one is the "... Archive" of past
        meetings (formatted as <p>/<a> pairs), the other is the current
        year's "Dates & Locations" list (formatted as <ul><li>).
        """
        for details in response.css("details"):
            summary_text = " ".join(details.css("summary ::text").getall())
            summary_text = " ".join(summary_text.split())

            if "Archive" in summary_text:
                yield from self._parse_archive(details, response)
            elif "Dates" in summary_text and "Locations" in summary_text:
                year_match = self.YEAR_RE.search(summary_text)
                year = year_match.group(0) if year_match else None
                yield from self._parse_current_year(details, response, year)

    def _parse_archive(self, details, response):
        for link in details.css("div p a"):
            link_text = " ".join(link.css("::text").getall())
            link_text = " ".join(link_text.split())
            href = link.attrib.get("href")

            date_match = self.DATE_RE.search(link_text)
            if not date_match or not href:
                continue

            start = parse(date_match.group(0))
            # No time is ever given for archived meetings; use the
            # regular 6:30pm start and flag it as an assumption.
            start = start.replace(hour=18, minute=30)

            meeting = Meeting(
                title=self._parse_title(link_text),
                description="",
                classification=BOARD,
                start=start,
                end=None,
                all_day=False,
                time_notes=(
                    "Meetings are usually held at 6:30pm on the fourth Monday "
                    "of the month (third Monday in May/December)"
                ),
                location={"name": "", "address": ""},
                links=[{"href": href, "title": "Video"}]
                + self._attachment_links(start, self._meeting_kind(link_text)),
                source=response.url,
            )
            meeting["status"] = self._get_status(meeting)
            meeting["id"] = self._get_id(meeting)
            yield meeting

    def _parse_current_year(self, details, response, year):
        """Parse the '<year> Dates & Locations' section.
 
        Structure: <li><strong>Month Day </strong>at <a>Location</a>[,
        extra address text][ - CANCELLED...][<br>RESCHEDULED to <a>...
        </a>][<br><a>Watch the ... Board Meeting</a>]</li>
        """
        for li in details.css("div ul li"):
            full_text = " ".join(li.css("::text").getall())
            full_text = " ".join(full_text.split())
 
            date_match = self.MONTH_DAY_RE.search(full_text)
            if not date_match or not year:
                continue
 
            # A rescheduled date, if present, overrides the original one.
            reschedule_match = self.DATE_RE.search(full_text)
            is_rescheduled = bool(reschedule_match and "RESCHEDULED" in full_text)
            if is_rescheduled:
                start_date = parse(reschedule_match.group(0))
                start = start_date.replace(hour=0, minute=0)
            else:
                start_date = parse(f"{date_match.group(0)}, {year}")
                start = start_date.replace(hour=18, minute=30)
 
            links = li.css("a")
            location_links = [
                a for a in links if "/locations/" in (a.attrib.get("href") or "")
            ]
            video_links_raw = [a for a in links if a not in location_links]
 
            if "cancel" in full_text.lower() and "RESCHEDULED" in full_text:
                original_start = parse(
                    f"{date_match.group(0)}, {year}"
                ).replace(hour=18, minute=30)
                original_location_link = (
                    location_links[0] if location_links else None
                )
                original_location_name = (
                    " ".join(
                        original_location_link.css("::text").getall()
                    ).strip()
                    if original_location_link is not None
                    else ""
                )
                original_location_url = (
                    original_location_link.attrib.get("href")
                    if original_location_link is not None
                    else None
                )
 
                cancelled_meeting_kwargs = dict(
                    title=self._parse_title(full_text),
                    description="",
                    classification=BOARD,
                    start=original_start,
                    end=None,
                    all_day=False,
                    time_notes=(
                        "Meetings are usually held at 6:30pm on the fourth "
                        "Monday of the month (third Monday in "
                        "May/December)"
                    ),
                )
                cancelled_trailing_kwargs = dict(
                    links=self._attachment_links(
                        original_start, self._meeting_kind(full_text)
                    ),
                    source=response.url,
                )
 
                if original_location_url:
                    yield response.follow(
                        original_location_url,
                        callback=self._parse_location_page,
                        cb_kwargs={
                            "location_name": original_location_name,
                            "meeting_kwargs": cancelled_meeting_kwargs,
                            "trailing_kwargs": cancelled_trailing_kwargs,
                            "status_text": full_text,
                        },
                        dont_filter=True,
                    )
                else:
                    cancelled_meeting = Meeting(
                        **cancelled_meeting_kwargs,
                        location={"name": original_location_name, "address": ""},
                        **cancelled_trailing_kwargs,
                    )
                    cancelled_meeting["status"] = self._get_status(
                        cancelled_meeting, full_text
                    )
                    cancelled_meeting["id"] = self._get_id(cancelled_meeting)
                    yield cancelled_meeting
 
            # If a meeting was rescheduled, the new location is always the
            # last location link listed, so just take the last one.
            location_link = location_links[-1] if location_links else None
 
            location_name = (
                " ".join(location_link.css("::text").getall()).strip()
                if location_link is not None
                else ""
            )
            location_url = (
                location_link.attrib.get("href") if location_link is not None else None
            )

            video_links = [
                {"href": a.attrib.get("href"), "title": "Video"}
                for a in video_links_raw
                if a.attrib.get("href")
            ]
 
            meeting_kwargs = dict(
                title=self._parse_title(full_text),
                description="",
                classification=BOARD,
                start=start,
                end=None,
                all_day=False,
                time_notes=(
                    "Meetings are usually held at 6:30pm on the fourth Monday "
                    "of the month (third Monday in May/December)"
                ),
            )
            trailing_kwargs = dict(
                links=video_links + self._attachment_links(
                    start, self._meeting_kind(full_text)
                ),
                source=response.url,
            )
 
            # Sometimes the text explicitly overrides the linked location,
            # e.g. "... to be held at the Mary Rigg Neighborhood Center,
            # 1920 West Morris Street". When that's present, trust it
            # instead of the branch page linked in the <a> tag.
            override_match = self.LOCATION_OVERRIDE_RE.search(full_text)
            if override_match:
                meeting = Meeting(
                    **meeting_kwargs,
                    location={
                        "name": override_match.group(1).strip(),
                        "address": override_match.group(2).strip(),
                    },
                    **trailing_kwargs,
                )
                meeting["status"] = self._get_status(meeting)
                meeting["id"] = self._get_id(meeting)
                yield meeting
            elif location_url:
                yield response.follow(
                    location_url,
                    callback=self._parse_location_page,
                    cb_kwargs={
                        "location_name": location_name,
                        "meeting_kwargs": meeting_kwargs,
                        "trailing_kwargs": trailing_kwargs,
                    },
                    # Multiple meetings can share the same location page
                    dont_filter=True,
                )
            else:
                meeting = Meeting(
                    **meeting_kwargs,
                    location={"name": location_name, "address": ""},
                    **trailing_kwargs,
                )
                meeting["status"] = self._get_status(meeting)
                meeting["id"] = self._get_id(meeting)
                yield meeting

    def _parse_location_page(
        self, response, location_name, meeting_kwargs, trailing_kwargs, status_text=""
    ):
        """Pull the street address off a location's own page"""
        address_parts = response.css("p.HeroLocation__address strong ::text").getall()
        address = ", ".join(part.strip() for part in address_parts if part.strip())

        meeting = Meeting(
            **meeting_kwargs,
            location={"name": location_name, "address": address},
            **trailing_kwargs,
        )
        meeting["status"] = self._get_status(meeting, status_text)
        meeting["id"] = self._get_id(meeting)
        yield meeting

    def _meeting_kind(self, text):
        """Classify a meeting or document as Regular, Special, or Annual
        based on its text, so documents only attach to the matching
        meeting rather than every meeting on the same date."""
        text_lower = text.lower()
        if "facilities committee" in text_lower:
            return "Facilities Committee"
        elif "special" in text_lower:
            return "Special"
        elif "annual" in text_lower:
            return "Annual"
        return "Regular"

    def _parse_title(self, text):
        title = "Indianapolis Public Library Board of Trustees"
        kind = self._meeting_kind(text)
        if kind != "Regular":
            title += f" - {kind} Meeting"
        return title
