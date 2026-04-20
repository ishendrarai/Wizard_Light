"""
color_temperature.py
====================
White-point / colour-temperature adjustment for NumPy RGB images.

Algorithm
---------
Uses the Tanner Helland piecewise approximation to convert a Kelvin
value into per-channel (R, G, B) scale factors.  The factors are
normalised against 6 500 K (D65, the sRGB reference white) so that
a "daylight" image is unchanged at 6 500 K.

The multiplication is performed in *linear* (gamma-decoded) light to
avoid hue-shifting artefacts that occur when scaling gamma-encoded values.

Reference
---------
Tanner Helland (2012):
  https://tannerhelland.com/2012/09/18/convert-temperature-rgb-algorithm-code.html
"""

import numpy as np
from typing import Tuple


# ---------------------------------------------------------------------------
# 1.  Kelvin → raw RGB  (Tanner Helland piecewise approximation)
# ---------------------------------------------------------------------------

def _kelvin_to_raw_rgb(kelvin: float) -> Tuple[float, float, float]:
    """
    Convert a colour temperature (K) to raw (R, G, B) values in [0, 255].

    Piecewise formulas are empirical curve-fits to Mitchell Charity's
    black-body colour table.  Each channel uses a different expression
    below / above the 6 600 K breakpoint.

    Parameters
    ----------
    kelvin : float
        Colour temperature, clamped internally to [1 000 K, 40 000 K].

    Returns
    -------
    (r, g, b) : floats clipped to [0, 255].
    """
    t = np.clip(float(kelvin), 1_000.0, 40_000.0) / 100.0  # work in units of 100 K

    # ── Red ──────────────────────────────────────────────────────────────
    if t <= 66.0:
        r = 255.0
    else:
        # Power-law decay for hot sources (blue-white stars, etc.)
        r = 329.698727446 * ((t - 60.0) ** -0.1332047592)

    # ── Green ─────────────────────────────────────────────────────────────
    if t <= 66.0:
        g = 99.4708025861 * np.log(t) - 161.1195681661
    else:
        g = 288.1221695283 * ((t - 60.0) ** -0.0755148492)

    # ── Blue ──────────────────────────────────────────────────────────────
    if t >= 66.0:
        b = 255.0
    elif t <= 19.0:
        b = 0.0
    else:
        b = 138.5177312231 * np.log(t - 10.0) - 305.0447927307

    return (
        float(np.clip(r, 0.0, 255.0)),
        float(np.clip(g, 0.0, 255.0)),
        float(np.clip(b, 0.0, 255.0)),
    )


# ---------------------------------------------------------------------------
# 2.  Normalised (R, G, B) multipliers
# ---------------------------------------------------------------------------

# Pre-compute the D65 reference once at import time so normalisation is free.
_REF_R, _REF_G, _REF_B = _kelvin_to_raw_rgb(6_500.0)


def kelvin_to_multipliers(kelvin: float) -> Tuple[float, float, float]:
    """
    Return per-channel multipliers (r_mul, g_mul, b_mul) for a given colour
    temperature, normalised so that 6 500 K → (1.0, 1.0, 1.0).

    Lower Kelvin  → r_mul > 1, b_mul < 1  (warm / amber tones)
    Higher Kelvin → r_mul < 1, b_mul > 1  (cool / blue tones)

    Parameters
    ----------
    kelvin : float
        Desired colour temperature in Kelvin.

    Returns
    -------
    (r_mul, g_mul, b_mul) : floats, each roughly in [0.0, 2.0].
    """
    r, g, b = _kelvin_to_raw_rgb(kelvin)
    return r / _REF_R, g / _REF_G, b / _REF_B


# ---------------------------------------------------------------------------
# 3.  Core adjustment function
# ---------------------------------------------------------------------------

def adjust_color_temperature(
    image: np.ndarray,
    kelvin: float,
    *,
    input_gamma: float = 2.2,
) -> np.ndarray:
    """
    Adjust the white-point of an RGB image to the given colour temperature.

    Pipeline
    --------
    1. Gamma-decode the image  (display-light → linear-light)
    2. Multiply each channel by the normalised Kelvin factors
    3. Clip to [0, 1]
    4. Gamma re-encode         (linear-light → display-light)
    5. Return in the original dtype

    Why linear light?
    -----------------
    Gamma encoding is nonlinear: scaling R in gamma-space is *not* the same
    as scaling red luminance.  Working in linear light ensures colour ratios
    are preserved and avoids unwanted hue shifts.

    Parameters
    ----------
    image : np.ndarray
        RGB image, uint8 (0-255) **or** float32/float64 (0.0-1.0 or 0-255).
    kelvin : float
        Target white-point in Kelvin.  6 500 K ≈ no change.
        Practical range: 1 000 K (candle flame) … 20 000 K (blue sky).
    input_gamma : float
        Display gamma for encode/decode.  2.2 is correct for sRGB monitors.
        Pass 1.0 to skip gamma handling (already-linear pipelines).

    Returns
    -------
    np.ndarray
        Colour-temperature-adjusted image, same shape and dtype as *image*.

    Examples
    --------
    >>> warm = adjust_color_temperature(img, 3_000)   # tungsten / warm
    >>> neut = adjust_color_temperature(img, 6_500)   # no change
    >>> cool = adjust_color_temperature(img, 9_000)   # overcast / cool
    """
    # ── Normalise input to float64 [0, 1] ───────────────────────────────
    is_uint8 = image.dtype == np.uint8
    if is_uint8:
        img_f = image.astype(np.float64) / 255.0
    elif image.dtype in (np.float32, np.float64):
        img_f = image.astype(np.float64)
        if img_f.max() > 1.0 + 1e-6:       # accept 0-255 float images too
            img_f = img_f / 255.0
    else:
        raise TypeError(
            f"Unsupported dtype '{image.dtype}'. "
            "Expected uint8, float32, or float64."
        )

    # ── Gamma decode → linear light ──────────────────────────────────────
    if input_gamma != 1.0:
        linear = np.power(np.clip(img_f, 1e-9, 1.0), input_gamma)
    else:
        linear = img_f.copy()

    # ── Per-channel multiply  (vectorised, no Python loop) ───────────────
    r_mul, g_mul, b_mul = kelvin_to_multipliers(kelvin)
    multipliers = np.array([r_mul, g_mul, b_mul], dtype=np.float64)  # (3,)
    linear = linear * multipliers                                      # broadcast HxWx3
    np.clip(linear, 0.0, 1.0, out=linear)

    # ── Gamma re-encode → display light ──────────────────────────────────
    if input_gamma != 1.0:
        result_f = np.power(linear, 1.0 / input_gamma)
    else:
        result_f = linear

    # ── Return in original dtype ─────────────────────────────────────────
    if is_uint8:
        return (result_f * 255.0).round().astype(np.uint8)
    return result_f.astype(image.dtype)


# ---------------------------------------------------------------------------
# 4.  Lookup table helpers  (optional – for high-frequency use)
# ---------------------------------------------------------------------------

def build_kelvin_lut(
    k_min: int = 1_000,
    k_max: int = 20_000,
    step: int = 100,
) -> dict:
    """
    Pre-compute a ``{kelvin: (r_mul, g_mul, b_mul)}`` lookup table.

    Useful in tight loops where you apply many different temperatures and
    want to avoid re-evaluating the piecewise math on every frame.

    Parameters
    ----------
    k_min, k_max : int
        Kelvin range to cover (inclusive).
    step : int
        Table resolution.  100 K is more than sufficient for visual work.

    Returns
    -------
    dict  –  {int kelvin: (float r_mul, float g_mul, float b_mul)}
    """
    return {
        k: kelvin_to_multipliers(float(k))
        for k in range(k_min, k_max + step, step)
    }


def lut_lookup(lut: dict, kelvin: float) -> Tuple[float, float, float]:
    """
    Retrieve multipliers from a pre-built LUT with **linear interpolation**
    between the two nearest table entries.

    Parameters
    ----------
    lut : dict
        Output of :func:`build_kelvin_lut`.
    kelvin : float
        Desired temperature (clamped to the LUT range).

    Returns
    -------
    (r_mul, g_mul, b_mul) : smoothly interpolated multipliers.
    """
    keys = sorted(lut.keys())
    kelvin = float(np.clip(kelvin, keys[0], keys[-1]))

    lo = max(k for k in keys if k <= kelvin)
    hi = min(k for k in keys if k >= kelvin)

    if lo == hi:
        return lut[lo]

    t = (kelvin - lo) / (hi - lo)           # interpolation weight  ∈ [0, 1]
    r0, g0, b0 = lut[lo]
    r1, g1, b1 = lut[hi]
    return (
        r0 + t * (r1 - r0),
        g0 + t * (g1 - g0),
        b0 + t * (b1 - b0),
    )
