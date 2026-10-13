"""An append-only record of configuration changes."""


class AuditLog:
    def __init__(self):
        self.entries = []

    def record(self, field, value):
        self.entries.append((field, value))
