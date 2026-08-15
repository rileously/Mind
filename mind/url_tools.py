from __future__ import annotations

import re
import urllib.parse
from dataclasses import dataclass


TRACKING_PARAMS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "utm_id",
    "utm_source_platform",
    "utm_creative_format",
    "utm_marketing_tactic",
    "fbclid",
    "igshid",
    "gclid",
    "gclsrc",
    "dclid",
    "wbraid",
    "gbraid",
    "si",
    "feature",
    "pp",
    "ref_src",
    "ref_url",
    "_hsenc",
    "_hsmi",
    "mc_cid",
    "mc_eid",
    "yclid",
    "msclkid",
    "zanpid",
    "twclid",
}

URL_REGEX = re.compile(
    r"^(https?://|www\.)[a-zA-Z0-9-]+(\.[a-zA-Z0-9-]+)+(:\d+)?(/[^\s]*)?$",
    re.IGNORECASE,
)


@dataclass
class UrlMetadata:
    url: str
    clean_url: str
    domain: str
    title: str
    description: str


def is_http_url(text: str) -> bool:
    """Return True if text is a single valid HTTP/HTTPS URL."""
    if not text:
        return False
    trimmed = text.strip()
    if "\n" in trimmed or " " in trimmed:
        return False
    return bool(URL_REGEX.match(trimmed))


def strip_tracking_params(url_str: str) -> str:
    """Remove tracking parameters (utm_*, fbclid, si, etc.) from a URL while preserving key parameters."""
    trimmed = url_str.strip()
    if not trimmed.lower().startswith(("http://", "https://")):
        trimmed = "https://" + trimmed

    parsed = urllib.parse.urlsplit(trimmed)
    if not parsed.query:
        return trimmed

    query_pairs = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    is_youtube = "youtube.com" in parsed.netloc.lower() or "youtu.be" in parsed.netloc.lower()
    is_twitter = "twitter.com" in parsed.netloc.lower() or "x.com" in parsed.netloc.lower()

    filtered_pairs: list[tuple[str, str]] = []
    for key, val in query_pairs:
        key_lower = key.lower()
        if key_lower in TRACKING_PARAMS:
            continue
        if is_twitter and key_lower in {"s", "t", "ref"}:
            continue
        filtered_pairs.append((key, val))

    clean_query = urllib.parse.urlencode(filtered_pairs)
    clean_url = urllib.parse.urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, clean_query, parsed.fragment)
    )
    return clean_url


def extract_quick_metadata(url_str: str, html_snippet: str = "") -> UrlMetadata:
    """Extract metadata from URL and optional HTML snippet."""
    trimmed = url_str.strip()
    if not trimmed.lower().startswith(("http://", "https://")):
        trimmed = "https://" + trimmed

    parsed = urllib.parse.urlsplit(trimmed)
    domain = parsed.netloc.lower()
    if domain.startswith("www."):
        domain = domain[4:]

    clean_url = strip_tracking_params(trimmed)

    title = ""
    description = ""

    if html_snippet:
        # Match og:title or <title>
        og_title = re.search(r'<meta\s+property=["\']og:title["\']\s+content=["\']([^"\']+)["\']', html_snippet, re.IGNORECASE)
        if not og_title:
            og_title = re.search(r'<meta\s+content=["\']([^"\']+)["\']\s+property=["\']og:title["\']', html_snippet, re.IGNORECASE)
        if og_title:
            title = og_title.group(1).strip()
        else:
            std_title = re.search(r'<title>([^<]+)</title>', html_snippet, re.IGNORECASE)
            if std_title:
                title = std_title.group(1).strip()

        # Match og:description or meta description
        og_desc = re.search(r'<meta\s+property=["\']og:description["\']\s+content=["\']([^"\']+)["\']', html_snippet, re.IGNORECASE)
        if not og_desc:
            og_desc = re.search(r'<meta\s+name=["\']description["\']\s+content=["\']([^"\']+)["\']', html_snippet, re.IGNORECASE)
        if og_desc:
            description = og_desc.group(1).strip()

    if not title:
        path_slug = parsed.path.strip("/").replace("-", " ").replace("_", " ")
        if path_slug:
            title = path_slug.title()
        else:
            title = domain.capitalize()

    if not description:
        description = clean_url

    return UrlMetadata(
        url=url_str,
        clean_url=clean_url,
        domain=domain,
        title=title,
        description=description,
    )
