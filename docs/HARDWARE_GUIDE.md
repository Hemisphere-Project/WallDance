# WallDance Hardware Guide

This document contains detailed hardware purchasing recommendations for the WallDance system.

---

## Production Hardware (Purchased Feb 2025)

The following hardware has been purchased for production deployment:

### Camera System

| Component | Model | Specification | Cost | Source |
|---|---|---|---|---|
| **Camera** | IDS U3-34E0XCP-M-GL Rev.1.2 | 4MP Sony IMX664 Starvis 2, Monochrome, USB3 Vision | ~260€ | Edmund Optics |
| **Lens** | Tamron M118FM08 | 8mm, 1/1.8", C-Mount, F1.8 | ~210€ | IDS Store |
| **IR Filter** | MidOpt BP850-25.4 | 850nm bandpass, 25.4mm C-Mount | ~100€ | MidOpt |

### Processing Hardware

| Component | Model | Specification | Notes |
|---|---|---|---|
| **Laptop** | ASUS ROG Strix SCAR 16 G635LW-RW075W | RTX 5080 (16GB), Core Ultra 9 275HX, 32GB DDR5 | Portable field deployment |

### Why This Setup?

**Camera Choice (IDS U3-34E0XCP-M-GL):**
- Sony IMX664 sensor (Starvis 2) - excellent low-light performance
- Monochrome sensor - 2-3× more sensitive than color equivalent
- USB3 Vision interface - low latency (~5-10ms), no capture card needed
- Industrial grade - reliable for outdoor events
- 4MP resolution (2688×1520) - better native resolution for distant subjects

**Lens Choice (Tamron M118FM08):**
- 8mm focal length on 1/1.8" sensor gives ~50° HFOV
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
| USB3 Cable | Lindy USB 3.0 Active Extension (10-20m) | ~80-150€ | Active cable mandatory for long runs |
| IR Illuminator | 850nm LED flood, 30-60W, 60° beam | ~100-200€ | CMVision or similar |
| Camera Housing | IP66 CCTV enclosure | ~80-100€ | For outdoor weather protection |
| Tripod/Mount | Heavy-duty with pan/tilt head | ~100-200€ | Stable positioning |

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

*Last updated: February 2025*


## RESEARCH legacy

camera
https://www.e-consystems.com/gige-cameras/4k-sony-imx678-low-light-hdr-camera.asp
Arducam

Yes, all three maintain C/CS mount lens compatibility:

AIDA UHD-NDI3-IP67: Features C/CS mount with a pre-installed 5mm CS lens (72° HFOV). The mount supports both C and CS lenses with appropriate adapters.​

Axis-One/AIDA 4K NDI POV Camera: Also uses a pre-installed 5mm CS-mount lens in its IP67 housing. The same C/CS compatibility applies.​

Teledyne Forge 1GigE IP67: Specifically designed with standard C-mount lens interface for industrial optics. This provides the widest lens selection for machine vision applications.​

All options allow you to swap lenses for your 50x50m stage coverage needs, with the Teledyne offering the most robust C-mount ecosystem and the AIDA/Axis-One units providing the convenience of included CS lenses optimized for wide-angle POV shots.



RouteCAM_CU86_IP67 - 4K Sony® Starvis™ 2 IMX678 HDR GigE Camera
AIDA UHD-NDI3-IP67
Axis-One/AIDA 4K NDI POV Camera
Teledyne Forge 1GigE IP67


--------------------------

With your updated needs (night scenes, 20–50 m distance, projector alignment, and slightly higher budget), the "standard" IP67 camera market becomes a trap: most affordable options have small sensors or slow lenses that will fail in low light at 50 m.

Since you are comfortable with technical integration, the "Box Camera + Enclosure" approach is far superior to buying a pre-sealed IP67 camera. It allows you to use a much larger sensor (Starvis 2 1/1.2") and a much brighter lens (F1.4) for the same price.

Recommended Solution: The "Industrial Box" Build
This custom setup fits your ~1000 € budget, solves the low-light/distance challenge, and uses GigE for low latency (<50ms).

Component	Recommendation	Approx. Cost	Why?
Camera	Daheng Imaging MER2-830-22GM-P	~450–550 €	Uses Sony IMX585 (Starvis 2). This is a 1/1.2" sensor—much larger and more sensitive than the 1/1.8" IMX678. 8MP (4K), GigE Vision.
Lens	Tamron M112FM08 (8mm) or Computar V0828-MPY	~200–300 €	You need a lens that covers the large 1/1.2" sensor. Standard C-mount lenses will vignette (black corners). An 8mm fixed lens gives ~60° FOV, matching a 1:1 throw ratio (50m width at 50m distance).
Housing	Generic IP67 CCTV Enclosure (Hele/Videotec style)	~40–80 €	Buy a standard "outdoor housing for box camera" (30cm long). It’s cheap, robust, and fits the camera + large lens + cabling easily.
Light	External 850nm IR Illuminator (60–90° beam)	~60–100 €	Built-in LEDs won't reach 50 m. A standalone 15–30W IR floodlight placed near the camera ensures the dancers glow in NIR without affecting the audience.
Total		~850–1050 €	
Detailed Analysis of the Setup
1. The Sensor: IMX585 vs. IMX678
You originally looked at IMX678 cameras (like RouteCAM_CU86). The IMX585 is the "Pro" version of that generation.

Surface Area: IMX585 is 1/1.2" vs IMX678's 1/1.8". It has ~2.9µm pixels vs 2.0µm. This means roughly 2× better light gathering per pixel.

Crucial for you: In a dark scene with fast-moving dancers, you need a fast shutter speed to avoid motion blur. The larger sensor allows you to run a faster shutter (e.g., 5ms) without the image becoming too noisy.​

2. The Lens Strategy
At 20–50 m distances, light loss is your enemy.

Fixed vs. Zoom: A fixed focal length lens (Prime) like the Tamron M112FM08 is usually F1.8 or F2.0, whereas a varifocal zoom covering this sensor size is often F3.5+. The fixed lens lets in ~4× more light.

FOV Logic: If your projector has a standard throw ratio (width ≈ distance), an 8mm lens on this sensor gives roughly 53° horizontal FOV.

At 20 m distance: Covers ~20 m width.

At 50 m distance: Covers ~50 m width.

Digital Zoom (ROI): Since you have 4K resolution (3840 px wide) but only need to track bodies (which will be ~150px tall even at 50m), you don't need an optical zoom. If the wall is smaller (20x20m) but you are far away (50m), you can simply crop the center 1920x1080 region in YOLO (or on the camera via ROI) and essentially get a "2.5x zoom" for free without losing light.​

3. Integration & Latency
Glass-to-GPU: GigE Vision (raw Bayer data) → Network Card → CPU/GPU Memory Copy.

Expected latency: 30–60 ms. This is significantly faster than NDI (100ms+) or RTSP security cameras (300ms+).

YOLO Optimization: With the IMX585, you can set the camera to output a 2x2 binned mode (increasing sensitivity even further) if 4K is too slow for your pipeline, or use "Decimation" to send 1080p to the GPU while keeping the full sensor field of view.

Verification of Your Needs
Check Needs/Approach: Coherent. The shift to a "Box Camera" approach is necessary to get the lens quality required for 50m night tracking. The move to IMX585 is a strong upgrade for night performance.

Narrowing Hardware:

Lens Mount: Must be C-Mount capable of 1" or 1/1.2" image circle. (Do not buy standard 2/3" or 1/1.8" lenses; they will clip the image).

IR Correction: Ensure the lens is "IR Corrected" or "Day/Night" compatible, otherwise the focus will shift when you switch from visible projection to IR tracking.

Sourcing:

Camera: Daheng Imaging has distributors in Europe (e.g., Sicube in Germany, Innosmart in Bulgaria). Alternatively, Hikrobot (model MV-CE080-80GM) is a direct equivalent often found cheaply via industrial suppliers.​

Housing: Widely available from generic electronics suppliers (Conrad, RS Components, Amazon) under "Outdoor Camera Housing".

Alternative: The "Security" Hybrid (If you want easy sourcing)
If building a custom housing is too DIY, you can buy a high-end Hikvision DeepinView camera (e.g., iDS-2CD7A86G0-IZHS).

Pros: Comes with IP67, motorized zoom (2.8-12mm or 8-32mm), and powerful IR built-in. Price ~800–1000 €.

Cons: Latency is the killer. Even with "Low Latency Mode" and tuning, it will be 150–250 ms. For interactive projection mapping, this lag is usually perceptible and annoying. I would stick to the GigE industrial camera (Option 1) for the responsiveness.


----------------------------


IMX585 -> Starvis2 / 8MP

    ZWO ASI585MM // USB3 // 540€  
    https://www.pierro-astro.com/materiel-astronomique/cameras-astro/cameras-planetaires/cam%C3%A9ra-asi585mm-monochrome-imx585-zwo_detail

    Player One Uranus-M // USB3 // 490€


IMX664 -> Starvis2 / 4MP
better dynamic

    IDS U3-34E0XCP-M-GL 1/1,8" // USB3 // 260€ 
    https://www.edmundoptics.fr/p/ids-imaging-u3-34e0xcp-m-gl-118-gige-monochrome-camera-rev-12/55735/


IMX183 -> Starvis1 / 20MP + 2x2bin -> 4MP / GigE
better pure darkness

    Basler ace acA5472-5gm // GigE // 750€
    https://www.edmundoptics.fr/p/basler-ace-aca5472-5gm-monochrome-gige-camera/40326/#

    Teledyne FLIR BFS-PGE-200S6M-CS // GigE // 700€
    https://www.edmundoptics.fr/p/bfs-pge-200s6m-c-poe-gige-blackflyr-s-monochrome-camera/40195/

    IDS U3-3800CP-M-GL // USB3 // 725€
    https://www.edmundoptics.com/p/u3-3800cp-1-monochrome-usb3-camera/43971/


IMX334 -> Starvis1 / 8MP

    Basler ace2 a2A3840-13gmPRO Monochrome // GigE // 463€
    https://www.edmundoptics.com/p/basler-ace2-a2a3840-13gmpro-monochrome-gige-pro-camera/44079/



IMX678 


-------------


Camera	

IDS U3-34E0XCP-M-GL	4MP Starvis 2 Mono.
https://www.edmundoptics.fr/p/ids-imaging-u3-34e0xcp-m-gl-118-gige-monochrome-camera-rev-12/55735/
~260 €	


Lens 6mm  (0.4 – 0.8 ratio) 

Tamron M118FM06 
https://fr.ids-imaging.com/store/lens-tamron-m118fm06-6-mm-1-1-8.html   220€
https://www.befr.ebay.be/itm/235757571679   160€



Ricoh FL-CC0614A-2M  
https://www.machine-vision-shop.com/all-products/lenses/ricoh-fl-cc0614a-2m  200€


Lens 8mm  (0.8 – 1.5 ratio)

Tamron M118FM08  
https://fr.ids-imaging.com/store/lens-tamron-m118fm08-8-mm-1-1-8.html   210€

Kowa LM8JCM
https://www.kowa-lenses.com/fr/LM8JCM-V-Objectif-Renforce-8mm-2-3-2MP-a-monture-C/11129  300€


Cable	Lindy USB 3.0 Active Extension Pro (20m)	~150 €	"Active" is mandatory. 

Housing	Generic IP66 CCTV Housing	~100 €	"Hele" or similar. Drill a hole for USB cable gland.

Light	IR Illuminator 850nm (60° beam)	~200 €	E.g., CMVision or generic 30W flood.

Filter	MidOpt BP850 (M30.5 or similar)	~100 €	Screw onto lens to block projector light.


