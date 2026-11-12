from django import template

register = template.Library()


@register.filter
def compact(v):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return "—"
    for div, suf in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(v) >= div:
            return f"{v / div:.2f}{suf}"
    return f"{v:,.0f}"


@register.filter
def get(d, key):
    return (d or {}).get(key)
