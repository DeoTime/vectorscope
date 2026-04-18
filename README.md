# Vectorscope Skin Tone Analyzer

A vectorscope-style visualization tool for analyzing skin tones in
**Adobe Lightroom Classic**.  The project consists of two parts:

1. **Lightroom Classic plugin** (`vectorscope.lrplugin/`) – written in Lua
   using the official LR SDK.  It exports a preview of the currently
   selected image and launches / signals the companion app.
2. **Python companion app** (`companion/`) – standalone GUI application
   (Tkinter + Matplotlib + Pillow + NumPy) that renders the vectorscope,
   detects skin tones, and shows live statistics with update-source metadata.

---

## Table of contents

- [Architecture](#architecture)
- [Installation](#installation)
  - [1. Plugin (Lightroom Classic)](#1-plugin-lightroom-classic)
  - [2. Python companion app](#2-python-companion-app)
- [Usage](#usage)
- [Vectorscope math](#vectorscope-math)
- [Skin tone detection](#skin-tone-detection)
- [UI controls](#ui-controls)
- [Performance notes](#performance-notes)
- [Optional enhancements](#optional-enhancements)
- [File structure](#file-structure)

---

## Architecture

```
┌─────────────────────────────────────────┐
│         Adobe Lightroom Classic          │
│                                          │
│  [File > Plug-in Extras >                │
│   Open Vectorscope Analyzer]             │
│           │                              │
│  VectorscopeMain.lua                     │
│     │                                   │
│  ExportPreview.lua ──► JPEG preview      │
│     │                  (1024 px max)     │
│  CompanionBridge.lua ──► trigger file    │
└─────────────────┬───────────────────────┘
                  │  file-based IPC
                  ▼
┌─────────────────────────────────────────┐
│      Python companion app               │
│  vectorscope_companion.py               │
│                                         │
│  ┌────────────┐  ┌─────────────────┐   │
│  │color_utils │  │skin_tone_       │   │
│  │.py         │  │detector.py      │   │
│  │            │  │                 │   │
│  │RGB→YCbCr   │  │ YCbCr ranges    │   │
│  │RGB→HSV     │  │ HSV ranges      │   │
│  │polar math  │  │ deviation calc  │   │
│  └────────────┘  └─────────────────┘   │
│                                         │
│  Tkinter UI + Matplotlib rendering      │
└─────────────────────────────────────────┘
```

**IPC protocol** – file-based, no sockets required:

1. Plugin exports a JPEG preview to `vectorscope_tmp/vectorscope_preview.jpg`.
2. Plugin writes the image path + timestamp to `vectorscope_tmp/vectorscope_trigger.txt`.
3. Companion app polls the trigger file every 500 ms; when the trigger changes it
   reloads the image and re-renders the vectorscope (with debounce/throttle).
4. Companion app writes its PID to `vectorscope_tmp/companion.pid` so the
   plugin can detect whether the app is already running.

---

## Installation

### 1. Plugin (Lightroom Classic)

1. Download or clone this repository.
2. In Lightroom Classic go to **File > Plug-in Manager…**.
3. Click **Add**, navigate to the `vectorscope.lrplugin/` folder inside the
   cloned repository, and click **Add Plug-in**.
4. The plugin status should show **Installed and running**.
5. The menu item **File > Plug-in Extras > Open Vectorscope Analyzer** will
   now be available in both the Library and Develop modules.

### 2. Python companion app

Python 3.9 or higher is required.

```bash
# Install dependencies
pip install -r companion/requirements.txt

# Run standalone (without Lightroom)
python companion/vectorscope_companion.py
```

The companion app can also be launched automatically by the Lightroom plugin.
The plugin calls `python3 companion/vectorscope_companion.py <trigger_file>`
(on macOS/Linux) or `pythonw companion/vectorscope_companion.py <trigger_file>`
(on Windows).

Standalone live watch mode is also available:

```bash
python companion/vectorscope_companion.py --watch-path /path/to/image-or-folder
```

**Tip:** To avoid managing a virtual environment, you can install the
packages globally or use `pipx`.

---

## Usage

### With Lightroom Classic

1. Select a photo in the Library or Develop module.
2. Go to **File > Plug-in Extras > Open Vectorscope Analyzer**.
3. The control dialog opens with **Auto Live mode enabled**.
4. Lightroom exports a JPEG preview automatically whenever the selected photo
   or core develop settings change (debounced + throttled).
5. Use **Refresh Now** for manual fallback, or **Stop Live / Start Live**
   to control automatic updates.

### Standalone (without Lightroom)

```bash
python companion/vectorscope_companion.py
```

Use **File > Open Image…** to load any JPEG, PNG, TIFF, or BMP file.
Or use:

```bash
python companion/vectorscope_companion.py --watch-path /path/to/image-or-folder
```

to continuously monitor one image file or the newest image inside a folder.

---

## Vectorscope math

### YCbCr colour space (default)

The vectorscope displays chroma in the **Cb–Cr plane** of the YCbCr colour
space (ITU-R BT.601 full-range):

```
Y  =  0.299·R + 0.587·G + 0.114·B          (luma / brightness)
Cb = −0.169·R − 0.331·G + 0.500·B + 128   (blue-difference chroma)
Cr =  0.500·R − 0.419·G − 0.081·B + 128   (red-difference chroma)
```

A pixel with no colour (grey) maps to `(Cb, Cr) = (128, 128)` – the centre
of the vectorscope.

### Polar coordinates

```
dCb    = Cb − 128                           (signed chroma, blue axis)
dCr    = Cr − 128                           (signed chroma, red axis)
angle  = atan2(dCb, dCr)                    (hue direction, −π … +π)
radius = √(dCb² + dCr²) / 127              (normalised chroma, 0 … 1)
```

- **Angle** encodes hue direction (which colour).
- **Radius** encodes chroma magnitude (how saturated).
- **Pixel density** is rendered as a 2-D log-scaled histogram (bright = many
  pixels at that chroma coordinate).

### HSV alternative

Switching to HSV in the UI maps `hue → angle` and `saturation → radius`
directly.  This is more intuitive for artists but less standard in broadcast
practice.

---

## Skin tone detection

### Skin-tone line

The **flesh line** is a reference line in the vectorscope that passes through
the average chroma angle of human skin at approximately **−33°** in the
YCbCr Cb–Cr plane (warm orange-red direction).  This is drawn as a dashed
gold line on the vectorscope.

A shaded wedge shows the **tolerance zone** (±° configurable).

### Detection heuristics

Two complementary range filters are applied:

**1. YCbCr rectangular range** (broadcast-standard):
```
77  ≤ Cb ≤ 127   (slightly sub-neutral blue chroma)
133 ≤ Cr ≤ 173   (slightly super-neutral red chroma)
```

**2. HSV range filter** (union across ethnic range table):

| Range      | H (°)   | S       | V       | Fitzpatrick |
|------------|---------|---------|---------|-------------|
| Very Light | 0–30    | 0.10–0.60 | 0.70–1.00 | I–II    |
| Light      | 0–35    | 0.15–0.75 | 0.50–1.00 | II–III  |
| Medium     | 5–40    | 0.20–0.85 | 0.30–0.85 | III–IV  |
| Dark       | 8–45    | 0.25–0.90 | 0.15–0.65 | IV–V    |
| Very Dark  | 10–50   | 0.15–0.85 | 0.08–0.45 | VI      |

### Deviation metric

For each detected skin pixel the **angular deviation from the skin-tone line**
is computed:

```
deviation (°) = pixel_angle − SKIN_TONE_ANGLE  (wrapped to ±180°)
```

The statistics panel shows mean deviation, percentage of pixels within ±15°,
and can be exported to a text file via **File > Export Analysis…**.

---

## UI controls

| Control | Description |
|---------|-------------|
| **Colour space** | YCbCr (broadcast standard) or HSV (perceptual) |
| **Enable skin detection** | Toggle skin pixel overlay on/off |
| **Show skin mask preview** | Show a mini-preview with detected skin highlighted |
| **Tolerance (±°)** | Width of the skin-tone tolerance wedge (1°–45°) |
| **Zoom** | Scale the vectorscope radius axis (0.5×–4×) |
| **Skin range** | Filter detection to a specific Fitzpatrick range |
| **Refresh** | Re-render with current settings |
| **Last update** | Shows timestamp + source (manual, Lightroom trigger, watch mode) |
| **File > Open Image…** | Load any image manually |
| **File > Export Analysis…** | Save analysis statistics to a `.txt` file |

---

## Performance notes

- Images are **downsampled** to ≤ 400 000 pixels before vectorscope
  rendering.  This is sufficient for accurate colour analysis while keeping
  frame times well under 1 second on modern hardware.
- The Lightroom plugin exports at **1024 px long edge**, so the companion
  app typically processes ≤ 786 k pixels.
- The 2-D histogram is computed with NumPy's `histogram2d` (C-optimised)
  and displayed with Matplotlib's `pcolormesh` – no per-pixel Python loops.

---

## Optional enhancements

- **Numeric deviation display** – shown in the statistics panel and
  exportable to text.
- **Multiple ethnic skin-tone ranges** – the `ETHNIC_RANGES` table in
  `skin_tone_detector.py` can be extended; the UI selector exposes each range.
- **Export analysis** – via **File > Export Analysis…**.
- The companion app can be run **independently** of Lightroom by pointing it
  at any image file.

---

## File structure

```
vectorscope/
├── vectorscope.lrplugin/       # Lightroom Classic plugin (Lua)
│   ├── Info.lua                # Plugin manifest (SDK version, menu items)
│   ├── VectorscopeMain.lua     # Menu handler, control dialog
│   ├── ExportPreview.lua       # JPEG export + trigger-file writer
│   └── CompanionBridge.lua     # Launch companion, PID check, IPC signal
│
├── companion/                  # Python companion app
│   ├── vectorscope_companion.py # Main Tkinter + Matplotlib GUI
│   ├── color_utils.py          # RGB→YCbCr, RGB→HSV, polar math
│   ├── skin_tone_detector.py   # Skin-tone ranges, deviation analysis
│   └── requirements.txt        # pip dependencies
│
└── README.md                   # This file
```
