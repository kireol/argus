"""Movies demo: an in-process application rendered with Pillow.

Screens::

    Home ─┬─ Movies ─┬─ Search (type a title) ─ Results ─ Movie details
          │          └─ Movie list
          └─ Settings

Includes a loading state (results appear after ``loading_frames`` screenshots)
and a deliberate failure mode: searching for "crash" shows an error screen.
Every rendered element is also reported as ``visible_text`` metadata so the
fake OCR provider is deterministic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from PIL import Image as PILImage
from PIL import ImageDraw, ImageFont
from PIL.Image import Image

from argus_test_creator.models.common import Rect

MOVIES = ["Batman Begins", "The Dark Knight", "Inception", "Interstellar", "Dunkirk"]
BACKGROUND = (16, 16, 24)
PANEL = (32, 34, 48)
ACCENT = (250, 190, 40)
TEXT = (235, 235, 240)
MUTED = (150, 150, 165)


@dataclass(frozen=True)
class Element:
    label: str
    rect: Rect
    action: str  # navigate:<screen> | select:<movie> | focus:search | none
    kind: str = "button"


@dataclass
class DemoState:
    screen: str = "home"
    query: str = ""
    selected: str | None = None
    loading_remaining: int = 0
    search_focused: bool = False
    running: bool = True
    history: list[str] = field(default_factory=list)


class MoviesDemoApp:
    def __init__(self, size: tuple[int, int] = (1280, 720), *, loading_frames: int = 1) -> None:
        self.size = size
        self.loading_frames = loading_frames
        self.state = DemoState()
        self._font_cache: dict[int, ImageFont.FreeTypeFont | ImageFont.ImageFont] = {}

    # -- interaction ------------------------------------------------------------------

    def tap(self, x: int, y: int) -> str | None:
        """Returns the action that was triggered (for tests) or None."""
        if not self.state.running:
            return None
        for element in self.elements():
            if element.rect.contains(x, y):
                self._trigger(element.action)
                return element.action
        return None

    def key(self, key: str) -> None:
        state = self.state
        if not state.running:
            return
        key_upper = key.upper()
        if key_upper in ("BACK", "ESCAPE"):
            self.back()
            return
        if state.screen == "search" and state.search_focused:
            if key_upper in ("ENTER", "DPAD_CENTER"):
                self._submit_search()
            elif key_upper in ("BACKSPACE", "DEL"):
                state.query = state.query[:-1]
            elif key_upper == "SPACE":
                state.query += " "
            elif len(key) == 1 and key.isprintable():
                state.query += key
        elif key_upper == "HOME":
            self.navigate("home")

    def type_text(self, text: str) -> None:
        for char in text:
            self.key(char)

    def back(self) -> None:
        if self.state.history:
            self.state.screen = self.state.history.pop()
        else:
            self.state.screen = "home"

    def navigate(self, screen: str) -> None:
        if screen != self.state.screen:
            self.state.history.append(self.state.screen)
        self.state.screen = screen
        self.state.search_focused = screen == "search"

    def start(self) -> None:
        self.state = DemoState()

    def stop(self) -> None:
        self.state.running = False

    def _trigger(self, action: str) -> None:
        kind, _, arg = action.partition(":")
        if kind == "navigate":
            self.navigate(arg)
        elif kind == "select":
            self.state.selected = arg
            self.navigate("details")
        elif kind == "focus":
            self.state.search_focused = True
        elif kind == "submit":
            self._submit_search()
        elif kind == "back":
            self.back()

    def _submit_search(self) -> None:
        state = self.state
        if state.query.strip().lower() == "crash":
            self.navigate("error")
            return
        state.loading_remaining = self.loading_frames
        self.navigate("results")

    # -- rendering ------------------------------------------------------------------------

    def elements(self) -> list[Element]:
        w, h = self.size
        s = self.state
        header = [Element("Argus Movies", Rect(x=40, y=24, width=300, height=48), "navigate:home",
                          "title")]
        match s.screen:
            case "home":
                return header + [
                    Element("Movies", Rect(x=120, y=200, width=320, height=120), "navigate:movies"),
                    Element("Settings", Rect(x=520, y=200, width=320, height=120),
                            "navigate:settings"),
                    Element("Welcome back", Rect(x=120, y=400, width=600, height=40), "none",
                            "text"),
                ]
            case "movies":
                return header + [
                    Element("Search", Rect(x=120, y=140, width=240, height=80), "navigate:search"),
                    Element("Movie list", Rect(x=400, y=140, width=240, height=80),
                            "navigate:list"),
                    Element("Back", Rect(x=w - 200, y=140, width=140, height=80), "back:"),
                ]
            case "search":
                items = header + [
                    Element(s.query or "Type a title…", Rect(x=120, y=160, width=700, height=72),
                            "focus:search", "input"),
                    Element("Go", Rect(x=860, y=160, width=140, height=72), "submit:"),
                    Element("Back", Rect(x=w - 200, y=24, width=140, height=48), "back:"),
                ]
                return items
            case "results":
                if s.loading_remaining > 0:
                    return header + [Element("Loading…", Rect(x=120, y=200, width=400, height=48),
                                             "none", "text")]
                matches = [m for m in MOVIES if s.query.strip().lower() in m.lower()] or []
                rows = [
                    Element(m, Rect(x=120, y=160 + i * 90, width=700, height=72), f"select:{m}")
                    for i, m in enumerate(matches)
                ]
                if not rows:
                    rows = [Element("No results", Rect(x=120, y=200, width=400, height=48),
                                    "none", "text")]
                return header + rows + [Element("Back", Rect(x=w - 200, y=24, width=140,
                                                            height=48), "back:")]
            case "list":
                rows = [
                    Element(m, Rect(x=120, y=140 + i * 90, width=700, height=72), f"select:{m}")
                    for i, m in enumerate(MOVIES)
                ]
                return header + rows + [Element("Back", Rect(x=w - 200, y=24, width=140,
                                                            height=48), "back:")]
            case "details":
                title = s.selected or "Unknown"
                return header + [
                    Element(title, Rect(x=120, y=140, width=800, height=64), "none", "heading"),
                    Element("Play", Rect(x=120, y=260, width=200, height=80), "none"),
                    Element("More Info", Rect(x=360, y=260, width=240, height=80), "none"),
                    Element("Artwork", Rect(x=120, y=380, width=240, height=240), "none",
                            "artwork"),
                    Element("Back", Rect(x=w - 200, y=24, width=140, height=48), "back:"),
                ]
            case "settings":
                return header + [
                    Element("Settings", Rect(x=120, y=140, width=400, height=64), "none",
                            "heading"),
                    Element("Theme: Dark", Rect(x=120, y=240, width=400, height=60), "none",
                            "text"),
                    Element("Back", Rect(x=w - 200, y=24, width=140, height=48), "back:"),
                ]
            case "error":
                return header + [
                    Element("Something went wrong", Rect(x=120, y=200, width=800, height=64),
                            "none", "heading"),
                    Element("Error code 500", Rect(x=120, y=300, width=400, height=48), "none",
                            "text"),
                    Element("Back", Rect(x=w - 200, y=24, width=140, height=48), "back:"),
                ]
        return header

    def render(self) -> Image:
        """Render the current screen; advances the loading state by one frame."""
        w, h = self.size
        image = PILImage.new("RGB", (w, h), BACKGROUND if self.state.running else (0, 0, 0))
        if not self.state.running:
            return image
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 0, w, 96), fill=PANEL)
        for element in self.elements():
            self._draw_element(draw, element)
        if self.state.screen == "results" and self.state.loading_remaining > 0:
            self.state.loading_remaining -= 1
        return image

    def visible_text(self) -> list[dict[str, Any]]:
        """Deterministic OCR ground truth for the fake OCR provider."""
        return [
            {"text": e.label, "region": e.rect.to_argus()}
            for e in self.elements()
            if e.kind != "artwork"
        ]

    def screen_metadata(self) -> dict[str, Any]:
        return {
            "visible_text": self.visible_text(),
            "screen": self.state.screen,
            "elements": [
                {"label": e.label, "region": e.rect.to_argus(), "kind": e.kind}
                for e in self.elements()
            ],
        }

    def _draw_element(self, draw: ImageDraw.ImageDraw, element: Element) -> None:
        r = element.rect
        box = (r.x, r.y, r.right, r.bottom)
        if element.kind == "button":
            draw.rounded_rectangle(box, radius=12, fill=ACCENT)
            self._text(draw, element.label, r, (20, 20, 20), 28)
        elif element.kind == "input":
            focused = self.state.search_focused
            draw.rounded_rectangle(box, radius=8, fill=PANEL,
                                   outline=ACCENT if focused else MUTED, width=3)
            color = TEXT if self.state.query else MUTED
            self._text(draw, element.label, r, color, 28, align="left")
        elif element.kind == "artwork":
            draw.rectangle(box, fill=(90, 40, 140))
            for i in range(0, r.width, 24):
                draw.line((r.x + i, r.y, r.x, r.y + i), fill=ACCENT, width=2)
            self._text(draw, element.label, r, TEXT, 20)
        elif element.kind == "title":
            self._text(draw, element.label, r, ACCENT, 34, align="left")
        elif element.kind == "heading":
            self._text(draw, element.label, r, TEXT, 40, align="left")
        else:
            self._text(draw, element.label, r, TEXT, 28, align="left")

    def _font(self, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
        font = self._font_cache.get(size)
        if font is None:
            try:
                font = ImageFont.truetype("DejaVuSans.ttf", size)
            except OSError:
                try:
                    font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", size)
                except OSError:
                    font = ImageFont.load_default()
            self._font_cache[size] = font
        return font

    def _text(
        self, draw: ImageDraw.ImageDraw, text: str, rect: Rect, color: tuple[int, int, int],
        size: int, *, align: str = "center",
    ) -> None:
        font = self._font(size)
        bbox = draw.textbbox((0, 0), text, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        y = rect.y + (rect.height - th) // 2 - bbox[1]
        x = rect.x + 16 if align == "left" else rect.x + (rect.width - tw) // 2 - bbox[0]
        draw.text((x, y), text, fill=color, font=font)
