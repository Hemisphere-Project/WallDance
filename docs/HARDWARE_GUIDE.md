# WallDance Hardware Guide

This document contains detailed hardware purchasing recommendations for the WallDance system.

---

## Production Hardware (Purchased Feb 2025)

The following hardware has been purchased for production deployment:

### Camera System

| Component | Model | Specification | Cost | Source |
|---|---|---|---|---|
| **Camera** | IDS U3-34E0XCP-M-GL Rev.1.2 | 4MP Sony IMX664 Starvis 2, Monochrome, USB3 Vision | ~260â‚¬ | Edmund Optics |
| **Lens** | Tamron M118FM08 | 8mm, 1/1.8", C-Mount, F1.8 | ~210â‚¬ | IDS Store |
| **IR Filter** | MidOpt BP850-25.4 | 850nm bandpass, 25.4mm C-Mount | ~100â‚¬ | MidOpt |

### Processing Hardware

| Component | Model | Specification | Notes |
|---|---|---|---|
| **Laptop** | ASUS ROG Strix SCAR 16 G635LW-RW075W | RTX 5080 (16GB), Core Ultra 9 275HX, 32GB DDR5 | Portable field deployment |

### Why This Setup?

**Camera Choice (IDS U3-34E0XCP-M-GL):**
- Sony IMX664 sensor (Starvis 2) - excellent low-light performance
- Monochrome sensor - 2-3Ã— more sensitive than color equivalent
- USB3 Vision interface - low latency (~5-10ms), no capture card needed
- Industrial grade - reliable for outdoor events
- 4MP resolution (2688Ã—1520) - better native resolution for distant subjects

**Lens Choice (Tamron M118FM08):**
- 8mm focal length on 1/1.8" sensor gives ~50Â° HFOV
- At 50m distance: covers ~47m width (perfect for 50m stage)
- F1.8 aperture - bright lens for low-light conditions
- IR-corrected for use with 850nm illumination

**IR Filter (MidOpt BP850):**
- Blocks visible light from projectors and stage lighting
- Passes 850nm IR illumination
- Essential for separating tracking from visual content

**Laptop Choice (ROG Strix SCAR 16):**
- RTX 5080 laptop GPU - latest Blackwell architecture, excellent for TensorRT
- Portable for on-site deployment
- 16GB VRAM - sufficient for 4K inference and large models
- USB3 ports for camera connection

### Additional Recommended Accessories

| Component | Recommendation | Approx. Cost | Notes |
|---|---|---|---|
| USB3 Cable | Lindy USB 3.0 Active Extension (10-20m) | ~80-150â‚¬ | Active cable mandatory for long runs |
| IR Illuminator | 850nm LED flood, 30-60W, 60Â° beam | ~100-200â‚¬ | CMVision or similar |
| Camera Housing | IP66 CCTV enclosure | ~80-100â‚¬ | For outdoor weather protection |
| Tripod/Mount | Heavy-duty with pan/tilt head | ~100-200â‚¬ | Stable positioning |

---

## Capture Interface Options

| Option | Latency | Quality | Cost | Pros | Cons |
|---|---|---|---|---|---|
| Elgato Cam Link 4K | ~100ms | Good | $130 | USB plug-and-play, portable, widely available | Higher latency, USB bandwidth limits, occasional driver issues |
| Blackmagic DeckLink | ~30ms | Excellent | $200+ | Lowest latency, professional SDI/HDMI, rock-solid drivers | Requires PCIe slot, higher cost, fixed installation |
| AVerMedia Live Gamer | ~50ms | Good | $150 | Good balance, PCIe reliability, gamer-focused features | Middle-ground on all specs, less pro features than Blackmagic |
| Magewell Pro Capture | ~20ms | Excellent | $300+ | Ultra-low latency, SDK support, multi-input options, Linux drivers | Premium price, overkill for simple setups |

---

## Machine Vision Cameras (Direct USB3/GigE)

These cameras connect directly to the PC without a capture card, providing lower latency and higher control.

| Option | Interface | Resolution | FPS | Cost | Pros | Cons |
|---|---|---|---|---|---|---|
| FLIR Blackfly S USB3 | USB3 Vision | Up to 5MP | 30-160 | $400-800 | Very low latency (~5ms), Spinnaker SDK, global shutter options, excellent Linux support | Requires SDK integration, no standard webcam interface |
| Basler ace 2 Basic | USB3/GigE | Up to 5MP | 30-120 | $300-500 | Low latency, Pylon SDK, good value, reliable industrial quality | SDK learning curve, basic feature set |
| Basler ace 2 Pro | USB3/GigE | Up to 5MP | 30-120 | $500-900 | Ultra-low latency, advanced features (PTP sync, chunk data), SFP+ GigE option | Higher cost, more complex setup |

**Notes:**
- Machine vision cameras bypass HDMI/SDI capture entirely
- USB3 Vision provides ~5-10ms glass-to-RAM latency
- GigE Vision allows cable runs up to 100m (vs 5m for USB3)
- Requires camera SDK (Spinnaker, Pylon) instead of OpenCV VideoCapture
- Global shutter recommended for moving subjects (no rolling shutter artifacts)

---

## Low-Light Machine Vision Cameras

For outdoor night performances, standard machine vision sensors struggle. The following cameras use specialized low-light sensors (Sony Starvis or large-pixel Global Shutter) optimized for dark conditions.

### Recommended Low-Light Models

| Brand | Model | Sensor | Pixel Size | Form Factor | Pros | Cons |
|---|---|---|---|---|---|---|
| **IDS** | uEye+ U3-3860CP | Sony IMX462 (Starvis 2) | 2.9Âµm | Metal C-Mount | **Best low-light sensor**, NIR sensitivity, rugged, modern ids_peak SDK | Less common brand |
| Basler | ace U acA1920-40uc | Sony IMX249 (Pregius GS) | 5.86Âµm | Metal C-Mount | Huge pixels = clean low-light, Global Shutter (no motion blur), proven Pylon SDK | Not Starvis, but excellent |
| Basler | dart daA1920-30uc | Sony IMX290 (Starvis 1) | 2.9Âµm | Board/S-Mount | Cheapest Starvis option, tiny form factor | Requires S-mount adapter, board-level |
| FLIR | BFS-U3-21S4C-C | Sony IMX290 (Starvis 1) | 2.9Âµm | Metal C-Mount | Starvis in robust case, Spinnaker SDK | Often backordered |
| FLIR | BFS-U3-31S4C-C | Sony IMX265 (Global Shutter) | 3.45Âµm | Metal C-Mount | High dynamic range, no motion blur | Not Starvis, moderate low-light |

### Sensor Technology Comparison

| Sensor Type | Example | Low-Light Performance | Motion Handling | Best For |
|---|---|---|---|---|
| **Sony Starvis 2** | IMX462 | â­â­â­â­â­ Excellent | Rolling shutter | Maximum darkness, NIR lighting |
| Sony Starvis 1 | IMX290 | â­â­â­â­ Very Good | Rolling shutter | Dark scenes, budget option |
| Sony Pregius (Large Pixel) | IMX249 | â­â­â­â­ Very Good | âœ… Global Shutter | Moving subjects in low light |
| Standard Global Shutter | IMX265 | â­â­â­ Good | âœ… Global Shutter | Moderate darkness with motion |

### Recommendations by Priority

1. **Best Overall (if open to IDS brand):** IDS uEye+ U3-3860CP
   - Sony IMX462 (Starvis 2) is the best low-light sensor available
   - Standard C-mount, rugged metal case
   - Modern `ids_peak` SDK works well on Linux

2. **Best Basler Option:** ace U acA1920-40uc
   - Sony IMX249 with huge 5.86Âµm pixels
   - Often cleaner than Starvis in moderate darkness
   - Global Shutter eliminates motion blur on dancers
   - Avoids board-level dart form factor hassle

3. **Best FLIR Option:** Blackfly S BFS-U3-21S4C-C
   - Sony IMX290 Starvis in standard metal case
   - Robust and field-proven
   - Note: Check availability (often backordered)

**Key Insight:** Large pixel sensors (IMX249: 5.86Âµm) can outperform smaller Starvis pixels (IMX462: 2.9Âµm) in moderate darkness by collecting more light per pixel with less noise. Global Shutter is a major advantage for capturing moving dancers.

---

## Integration Notes for IDS Camera

The IDS U3-34E0XCP-M-GL uses the IDS Peak SDK:

```bash
# Install IDS Peak SDK (download from ids-imaging.com)
# Then install Python bindings:
pip install ids-peak ids-peak-ipl
```

**Key Settings for Low-Light:**
- Exposure: Auto or manual (5-50ms depending on motion blur tolerance)
- Gain: Auto with upper limit (avoid excessive noise)
- Pixel Format: Mono8 or Mono12 (higher bit depth = better enhancement headroom)
- Trigger: Free-running or software trigger

**Integration Path:**
1. Replace OpenCV VideoCapture with IDS Peak acquisition
2. Frame arrives as numpy array, compatible with existing pipeline
3. Consider binning (2x2) if 4MP is too slow - doubles sensitivity

---

*Last updated: March 2026*
