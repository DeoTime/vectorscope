"""
vectorscope_companion.py – External companion application for the
Vectorscope Skin Tone Analyzer Lightroom plugin.

Overview
────────
This app provides a real-time vectorscope display and skin-tone analysis
window that sits alongside Adobe Lightroom Classic.

The Lightroom plugin exports a downsampled JPEG preview to a trigger file.
This app watches that trigger file and automatically re-renders the
vectorscope whenever the file changes.

Vectorscope display
───────────────────
• The circular plot shows chroma in the YCbCr Cb–Cr plane.
• Angle from centre   → hue direction (colour)
• Distance from centre → chroma magnitude (saturation)
• Pixel density       → rendered as a 2-D histogram coloured by frequency
• A reference "skin-tone line" is drawn at ~−33° (warm orange-red)
• Detected skin-tone pixels are highlighted in a contrasting colour

Controls (left panel)
─────────────────────
• Toggle skin detection on/off
• Adjust detection tolerance (±° from skin-tone line)
• Choose colour space (YCbCr or HSV)
• Zoom / scale vectorscope
• Show/hide skin mask preview

Usage
─────
    python vectorscope_companion.py [trigger_file_path] [--watch-path PATH]

If trigger_file_path is omitted the app can load an image manually via
File > Open Image, or watch a standalone file/folder with --watch-path.

Dependencies: Pillow, NumPy, Matplotlib (see requirements.txt)
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from PIL import Image
import matplotlib
matplotlib.use('TkAgg')
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from vectorscope_renderer import VectorscopeRenderer
from skin_tone_detector import SkinToneDetector, SkinRange, ETHNIC_RANGES
from color_utils import SKIN_TONE_ANGLE_YCBCR_RAD, ycbcr_to_polar, rgb_to_ycbcr, rgb_to_hsv
from live_update import DebounceGate, file_marker, parse_trigger_image_path, resolve_watch_image


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

POLL_INTERVAL_MS   = 500      # How often to check the trigger file (ms)
MAX_PIXELS         = 400_000  # Downsample threshold for vectorscope rendering
WINDOW_TITLE       = 'Vectorscope – Skin Tone Analyzer'
DEBOUNCE_SECONDS   = 0.25
MIN_UPDATE_SECONDS = 0.50


# ---------------------------------------------------------------------------
# Helper: write PID file so the plugin can detect whether we are running
# ---------------------------------------------------------------------------

def _write_pid_file(trigger_path: str | None) -> None:
    if trigger_path is None:
        return
    pid_dir  = Path(trigger_path).parent
    pid_file = pid_dir / 'companion.pid'
    pid_dir.mkdir(parents=True, exist_ok=True)
    pid_file.write_text(str(os.getpid()))


# ---------------------------------------------------------------------------
# Image loading & downsampling
# ---------------------------------------------------------------------------

def load_image(path: str) -> np.ndarray | None:
    """Load an image from disk and return an (H, W, 3) uint8 RGB array.

    Downsamples large images so that the total number of pixels is at most
    MAX_PIXELS, which keeps vectorscope rendering fast while preserving
    colour distribution accuracy.
    """
    try:
        img = Image.open(path).convert('RGB')
    except Exception as exc:
        print(f'[companion] Could not open image: {exc}')
        return None

    w, h = img.size
    total = w * h
    if total > MAX_PIXELS:
        scale = (MAX_PIXELS / total) ** 0.5
        new_w = max(1, int(w * scale))
        new_h = max(1, int(h * scale))
        img = img.resize((new_w, new_h), Image.LANCZOS)

    return np.asarray(img, dtype=np.uint8)


# ---------------------------------------------------------------------------
# Main application window
# ---------------------------------------------------------------------------

class VectorscopeApp:
    """Main Tkinter application window."""

    def __init__(
        self,
        root: tk.Tk,
        trigger_path: str | None = None,
        watch_path: str | None = None,
    ) -> None:
        self.root                 = root
        self.trigger_path         = trigger_path
        self._suppressed_watch_path = watch_path if (trigger_path and watch_path) else None
        self.watch_path           = watch_path if trigger_path is None else None
        self._current_rgb         = None  # most recently loaded image
        self._detector            = SkinToneDetector()
        self._detection           = None

        self._trigger_gate = DebounceGate(DEBOUNCE_SECONDS, MIN_UPDATE_SECONDS)
        self._watch_gate   = DebounceGate(DEBOUNCE_SECONDS, MIN_UPDATE_SECONDS)
        self._last_watch_image: Path | None = None

        root.title(WINDOW_TITLE)
        root.configure(bg='#1a1a1a')
        root.minsize(900, 600)

        self._build_ui()
        self._start_file_watcher()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        """Build the left control panel and the right Matplotlib canvas."""
        # ── Top menu bar ──
        menubar = tk.Menu(self.root, bg='#2a2a2a', fg='white')
        file_menu = tk.Menu(menubar, tearoff=0, bg='#2a2a2a', fg='white')
        file_menu.add_command(label='Open Image…', command=self._open_image)
        file_menu.add_separator()
        file_menu.add_command(label='Export Analysis…', command=self._export_analysis)
        file_menu.add_separator()
        file_menu.add_command(label='Quit', command=self.root.quit)
        menubar.add_cascade(label='File', menu=file_menu)
        self.root.config(menu=menubar)

        # ── Main layout: controls left, canvas right ──
        paned = tk.PanedWindow(
            self.root, orient=tk.HORIZONTAL,
            bg='#1a1a1a', sashwidth=4, sashrelief=tk.FLAT,
        )
        paned.pack(fill=tk.BOTH, expand=True)

        # Left control panel
        ctrl_frame = tk.Frame(paned, bg='#222', width=220)
        paned.add(ctrl_frame, minsize=200)
        self._build_controls(ctrl_frame)

        # Right canvas
        canvas_frame = tk.Frame(paned, bg='#1a1a1a')
        paned.add(canvas_frame, minsize=500)
        self._build_canvas(canvas_frame)

    def _build_controls(self, parent: tk.Frame) -> None:
        """Build the left-side control widgets."""
        lbl_kw  = dict(bg='#222', fg='white', font=('Helvetica', 10))
        sect_kw = dict(bg='#222', fg='#888', font=('Helvetica', 9, 'bold'))
        sep_kw  = dict(bg='#444', height=1)

        pad = dict(padx=10, pady=4)

        tk.Label(
            parent, text='Vectorscope Controls',
            **{**lbl_kw, 'font': ('Helvetica', 11, 'bold')},
        ).pack(fill=tk.X, **pad)
        tk.Frame(parent, **sep_kw).pack(fill=tk.X, padx=8, pady=2)

        # ── Colour space ──
        tk.Label(parent, text='COLOUR SPACE', **sect_kw).pack(anchor='w', **pad)
        self._cs_var = tk.StringVar(value='ycbcr')
        for text, val in [('YCbCr (broadcast)', 'ycbcr'), ('HSV (perceptual)', 'hsv')]:
            tk.Radiobutton(
                parent, text=text, variable=self._cs_var, value=val,
                bg='#222', fg='white', selectcolor='#333',
                activebackground='#333', activeforeground='white',
                command=self._on_settings_changed,
            ).pack(anchor='w', padx=14, pady=1)

        tk.Frame(parent, **sep_kw).pack(fill=tk.X, padx=8, pady=6)

        # ── Skin detection toggle ──
        tk.Label(parent, text='SKIN DETECTION', **sect_kw).pack(anchor='w', **pad)
        self._skin_var = tk.BooleanVar(value=True)
        tk.Checkbutton(
            parent, text='Enable skin detection',
            variable=self._skin_var,
            bg='#222', fg='white', selectcolor='#333',
            activebackground='#333', activeforeground='white',
            command=self._on_settings_changed,
        ).pack(anchor='w', padx=14, pady=1)

        self._mask_var = tk.BooleanVar(value=True)
        tk.Checkbutton(
            parent, text='Show skin mask preview',
            variable=self._mask_var,
            bg='#222', fg='white', selectcolor='#333',
            activebackground='#333', activeforeground='white',
            command=self._on_settings_changed,
        ).pack(anchor='w', padx=14, pady=1)

        tk.Frame(parent, **sep_kw).pack(fill=tk.X, padx=8, pady=6)

        # ── Tolerance ──
        tk.Label(parent, text='TOLERANCE (±°)', **sect_kw).pack(anchor='w', **pad)
        self._tol_var = tk.DoubleVar(value=15.0)
        ttk.Scale(
            parent, from_=1, to=45, variable=self._tol_var,
            orient=tk.HORIZONTAL, command=lambda _: self._on_settings_changed(),
        ).pack(fill=tk.X, padx=14, pady=2)
        self._tol_label = tk.Label(parent, text='15.0°', **lbl_kw)
        self._tol_label.pack(anchor='w', padx=14)

        tk.Frame(parent, **sep_kw).pack(fill=tk.X, padx=8, pady=6)

        # ── Zoom ──
        tk.Label(parent, text='ZOOM', **sect_kw).pack(anchor='w', **pad)
        self._zoom_var = tk.DoubleVar(value=1.0)
        ttk.Scale(
            parent, from_=0.5, to=4.0, variable=self._zoom_var,
            orient=tk.HORIZONTAL, command=lambda _: self._on_settings_changed(),
        ).pack(fill=tk.X, padx=14, pady=2)
        self._zoom_label = tk.Label(parent, text='1.0×', **lbl_kw)
        self._zoom_label.pack(anchor='w', padx=14)

        tk.Frame(parent, **sep_kw).pack(fill=tk.X, padx=8, pady=6)

        # ── Ethnic range filter ──
        tk.Label(parent, text='SKIN RANGE', **sect_kw).pack(anchor='w', **pad)
        self._range_var = tk.StringVar(value='All')
        range_cb = ttk.Combobox(
            parent, textvariable=self._range_var,
            values=['All'] + [r.label for r in ETHNIC_RANGES],
            state='readonly', width=22,
        )
        range_cb.pack(padx=14, pady=2, anchor='w')
        range_cb.bind('<<ComboboxSelected>>', lambda _: self._on_settings_changed())

        tk.Frame(parent, **sep_kw).pack(fill=tk.X, padx=8, pady=6)

        # ── Refresh button ──
        ttk.Button(
            parent, text='Refresh',
            command=self._refresh,
        ).pack(fill=tk.X, padx=14, pady=4)

        # ── Status section ──
        self._last_update_var = tk.StringVar(value='Last update: none')
        tk.Label(
            parent, textvariable=self._last_update_var,
            bg='#111', fg='#7ec8ff', font=('Helvetica', 8),
            wraplength=190, justify=tk.LEFT, anchor='nw',
        ).pack(fill=tk.X, padx=8, pady=(12, 2), side=tk.BOTTOM)

        self._status_var = tk.StringVar(
            value='Ready. Open an image or wait for Lightroom.',
        )
        tk.Label(
            parent, textvariable=self._status_var,
            bg='#111', fg='#aaa', font=('Helvetica', 8),
            wraplength=190, justify=tk.LEFT, anchor='nw',
        ).pack(fill=tk.X, padx=8, pady=(2, 4), side=tk.BOTTOM)

    def _build_canvas(self, parent: tk.Frame) -> None:
        """Create the Matplotlib figure and embed it in a Tkinter canvas."""
        self._fig      = Figure(figsize=(9, 6), dpi=100)
        self._renderer = VectorscopeRenderer(self._fig)

        self._canvas = FigureCanvasTkAgg(self._fig, master=parent)
        self._canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)

    # ------------------------------------------------------------------
    # Settings / event handlers
    # ------------------------------------------------------------------

    def _set_status(self, text: str) -> None:
        self._status_var.set(text)

    def _mark_update(self, source: str, path: str | None = None) -> None:
        ts = datetime.now(timezone.utc).astimezone().strftime('%H:%M:%S')
        suffix = f' – {Path(path).name}' if path else ''
        self._last_update_var.set(f'Last update: {ts} ({source}){suffix}')

    def _on_settings_changed(self) -> None:
        """Called whenever a control value changes; re-renders if image loaded."""
        tol = self._tol_var.get()
        self._tol_label.config(text=f'{tol:.0f}°')
        self._zoom_label.config(text=f'{self._zoom_var.get():.1f}×')

        self._detector.ycbcr_tolerance_rad = np.deg2rad(tol)

        if self._current_rgb is not None:
            self._render()

    def _refresh(self) -> None:
        """Manually trigger a re-render with the current image."""
        if self._current_rgb is not None:
            self._detect_and_render(self._current_rgb, source='Manual refresh')
        else:
            self._set_status('No image loaded.')

    # ------------------------------------------------------------------
    # Image loading
    # ------------------------------------------------------------------

    def _open_image(self) -> None:
        """Open a file dialog to load an image manually."""
        path = filedialog.askopenfilename(
            title='Open Image',
            filetypes=[
                ('Images', '*.jpg *.jpeg *.png *.tiff *.tif *.bmp *.webp'),
                ('All files', '*.*'),
            ],
        )
        if path:
            self._load_and_render(path, source='Manual open')

    def _load_and_render(self, path: str, source: str = 'Image load') -> None:
        """Load an image from disk and render the vectorscope."""
        self._set_status(f'Loading {Path(path).name}…')
        rgb = load_image(path)
        if rgb is None:
            self._set_status('Failed to load image.')
            return
        self._detect_and_render(rgb, source=source, path=path)
        self._set_status(
            f'Loaded: {Path(path).name}  ({rgb.shape[1]}×{rgb.shape[0]} px)',
        )

    def _detect_and_render(
        self,
        rgb: np.ndarray,
        source: str = 'Update',
        path: str | None = None,
    ) -> None:
        """Run skin detection and render the vectorscope."""
        self._current_rgb = rgb

        show_skin = self._skin_var.get()
        if show_skin:
            selected_range = self._range_var.get()
            if selected_range == 'All':
                ranges = None
            else:
                ranges = [r for r in ETHNIC_RANGES if r.label == selected_range]
            self._detection = _detect_with_ranges(self._detector, rgb, ranges)
        else:
            self._detection = None

        self._render()
        self._mark_update(source, path=path)

    def _render(self) -> None:
        """Render the vectorscope with current settings."""
        if self._current_rgb is None:
            return
        self._renderer.render(
            self._current_rgb,
            self._detection if self._skin_var.get() else None,
            colour_space  = self._cs_var.get(),
            show_mask     = self._mask_var.get(),
            zoom          = self._zoom_var.get(),
            show_skin     = self._skin_var.get(),
            tolerance_deg = self._tol_var.get(),
        )

    # ------------------------------------------------------------------
    # Input watchers (trigger file and optional standalone watch-path)
    # ------------------------------------------------------------------

    def _start_file_watcher(self) -> None:
        """Start polling configured live-update sources."""
        if self.trigger_path:
            self._set_status('Lightroom trigger mode active.')
            if self._suppressed_watch_path:
                print(
                    '[companion] --watch-path ignored because trigger_file mode is active.',
                    file=sys.stderr,
                )
        elif self.watch_path:
            self._set_status(f'Standalone watch mode active: {self.watch_path}')
        self._poll_inputs()

    def _poll_inputs(self) -> None:
        """Check all update sources and load changed images."""
        self._poll_trigger_source()
        self._poll_watch_source()
        self.root.after(POLL_INTERVAL_MS, self._poll_inputs)

    def _poll_trigger_source(self) -> None:
        """Handle Lightroom trigger-file updates."""
        if not self.trigger_path:
            return

        trigger_file = Path(self.trigger_path)
        marker = file_marker(trigger_file)
        if not self._trigger_gate.update(marker):
            return

        try:
            img_path = parse_trigger_image_path(trigger_file.read_text())
            if img_path and Path(img_path).exists():
                self._load_and_render(img_path, source='Lightroom trigger')
            else:
                self._set_status('Trigger updated, but no valid image path found.')
        except Exception as exc:
            print(f'[companion] Trigger file error: {exc}')

    def _poll_watch_source(self) -> None:
        """Handle standalone watch-path updates (file or folder)."""
        if self.trigger_path or not self.watch_path:
            return

        watch_target = Path(self.watch_path)
        try:
            image_path = resolve_watch_image(watch_target)
            marker = (
                str(image_path),
                file_marker(image_path) if image_path else None,
            )
            if not self._watch_gate.update(marker):
                return

            if image_path and image_path.exists():
                self._last_watch_image = image_path
                self._load_and_render(str(image_path), source='Standalone watch')
            else:
                self._set_status(f'Waiting for image files in: {watch_target}')
        except Exception as exc:
            print(f'[companion] Watch-path error: {exc}')

    # ------------------------------------------------------------------
    # Export analysis
    # ------------------------------------------------------------------

    def _export_analysis(self) -> None:
        """Export analysis results to a text file."""
        if self._detection is None:
            messagebox.showinfo(
                'Export', 'No analysis data to export. Load an image first.',
            )
            return

        path = filedialog.asksaveasfilename(
            title='Export Analysis',
            defaultextension='.txt',
            filetypes=[('Text files', '*.txt'), ('All files', '*.*')],
        )
        if not path:
            return

        det   = self._detection
        lines = [
            'Vectorscope Skin Tone Analysis',
            '================================',
            f'Skin pixels detected : {det["pixel_count"]:,}',
            f'Mean deviation       : {det["mean_deviation_deg"]:.2f}°',
        ]
        if len(det['deviation_deg']) > 0:
            dev = det['deviation_deg']
            lines += [
                f'Min deviation        : {dev.min():.2f}°',
                f'Max deviation        : {dev.max():.2f}°',
                f'Std deviation        : {dev.std():.2f}°',
                f'Within ±15°          : {100*np.mean(np.abs(dev)<=15):.1f} %',
                f'Within ±10°          : {100*np.mean(np.abs(dev)<=10):.1f} %',
                f'Within ±5°           : {100*np.mean(np.abs(dev)<=5):.1f} %',
            ]
        lines += [
            '',
            'Colour space  : ' + self._cs_var.get().upper(),
            'Tolerance     : ' + f'{self._tol_var.get():.0f}°',
            'Skin range    : ' + self._range_var.get(),
        ]

        try:
            Path(path).write_text('\n'.join(lines))
            messagebox.showinfo('Export', f'Analysis saved to:\n{path}')
        except Exception as exc:
            messagebox.showerror('Export Error', str(exc))


# ---------------------------------------------------------------------------
# Module-level helper (used by VectorscopeApp and importable by tests)
# ---------------------------------------------------------------------------

def _detect_with_ranges(
    detector: SkinToneDetector,
    rgb: np.ndarray,
    ranges: list[SkinRange] | None,
) -> dict:
    """Run skin detection, optionally restricted to specific ethnic ranges."""
    if ranges is None:
        return detector.detect(rgb)

    # Run with a custom range subset
    ycbcr = rgb_to_ycbcr(rgb)
    hsv   = rgb_to_hsv(rgb)
    mask  = detector._ycbcr_mask(ycbcr)
    if detector.use_hsv_filter:
        mask &= detector._hsv_mask(hsv, ranges)
    skin_ycbcr    = ycbcr[mask]
    angle, radius = ycbcr_to_polar(skin_ycbcr)
    deviation_rad = angle - SKIN_TONE_ANGLE_YCBCR_RAD
    deviation_rad = (deviation_rad + np.pi) % (2 * np.pi) - np.pi
    deviation_deg = np.rad2deg(deviation_rad)
    mean_dev      = float(np.mean(np.abs(deviation_deg))) if len(deviation_deg) > 0 else 0.0
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


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description='Vectorscope Skin Tone Analyzer – companion app for Lightroom',
    )
    parser.add_argument(
        'trigger_file', nargs='?', default=None,
        help='Path to the IPC trigger file written by the Lightroom plugin.',
    )
    parser.add_argument(
        '--watch-path', default=None,
        help='Standalone mode: watch an image file or folder for live updates.',
    )
    args = parser.parse_args()

    trigger = args.trigger_file
    _write_pid_file(trigger)

    root = tk.Tk()
    app  = VectorscopeApp(root, trigger_path=trigger, watch_path=args.watch_path)

    # If a trigger file already contains a valid image path, load it now
    if trigger and Path(trigger).exists():
        try:
            img_path = parse_trigger_image_path(Path(trigger).read_text())
            if img_path and Path(img_path).exists():
                app._load_and_render(img_path, source='Initial trigger')
        except Exception:
            pass

    root.mainloop()


if __name__ == '__main__':
    main()
