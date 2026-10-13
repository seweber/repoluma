"""Turns a report request into text."""


def render(request):
    header = f"{request.title} ({request.currency})"
    period = f"{request.period[0]} to {request.period[1]}" if request.period else "all time"
    return "\n".join([header, period, ", ".join(request.columns)])
