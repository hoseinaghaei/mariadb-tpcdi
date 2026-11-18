"""TPC-DI on MariaDB.

One module per phase, each owning both its loading and its checks, so that
everything about a phase is in one place.  Add a phase by writing
tpcdi/phaseNN_name.py with NUMBER, TITLE, DESCRIPTION, run() and verify(),
then listing it in PHASES below.
"""
from . import (phase02_generate, phase03_schema, phase04_reference,
               phase05_broker)

PHASES = [phase02_generate, phase03_schema, phase04_reference,
          phase05_broker]

__all__ = ["PHASES"]


def phase(number):
    """The module implementing a given phase, or None."""
    return next((p for p in PHASES if p.NUMBER == number), None)


def numbers():
    return [p.NUMBER for p in PHASES]
