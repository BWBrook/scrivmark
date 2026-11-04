"""A final allowlist pass over rendered HTML, after metadata, templates and
citeproc have all had their say. The Lua filter already cleans manuscript
attributes; this catches any route round it."""

import re
from html import escape
from html.parser import HTMLParser

DROP_WITH_CONTENT = {"script", "style", "iframe", "object", "embed", "noscript", "template", "frame", "frameset"}
DROP_TAG = {"link", "meta", "base", "form", "input", "button", "textarea", "select", "option"}
# pandoc writes an image's width= and height= as style; nothing else in a style survives
SIZE_STYLE = re.compile(r"^\s*(?:(?:width|height)\s*:\s*[\d.]+(?:%|px|em|rem|in|cm|mm|pt)\s*;?\s*)+$")
URL_ATTRS = {"href", "src", "xlink:href", "action", "formaction", "poster", "background"}
SAFE_SCHEMES = ("http:", "https:", "mailto:")


def _safe_url(value: str) -> bool:
    v = "".join(value.split()).lower()
    if ":" not in v.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]:
        return True
    return v.startswith(SAFE_SCHEMES)


class _Clean(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.out: list[str] = []
        self.skip = 0

    def _attrs(self, attrs, tag: str = "") -> str:
        kept = []
        for k, v in attrs:
            k = k.lower()
            if k == "style" and tag == "img" and v and SIZE_STYLE.match(v):
                kept.append(f' style="{escape(v, quote=True)}"')
                continue
            if k.startswith("on") or k in ("style", "srcdoc", "formaction"):
                continue
            if k in URL_ATTRS and v is not None and not _safe_url(v):
                continue
            kept.append(f' {k}="{escape(v, quote=True)}"' if v is not None else f" {k}")
        return "".join(kept)

    def handle_starttag(self, tag, attrs):
        if tag in DROP_WITH_CONTENT:
            self.skip += 1
            return
        if self.skip or tag in DROP_TAG:
            return
        self.out.append(f"<{tag}{self._attrs(attrs, tag)}>")

    def handle_startendtag(self, tag, attrs):
        if self.skip or tag in DROP_TAG or tag in DROP_WITH_CONTENT:
            return
        self.out.append(f"<{tag}{self._attrs(attrs, tag)} />")

    def handle_endtag(self, tag):
        if tag in DROP_WITH_CONTENT:
            self.skip = max(0, self.skip - 1)
            return
        if not self.skip and tag not in DROP_TAG:
            self.out.append(f"</{tag}>")

    def handle_data(self, data):
        if not self.skip:
            self.out.append(escape(data, quote=False))

    def handle_entityref(self, name):
        if not self.skip:
            self.out.append(f"&{name};")

    def handle_charref(self, name):
        if not self.skip:
            self.out.append(f"&#{name};")


def clean(html: str) -> str:
    p = _Clean()
    p.feed(html)
    p.close()
    return "".join(p.out)
