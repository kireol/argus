"""Optional OCR image preprocessing helpers."""

from __future__ import annotations

import cv2
import numpy as np
from PIL import Image as PILImage
from PIL.Image import Image


def isolate_light_text(image: Image, *, luminance: int = 180) -> Image:
    """Keep near-white glyphs as black text on white; drop darker wallpaper.

    Digital readouts are bright white; colorful wallpapers often sit in
    a mid luminance band. Thresholding above ``luminance`` leaves only the
    glyphs for Tesseract. A light erode thickens thin strokes (e.g. ``1``)
    so they still OCR after aggressive cutoffs.
    """
    rgb = np.asarray(image.convert("RGB"), dtype=np.int16)
    # Rec. 601 luma approximation is enough for this binary gate.
    luma = (0.299 * rgb[:, :, 0] + 0.587 * rgb[:, :, 1] + 0.114 * rgb[:, :, 2]).astype(
        np.uint8
    )
    # Bright glyph pixels → black (0); everything else → white (255).
    binary = np.where(luma >= luminance, 0, 255).astype(np.uint8)
    # Expand black glyphs slightly so thin digit stems survive thresholding.
    kernel = np.ones((2, 2), np.uint8)
    binary = cv2.erode(binary, kernel, iterations=1)
    return PILImage.fromarray(binary, mode="L")
