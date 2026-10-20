"""Poll observed browser state without injecting an eval-based page-side loop."""
from time import monotonic


def wait_for_browser_condition(page, expression, *, timeout=5000):
    deadline = monotonic() + timeout / 1000
    while True:
        if page.evaluate(expression):
            return
        if monotonic() >= deadline:
            raise AssertionError(f'Browser condition did not become true: {expression}')
        page.wait_for_timeout(50)
