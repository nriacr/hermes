#!/usr/bin/env python3
"""Save a cleaned real product page as a parser fixture.

    python tools/capture_fixture.py SITE NAME URL            # fetch a public page
    python tools/capture_fixture.py SITE NAME URL --file saved.html   # clean a page you already saved

Writes tests/fixtures/SITE/NAME.html. Create NAME.expected.json by hand (see
tests/test_fixtures.py): the parser output you verified against the live page. Only the parts
the parsers read are kept (head metas, JSON-LD, price/variant scripts, visible text); styles, svg,
images, tracking and token-like scripts are dropped. Never capture a signed-in page.
"""
import argparse
import re
import sys
import urllib.request
from pathlib import Path

from bs4 import BeautifulSoup, Comment

KEEP_SCRIPT = re.compile(r"price|offer|variant|availability|sku|stock", re.I)
DROP_SCRIPT = re.compile(r"token|csrf|session|cookie|password|email|phone", re.I)
MAX_SCRIPT = 200_000
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"


def clean(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for node in soup.find_all(string=lambda text: isinstance(text, Comment)):
        node.extract()
    for tag in soup.find_all(["style", "svg", "img", "picture", "source", "link", "iframe", "noscript", "video", "canvas"]):
        tag.decompose()
    for tag in soup.find_all("script"):
        body = tag.string or tag.get_text() or ""
        is_jsonld = "ld+json" in str(tag.get("type", ""))
        keep = (is_jsonld or KEEP_SCRIPT.search(body)) and len(body) <= MAX_SCRIPT and not (DROP_SCRIPT.search(body) and not is_jsonld)
        if not keep:
            tag.decompose()
    for tag in soup.find_all("meta"):
        if re.search(r"csrf|token", str(tag.get("name", "")), re.I):
            tag.decompose()
    for tag in soup.find_all(True):
        for attr in list(tag.attrs):
            if attr in {"style", "srcset", "sizes", "nonce", "integrity"} or attr.startswith("on"):
                del tag.attrs[attr]
    return str(soup)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("site")
    parser.add_argument("name")
    parser.add_argument("url")
    parser.add_argument("--file")
    args = parser.parse_args()
    if args.file:
        html = Path(args.file).read_text(encoding="utf-8", errors="replace")
    else:
        request = urllib.request.Request(args.url, headers={"User-Agent": UA, "Accept-Language": "tr-TR,tr;q=0.9"})
        with urllib.request.urlopen(request, timeout=30) as response:
            html = response.read().decode("utf-8", errors="replace")
    target = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / args.site / f"{args.name}.html"
    target.parent.mkdir(parents=True, exist_ok=True)
    cleaned = clean(html)
    target.write_text(cleaned, encoding="utf-8")
    print(f"{target} ({len(html) // 1024} KB -> {len(cleaned) // 1024} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
