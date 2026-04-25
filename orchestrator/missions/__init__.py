"""Mission templates — system prompts and pre-recorded voice commands."""

from .payday import PAYDAY_MISSION
from .travel import TRAVEL_MISSION
from .weekend import WEEKEND_MISSION

MISSIONS = {
    "weekend": WEEKEND_MISSION,
    "payday": PAYDAY_MISSION,
    "travel": TRAVEL_MISSION,
}
