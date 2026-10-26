"""Small, escaped Markdown subset rendered into Telegram-compatible HTML."""
import re
from html import escape
from html.parser import HTMLParser
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


def chat_url(base, session_id=None):
    if session_id is None:
        return base
    parts = urlsplit(base)
    return urlunsplit((parts.scheme, parts.netloc, parts.path,
                      urlencode([*parse_qsl(parts.query), ("session", str(int(session_id)))]), parts.fragment))


def split_text(text, limit=4000):
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


def safe_url(value):
    try:
        parts = urlsplit(value)
        return (parts.scheme == "https" and bool(parts.hostname) and
                not parts.username and not parts.password and not any(c.isspace() for c in value))
    except (ValueError, TypeError):
        return False


INLINE = re.compile(r"(`[^`\n]+`|\*\*.+?\*\*|__.+?__|\[[^\]\n]+\]\([^\s)]+\)|(?<!\w)\*[^*\n]+\*(?!\w)|(?<!\w)_[^_\n]+_(?!\w))")


def inline_html(text, depth=0):
    if depth > 4:
        return escape(text)
    output, previous = [], 0
    for match in INLINE.finditer(text):
        output.append(escape(text[previous:match.start()]))
        token = match.group()
        if token.startswith('`'):
            output.append(f"<code>{escape(token[1:-1])}</code>")
        elif token.startswith(('**', '__')):
            output.append(f"<b>{inline_html(token[2:-2], depth + 1)}</b>")
        elif token.startswith('['):
            label, url = token[1:].split('](', 1)
            url = url[:-1]
            output.append(f'<a href="{escape(url, quote=True)}">{escape(label)}</a>' if safe_url(url) else escape(label))
        else:
            output.append(f"<i>{inline_html(token[1:-1], depth + 1)}</i>")
        previous = match.end()
    output.append(escape(text[previous:]))
    return ''.join(output)


def markdown_html(text):
    output, code, in_code = [], [], False
    for line in str(text).splitlines():
        if line.lstrip().startswith('```'):
            if in_code:
                output.append('<pre>' + escape('\n'.join(code)) + '</pre>')
                code = []
            in_code = not in_code
        elif in_code:
            code.append(line)
        else:
            heading = re.match(r'^\s{0,3}#{1,6}\s+(.+?)\s*#*$', line)
            if heading:
                output.append('<b>' + inline_html(heading.group(1)) + '</b>')
            else:
                line = re.sub(r'^\s*[-*+]\s+', '• ', line)
                output.append(inline_html(line))
    if in_code:
        output.append('<pre>' + escape('\n'.join(code)) + '</pre>')
    return '\n'.join(output)


class _HTMLChunks(HTMLParser):
    """Split only generated HTML; balance entities/tags at UTF-16 boundaries."""
    def __init__(self, limit):
        super().__init__(convert_charrefs=True)
        self.limit, self.units = limit, 0
        self.current, self.stack, self.chunks = [], [], []

    def handle_starttag(self, tag, attrs):
        opening = self.get_starttag_text()
        self.current.append(opening)
        self.stack.append((tag, opening))

    def handle_endtag(self, tag):
        self.current.append(f'</{tag}>')
        self.stack.pop()

    def flush(self):
        if self.units:
            self.chunks.append(''.join(self.current) + ''.join(f'</{tag}>' for tag, _ in reversed(self.stack)))
        self.current = [opening for _, opening in self.stack]
        self.units = 0

    def handle_data(self, data):
        # Prefer word boundaries; oversized words still split safely by code point.
        for piece in re.findall(r"\S+|\s+", data):
            piece_units = len(piece.encode('utf-16-le')) // 2
            if self.units and self.units + piece_units > self.limit and piece_units <= self.limit:
                self.flush()
            for char in piece:
                size = len(char.encode('utf-16-le')) // 2
                if self.units + size > self.limit:
                    self.flush()
                self.current.append(escape(char))
                self.units += size


def split_html(text, limit=3800):
    if limit < 2:
        raise ValueError('HTML chunk limit must be at least two UTF-16 units.')
    parser = _HTMLChunks(limit)
    parser.feed(text)
    parser.close()
    parser.flush()
    return parser.chunks


LABELS = {
    'en': {'examples': 'A little more clarity', 'links': 'Explore further', 'followups': 'You could ask next',
           'notice': 'Educational information. AI can make mistakes. For personal health decisions, speak with a qualified professional.'},
    'am': {'examples': 'ተጨማሪ ማብራሪያ', 'links': 'ተጨማሪ ንባብ', 'followups': 'ቀጥሎ መጠየቅ የሚችሉት',
           'notice': 'ይህ ትምህርታዊ መረጃ ነው። AI ስህተት ሊሠራ ይችላል። ለግል የጤና ውሳኔዎች ባለሙያ ያማክሩ።'},
}


def reply_html(result, language='en'):
    labels = LABELS.get(language, LABELS['en'])
    structured = result.get('structured') or {}
    sections = ['<b>🌼 Abeba</b>', markdown_html(result.get('reply') or '')]
    for key, title in [('examples', 'examples'), ('suggested_followups', 'followups')]:
        items = [item.strip() for item in (structured.get(key) or []) if isinstance(item, str) and item.strip()][:3]
        if items:
            sections.append(f'<b>{labels[title]}</b>\n' + '\n'.join('• ' + inline_html(item) for item in items))
    links = [item for item in (structured.get('links') or []) if isinstance(item, dict) and safe_url(item.get('url', ''))][:5]
    if links:
        sections.append(f'<b>{labels["links"]}</b>\n' + '\n'.join(
            f'• <a href="{escape(item["url"], quote=True)}">{escape(str(item.get("title") or item["url"]))}</a>' for item in links))
    sections.append('<i>' + escape(labels['notice']) + '</i>')
    return '\n\n'.join(sections)
