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
    },
    {
        "class_name": "IndIndygoFinanceSpider",
        "name": "ind_indygo_finance",
        "agency": "Indianapolis Indygo Finance Committee",
        "agency_name": "IndyGo Board of Directors",
        "title": "IndyGo Finance Committee",
        "section_heading_match": "Finance Committee Meetings",
        "video_archive_pattern": r"^Finance Committee\b",
    },
    {
        "class_name": "IndIndygoGovAuditSpider",
        "name": "ind_indygo_gov_audit",
        "agency": "Indianapolis Indygo Governance Audit Committee",
        "agency_name": "IndyGo Board of Directors",
        "title": "IndyGo Governance and Audit Committee",
        "section_heading_match": "Governance and Audit Committee",
        "video_archive_pattern": r"^Governance\s*(?:and|&)\s*Audit",
    },
    {
        "class_name": "IndIndygoServiceSpider",
        "name": "ind_indygo_service",
        "agency": "Indianapolis Indygo Service Committee",
        "agency_name": "IndyGo Board of Directors",
        "title": "IndyGo Service Committee",
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
