"""
skin_tone_detector.py – Heuristic skin-tone detection and deviation analysis.

Theory
──────
Human skin tones span a wide luminance range (from very light to very dark)
but tend to cluster within a narrow band of hue and saturation.  Two
complementary approaches are used here:

1. YCbCr range filter (fast, broadcast-standard)
   Skin pixels in the BT.601 YCbCr space typically satisfy:
       77  ≤ Cb ≤ 127   (slightly below the neutral 128)
       133 ≤ Cr ≤ 173   (slightly above the neutral 128)
   These ranges are widely used in face-detection literature (e.g.
   "Robust Human Face Detection in Complex Color Images", Soriano et al.).

2. HSV range filter (perceptually intuitive)
   Skin tones in HSV (H°, S ∈ [0,1], V ∈ [0,1]) satisfy:
       0°  ≤ H ≤ 35°    (red-orange range)
       0.15 ≤ S ≤ 0.90  (not too grey, not too vivid)
       0.20 ≤ V ≤ 1.00  (not too dark)

   Multiple ethnic ranges are supported through the ETHNIC_RANGES table.

Deviation metric
────────────────
The "skin-tone line" is an ideal reference line in the Cb–Cr plane at a
fixed angle (~−33°).  The angular deviation of a pixel from this line
quantifies how "off-hue" the skin is:

    deviation (radians) = pixel_angle − SKIN_TONE_ANGLE_YCBCR_RAD

Pixels within ±tolerance radians are considered "on the skin tone line".
"""

from __future__ import annotations
from dataclasses import dataclass, field
import numpy as np

from color_utils import (
    rgb_to_ycbcr,
    rgb_to_hsv,
    ycbcr_to_polar,
    SKIN_TONE_ANGLE_YCBCR_RAD,
    SKIN_TONE_HUE_HSV_DEG,
)


# ---------------------------------------------------------------------------
# Ethnic skin-tone HSV ranges
# ---------------------------------------------------------------------------
# Each entry is (label, H_min°, H_max°, S_min, S_max, V_min, V_max).
# A pixel is considered a skin tone if it falls within *any* of these ranges.

@dataclass
class SkinRange:
    label: str
    h_min: float    # degrees
    h_max: float
    s_min: float    # 0–1
    s_max: float
    v_min: float    # 0–1
    v_max: float


ETHNIC_RANGES: list[SkinRange] = [
    # Very light / pale (Fitzpatrick I–II)
    SkinRange('Very Light', h_min=0,  h_max=30,  s_min=0.10, s_max=0.60, v_min=0.70, v_max=1.00),
    # Light to medium (Fitzpatrick II–III)
    SkinRange('Light',      h_min=0,  h_max=35,  s_min=0.15, s_max=0.75, v_min=0.50, v_max=1.00),
    # Medium (Fitzpatrick III–IV)
    SkinRange('Medium',     h_min=5,  h_max=40,  s_min=0.20, s_max=0.85, v_min=0.30, v_max=0.85),
    # Medium-dark to dark (Fitzpatrick IV–V)
    SkinRange('Dark',       h_min=8,  h_max=45,  s_min=0.25, s_max=0.90, v_min=0.15, v_max=0.65),
    # Very dark (Fitzpatrick VI)
    SkinRange('Very Dark',  h_min=10, h_max=50,  s_min=0.15, s_max=0.85, v_min=0.08, v_max=0.45),
]


# ---------------------------------------------------------------------------
# Detector class
# ---------------------------------------------------------------------------

class SkinToneDetector:
    """Detect skin-tone pixels and compute their vectorscope statistics.

    Parameters
    ----------
    ycbcr_tolerance_deg : float
        Angular tolerance around the skin-tone line in degrees.
        Pixels whose chroma angle is within ±tolerance of the ideal
        skin-tone angle are highlighted as "on the line".
    use_hsv_filter : bool
        When True, combine the YCbCr range filter with the HSV hue/sat
        range filter for more precise detection.
    """

    def __init__(
        self,
        ycbcr_tolerance_deg: float = 15.0,
        use_hsv_filter: bool = True,
    ) -> None:
        self.ycbcr_tolerance_rad = np.deg2rad(ycbcr_tolerance_deg)
        self.use_hsv_filter      = use_hsv_filter

    # ------------------------------------------------------------------
    # Primary filter: YCbCr rectangular range
    # ------------------------------------------------------------------

    def _ycbcr_mask(self, ycbcr: np.ndarray) -> np.ndarray:
        """Return a boolean mask (H×W) for pixels in the YCbCr skin range."""
        Cb = ycbcr[..., 1]
        Cr = ycbcr[..., 2]
        return (
            (Cb >= 77)  & (Cb <= 127) &
            (Cr >= 133) & (Cr <= 173)
        )

    # ------------------------------------------------------------------
    # Secondary filter: HSV range (union across all ethnic ranges)
    # ------------------------------------------------------------------

    def _hsv_mask(self, hsv: np.ndarray, ranges: list[SkinRange] | None = None) -> np.ndarray:
        """Return a boolean mask (H×W) for pixels in *any* HSV skin range."""
        if ranges is None:
            ranges = ETHNIC_RANGES

        H = hsv[..., 0]   # degrees
        S = hsv[..., 1]
        V = hsv[..., 2]

        combined = np.zeros(H.shape, dtype=bool)
        for r in ranges:
            combined |= (
                (H >= r.h_min) & (H <= r.h_max) &
                (S >= r.s_min) & (S <= r.s_max) &
                (V >= r.v_min) & (V <= r.v_max)
            )
        return combined

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def detect(self, rgb: np.ndarray) -> dict:
        """Run full skin-tone analysis on an RGB image.

        Parameters
        ----------
        rgb : np.ndarray  shape (H, W, 3)  dtype uint8

        Returns
        -------
        dict with keys:
            'mask'          – boolean (H, W) array of detected skin pixels
            'ycbcr'         – float32 (H, W, 3) YCbCr image
            'hsv'           – float32 (H, W, 3) HSV image
            'angle'         – float32 (N,) chroma angles for skin pixels (rad)
            'radius'        – float32 (N,) chroma radii for skin pixels (0–1)
            'deviation_rad' – float32 (N,) angular deviation from skin line
            'deviation_deg' – float32 (N,) same deviation in degrees
            'mean_deviation_deg' – float  mean absolute deviation (degrees)
            'pixel_count'   – int  number of detected skin pixels
        """
        ycbcr = rgb_to_ycbcr(rgb)
        hsv   = rgb_to_hsv(rgb)

        # Build detection mask
        mask = self._ycbcr_mask(ycbcr)
        if self.use_hsv_filter:
            mask &= self._hsv_mask(hsv)

        # Polar coordinates for skin pixels only
        skin_ycbcr = ycbcr[mask]                      # (N, 3)
        angle, radius = ycbcr_to_polar(skin_ycbcr)

        # Angular deviation from the ideal skin-tone line
        deviation_rad = angle - SKIN_TONE_ANGLE_YCBCR_RAD
        # Wrap to −π … +π
        deviation_rad = (deviation_rad + np.pi) % (2 * np.pi) - np.pi
        deviation_deg = np.rad2deg(deviation_rad)

        mean_dev = float(np.mean(np.abs(deviation_deg))) if len(deviation_deg) > 0 else 0.0

        return {
            'mask':               mask,
            'ycbcr':              ycbcr,
            'hsv':                hsv,
            'angle':              angle,
            'radius':             radius,
            'deviation_rad':      deviation_rad,
            'deviation_deg':      deviation_deg,
            'mean_deviation_deg': mean_dev,
            'pixel_count':        int(np.sum(mask)),
        }

    def on_skin_line_mask(self, detection_result: dict) -> np.ndarray:
        """Return a boolean array indicating which detected skin pixels are
        within ±tolerance of the ideal skin-tone line.

        Parameters
        ----------
        detection_result : dict  output of detect()

        Returns
        -------
        np.ndarray  shape (N,)  boolean
        """
        dev = detection_result['deviation_rad']
        return np.abs(dev) <= self.ycbcr_tolerance_rad
