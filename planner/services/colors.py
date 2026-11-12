"""Distinct, stable colours for 160+ franchises.

Hues are spread with the golden angle so neighbouring indices never look
alike; lightness/saturation cycle through three bands to multiply the number
of distinguishable colours. Colours are stored on the Franchise row, so they can
be edited in /admin and stay the same across sessions.
"""
import colorsys
import hashlib

GOLDEN = 137.508
BANDS = [(0.72, 0.45), (0.85, 0.36), (0.60, 0.55)]  # (saturation, lightness)


# hand-picked, high-contrast qualitative colours (Tableau 10 + Kelly) used first
DISTINCT = [
    "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd", "#8c564b", "#e377c2", "#17becf",
    "#bcbd22", "#4e4e4e", "#F3C300", "#875692", "#00A3A3", "#BE0032", "#0067A5", "#E68FAC",
    "#604E97", "#882D17", "#8DB600", "#654522", "#A1CAF1", "#F99379", "#2B3D26", "#DCD300",
]


def color_for_index(i):
    if i < len(DISTINCT):
        return DISTINCT[i]
    i -= len(DISTINCT)
    hue = (i * GOLDEN) % 360 / 360.0
    s, light = BANDS[(i // 7) % len(BANDS)]
    r, g, b = colorsys.hls_to_rgb(hue, light, s)
    return "#{:02x}{:02x}{:02x}".format(int(r * 255), int(g * 255), int(b * 255))


def color_for_code(code):
    """Fallback when a franchise has no stored colour (deterministic by code)."""
    n = int(hashlib.md5(code.encode()).hexdigest()[:6], 16)
    return color_for_index(n % 997)


def assign_missing_colors():
    from planner.models import Franchise

    used = Franchise.objects.exclude(color="").count()
    for i, fr in enumerate(Franchise.objects.filter(color="").order_by("code")):
        fr.color = color_for_index(used + i)
        fr.save(update_fields=["color"])
