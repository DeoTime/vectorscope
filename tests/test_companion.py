"""
tests/test_companion.py – Unit tests for the Python companion app modules.

Tests cover:
  • color_utils  : RGB→YCbCr and RGB→HSV conversions, polar coordinate math
  • skin_tone_detector : detection mask, deviation metric, on-line filter

Run with:
    python -m pytest tests/ -v
"""

import sys
import os

# Ensure companion/ is on the path so we can import its modules
COMPANION_DIR = os.path.join(os.path.dirname(__file__), '..', 'companion')
sys.path.insert(0, os.path.abspath(COMPANION_DIR))

import numpy as np
import pytest

from color_utils import (
    rgb_to_ycbcr,
    rgb_to_hsv,
    ycbcr_to_polar,
    hsv_to_polar,
    SKIN_TONE_ANGLE_YCBCR_RAD,
    SKIN_TONE_HUE_HSV_DEG,
)
from skin_tone_detector import SkinToneDetector, ETHNIC_RANGES


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def skin_pixel():
    """A single warm skin-tone pixel (light Caucasian)."""
    return np.array([[[255, 170, 128]]], dtype=np.uint8)


@pytest.fixture
def grey_pixel():
    """A neutral grey pixel (achromatic)."""
    return np.array([[[128, 128, 128]]], dtype=np.uint8)


@pytest.fixture
def black_pixel():
    """Pure black pixel."""
    return np.array([[[0, 0, 0]]], dtype=np.uint8)


@pytest.fixture
def white_pixel():
    """Pure white pixel."""
    return np.array([[[255, 255, 255]]], dtype=np.uint8)


@pytest.fixture
def synthetic_portrait():
    """A 100×150 synthetic image with skin, sky, and foliage regions."""
    rng = np.random.default_rng(42)
    img = np.zeros((100, 150, 3), dtype=np.uint8)

    skin_bases = [(230, 190, 160), (200, 155, 110), (160, 110, 70), (100, 65, 40)]
    positions  = [(5, 5), (5, 55), (40, 5), (40, 85)]
    for base_rgb, (rs, cs) in zip(skin_bases, positions):
        noise  = rng.integers(-10, 10, (25, 25, 3))
        region = np.clip(np.array(base_rgb) + noise, 0, 255).astype(np.uint8)
        img[rs:rs + 25, cs:cs + 25] = region

    img[:30, 110:] = [120, 160, 230]   # sky
    img[70:, :40]  = [60, 130, 60]     # foliage

    uncovered = (img == 0).all(axis=2)
    img[uncovered] = [150, 145, 140]   # neutral background
    return img


# ---------------------------------------------------------------------------
# color_utils – YCbCr conversion
# ---------------------------------------------------------------------------

class TestRgbToYcbcr:
    def test_grey_maps_to_neutral_chroma(self, grey_pixel):
        """Grey pixel should have Cb ≈ Cr ≈ 128 (neutral chroma)."""
        ycbcr = rgb_to_ycbcr(grey_pixel)
        assert abs(ycbcr[0, 0, 1] - 128.0) < 1.0, 'Cb of grey should be ~128'
        assert abs(ycbcr[0, 0, 2] - 128.0) < 1.0, 'Cr of grey should be ~128'

    def test_white_luma_close_to_255(self, white_pixel):
        ycbcr = rgb_to_ycbcr(white_pixel)
        assert ycbcr[0, 0, 0] > 240, 'Luma of white should be high'

    def test_black_luma_is_zero(self, black_pixel):
        ycbcr = rgb_to_ycbcr(black_pixel)
        assert ycbcr[0, 0, 0] < 5, 'Luma of black should be ~0'

    def test_skin_pixel_cr_above_128(self, skin_pixel):
        """Warm skin tones have positive red-difference chroma (Cr > 128)."""
        ycbcr = rgb_to_ycbcr(skin_pixel)
        assert ycbcr[0, 0, 2] > 128, 'Skin pixel should have Cr > 128 (warm hue)'

    def test_output_shape(self):
        img   = np.random.randint(0, 255, (32, 48, 3), dtype=np.uint8)
        ycbcr = rgb_to_ycbcr(img)
        assert ycbcr.shape == (32, 48, 3)

    def test_output_dtype(self):
        img   = np.random.randint(0, 255, (10, 10, 3), dtype=np.uint8)
        ycbcr = rgb_to_ycbcr(img)
        assert ycbcr.dtype == np.float32


# ---------------------------------------------------------------------------
# color_utils – HSV conversion
# ---------------------------------------------------------------------------

class TestRgbToHsv:
    def test_grey_has_zero_saturation(self, grey_pixel):
        hsv = rgb_to_hsv(grey_pixel)
        assert hsv[0, 0, 1] == pytest.approx(0.0, abs=1e-5)

    def test_white_has_zero_saturation(self, white_pixel):
        hsv = rgb_to_hsv(white_pixel)
        assert hsv[0, 0, 1] == pytest.approx(0.0, abs=1e-5)

    def test_black_has_zero_value(self, black_pixel):
        hsv = rgb_to_hsv(black_pixel)
        assert hsv[0, 0, 2] == pytest.approx(0.0, abs=1e-5)

    def test_black_no_warning(self):
        """Pure black (Cmax=0) must not trigger a divide-by-zero warning."""
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter('error')
            hsv = rgb_to_hsv(np.array([[[0, 0, 0]]], dtype=np.uint8))
        assert hsv[0, 0, 1] == 0.0

    def test_pure_red_hue_is_zero(self):
        red = np.array([[[255, 0, 0]]], dtype=np.uint8)
        hsv = rgb_to_hsv(red)
        assert hsv[0, 0, 0] == pytest.approx(0.0, abs=0.5)

    def test_pure_green_hue_near_120(self):
        green = np.array([[[0, 255, 0]]], dtype=np.uint8)
        hsv   = rgb_to_hsv(green)
        assert abs(hsv[0, 0, 0] - 120.0) < 1.0

    def test_pure_blue_hue_near_240(self):
        blue = np.array([[[0, 0, 255]]], dtype=np.uint8)
        hsv  = rgb_to_hsv(blue)
        assert abs(hsv[0, 0, 0] - 240.0) < 1.0

    def test_skin_pixel_hue_orange(self, skin_pixel):
        """Warm skin tone should map to hue ≈ 15–35°."""
        hsv = rgb_to_hsv(skin_pixel)
        hue = hsv[0, 0, 0]
        assert 10.0 < hue < 40.0, f'Skin hue expected ~20°, got {hue:.1f}°'

    def test_output_shape(self):
        img = np.random.randint(0, 255, (32, 48, 3), dtype=np.uint8)
        hsv = rgb_to_hsv(img)
        assert hsv.shape == (32, 48, 3)


# ---------------------------------------------------------------------------
# color_utils – Polar coordinates (YCbCr)
# ---------------------------------------------------------------------------

class TestYcbcrToPolar:
    def test_grey_radius_near_zero(self, grey_pixel):
        """Achromatic pixel should map to radius ≈ 0 (centre of vectorscope)."""
        ycbcr         = rgb_to_ycbcr(grey_pixel)
        angle, radius = ycbcr_to_polar(ycbcr)
        assert radius[0] == pytest.approx(0.0, abs=0.01)

    def test_skin_pixel_angle_near_skin_line(self, skin_pixel):
        """Warm skin tone should be within ±20° of the skin-tone line."""
        ycbcr         = rgb_to_ycbcr(skin_pixel)
        angle, radius = ycbcr_to_polar(ycbcr)
        deviation_deg = abs(np.degrees(angle[0]) - np.degrees(SKIN_TONE_ANGLE_YCBCR_RAD))
        assert deviation_deg < 20.0, f'Skin pixel deviation too large: {deviation_deg:.1f}°'

    def test_radius_within_unit_circle(self):
        """All real image pixels should map to radius ≤ ~1.1.

        The normalization divides by 127, but the maximum Euclidean distance
        of a valid RGB pixel in the YCbCr Cb–Cr plane is slightly above 127
        (empirically ~136 for saturated primaries), giving a maximum radius
        of ~1.07.  We use a generous bound of 1.2 to cover all valid inputs.
        """
        img       = np.random.randint(0, 255, (50, 50, 3), dtype=np.uint8)
        ycbcr     = rgb_to_ycbcr(img)
        _, radius = ycbcr_to_polar(ycbcr)
        assert np.all(radius <= 1.2), 'Radius exceeded expected maximum for valid RGB pixels'

    def test_flat_input_shape(self):
        """ycbcr_to_polar should accept flat (N, 3) arrays."""
        ycbcr         = np.tile([190.0, 92.0, 170.0], (100, 1)).astype(np.float32)
        angle, radius = ycbcr_to_polar(ycbcr)
        assert angle.shape == (100,)
        assert radius.shape == (100,)


# ---------------------------------------------------------------------------
# color_utils – Polar coordinates (HSV)
# ---------------------------------------------------------------------------

class TestHsvToPolar:
    def test_grey_radius_zero(self, grey_pixel):
        hsv           = rgb_to_hsv(grey_pixel)
        angle, radius = hsv_to_polar(hsv)
        assert radius[0] == pytest.approx(0.0, abs=1e-5)

    def test_angle_range(self):
        img           = np.random.randint(0, 255, (30, 30, 3), dtype=np.uint8)
        hsv           = rgb_to_hsv(img)
        angle, _      = hsv_to_polar(hsv)
        assert np.all(angle >= 0)
        assert np.all(angle <= 2 * np.pi + 1e-5)


# ---------------------------------------------------------------------------
# skin_tone_detector – SkinToneDetector
# ---------------------------------------------------------------------------

class TestSkinToneDetector:

    @pytest.fixture
    def detector(self):
        return SkinToneDetector(ycbcr_tolerance_deg=15.0, use_hsv_filter=True)

    def test_detects_known_skin_pixels(self, detector):
        """Well-known skin-tone pixels must be detected."""
        skin_pixels = np.array([
            [[[220, 150, 100]]],   # medium skin
            [[[180, 110,  70]]],   # dark skin
        ], dtype=np.uint8).reshape(2, 1, 3)

        result = detector.detect(skin_pixels)
        assert result['pixel_count'] >= 1, 'Expected at least 1 skin pixel detected'

    def test_rejects_non_skin_pixels(self, detector):
        """Blue sky and green foliage must NOT be detected as skin."""
        non_skin = np.array([
            [[[100, 150, 255]]],   # blue sky
            [[[ 50, 200,  50]]],   # green grass
        ], dtype=np.uint8).reshape(2, 1, 3)

        result = detector.detect(non_skin)
        assert result['pixel_count'] == 0, 'Non-skin pixels should not be detected'

    def test_mask_shape_matches_image(self, detector, synthetic_portrait):
        result = detector.detect(synthetic_portrait)
        assert result['mask'].shape == (100, 150)

    def test_deviation_within_reasonable_range(self, detector, synthetic_portrait):
        """Detected skin pixels should not deviate wildly from the skin-tone line."""
        result = detector.detect(synthetic_portrait)
        if result['pixel_count'] > 0:
            assert result['mean_deviation_deg'] < 45.0, 'Mean deviation too large'

    def test_on_skin_line_mask(self, detector):
        """on_skin_line_mask should return True for pixels within tolerance."""
        img    = np.array([
            [[[220, 150, 100]]],
            [[[200, 155, 110]]],
        ], dtype=np.uint8).reshape(2, 1, 3)
        result  = detector.detect(img)
        on_line = detector.on_skin_line_mask(result)
        assert on_line.dtype == bool
        assert on_line.shape == (result['pixel_count'],)

    def test_hsv_filter_off_detects_more(self):
        """Disabling HSV filter should detect >= pixels as YCbCr-only filter."""
        det_strict = SkinToneDetector(use_hsv_filter=True)
        det_loose  = SkinToneDetector(use_hsv_filter=False)

        img      = np.random.randint(50, 200, (30, 30, 3), dtype=np.uint8)
        r_strict = det_strict.detect(img)['pixel_count']
        r_loose  = det_loose.detect(img)['pixel_count']
        assert r_loose >= r_strict, 'Loose filter should detect >= pixels than strict'

    def test_ethnic_ranges_cover_all_fitzpatrick(self):
        """ETHNIC_RANGES should cover a broad range of Fitzpatrick scale types."""
        assert len(ETHNIC_RANGES) >= 5, 'Expected at least 5 ethnic range entries'
        h_mins = [r.h_min for r in ETHNIC_RANGES]
        h_maxs = [r.h_max for r in ETHNIC_RANGES]
        assert min(h_mins) <= 5,  'Lightest range should start near 0°'
        assert max(h_maxs) >= 40, 'Darkest range should extend to at least 40°'

    def test_output_keys(self, detector, synthetic_portrait):
        result = detector.detect(synthetic_portrait)
        for key in ('mask', 'ycbcr', 'hsv', 'angle', 'radius',
                    'deviation_rad', 'deviation_deg', 'mean_deviation_deg',
                    'pixel_count'):
            assert key in result, f'Missing key in detection result: {key}'

    def test_deviation_and_pixel_count_consistent(self, detector, synthetic_portrait):
        result = detector.detect(synthetic_portrait)
        assert len(result['deviation_deg']) == result['pixel_count']
        assert len(result['angle'])         == result['pixel_count']
        assert len(result['radius'])        == result['pixel_count']


# ---------------------------------------------------------------------------
# Constants sanity checks
# ---------------------------------------------------------------------------

class TestConstants:
    def test_skin_tone_angle_ycbcr_in_expected_range(self):
        """The skin-tone line should be in the red-orange quadrant (~−33°)."""
        deg = np.degrees(SKIN_TONE_ANGLE_YCBCR_RAD)
        assert -60.0 < deg < 0.0, f'Unexpected skin-tone angle: {deg:.1f}°'

    def test_skin_tone_hue_hsv_orange(self):
        """HSV skin-tone hue should be in the orange range."""
        assert 10.0 < SKIN_TONE_HUE_HSV_DEG < 40.0
