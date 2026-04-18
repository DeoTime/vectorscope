"""
vectorscope_renderer.py – Pure-Matplotlib vectorscope rendering.

This module contains only the rendering logic (Figure + Axes manipulation).
It has **no** dependency on Tkinter, so it can be unit-tested in headless
environments and reused from non-GUI contexts.

The Tkinter application in vectorscope_companion.py imports this module and
embeds the Figure in its window via FigureCanvasTkAgg.
"""

from __future__ import annotations

import numpy as np
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.colors import LogNorm
from matplotlib.figure import Figure

from color_utils import (
    rgb_to_ycbcr,
    rgb_to_hsv,
    ycbcr_to_polar,
    hsv_to_polar,
    SKIN_TONE_ANGLE_YCBCR_RAD,
    SKIN_TONE_ANGLE_HSV_RAD,
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VECTORSCOPE_BINS = 256   # Histogram resolution (bins per axis)


# ---------------------------------------------------------------------------
# Vectorscope renderer
# ---------------------------------------------------------------------------

class VectorscopeRenderer:
    """Renders the vectorscope onto a Matplotlib Figure.

    Parameters
    ----------
    figure : matplotlib.figure.Figure
        The Figure to draw into.  The caller is responsible for embedding it
        in a Tkinter, Qt, or other backend canvas.
    """

    def __init__(self, figure: Figure) -> None:
        self.figure = figure
        self._setup_axes()

    def _setup_axes(self) -> None:
        """Create the main vectorscope axes, mask preview, and stats axes."""
        self.figure.clear()
        self.figure.patch.set_facecolor('#1a1a1a')

        # Main vectorscope axis
        self.ax_scope = self.figure.add_axes(
            [0.05, 0.08, 0.58, 0.88],
            facecolor='#0d0d0d',
        )
        self.ax_scope.set_aspect('equal')

        # Skin mask preview axis
        self.ax_mask = self.figure.add_axes(
            [0.67, 0.45, 0.30, 0.50],
            facecolor='#0d0d0d',
        )
        self.ax_mask.set_xticks([])
        self.ax_mask.set_yticks([])
        self.ax_mask.set_title('Skin Mask', color='white', fontsize=9)

        # Statistics text axis
        self.ax_stats = self.figure.add_axes(
            [0.67, 0.05, 0.30, 0.36],
            facecolor='#1a1a1a',
        )
        self.ax_stats.set_xticks([])
        self.ax_stats.set_yticks([])
        for spine in self.ax_stats.spines.values():
            spine.set_edgecolor('#444')

    def render(
        self,
        rgb: np.ndarray,
        detection: dict | None,
        *,
        colour_space: str = 'ycbcr',
        show_mask: bool = True,
        zoom: float = 1.0,
        show_skin: bool = True,
        tolerance_deg: float = 15.0,
    ) -> None:
        """Render the vectorscope, optional skin mask, and statistics.

        Parameters
        ----------
        rgb           : np.ndarray  (H, W, 3) uint8
            Input image in RGB colour space.
        detection     : dict | None
            Output of SkinToneDetector.detect(), or None to disable skin
            highlighting.
        colour_space  : str
            'ycbcr' (broadcast standard) or 'hsv' (perceptual).
        show_mask     : bool
            Whether to show the skin-mask preview panel.
        zoom          : float
            Scale factor for the vectorscope radius axis (≥ 1 zooms in).
        show_skin     : bool
            Whether to draw the skin-tone line and overlay.
        tolerance_deg : float
            Half-width of the tolerance wedge around the skin-tone line.
        """
        self._setup_axes()
        ax = self.ax_scope

        # ------------------------------------------------------------------
        # 1. Compute polar coordinates for ALL pixels
        # ------------------------------------------------------------------
        flat_rgb = rgb.reshape(-1, 3)

        if colour_space == 'ycbcr':
            ycbcr_flat = rgb_to_ycbcr(flat_rgb.reshape(-1, 1, 3)).reshape(-1, 3)
            angle_all, radius_all = ycbcr_to_polar(ycbcr_flat)
            skin_line_angle  = SKIN_TONE_ANGLE_YCBCR_RAD
            axis_label_x = 'Cr (red-diff)'
            axis_label_y = 'Cb (blue-diff)'
        else:  # hsv
            hsv_flat = rgb_to_hsv(flat_rgb.reshape(-1, 1, 3)).reshape(-1, 3)
            angle_all, radius_all = hsv_to_polar(hsv_flat)
            skin_line_angle  = SKIN_TONE_ANGLE_HSV_RAD
            axis_label_x = 'Saturation × cos(H)'
            axis_label_y = 'Saturation × sin(H)'

        # ------------------------------------------------------------------
        # 2. 2-D density histogram
        # ------------------------------------------------------------------
        r_max = 1.0 / zoom
        x_all = radius_all * np.cos(angle_all)
        y_all = radius_all * np.sin(angle_all)

        bins  = VECTORSCOPE_BINS
        edges = np.linspace(-r_max, r_max, bins + 1)
        hist, xe, ye = np.histogram2d(x_all, y_all, bins=[edges, edges])
        hist = hist.T

        # Mask pixels outside the visible circle
        xc = 0.5 * (xe[:-1] + xe[1:])
        yc = 0.5 * (ye[:-1] + ye[1:])
        XX, YY = np.meshgrid(xc, yc)
        outside = (XX ** 2 + YY ** 2) > r_max ** 2
        hist[outside] = 0

        # Log-normalised colour map so faint colours don't get lost
        hist_masked = np.ma.masked_where(hist == 0, hist)
        ax.pcolormesh(
            xe, ye, hist_masked,
            cmap='inferno',
            norm=LogNorm(vmin=1, vmax=max(hist.max(), 2.0)),
            shading='auto',
        )

        # ------------------------------------------------------------------
        # 3. Reference circles, crosshairs, hue labels
        # ------------------------------------------------------------------
        self._draw_reference(ax, r_max, axis_label_x, axis_label_y)

        # ------------------------------------------------------------------
        # 4. Skin-tone reference line and tolerance wedge
        # ------------------------------------------------------------------
        if show_skin:
            lx = [0.0, r_max * np.cos(skin_line_angle)]
            ly = [0.0, r_max * np.sin(skin_line_angle)]
            ax.plot(lx, ly, color='#FFD700', linewidth=1.8,
                    linestyle='--', label='Skin tone line', zorder=5)

            tol_rad = np.deg2rad(tolerance_deg)
            angles_wedge = np.linspace(
                skin_line_angle - tol_rad,
                skin_line_angle + tol_rad,
                60,
            )
            wx = np.concatenate([[0.0], r_max * np.cos(angles_wedge), [0.0]])
            wy = np.concatenate([[0.0], r_max * np.sin(angles_wedge), [0.0]])
            ax.fill(wx, wy, color='#FFD700', alpha=0.08, zorder=4)

        # ------------------------------------------------------------------
        # 5. Skin pixel scatter overlay
        # ------------------------------------------------------------------
        if show_skin and detection is not None and detection['pixel_count'] > 0:
            s_angle  = detection['angle']
            s_radius = detection['radius']

            max_plot = 8000
            if len(s_angle) > max_plot:
                idx      = np.random.choice(len(s_angle), max_plot, replace=False)
                s_angle  = s_angle[idx]
                s_radius = s_radius[idx]

            sx = s_radius * np.cos(s_angle)
            sy = s_radius * np.sin(s_angle)
            ax.scatter(
                sx, sy,
                s=2, c='#00FF88', alpha=0.5, linewidths=0,
                zorder=6, label='Skin pixels',
            )

        if show_skin:
            ax.legend(
                loc='upper right', fontsize=8,
                facecolor='#222', edgecolor='#555', labelcolor='white',
            )

        ax.set_xlim(-r_max, r_max)
        ax.set_ylim(-r_max, r_max)
        ax.set_title('Vectorscope', color='white', fontsize=11, pad=6)

        # ------------------------------------------------------------------
        # 6. Skin mask preview
        # ------------------------------------------------------------------
        if show_skin and show_mask and detection is not None:
            # Darken non-skin pixels; keep skin pixels at full brightness
            preview = (rgb.astype(np.float32) * 0.25).astype(np.uint8)
            preview[detection['mask']] = rgb[detection['mask']]
            self.ax_mask.imshow(preview)
            self.ax_mask.set_title('Skin Mask', color='white', fontsize=9)
        else:
            self.ax_mask.set_title('Skin Mask (off)', color='#666', fontsize=9)

        # ------------------------------------------------------------------
        # 7. Statistics panel
        # ------------------------------------------------------------------
        self._draw_stats(detection)

        self.figure.canvas.draw_idle()

    # ------------------------------------------------------------------
    # Internal drawing helpers
    # ------------------------------------------------------------------

    def _draw_reference(
        self,
        ax,
        r_max: float,
        label_x: str,
        label_y: str,
    ) -> None:
        """Draw reference circles, crosshairs, and hue-name labels."""
        theta = np.linspace(0, 2 * np.pi, 360)

        # Outer boundary
        ax.plot(
            r_max * np.cos(theta), r_max * np.sin(theta),
            color='#444', linewidth=0.8, zorder=1,
        )

        # Inner graticule circles at 25 %, 50 %, 75 %
        for frac in (0.25, 0.50, 0.75):
            r = r_max * frac
            ax.plot(
                r * np.cos(theta), r * np.sin(theta),
                color='#333', linewidth=0.5, linestyle=':', zorder=1,
            )

        # Crosshairs
        ax.axhline(0, color='#333', linewidth=0.5, zorder=1)
        ax.axvline(0, color='#333', linewidth=0.5, zorder=1)

        # Colour-name labels at approximate hue positions
        hue_labels = {
            'R':  0.0,
            'Yl': np.deg2rad(60),
            'G':  np.deg2rad(120),
            'Cy': np.deg2rad(180),
            'B':  np.deg2rad(240),
            'Mg': np.deg2rad(300),
        }
        label_r = r_max * 1.07
        for name, ang in hue_labels.items():
            ax.text(
                label_r * np.cos(ang), label_r * np.sin(ang),
                name, color='#888', fontsize=8,
                ha='center', va='center',
            )

        ax.set_xlabel(label_x, color='#888', fontsize=9)
        ax.set_ylabel(label_y, color='#888', fontsize=9)
        ax.tick_params(colors='#555', labelsize=7)
        for spine in ax.spines.values():
            spine.set_edgecolor('#333')

    def _draw_stats(self, detection: dict | None) -> None:
        """Render the statistics text in the bottom-right panel."""
        ax = self.ax_stats
        ax.clear()
        ax.set_xticks([])
        ax.set_yticks([])
        ax.set_facecolor('#1a1a1a')

        if detection is None or detection['pixel_count'] == 0:
            lines = ['No skin pixels', 'detected.']
        else:
            cnt     = detection['pixel_count']
            mean    = detection['mean_deviation_deg']
            dev     = detection['deviation_deg']
            pct_on  = (
                100.0 * float(np.sum(np.abs(dev) <= 15.0)) / len(dev)
                if len(dev) > 0 else 0.0
            )
            lines = [
                '── Skin Stats ──',
                f'Pixels detected:',
                f'  {cnt:,}',
                f'Mean deviation:',
                f'  {mean:.1f}°',
                f'Within ±15°:',
                f'  {pct_on:.1f} %',
            ]

        for i, line in enumerate(lines):
            ax.text(
                0.1, 0.92 - i * 0.13,
                line,
                transform=ax.transAxes,
                color='#cccccc',
                fontsize=9,
                va='top',
            )
