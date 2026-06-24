"""Captcha solver using ddddocr for slider and text recognition."""

from __future__ import annotations

import io
import logging
import random
import asyncio
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.async_api import Page

logger = logging.getLogger(__name__)


class CaptchaSolver:
    """Solves slider and text captchas using ddddocr."""

    def __init__(self) -> None:
        self._ocr = None
        self._det = None
        self._slide = None

    def _get_ocr(self):
        if self._ocr is None:
            import ddddocr
            self._ocr = ddddocr.DdddOcr(show_ad=False)
        return self._ocr

    def _get_det(self):
        if self._det is None:
            import ddddocr
            self._det = ddddocr.DdddOcr(det=True, show_ad=False)
        return self._det

    def _get_slide(self):
        if self._slide is None:
            import ddddocr
            self._slide = ddddocr.DdddOcr(det=False, ocr=False, show_ad=False)
        return self._slide

    def detect_slide_gap(self, bg_image: bytes, slide_image: bytes) -> int:
        """
        Detect the gap position for a slider captcha.

        Args:
            bg_image: Background image bytes (the full captcha background)
            slide_image: Slider piece image bytes (the piece to drag)

        Returns:
            x-coordinate of the gap position (pixels from left)
        """
        slide = self._get_slide()
        result = slide.slide_match(slide_image, bg_image, simple_target=True)
        if result and "target" in result:
            target = result["target"]
            # target is [x1, y1, x2, y2] — return center x
            return (target[0] + target[2]) // 2
        return 0

    def recognize_text(self, image: bytes) -> str:
        """Recognize text from a captcha image."""
        ocr = self._get_ocr()
        return ocr.classification(image)

    def generate_trajectory(self, distance: int) -> list[tuple[int, int, int]]:
        """
        Generate a human-like mouse trajectory for slider dragging.

        Returns list of (x_offset, y_offset, duration_ms) tuples.
        Simulates acceleration → deceleration with slight y-axis wobble.
        """
        trajectory: list[tuple[int, int, int]] = []
        current_x = 0
        t = 0

        # Phase 1: Quick acceleration (first 60% of distance)
        phase1_dist = int(distance * 0.6)
        while current_x < phase1_dist:
            step = random.randint(8, 18)
            y_wobble = random.randint(-2, 2)
            dt = random.randint(10, 25)
            current_x = min(current_x + step, phase1_dist)
            t += dt
            trajectory.append((current_x, y_wobble, dt))

        # Phase 2: Deceleration (remaining 40%)
        while current_x < distance:
            step = random.randint(2, 6)
            y_wobble = random.randint(-1, 1)
            dt = random.randint(20, 50)
            current_x = min(current_x + step, distance)
            t += dt
            trajectory.append((current_x, y_wobble, dt))

        # Phase 3: Slight overshoot and correction
        if random.random() > 0.3:
            overshoot = random.randint(2, 5)
            trajectory.append((current_x + overshoot, 0, random.randint(15, 30)))
            trajectory.append((current_x, 0, random.randint(30, 60)))

        return trajectory

    async def solve_slide_captcha(
        self,
        page: "Page",
        bg_selector: str = ".captcha-bg img, .yzsm_bg img, [class*='captcha'] img",
        slide_selector: str = ".slider-btn, .captcha-slider, [class*='slide'] .btn",
        bg_image_attr: str = "src",
    ) -> bool:
        """
        Complete slider captcha solving pipeline.

        1. Locate and screenshot the background image
        2. Locate the slider button
        3. Use ddddocr to detect gap position
        4. Generate human-like trajectory
        5. Perform drag action

        Returns True if captcha appears solved (page changes after drag).
        """
        try:
            # Wait for captcha elements to be visible
            await page.wait_for_timeout(1000)

            # Try to get background image
            bg_element = await page.query_selector(bg_selector)
            if not bg_element:
                logger.warning("Background image element not found")
                return False

            # Screenshot the background
            bg_bytes = await bg_element.screenshot()

            # Try to get slide piece image (sometimes it's a separate element)
            slide_piece_selector = ".slide-piece, .captcha-piece, [class*='slide'] img"
            slide_piece = await page.query_selector(slide_piece_selector)
            slide_bytes = None
            if slide_piece:
                slide_bytes = await slide_piece.screenshot()

            # Get the slider button
            slider_btn = await page.query_selector(slide_selector)
            if not slider_btn:
                logger.warning("Slider button not found")
                return False

            # Detect gap position
            if slide_bytes:
                gap_x = self.detect_slide_gap(bg_bytes, slide_bytes)
            else:
                # Without slide piece, try detect from background alone
                gap_x = self._detect_gap_from_bg(bg_bytes)

            if gap_x <= 0:
                logger.warning(f"Could not detect gap position (got {gap_x})")
                return False

            logger.info(f"Detected gap at x={gap_x}")

            # Get slider button position and calculate drag distance
            btn_box = await slider_btn.bounding_box()
            if not btn_box:
                return False

            # Account for slider track offset
            start_x = btn_box["x"] + btn_box["width"] / 2
            start_y = btn_box["y"] + btn_box["height"] / 2

            # Generate trajectory
            trajectory = self.generate_trajectory(gap_x)

            # Perform the drag
            await page.mouse.move(start_x, start_y)
            await page.mouse.down()
            await asyncio.sleep(random.uniform(0.1, 0.2))

            for x_offset, y_offset, dt in trajectory:
                await page.mouse.move(start_x + x_offset, start_y + y_offset)
                await asyncio.sleep(dt / 1000.0)

            await asyncio.sleep(random.uniform(0.05, 0.15))
            await page.mouse.up()

            # Wait and check if captcha was solved
            await page.wait_for_timeout(2000)

            # Check if captcha elements are still visible
            still_visible = await page.query_selector(bg_selector)
            if not still_visible:
                logger.info("Captcha appears solved (elements disappeared)")
                return True

            # Check for success indicators
            success_indicators = [".captcha-success", ".verify-success", "[class*='success']"]
            for sel in success_indicators:
                elem = await page.query_selector(sel)
                if elem and await elem.is_visible():
                    logger.info("Captcha solved (success indicator found)")
                    return True

            logger.warning("Captcha may not be solved (elements still visible)")
            return False

        except Exception as e:
            logger.error(f"Captcha solving error: {e}")
            return False

    def _detect_gap_from_bg(self, bg_bytes: bytes) -> int:
        """
        Detect gap position from background image alone.
        Uses edge detection to find the notch/shadow.
        """
        try:
            from PIL import Image
            import numpy as np

            img = Image.open(io.BytesIO(bg_bytes)).convert("L")
            arr = np.array(img)

            # Simple edge detection: find column with highest variance
            # (the gap creates a dark shadow/edge)
            col_variance = arr.var(axis=0)

            # Skip first 50px (slider start area) and last 20px
            search_area = col_variance[50:-20]
            if len(search_area) == 0:
                return 0

            gap_col = int(np.argmax(search_area)) + 50
            return gap_col

        except ImportError:
            logger.warning("PIL/numpy not available for gap detection fallback")
            return 0
        except Exception as e:
            logger.warning(f"Gap detection from BG failed: {e}")
            return 0
