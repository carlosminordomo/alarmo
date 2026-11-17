"""Slow down guessing alarm codes, per Home Assistant user."""

import time
import asyncio
import logging
import collections

from . import const

_LOGGER = logging.getLogger(__name__)

FREE_FAILURES = 3
LONGEST_WAIT = 60
NEW_CODES_PER_DAY = 20
DAY = 24 * 3600


def _now():
    return time.monotonic()


class CodeGuard:
    """Slow down guessing codes: one check at a time, and longer waits after misses."""

    def __init__(self):
        """Start with no wrong code."""
        self.failures = 0
        self.not_before = 0.0
        self.new_codes = collections.deque()
        self.lock = asyncio.Lock()

    def allows(self):
        """Whether a code may be checked now."""
        return _now() >= self.not_before

    def failed(self):
        """Count a wrong code; from the third, wait 1, 2, 4... seconds, up to 60."""
        self.failures += 1
        if self.failures >= FREE_FAILURES:
            wait = min(2 ** (self.failures - FREE_FAILURES), LONGEST_WAIT)
            self.not_before = _now() + wait
        _LOGGER.warning("Wrong alarm code given %s times in a row", self.failures)

    def takes_new_code(self):
        """Count a new code tried, taken or not; refuse past the day's limit."""
        now = _now()
        while self.new_codes and self.new_codes[0] <= now - DAY:
            self.new_codes.popleft()
        if len(self.new_codes) >= NEW_CODES_PER_DAY:
            _LOGGER.warning("Too many new alarm codes tried in a day")
            return False
        self.new_codes.append(now)
        return True

    def passed(self):
        """Forget the wrong codes before a right one."""
        self.failures = 0
        self.not_before = 0.0


def code_guard(hass, ha_user_id):
    """Return the guard the code checks of this Home Assistant user go through.

    Checks with no user behind them, such as MQTT's, share one guard.
    """
    guards = hass.data[const.DOMAIN].setdefault("code_guards", {})
    return guards.setdefault(ha_user_id, CodeGuard())
