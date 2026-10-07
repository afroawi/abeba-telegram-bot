from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


def chat_url(base, session_id=None):
    if session_id is None:
        return base
    parts = urlsplit(base)
    return urlunsplit(
        (
            parts.scheme,
            parts.netloc,
            parts.path,
            urlencode([*parse_qsl(parts.query), ("session", str(int(session_id)))]),
            parts.fragment,
        )
    )


def split_text(text, limit=4000):
    # Telegram counts UTF-16 code units; keep emoji within the message limit.
    current, length = [], 0
    for char in text:
        size = len(char.encode("utf-16-le")) // 2
        if length + size > limit:
            yield "".join(current)
            current, length = [], 0
        current.append(char)
        length += size
    if current:
        yield "".join(current)
