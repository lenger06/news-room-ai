"""
News desk anchor roster.
Add or remove anchors here. Each entry needs a HeyGen avatar_id and voice_id.
To find IDs: GET https://api.heygen.com/v2/avatars and /v2/voices
"""

from dataclasses import dataclass
from typing import Optional
import random


@dataclass
class Anchor:
    name: str           # On-air name used in scripts
    avatar_id: str      # HeyGen avatar ID
    voice_id: str       # HeyGen voice ID
    bio: str            # Short description for script-writer context (tone, style)


# ── Anchor Roster ─────────────────────────────────────────────────────────────
ANCHORS: list[Anchor] = [
    Anchor(
        name="Alex Morgan",
        avatar_id="d61bc87e91f84f3bb22cb02eb91d8151",
        voice_id="5eb15f7ed1254e658faccf14e67f2cd9",
        bio="Authoritative and composed. Delivers hard news with calm gravitas.",
    ),
    Anchor(
        name="Rick Johnson",
        avatar_id="ec08a8bb0119489aa0019a090274c631",
        voice_id="c701a9c07ff74f7ca9d71cbd24abb3a1",
        bio="Authoritative and composed. Delivers hard news with calm gravitas.",
    ),
    Anchor(
        name="Darlene Smith",
        avatar_id="6ab4b4c705d14773bb0cb7c1dda31db0",
        voice_id="d6a657274b184772ac28a6146f729d3a",
        bio="Friendly and approachable. Brings warmth and relatability to the news desk.",
    ),
    Anchor(
        name="Shawn Green",
        avatar_id="Shawn_Suit_Front_public",
        voice_id="e1a429dbe823406dbae5fa7c3612314d",
        bio="Authoritative and composed. Delivers hard news with calm gravitas.",
    ),            
    # Add more anchors below:
    # Anchor(
    #     name="Jordan Lee",
    #     avatar_id="<avatar_id>",
    #     voice_id="<voice_id>",
    #     bio="Warm and conversational. Strong on feature stories and human interest.",
    # ),
]

def get_anchor(name: Optional[str] = None) -> Anchor:
    """
    Return an anchor by name (case-insensitive partial match), or random if not specified.
    Falls back to the first anchor if the name is not found.
    """
    if not ANCHORS:
        raise ValueError("No anchors configured in config/anchors.py")

    if name:
        name_lower = name.lower()
        for anchor in ANCHORS:
            if name_lower in anchor.name.lower():
                return anchor
        # Name not found — fall back to first anchor
        return ANCHORS[0]

    return random.choice(ANCHORS)


def list_anchors() -> list[dict]:
    """Return anchor roster as a list of dicts (safe to serialize)."""
    return [{"name": a.name, "bio": a.bio} for a in ANCHORS]
