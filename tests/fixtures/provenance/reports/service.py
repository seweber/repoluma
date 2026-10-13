"""Monthly reports for a user, remembered until the next one."""

from .render import render
from .request import ReportRequest

# shared mutable state: the last report rendered per user, read by last_report
RECENT = {}


def monthly_report(user, month, audit, detailed=False):
    request = ReportRequest().set_title(f"Monthly report {month.name}")
    request.set_period(month.first, month.last)
    request.add_column("revenue")
    if detailed:
        request.add_column("refunds")
    request.set_currency(user.currency, audit)
    text = render(request)
    RECENT[user.name] = text
    return text


def last_report(user):
    return RECENT.get(user.name)
