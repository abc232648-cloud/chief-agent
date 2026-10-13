"""Host timezone detection, independent of any domain or particular country."""
import warnings
from zoneinfo import ZoneInfo


def host_zone():
    from tzlocal import reload_localzone
    # Missing/conflicting OS settings must not silently become a guessed location.
    with warnings.catch_warnings():
        warnings.simplefilter('error')
        zone = reload_localzone()
    return ZoneInfo(zone.key)
