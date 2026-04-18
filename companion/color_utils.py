"""
color_utils.py – Color space conversion utilities for the vectorscope.

All functions operate on NumPy arrays for vectorised, high-performance
processing of large image data.

Colour space math
─────────────────
YCbCr (BT.601 full-range)
  Used for the vectorscope's chroma plane.  Each pixel is mapped to a 2-D
  point in the Cb–Cr plane; the distance from the centre (128, 128) is the
  chroma magnitude, and the angle is the hue direction.

HSV
  Used as an alternative representation and for skin-tone range filtering
  because its hue channel directly corresponds to perceptual colour.

Polar coordinates
  Both representations are ultimately converted to polar (angle, radius) to
  drive the vectorscope display:
    • angle  = hue direction  (radians, −π … +π)
    • radius = chroma / saturation magnitude  (0 … 1)
"""

import numpy as np


# ---------------------------------------------------------------------------
# RGB → YCbCr  (BT.601 full-range, 8-bit)
# ---------------------------------------------------------------------------

def rgb_to_ycbcr(rgb: np.ndarray) -> np.ndarray:
    """Convert an (H, W, 3) uint8 RGB array to YCbCr.

    Uses the ITU-R BT.601 matrix (same coefficients used in JPEG / MPEG):

        Y  =  0.299·R + 0.587·G + 0.114·B
        Cb = −0.169·R − 0.331·G + 0.500·B + 128
        Cr =  0.500·R − 0.419·G − 0.081·B + 128

    Y  ∈ [0, 255]  – luma (brightness)
    Cb ∈ [0, 255]  – blue-difference chroma (128 = neutral)
    Cr ∈ [0, 255]  – red-difference chroma  (128 = neutral)

    Returns an (H, W, 3) float32 array [Y, Cb, Cr].
    """
    rgb_f = rgb.astype(np.float32)
    R, G, B = rgb_f[..., 0], rgb_f[..., 1], rgb_f[..., 2]

    Y  =  0.299  * R + 0.587  * G + 0.114  * B
    Cb = -0.16874 * R - 0.33126 * G + 0.5    * B + 128.0
    Cr =  0.5    * R - 0.41869 * G - 0.08131 * B + 128.0

    return np.stack([Y, Cb, Cr], axis=-1).astype(np.float32)


# ---------------------------------------------------------------------------
# RGB → HSV  (OpenCV-compatible, H ∈ [0°, 360°])
# ---------------------------------------------------------------------------

def rgb_to_hsv(rgb: np.ndarray) -> np.ndarray:
    """Convert an (H, W, 3) uint8 RGB array to HSV.

    Returns an (H, W, 3) float32 array where:
        H ∈ [0, 360)   – hue in degrees
        S ∈ [0,   1]   – saturation (0 = grey, 1 = fully saturated)
        V ∈ [0,   1]   – value / brightness

    This is a pure-NumPy implementation that avoids the opencv dependency
    for the conversion step (opencv is still used for image I/O).
    """
    rgb_f = rgb.astype(np.float32) / 255.0
    R, G, B = rgb_f[..., 0], rgb_f[..., 1], rgb_f[..., 2]

    Cmax = np.maximum(np.maximum(R, G), B)
    Cmin = np.minimum(np.minimum(R, G), B)
    delta = Cmax - Cmin

    # --- Value ---
    V = Cmax

    # --- Saturation ---
    # Use a safe divisor to avoid divide-by-zero warnings on pure-black pixels
    # (Cmax == 0).  The np.where branch selects 0.0 for those pixels anyway,
    # but NumPy still evaluates the true-branch expression first.
    safe_max = np.where(Cmax > 0, Cmax, 1.0)
    S = np.where(Cmax > 0, delta / safe_max, 0.0)

    # --- Hue ---
    H = np.zeros_like(R)

    # Masks for which channel is the maximum
    mask_r = (Cmax == R) & (delta > 0)
    mask_g = (Cmax == G) & (delta > 0)
    mask_b = (Cmax == B) & (delta > 0)

    H[mask_r] = 60.0 * (((G[mask_r] - B[mask_r]) / delta[mask_r]) % 6)
    H[mask_g] = 60.0 * (((B[mask_g] - R[mask_g]) / delta[mask_g]) + 2)
    H[mask_b] = 60.0 * (((R[mask_b] - G[mask_b]) / delta[mask_b]) + 4)

    return np.stack([H, S, V], axis=-1).astype(np.float32)


# ---------------------------------------------------------------------------
# Polar coordinates from YCbCr
# ---------------------------------------------------------------------------

def ycbcr_to_polar(ycbcr: np.ndarray):
    """Convert YCbCr pixels to vectorscope polar coordinates.

    The vectorscope displays chroma in a 2-D circular plot:
        • The centre of the circle represents achromatic (grey) pixels.
        • The angle from the centre represents the hue direction.
        • The distance from the centre represents the chroma magnitude
          (i.e. colour saturation).

    Math:
        dCb = Cb − 128        (signed blue-difference chroma)
        dCr = Cr − 128        (signed red-difference chroma)
        angle  = atan2(dCb, dCr)          ∈ (−π, +π]
        radius = √(dCb² + dCr²) / 127    ∈ [0, 1]  (normalised to max 127)

    Parameters
    ----------
    ycbcr : np.ndarray  shape (N, 3) or (H, W, 3), float32
        YCbCr values in the 0–255 range.

    Returns
    -------
    angle  : np.ndarray  shape (N,)   radians, −π … +π
    radius : np.ndarray  shape (N,)   normalised chroma 0 … 1
    """
    flat = ycbcr.reshape(-1, 3)
    dCb  = flat[:, 1] - 128.0   # signed blue-difference
    dCr  = flat[:, 2] - 128.0   # signed red-difference

    angle  = np.arctan2(dCb, dCr)                   # hue direction (radians)
    radius = np.sqrt(dCb ** 2 + dCr ** 2) / 127.0   # normalised chroma

    return angle, radius


# ---------------------------------------------------------------------------
# Polar coordinates from HSV
# ---------------------------------------------------------------------------

def hsv_to_polar(hsv: np.ndarray):
    """Convert HSV pixels to vectorscope polar coordinates.

    This HSV-based vectorscope maps:
        angle  = hue in radians        H° × π/180
        radius = saturation            S ∈ [0, 1]

    Parameters
    ----------
    hsv : np.ndarray  shape (N, 3) or (H, W, 3), float32
        HSV with H ∈ [0°, 360°), S ∈ [0,1], V ∈ [0,1].

    Returns
    -------
    angle  : np.ndarray  shape (N,)   radians 0 … 2π
    radius : np.ndarray  shape (N,)   saturation 0 … 1
    """
    flat   = hsv.reshape(-1, 3)
    angle  = np.deg2rad(flat[:, 0])   # hue → radians
    radius = flat[:, 1]               # saturation

    return angle, radius


# ---------------------------------------------------------------------------
# Skin-tone line reference angle
# ---------------------------------------------------------------------------

# The "flesh line" is a well-known reference line in broadcast vectorscopes.
# In YCbCr (BT.601) space it passes through the average chroma angle of
# human skin tones across a range of luminances and ethnicities.
#
# Empirical measurements put the angle at approximately:
#   atan2(Cb − 128, Cr − 128) ≈ −33°  (≈ −0.576 radians)
# which corresponds to a warm orange-red direction in the Cb–Cr plane.
#
# In HSV space the equivalent hue is approximately 20° (warm orange).
#
# Both constants are exposed so callers can use whichever space they prefer.

SKIN_TONE_ANGLE_YCBCR_RAD = np.deg2rad(-33.0)   # ≈ −0.576 rad
SKIN_TONE_HUE_HSV_DEG      = 20.0                # degrees
SKIN_TONE_ANGLE_HSV_RAD    = np.deg2rad(SKIN_TONE_HUE_HSV_DEG)
