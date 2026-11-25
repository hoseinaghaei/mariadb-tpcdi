"""TPC-DI on MariaDB.

One module per phase, each owning both its loading and its checks.
"""

from . import (
    phase02_generate,
    phase03_schema,
    phase04_reference,
    phase05_broker,
    phase06_finwire,
    phase07_customer,
    phase08_facts,
)

PHASES = [
    phase02_generate,
    phase03_schema,
    phase04_reference,
    phase05_broker,
    phase06_finwire,
    phase07_customer,
    phase08_facts,
]

__all__ = ["PHASES"]


def phase(number):
    """The module implementing a given phase, or None."""
    return next((p for p in PHASES if p.NUMBER == number), None)


def numbers():
    return [p.NUMBER for p in PHASES]
