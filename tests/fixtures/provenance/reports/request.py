"""What a report shows. Built step by step with setters before it is rendered."""

SUPPORTED_CURRENCIES = ("EUR", "USD", "GBP")


class ReportRequest:
    def __init__(self):
        self.title = "Report"
        self.period = None
        self.columns = []
        self.currency = "EUR"

    def set_title(self, title):
        self.title = title
        return self

    def set_period(self, first, last):
        self.period = (first, last)
        return self

    def add_column(self, name):
        self.columns.append(name)
        return self

    def set_currency(self, code, audit):
        """Not just configuration: rejects unknown codes and records every change."""
        if code not in SUPPORTED_CURRENCIES:
            raise ValueError(f"unsupported currency {code}")
        audit.record("currency", code)
        self.currency = code
        return self
