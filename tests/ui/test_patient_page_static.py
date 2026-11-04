"""Checks on the patient page files that need no browser (BR-17, BR-21, BR-22, BR-23).

These read the HTML, CSS and JavaScript as text. The browser tests then prove the same
things in a real browser; these are fast and catch mistakes early.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path

import pytest

from config.settings import DEMO_BANNER_TEXT
from rules.business_rules import demo_banner_present, meets_wcag_aa

PAGE = Path(__file__).resolve().parents[2] / "static" / "patient"
HTML = (PAGE / "index.html").read_text(encoding="utf-8")
THEME_CSS = (PAGE / "css" / "theme.css").read_text(encoding="utf-8")
APP_CSS = (PAGE / "css" / "app.css").read_text(encoding="utf-8")
JS_FILES = {
    name: (PAGE / "js" / name).read_text(encoding="utf-8")
    for name in ("theme.js", "api.js", "app.js")
}


# --------------------------------------------------------------------- reading the HTML
class Page(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tags: list[tuple[str, dict[str, str | None]]] = []
        self.text: list[str] = []
        self.label_for: set[str] = set()
        self.wrapped_inputs: set[str] = set()
        self._label_depth = 0
        self._buttons: list[list[str]] = []
        self.button_texts: list[tuple[dict, str]] = []
        self._open_button: dict | None = None
        self._button_text: list[str] = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        self.tags.append((tag, attributes))
        if tag == "label":
            self._label_depth += 1
            if attributes.get("for"):
                self.label_for.add(attributes["for"])
        if tag == "input" and self._label_depth and attributes.get("id"):
            self.wrapped_inputs.add(attributes["id"])
        if tag == "button":
            self._open_button, self._button_text = attributes, []

    def handle_endtag(self, tag):
        if tag == "label":
            self._label_depth -= 1
        if tag == "button" and self._open_button is not None:
            self.button_texts.append((self._open_button, "".join(self._button_text).strip()))
            self._open_button = None

    def handle_data(self, data):
        self.text.append(data)
        if self._open_button is not None:
            self._button_text.append(data)


page = Page()
page.feed(HTML)


def tags(name: str) -> list[dict]:
    return [attrs for tag, attrs in page.tags if tag == name]


# --------------------------------------------------------------------- structure and text
def test_basics():
    assert re.search(r'<html lang="en">', HTML)
    assert len(tags("h1")) == 1
    assert "DEMO" in re.search(r"<title>(.*?)</title>", HTML).group(1)
    viewport = next(m for m in tags("meta") if m.get("name") == "viewport")["content"]
    assert "width=device-width" in viewport and "viewport-fit=cover" in viewport
    assert any(m.get("name") == "color-scheme" and "dark" in m["content"] for m in tags("meta"))


def test_demo_banner_and_urgent_notice_are_in_the_page_text():  # BR-17
    visible_text = " ".join(" ".join(page.text).split())
    assert demo_banner_present(visible_text)
    assert DEMO_BANNER_TEXT in HTML
    assert "urgent medical help" in visible_text and "does not give medical advice" in visible_text


def test_skip_link_and_landmarks():
    first_link = tags("a")[0]
    assert first_link["href"] == "#main" and "skip-link" in first_link["class"]
    main = next(attrs for attrs in tags("main"))
    assert main["id"] == "main" and main["tabindex"] == "-1"
    assert tags("header") and tags("footer")


def test_ids_are_unique_and_every_reference_points_somewhere():
    ids = [a["id"] for _, a in page.tags if a.get("id")]
    assert len(ids) == len(set(ids)), "duplicate ids: " + str({i for i in ids if ids.count(i) > 1})
    known = set(ids)
    for _, attrs in page.tags:
        for key in ("aria-labelledby", "for"):
            if attrs.get(key):
                assert attrs[key] in known, f"{key}={attrs[key]} has no target"
        href = attrs.get("href") or ""
        if href.startswith("#"):
            assert href[1:] in known


def test_every_input_has_a_label_and_every_button_has_a_name():
    for attrs in tags("input"):
        identifier = attrs.get("id")
        assert identifier and (identifier in page.label_for or identifier in page.wrapped_inputs), (
            f"input {attrs} has no label"
        )
    for attrs, label in page.button_texts:
        assert label or attrs.get("aria-label"), f"button {attrs} has no name"


def test_screens_are_labelled_regions():
    for section in tags("section"):
        assert section.get("aria-labelledby"), section


def test_sensitive_fields_do_not_invite_saving():
    code = next(a for a in tags("input") if a.get("id") == "access-code")
    assert code["type"] == "password" and code["autocomplete"] == "off"
    assert next(a for a in tags("input") if a.get("id") == "appt-id")["autocomplete"] == "off"


# ---------------------------------------------------------- strict security policy (BR-22)
def test_nothing_the_policy_forbids_is_used():
    assert "style=" not in HTML.replace('rel="stylesheet"', ""), (
        "inline style attributes are blocked"
    )
    assert not re.search(r"\son[a-z]+\s*=", HTML), "inline event handlers are blocked"
    assert "javascript:" not in HTML.lower()
    for script in tags("script"):
        assert script.get("src"), "inline scripts are blocked"
    assert "<style" not in HTML.lower()
    for name, source in {
        "index.html": HTML,
        "theme.css": THEME_CSS,
        "app.css": APP_CSS,
        **JS_FILES,
    }.items():
        assert not re.search(r"(?<![\w-])(https?:)?//[a-z0-9]", source.replace("</", ""), re.I), (
            f"{name} refers to an outside address"
        )


def test_scripts_load_in_the_right_order():
    scripts = [(a["src"], "defer" in a, "async" in a) for a in tags("script")]
    assert scripts == [
        ("js/theme.js", False, False),
        ("js/api.js", True, False),
        ("js/app.js", True, False),
    ]
    order = [
        a["src"] if t == "script" else a["href"]
        for t, a in page.tags
        if t == "script" or (t == "link" and a.get("rel") == "stylesheet")
    ]
    assert order.index("js/theme.js") < order.index("css/theme.css"), (
        "theme.js must run before any styling"
    )


def test_javascript_avoids_unsafe_constructs():
    forbidden = [
        "innerHTML",
        "outerHTML",
        "insertAdjacentHTML",
        "eval(",
        "new Function",
        "document.write",
        'setAttribute("style"',
        "cssText",
        "javascript:",
    ]
    for name, source in JS_FILES.items():
        for pattern in forbidden:
            assert pattern not in source, f"{name} uses {pattern}"


def test_the_sign_in_token_is_never_stored():
    assert "localStorage" in JS_FILES["theme.js"]
    for name in ("api.js", "app.js"):
        for storage in ("localStorage", "sessionStorage", "document.cookie", "indexedDB"):
            assert storage not in JS_FILES[name], f"{name} must not use {storage}"
    assert "sessionStorage" not in JS_FILES["theme.js"] and "cookie" not in JS_FILES["theme.js"]
    assert 'credentials: "omit"' in JS_FILES["api.js"] and 'cache: "no-store"' in JS_FILES["api.js"]
    assert "fetch(" not in JS_FILES["app.js"], "the page talks to the server only through api.js"
    assert "Authorization" not in JS_FILES["app.js"]


def test_files_are_plain_ascii_for_safe_copy_and_paste():
    for path in PAGE.rglob("*"):
        if path.is_file():
            path.read_bytes().decode("ascii")


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is not installed")
@pytest.mark.parametrize("name", sorted(JS_FILES))
def test_javascript_has_no_syntax_errors(name):
    result = subprocess.run(
        ["node", "--check", str(PAGE / "js" / name)], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr


# --------------------------------------------------------------------- theme colors (BR-23)
def block(pattern: str) -> dict[str, str]:
    match = re.search(pattern, THEME_CSS, re.S)
    assert match, "theme block not found"
    return dict(re.findall(r"--([a-z0-9-]+):\s*(#[0-9a-fA-F]{3,6})\s*;", match.group(1)))


LIGHT = block(r":root \{(.*?)\n\}")
DARK = block(r':root\[data-theme="dark"\] \{(.*?)\n\}')
DARK_FALLBACK = block(
    r'@media \(prefers-color-scheme: dark\) \{\s*:root:not\(\[data-theme="light"\]\) \{(.*?)\n  \}'
)

PAIRS = [
    ("text", "bg", "text"),
    ("text", "surface", "text"),
    ("muted", "bg", "text"),
    ("muted", "surface", "text"),
    ("link", "bg", "text"),
    ("link", "surface", "text"),
    ("on-primary", "primary", "text"),
    ("on-primary", "primary-hover", "text"),
    ("on-danger", "danger", "text"),
    ("on-danger", "danger-hover", "text"),
    ("banner-text", "banner-bg", "text"),
    ("urgent-text", "urgent-bg", "text"),
    ("ok-text", "ok-bg", "text"),
    ("info-text", "info-bg", "text"),
    ("bad-text", "bad-bg", "text"),
    ("selected-text", "selected-bg", "text"),
    ("border", "bg", "component"),
    ("border", "surface", "component"),
    ("focus", "bg", "component"),
    ("focus", "surface", "component"),
    ("urgent-border", "urgent-bg", "component"),
    ("selected-border", "surface", "component"),
    ("primary", "surface", "component"),
    ("danger", "surface", "component"),
]


def test_both_themes_define_the_same_tokens():
    assert LIGHT and set(LIGHT) == set(DARK)
    assert DARK == DARK_FALLBACK, (
        "the no-JavaScript dark fallback must match the dark theme exactly"
    )
    assert LIGHT != DARK


@pytest.mark.parametrize("theme_name", ["light", "dark"])
@pytest.mark.parametrize(("foreground", "background", "kind"), PAIRS)
def test_every_color_pair_meets_wcag_aa(theme_name, foreground, background, kind):
    tokens = LIGHT if theme_name == "light" else DARK
    assert meets_wcag_aa(tokens[foreground], tokens[background], kind), (
        f"{theme_name}: {foreground} {tokens[foreground]} on {background} {tokens[background]}"
    )


def test_stylesheet_uses_only_defined_color_tokens():
    defined = set(LIGHT) | {"radius", "tap-min", "page-width"}
    used = set(re.findall(r"var\(--([a-z0-9-]+)\)", APP_CSS))
    assert used <= defined, f"undefined tokens: {used - defined}"
    stripped = re.sub(r"/\*.*?\*/", "", APP_CSS, flags=re.S)
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b|\brgba?\(|\bhsla?\(", stripped), (
        "colors must come from theme.css tokens so both themes stay in step"
    )


def test_touch_targets_and_responsive_rules_exist():
    assert "--tap-min: 2.75rem" in APP_CSS
    assert "min-height: var(--tap-min)" in APP_CSS
    assert "@media (min-width" in APP_CSS and "@media (max-width" in APP_CSS
    assert "safe-area-inset" in APP_CSS and "forced-colors" in APP_CSS
    assert "overflow-wrap" in APP_CSS
