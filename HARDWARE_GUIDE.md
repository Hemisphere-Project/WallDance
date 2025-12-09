# WallDance Hardware Guide

This document contains detailed hardware purchasing recommendations for the WallDance system.

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
| **IDS** | uEye+ U3-3860CP | Sony IMX462 (Starvis 2) | 2.9µm | Metal C-Mount | **Best low-light sensor**, NIR sensitivity, rugged, modern ids_peak SDK | Less common brand |
| Basler | ace U acA1920-40uc | Sony IMX249 (Pregius GS) | 5.86µm | Metal C-Mount | Huge pixels = clean low-light, Global Shutter (no motion blur), proven Pylon SDK | Not Starvis, but excellent |
| Basler | dart daA1920-30uc | Sony IMX290 (Starvis 1) | 2.9µm | Board/S-Mount | Cheapest Starvis option, tiny form factor | Requires S-mount adapter, board-level |
| FLIR | BFS-U3-21S4C-C | Sony IMX290 (Starvis 1) | 2.9µm | Metal C-Mount | Starvis in robust case, Spinnaker SDK | Often backordered |
| FLIR | BFS-U3-31S4C-C | Sony IMX265 (Global Shutter) | 3.45µm | Metal C-Mount | High dynamic range, no motion blur | Not Starvis, moderate low-light |

### Sensor Technology Comparison

| Sensor Type | Example | Low-Light Performance | Motion Handling | Best For |
|---|---|---|---|---|
| **Sony Starvis 2** | IMX462 | ⭐⭐⭐⭐⭐ Excellent | Rolling shutter | Maximum darkness, NIR lighting |
| Sony Starvis 1 | IMX290 | ⭐⭐⭐⭐ Very Good | Rolling shutter | Dark scenes, budget option |
| Sony Pregius (Large Pixel) | IMX249 | ⭐⭐⭐⭐ Very Good | ✅ Global Shutter | Moving subjects in low light |
| Standard Global Shutter | IMX265 | ⭐⭐⭐ Good | ✅ Global Shutter | Moderate darkness with motion |

### Recommendations by Priority

1. **Best Overall (if open to IDS brand):** IDS uEye+ U3-3860CP
   - Sony IMX462 (Starvis 2) is the best low-light sensor available
   - Standard C-mount, rugged metal case
   - Modern `ids_peak` SDK works well on Linux

2. **Best Basler Option:** ace U acA1920-40uc
   - Sony IMX249 with huge 5.86µm pixels
   - Often cleaner than Starvis in moderate darkness
   - Global Shutter eliminates motion blur on dancers
   - Avoids board-level dart form factor hassle

3. **Best FLIR Option:** Blackfly S BFS-U3-21S4C-C
   - Sony IMX290 Starvis in standard metal case
   - Robust and field-proven
   - Note: Check availability (often backordered)

**Key Insight:** Large pixel sensors (IMX249: 5.86µm) can outperform smaller Starvis pixels (IMX462: 2.9µm) in moderate darkness by collecting more light per pixel with less noise. Global Shutter is a major advantage for capturing moving dancers.

---

*Last updated: December 2025*
