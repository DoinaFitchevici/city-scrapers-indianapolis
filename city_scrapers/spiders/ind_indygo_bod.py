from city_scrapers_core.constants import COMMITTEE

from city_scrapers.mixins.ind_indygo_bod import IndIndygoBodSpiderMixin

spider_configs = [
    {
        "class_name": "IndIndygoSpider",
        "name": "ind_indygo",
        "agency": "Indianapolis Indygo Board",
        "agency_name": "IndyGo Board of Directors",
        "title": "IndyGo Board",
        "section_heading_match": "Board Meeting Dates",
        "board_reports_container_selector": ".rc-accordion",
        "video_archive_pattern": r"^Board\b",
        "links": [
            {
                "href": "https://www.youtube.com/@iptcIndyGo/streams",
                "title": "Live Stream",
            },
        ],
        # indygo.net's board page only shows the current year, so 2024 and
        # 2025 are backfilled from Wayback snapshots below -- a one-time
        # backfill, not annual upkeep: 2027+ comes from the live page.
        "historical_snapshots": [
            {
                # Dec 2025, after indygo.net's Oct 2025 redesign -- same
                # page design as today, so parses like the live page.
                "url": (
                    "https://web.archive.org/web/20251211205320/"
                    "https://www.indygo.net/about-indygo/board-of-directors/"
                ),
                "design": "new",
                "container_selector": ".rc-layout-content.rc-text-large",
            },
            {
                # Dec 2024, before the redesign -- old page design, needs
                # `_parse_old_design_section`. Late in the year, so its
                # schedule and Board Reports are both already complete.
                "url": (
                    "https://web.archive.org/web/20241227050537/"
                    "https://www.indygo.net/about-indygo/board-of-directors/"
                ),
                "design": "old",
            },
        ],
    },
    {
        "class_name": "IndIndygoFinanceSpider",
        "name": "ind_indygo_finance",
        "agency": "Indianapolis Indygo Finance Committee",
        "agency_name": "IndyGo Board of Directors",
        "title": "IndyGo Finance Committee",
        "classification": COMMITTEE,
        "section_heading_match": "Finance Committee Meetings",
        "video_archive_pattern": r"^Finance Committee\b",
    },
    {
        "class_name": "IndIndygoGovAuditSpider",
        "name": "ind_indygo_gov_audit",
        "agency": "Indianapolis Indygo Governance Audit Committee",
        "agency_name": "IndyGo Board of Directors",
        "title": "IndyGo Governance and Audit Committee",
        "classification": COMMITTEE,
        "section_heading_match": "Governance and Audit Committee",
        "video_archive_pattern": r"^Governance\s*(?:and|&)\s*Audit",
    },
    {
        "class_name": "IndIndygoServiceSpider",
        "name": "ind_indygo_service",
        "agency": "Indianapolis Indygo Service Committee",
        "agency_name": "IndyGo Board of Directors",
        "title": "IndyGo Service Committee",
        "classification": COMMITTEE,
        "section_heading_match": "Service Committee Meetings",
        "video_archive_pattern": r"^Service Committee\b",
    },
]


def create_spiders():
    """
    Create one spider class for every entry in spider_configs and register
    each generated class in this module's global namespace.

    Registering the classes globally allows Scrapy to discover them as
    normal spider classes.
    """
    for config in spider_configs:
        class_name = config["class_name"]

        if class_name in globals():
            continue

        spider_attributes = {
            key: value for key, value in config.items() if key != "class_name"
        }

        spider_class = type(
            class_name,
            (IndIndygoBodSpiderMixin,),
            spider_attributes,
        )

        globals()[class_name] = spider_class


create_spiders()
