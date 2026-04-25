"""Mission templates — system prompts and pre-recorded voice commands."""

from .council import COUNCIL_MISSION
from .payday import PAYDAY_MISSION
from .travel import TRAVEL_MISSION
from .trip import TRIP_MISSION
from .weekend import WEEKEND_MISSION

MISSIONS = {
    "weekend": WEEKEND_MISSION,
    "payday": PAYDAY_MISSION,
    "travel": TRAVEL_MISSION,
    "council": COUNCIL_MISSION,
    "trip": TRIP_MISSION,
}
