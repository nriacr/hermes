"""Indexed CSS selection for one parsed Amazon product page.

soupsieve walks the whole page (~30 000 elements on a 1.3 MB product page) for every select; a page
needs a dozen of them and each took 0.4-1.8 s on the Pi. The page is indexed once by id, class and
tag name, and the simple selectors the parser uses (compounds of tag, #id, .class and [attr], [attr=v],
[attr^=v], [attr*=v], joined by spaces) are answered from the index. Anything else (">", "+", "~",
pseudo-classes, other attribute operators) falls back to soupsieve, so results never differ.
"""

import re
from typing import Dict, List, Optional, Tuple

from bs4 import BeautifulSoup, Tag

_ATTR = r"\[\s*([\w-]+)\s*(?:([\^*]?=)\s*(?:'([^']*)'|\"([^\"]*)\"|([\w-]+))\s*)?\]"
_COMPOUND = re.compile(r"^(?P<tag>[a-zA-Z][\w-]*)?(?P<rest>(?:#[\w-]+|\.[\w-]+|" + _ATTR + r")*)$")
_PART = re.compile(r"#([\w-]+)|\.([\w-]+)|" + _ATTR)

# (tag, ids, classes, attributes as (name, operator, value))
Compound = Tuple[Optional[str], Tuple[str, ...], Tuple[str, ...], Tuple[Tuple[str, str, str], ...]]


def _compound(text: str) -> Optional[Compound]:
    match = _COMPOUND.match(text)
    if not match or not text:
        return None
    ids, classes, attrs = [], [], []
    for part in _PART.finditer(match.group("rest") or ""):
        if part.group(1):
            ids.append(part.group(1))
        elif part.group(2):
            classes.append(part.group(2))
        else:
            value = next((item for item in part.group(5, 6, 7) if item is not None), "")
            attrs.append((part.group(3), part.group(4) or "", value))
    return (match.group("tag").lower() if match.group("tag") else None), tuple(ids), tuple(classes), tuple(attrs)


def _complex(selector: str) -> Optional[List[Compound]]:
    """One selector of a list: compounds joined by descendant spaces, or None when unsupported."""
    parts = selector.split()
    compounds = [_compound(part) for part in parts]
    return compounds if parts and all(compounds) else None


def _classes(element: Tag) -> List[str]:
    value = element.get("class") or []
    return value if isinstance(value, list) else str(value).split()


def _attribute_matches(element: Tag, name: str, operator: str, value: str) -> bool:
    if not element.has_attr(name):
        return False
    if not operator:
        return True
    actual = element.get(name)
    actual = " ".join(actual) if isinstance(actual, list) else str(actual)
    if operator == "=":
        return actual == value
    if operator == "^=":
        return bool(value) and actual.startswith(value)
    return bool(value) and value in actual  # *=


def _matches(element: Tag, compound: Compound) -> bool:
    tag, ids, classes, attrs = compound
    if tag and element.name != tag:
        return False
    if ids and any(element.get("id") != item for item in ids):
        return False
    if classes:
        own = _classes(element)
        if any(item not in own for item in classes):
            return False
    return all(_attribute_matches(element, *attr) for attr in attrs)


class PageIndex:
    def __init__(self, soup: BeautifulSoup) -> None:
        # Attribute names are indexed too: "[data-cy='aod-offer']" then looks at the few elements
        # that have the attribute instead of the whole page.
        self.order: Dict[int, int] = {}
        self.by_id: Dict[str, List[Tag]] = {}
        self.by_class: Dict[str, List[Tag]] = {}
        self.by_tag: Dict[str, List[Tag]] = {}
        self.by_attr: Dict[str, List[Tag]] = {}
        self.all: List[Tag] = []
        for position, element in enumerate(soup.find_all(True)):
            self.order[id(element)] = position
            self.all.append(element)
            self.by_tag.setdefault(element.name, []).append(element)
            element_id = element.get("id")
            if isinstance(element_id, str):
                self.by_id.setdefault(element_id, []).append(element)
            for name in _classes(element):
                self.by_class.setdefault(name, []).append(element)
            for name in element.attrs:
                self.by_attr.setdefault(name, []).append(element)

    def _candidates(self, compound: Compound) -> List[Tag]:
        tag, ids, classes, attrs = compound
        if ids:
            return self.by_id.get(ids[0], [])
        if classes:
            return self.by_class.get(classes[0], [])
        if attrs:
            return self.by_attr.get(attrs[0][0], [])
        if tag:
            return self.by_tag.get(tag, [])
        return self.all

    def select(self, scope: Tag, compounds: List[Compound]) -> List[Tag]:
        found = []
        for element in self._candidates(compounds[-1]):
            # A removed element (decompose) is no longer part of the page.
            if element.decomposed or not _matches(element, compounds[-1]):
                continue
            if self._ancestors_match(element, compounds[:-1], scope):
                found.append(element)
        return found

    @staticmethod
    def _ancestors_match(element: Tag, compounds: List[Compound], scope: Tag) -> bool:
        """Descendant combinators, right to left; also requires the element to be inside the page."""
        remaining = list(compounds)
        for parent in element.parents:
            if remaining and isinstance(parent, Tag) and parent is not scope and _matches(parent, remaining[-1]):
                remaining.pop()
            if parent is scope:
                return not remaining
        return False


def indexed(soup: BeautifulSoup) -> BeautifulSoup:
    """The same soup whose select/select_one use the page index for supported selectors."""
    index: List[PageIndex] = []
    original_select = soup.select

    def select(selector: str, namespaces=None, limit=None, **kwargs):
        parsed = [_complex(item.strip()) for item in selector.split(",")]
        if namespaces is not None or kwargs or not all(parsed):
            return original_select(selector, namespaces=namespaces, limit=limit, **kwargs)
        if not index:
            index.append(PageIndex(soup))
        page = index[0]
        merged: Dict[int, Tag] = {}
        for compounds in parsed:
            for element in page.select(soup, compounds):
                merged[id(element)] = element
        result = sorted(merged.values(), key=lambda element: page.order[id(element)])
        return result[:limit] if limit else result

    def select_one(selector: str, namespaces=None, **kwargs):
        found = select(selector, namespaces=namespaces, limit=1, **kwargs)
        return found[0] if found else None

    soup.select = select
    soup.select_one = select_one
    return soup
