"""DemoStore — a deterministic fake product/checkout application for stress scenarios.

The world is two cooperating fakes:

* :class:`DemoStoreBackend` (a ``FakeBackend``) holds ``products``, ``cart``,
  ``orders`` and the current ``screen`` in its state document, so the generic
  :class:`~argus.stress.mutations.backend.StateMutationBackend` can mutate
  products underneath the app exactly as it would for a real backend.
* :class:`DemoStoreDevice` (a ``FakeDevice``) *is* the application: taps and
  keys drive a tiny catalog → product → cart → checkout flow, and every
  screenshot is rendered from the shared state.

Two opt-in defects make the scenario realistic:

* ``buggy: true`` — checkout succeeds with a product that the backend has
  deleted or disabled (stale state) instead of showing "Product unavailable".
* ``crash_on_text`` — typing that text into search crashes the app.

Device type ``stress_demo`` is registered with the device registry; OCR is
served by :class:`DeviceTextOCRProvider` from the device's own text layer.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from PIL import Image as PILImage
from PIL import ImageDraw, ImageFont
from PIL.Image import Image

from argus.adapters.fake import FakeBackend, FakeDevice
from argus.config.models import DeviceConfig
from argus.models.common import Region
from argus.ocr.base import OCRProvider, OCRResult, OCRWord

DEFAULT_PRODUCTS: list[dict[str, Any]] = [
    {"id": 1, "title": "Batman Begins", "price": 12.99, "stock": 5, "status": "active"},
    {"id": 2, "title": "The Matrix", "price": 9.99, "stock": 3, "status": "active"},
    {"id": 3, "title": "Interstellar", "price": 14.5, "stock": 8, "status": "active"},
    {"id": 4, "title": "Inception", "price": 11.0, "stock": 2, "status": "active"},
    {"id": 5, "title": "Dune", "price": 15.25, "stock": 6, "status": "active"},
]

SCREEN_WIDTH, SCREEN_HEIGHT = 720, 1280
ROW_HEIGHT = 90
LIST_TOP = 140
BUTTON_HEIGHT = 80


def initial_state(products: Sequence[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "environment": "test",
        "products": [dict(p) for p in (products or DEFAULT_PRODUCTS)],
        "cart": [],
        "orders": [],
        "screen": "catalog",
        "current_product": None,
        "search": "",
        "message": "",
    }


class DemoStoreBackend(FakeBackend):
    """State-document backend for the demo store (``backend: {type: stress_demo}``)."""

    def __init__(self, products: Sequence[dict[str, Any]] | None = None) -> None:
        super().__init__(initial_state(products))
        self.reset_state = initial_state(products)

    def reset(self) -> None:
        self.state = initial_state(self.reset_state["products"])

    def product(self, product_id: Any) -> dict[str, Any] | None:
        for item in self.state.get("products", []):
            if str(item.get("id")) == str(product_id):
                return item
        return None


class DemoStoreDevice(FakeDevice):
    """The demo application: renders and reacts to input based on backend state."""

    def __init__(self, name: str = "demo", *, backend: DemoStoreBackend | None = None,
                 buggy: bool = False, crash_on_text: str | None = None,
                 platform: str = "fake") -> None:
        super().__init__(name, screen_size=(SCREEN_WIDTH, SCREEN_HEIGHT), platform=platform)
        self.backend = backend or DemoStoreBackend()
        self.buggy = buggy
        self.crash_on_text = crash_on_text
        self.app_running = True
        self.typed = ""
        self.text_layer: list[tuple[str, Region]] = []
        self.checkouts: list[dict[str, Any]] = []
        self.bind_state_provider(self.backend.get_state)

    @classmethod
    def from_config(cls, name: str, config: DeviceConfig) -> DemoStoreDevice:
        options = config.options
        return cls(name, buggy=bool(options.get("buggy", False)),
                   crash_on_text=options.get("crash_on_text"),
                   platform=config.effective_platform)

    # -- state helpers --------------------------------------------------------------------------

    @property
    def state(self) -> dict[str, Any]:
        return self.backend.state

    def _set(self, **fields: Any) -> None:
        self.backend.state.update(fields)

    def _products(self) -> list[dict[str, Any]]:
        items = self.state.get("products", [])
        if isinstance(items, dict):
            items = list(items.values())
        search = str(self.state.get("search") or "").lower()
        return [p for p in items if not search or search in str(p.get("title", "")).lower()]

    def _visible_product(self) -> dict[str, Any] | None:
        current = self.state.get("current_product")
        if current is None:
            return None
        return self.backend.product(current)

    # -- lifecycle --------------------------------------------------------------------------------

    def start_application(self) -> None:
        self.app_running = True
        self.typed = ""
        self._set(screen="catalog", message="", search="")

    def stop_application(self) -> None:
        self.app_running = False

    def restart_application(self) -> None:
        self.stop_application()
        self.backend.state["cart"] = []
        self.start_application()

    # -- input ----------------------------------------------------------------------------------

    def tap(self, x: int, y: int) -> None:
        super().tap(x, y)
        if not self.app_running:
            return
        screen = self.state.get("screen", "catalog")
        if screen == "catalog":
            self._tap_catalog(x, y)
        elif screen == "product":
            self._tap_product(x, y)
        elif screen == "cart":
            self._tap_cart(x, y)
        elif screen in ("order", "error"):
            self._set(screen="catalog", message="")

    def _tap_catalog(self, x: int, y: int) -> None:
        if y < LIST_TOP:
            return  # search box: typing goes here
        index = (y - LIST_TOP) // ROW_HEIGHT
        products = self._products()
        if 0 <= index < len(products):
            self._set(screen="product", current_product=products[index]["id"], message="")

    def _tap_product(self, x: int, y: int) -> None:
        if self._button_hit("add", y):
            product = self._visible_product()
            if product is not None:
                cart = list(self.state.get("cart", []))
                cart.append({"product_id": product["id"], "title": product["title"],
                             "price": product["price"]})
                self._set(cart=cart, screen="cart", message="")
        elif self._button_hit("back", y):
            self._set(screen="catalog", current_product=None)

    def _tap_cart(self, x: int, y: int) -> None:
        if self._button_hit("add", y):  # "Checkout" sits where "Add to cart" does
            self._checkout()
        elif self._button_hit("back", y):
            self._set(screen="catalog")

    def _checkout(self) -> None:
        cart = list(self.state.get("cart", []))
        if not cart:
            self._set(screen="error", message="Cart is empty")
            return
        unavailable = []
        for line in cart:
            product = self.backend.product(line["product_id"])
            if product is None or product.get("status") != "active" or int(product.get("stock", 0)) <= 0:  # noqa: E501
                unavailable.append(line["title"])
        record = {"lines": cart, "stale": bool(unavailable)}
        if unavailable and not self.buggy:
            self._set(screen="error", message=f"Product unavailable: {unavailable[0]}")
            self.checkouts.append({**record, "accepted": False})
            return
        # Correct path — or the bug: stale cart lines are accepted anyway.
        orders = list(self.state.get("orders", []))
        orders.append(record)
        self.checkouts.append({**record, "accepted": True})
        self._set(orders=orders, cart=[], screen="order",
                  message=f"Order confirmed: {', '.join(line['title'] for line in cart)}")

    @staticmethod
    def _button_hit(which: str, y: int) -> bool:
        top = SCREEN_HEIGHT - 2 * BUTTON_HEIGHT - 40 if which == "add" else SCREEN_HEIGHT - BUTTON_HEIGHT - 20  # noqa: E501
        return top <= y < top + BUTTON_HEIGHT

    def press_key(self, key: str) -> None:
        super().press_key(key)
        if not self.app_running:
            return
        if key == "BACK":
            screen = self.state.get("screen")
            if screen == "catalog":
                self.typed = ""  # back on the catalog clears the search box
                self._set(search="", message="")
            else:
                self._set(screen="catalog", message="",
                          current_product=None if screen == "product"
                          else self.state.get("current_product"))
            return
        if key == "HOME":
            self.app_running = False  # backgrounded; foreground restores
            return
        if key == "BACKSPACE":
            self.typed = self.typed[:-1]
        elif key == "ENTER":
            self._set(search=self.typed)
            return
        elif key == "SPACE":
            self.typed += " "
        elif len(key) == 1:
            self.typed += key
        if self.state.get("screen") == "catalog":
            self._set(search=self.typed)
        if self.crash_on_text and self.crash_on_text in self.typed:
            self.app_running = False
            self.log_lines.append(f"FATAL EXCEPTION: input {self.typed!r}")

    def type_text(self, text: str) -> None:
        for char in text:
            self.press_key("SPACE" if char == " " else char)

    def clear_text(self) -> None:
        self.typed = ""
        self._set(search="")

    def background_application(self) -> None:
        self.press_key("HOME")

    def foreground_application(self) -> None:
        self.app_running = True

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300) -> None:
        super().swipe(x1, y1, x2, y2, duration_ms)

    def long_press(self, x: int, y: int, duration_ms: int = 1000) -> None:
        super().long_press(x, y, duration_ms)

    def is_application_running(self) -> bool:
        return self.app_running

    # -- observation ------------------------------------------------------------------------

    def screenshot(self) -> Image:
        self.screenshot_count += 1
        return self._render_screen()

    def screen_text(self) -> OCRResult:
        """The text layer of the last render (an instrumented OCR source)."""
        if not self.text_layer:
            self._render_screen()
        words = [OCRWord(text=t, confidence=1.0, region=r) for t, r in self.text_layer]
        return OCRResult(text="\n".join(t for t, _r in self.text_layer), words=words)

    def _render_screen(self) -> Image:
        self.text_layer = []
        image = PILImage.new("RGB", (SCREEN_WIDTH, SCREEN_HEIGHT), "#101018")
        draw = ImageDraw.Draw(image)
        try:
            font = ImageFont.load_default(size=32)
            small = ImageFont.load_default(size=24)
        except TypeError:  # Pillow < 10.1
            font = small = ImageFont.load_default()
        if not self.app_running:
            self._text(draw, "Application not running", (40, 600), small, "#777777")
            return image
        screen = self.state.get("screen", "catalog")
        if screen == "catalog":
            self._text(draw, "Movies Store", (40, 40), font)
            self._text(draw, f"Search: {self.state.get('search') or ''}", (40, 95), small)
            for index, product in enumerate(self._products()[:11]):
                y = LIST_TOP + index * ROW_HEIGHT
                draw.rectangle([20, y + 5, SCREEN_WIDTH - 20, y + ROW_HEIGHT - 5],
                               outline="#333355")
                self._text(draw, str(product["title"]), (40, y + 25), font)
                self._text(draw, f"${product['price']}", (SCREEN_WIDTH - 180, y + 30), small)
        elif screen == "product":
            shown = self._visible_product()
            if shown is None:
                self._text(draw, "Product unavailable", (40, 200), font, "#ff6666")
            else:
                self._text(draw, str(shown["title"]), (40, 120), font)
                self._text(draw, f"${shown['price']}  ·  {shown.get('stock', 0)} in stock",
                           (40, 190), small)
                self._text(draw, f"Status: {shown.get('status', '')}", (40, 240), small)
            self._button(draw, "Add to cart", "add", font)
            self._button(draw, "Back", "back", font)
        elif screen == "cart":
            self._text(draw, "Your cart", (40, 40), font)
            for index, line in enumerate(self.state.get("cart", [])[:8]):
                self._text(draw, f"{line['title']}  ${line['price']}", (40, LIST_TOP + index * 60), small)  # noqa: E501
            self._button(draw, "Checkout", "add", font)
            self._button(draw, "Back", "back", font)
        elif screen == "order":
            self._text(draw, "Thank you!", (40, 200), font)
            self._text(draw, str(self.state.get("message", "")), (40, 280), small)
            self._text(draw, "Tap to continue", (40, 400), small)
        else:  # error
            self._text(draw, "Something went wrong", (40, 200), font, "#ff6666")
            self._text(draw, str(self.state.get("message", "")), (40, 280), small)
            self._text(draw, "Tap to continue", (40, 400), small)
        return image

    def _button(self, draw: ImageDraw.ImageDraw, label: str, which: str, font: Any) -> None:
        top = SCREEN_HEIGHT - 2 * BUTTON_HEIGHT - 40 if which == "add" else SCREEN_HEIGHT - BUTTON_HEIGHT - 20  # noqa: E501
        draw.rectangle([40, top, SCREEN_WIDTH - 40, top + BUTTON_HEIGHT], fill="#2244aa")
        self._text(draw, label, (SCREEN_WIDTH // 2 - 80, top + 22), font)

    def _text(self, draw: ImageDraw.ImageDraw, text: str, position: tuple[int, int],
              font: Any, fill: str = "#ffffff") -> None:
        draw.text(position, text, fill=fill, font=font)
        width = max(int(draw.textlength(text, font=font)), 1)
        height = 36
        for word in text.split():
            self.text_layer.append((word, Region(x=position[0], y=position[1], width=width,
                                                 height=height)))
        self.text_layer.append((text, Region(x=position[0], y=position[1], width=width,
                                             height=height)))


class DeviceTextOCRProvider(OCRProvider):
    """OCR served by a device's own text layer (``device.screen_text()``)."""

    name = "device_text"

    def __init__(self, device: Any) -> None:
        self._device = device

    def is_available(self) -> tuple[bool, str]:
        return callable(getattr(self._device, "screen_text", None)), "device has no screen_text()"

    def extract_text(self, image: Image) -> OCRResult:
        return self._device.screen_text()


def register_demo_devices(registry: Any) -> None:
    registry.register("stress_demo", DemoStoreDevice.from_config)


__all__ = ["DEFAULT_PRODUCTS", "DemoStoreBackend", "DemoStoreDevice", "DeviceTextOCRProvider",
           "initial_state", "register_demo_devices"]
