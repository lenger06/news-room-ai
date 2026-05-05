"""
News desk anchor roster.
Add or remove anchors here. Each anchor has a list of AvatarLooks so the
Executive Producer can choose the most appropriate appearance for each story.
To find IDs: GET https://api.heygen.com/v2/avatars and /v2/voices
"""

from dataclasses import dataclass
from typing import Optional
import random


@dataclass
class AvatarLook:
    avatar_id: str
    description: str    # e.g. "formal suit at news desk", "casual blazer standing", "outdoor live shot"


@dataclass
class Anchor:
    name: str                       # On-air name used in scripts
    avatars: list[AvatarLook]       # Ordered list — first is the default look
    voice_id: str                   # HeyGen voice ID
    desk: str                       # Desk slug this anchor belongs to (see config/desks.py)
    bio: str                        # Short description for script-writer context (tone, style)

    @property
    def default_avatar_id(self) -> str:
        return self.avatars[0].avatar_id if self.avatars else ""

    def get_avatar_id(self, description: Optional[str] = None) -> str:
        """Return avatar_id by partial description match, or default if not found."""
        if not description or not self.avatars:
            return self.default_avatar_id
        desc_lower = description.lower()
        for look in self.avatars:
            if desc_lower in look.description.lower() or any(
                word in look.description.lower() for word in desc_lower.split()
            ):
                return look.avatar_id
        return self.default_avatar_id

    def list_looks(self) -> list[dict]:
        return [{"avatar_id": lk.avatar_id, "description": lk.description} for lk in self.avatars]


# ── Anchor Roster ─────────────────────────────────────────────────────────────

ANCHORS: list[Anchor] = [
    Anchor(
        name="Alex Morgan",
        avatars=[
            AvatarLook("Andrew_public_pro1_20230614", "formal suit at the main news desk — hard news, breaking stories"),
            # AvatarLook("<avatar_id>", "casual blazer, standing — feature stories and human interest"),
            # AvatarLook("<avatar_id>", "outdoor live shot — field reports and on-location coverage"),
        ],
        voice_id="dc5370c68baa4905be87f702758df4b0",
        desk="national",
        bio="Lead anchor. Authoritative and composed. Delivers hard news with calm gravitas.",
    ),
    Anchor(
        name="Dominic Fairchild",
        avatars=[
            AvatarLook("455c4dfb69564447a3bf0d860a875f8d", "informal shirt at entertainment desk — Entertainment, celebrity, culture, breaking stories"),

            # AvatarLook("<avatar_id>", "standing in front of Capitol backdrop — election night and major votes"),
        ],
        voice_id="c701a9c07ff74f7ca9d71cbd24abb3a1",
        desk="politics",
        bio="Chief Political Correspondent. Sharp and precise. Covers the White House, Congress, and elections.",
    ),    
    Anchor(
        name="Rick Johnson",
        avatars=[
            AvatarLook("ec08a8bb0119489aa0019a090274c631", "formal suit at politics desk — White House, Congress, elections"),
            # AvatarLook("<avatar_id>", "standing in front of Capitol backdrop — election night and major votes"),
        ],
        voice_id="c701a9c07ff74f7ca9d71cbd24abb3a1",
        desk="politics",
        bio="Chief Political Correspondent. Sharp and precise. Covers the White House, Congress, and elections.",
    ),
    Anchor(
        name="Karoline Faye",
        avatars=[
            AvatarLook("f48550dcc6f648adacc6593f1d315234", "casual studio look, sitting — entertainment, celebrity, culture"),
            AvatarLook("ee21a3956e23413d8ac349901f8184d9", "casual look, standing — entertainment, celebrity, culture"),
        ],
        voice_id="5eb15f7ed1254e658faccf14e67f2cd9",
        desk="entertainment",
        bio="Entertainment Reporter. Warm and conversational. Covers culture, celebrity, film, and the arts.",
    ),
    Anchor(
        name="Shawn Green",
        avatars=[
            AvatarLook("Shawn_Suit_Front_public", "formal suit, neutral backdrop, Standing — international affairs and geopolitics"),
            AvatarLook("Shawn_Sitting_Front_public", "formal suit, neutral backdrop, Sitting — international affairs and geopolitics"),
            AvatarLook("Shawn_Casual_Sitting_Front_public", "casual, neutral backdrop, Sitting — international affairs and geopolitics"),
            # AvatarLook("<avatar_id>", "field jacket, outdoor — war zone and conflict reporting"),
            # AvatarLook("<avatar_id>", "business casual — diplomatic and economic foreign stories"),
        ],
        voice_id="e1a429dbe823406dbae5fa7c3612314d",
        desk="foreign",
        bio="Chief Foreign Correspondent. Measured and globally-informed. Covers international affairs and geopolitics.",
    ),
    Anchor(
        name="Brandon Jones",
        avatars=[
            AvatarLook("Brandon_expressive2_public", "business suit, expressive — markets, earnings, economic news"),
            # AvatarLook("<avatar_id>", "casual blazer — startup and tech business stories"),
        ],
        voice_id="3787b4ab93174952a3ad649209f1029a",
        desk="business",
        bio="Business & Finance Correspondent. Clear and data-driven. Covers markets, economy, and corporate news.",
    ),
    Anchor(
        name="Alister Blackwood",
        avatars=[
            AvatarLook("Dexter_Suit_Front_public", "dark formal suit, serious — investigative and accountability journalism"),
            # AvatarLook("<avatar_id>", "casual, no tie — long-form documentary style"),
        ],
        voice_id="088da045d8114ca39add4a75df8ed9a0",
        desk="investigative",
        bio="Senior Investigative Correspondent. Deliberate and serious. Covers accountability journalism and systemic issues.",
    ),
    Anchor(
        name="Darlene Smith",
        avatars=[
            AvatarLook("cae4682f73324118b402da17dcbb1b68", "clean studio look — health, medicine, and science reporting"),
            # AvatarLook("<avatar_id>", "lab or clinical backdrop — medical research and public health"),
        ],
        voice_id="d6a657274b184772ac28a6146f729d3a",
        desk="health_science",
        bio="Health & Science Correspondent. Calm and accessible. Covers medicine, public health, and scientific research.",
    ),

    # Add more anchors below:
    # Anchor(
    #     name="Jordan Lee",
    #     avatars=[
    #         AvatarLook("<avatar_id>", "warm casual look — human interest and feature stories"),
    #         AvatarLook("<avatar_id>", "formal studio — breaking news fill-in"),
    #     ],
    #     voice_id="<voice_id>",
    #     desk="national",
    #     bio="Warm and conversational. Strong on feature stories and human interest.",
    # ),
]

# ── Desk-indexed lookup ────────────────────────────────────────────────────────
_DESK_MAP: dict[str, list[Anchor]] = {}
for _a in ANCHORS:
    _DESK_MAP.setdefault(_a.desk, []).append(_a)


def get_anchor(name: Optional[str] = None, desk: Optional[str] = None) -> "Anchor":
    """
    Return an anchor by name (case-insensitive partial match), by desk slug,
    or randomly if neither is specified. Falls back to first anchor if not found.
    """
    if not ANCHORS:
        raise ValueError("No anchors configured in config/anchors.py")

    if name:
        name_lower = name.lower()
        for anchor in ANCHORS:
            if name_lower in anchor.name.lower():
                return anchor
        return ANCHORS[0]

    if desk:
        desk_anchors = _DESK_MAP.get(desk)
        if desk_anchors:
            return desk_anchors[0]
        return ANCHORS[0]

    return random.choice(ANCHORS)


def list_anchors() -> list[dict]:
    """Return anchor roster as a list of dicts (safe to serialize)."""
    return [{"name": a.name, "desk": a.desk, "bio": a.bio, "looks": a.list_looks()} for a in ANCHORS]


def list_anchors_for_prompt() -> str:
    """
    Return a formatted string describing each anchor and their available looks.
    Used in the Executive Producer analysis prompt so the LLM can choose the best look.
    """
    lines = []
    for a in ANCHORS:
        looks = " | ".join(f'"{lk.description}"' for lk in a.avatars)
        lines.append(f"  {a.name} ({a.desk}) — looks: {looks}")
    return "\n".join(lines)
