"""HTML is untrusted, including revisions created before this release."""
import re

import bleach

VIDEO_URL = re.compile(
    r"^/api/videos/[0-9a-f]{8}-"
    r"[0-9a-f]{4}-"
    r"[0-9a-f]{4}-"
    r"[0-9a-f]{4}-"
    r"[0-9a-f]{12}$",
    re.IGNORECASE,
)

TAGS = [
    'p', 'br', 'b', 'strong', 'i', 'em', 'u',
    'h1', 'h2', 'h3', 'ul', 'ol', 'li',
    'blockquote', 'pre', 'code', 'table',
    'thead', 'tbody', 'tr', 'th', 'td',
    'a', 'div', 'span', 'video',
]


def allowed_attribute(tag, name, value):
    if tag == 'a':
        return name in ('href', 'title')

    if tag in ('pre', 'code'):
        return name == 'data-lang'

    if tag == 'video':
        if name == 'src':
            return bool(VIDEO_URL.fullmatch(value))

        if name == 'controls':
            return True

        if name == 'preload':
            return value in ('none', 'metadata')

    return False


def clean(html):
    return bleach.clean(
        html or '',
        tags=TAGS,
        attributes=allowed_attribute,
        protocols=['http', 'https', 'mailto'],
        strip=True,
    )
