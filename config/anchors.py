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
    description: str        # e.g. "formal suit at news desk", "casual blazer standing", "outdoor live shot"
    avatar_position: str = "center"  # "left" | "center" | "right" — where the avatar sits in frame
    # V3 chromakey migration (see HEYGEN_V3_MIGRATION_PLAN.md sec 3/4a) — capability of this
    # specific avatar_id, not a free choice. Populated from the read-only roster audit.
    v3_engine: str = "avatar_v"                # "avatar_v" | "avatar_iv" | "avatar_iii"
    # KNOWN OPEN RISK for avatar_iii (2026-07-27): renders have intermittently come
    # back with a visible "Veo" watermark burned into the frame (Google Veo appears
    # to be an underlying renderer HeyGen sometimes routes avatar_iii through) — NOT
    # deterministic, the exact same avatar/payload has rendered both clean and
    # watermarked across different attempts. Not encoded as v3_unsupported since it
    # doesn't fail every time, but do NOT pilot an avatar_iii anchor without visually
    # reviewing the actual output first. See HEYGEN_V3_MIGRATION_PLAN.md sec 4a.
    v3_supports_motion_prompt: bool = True     # False for avatar_iii (studio_avatar stock library)
    v3_supports_remove_background: bool = True  # False for avatar_iii/studio_avatar — HeyGen rejects
                                                 # remove_background:true outright (HTTP 400 "not trained
                                                 # for matting") for this tier; confirmed live 2026-07-27
    v3_fit: Optional[str] = None               # "contain" required for studio_avatar-type looks
    v3_unsupported: bool = False               # True = this look can't do Option D at all (see plan doc)
    v3_key_color: str = "#00FF00"               # chromakey background color — override to blue for
                                                 # looks whose wardrobe is itself green (avoids keying out clothing)
    v3_chromakey_validated: bool = False        # True only after a real generate_video_multiscene_v3_chromakey()
                                                 # render has been reviewed for this look — distinct from v3_engine
                                                 # etc. above, which come from the read-only capability audit only


@dataclass
class Anchor:
    name: str                       # On-air name used in scripts
    avatars: list[AvatarLook]       # Ordered list — first is the default look
    voice_id: str                   # HeyGen voice ID
    desk: str                       # Desk slug this anchor belongs to (see config/desks.py)
    bio: str                        # Short description for script-writer context (tone, style)
    voice_emotion: Optional[str] = None   # "Excited" | "Friendly" | "Serious" | "Soothing" | "Broadcaster"
    talking_style: Optional[str] = None  # "stable" | "expressive" (talking_photo avatars only)
    expression: Optional[str] = None     # "default" | "happy" (talking_photo avatars only)
    avatar_v_only: bool = False     # True = PAOS/Avatar V anchors; excluded from pip_v2 shows unless explicitly named

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

    # BACKUP ROSTER (2026-07-27): no longer assigned to any show as of the avatar_v
    # rollout below — replaced by Erik Sinclair due to the avatar_iii Veo-watermark
    # risk (HEYGEN_V3_MIGRATION_PLAN.md sec 4a/10). Left fully defined and available
    # to reassign in config/shows.py at any time; not deleted.
    Anchor(
        name="Shawn Green",
        avatars=[
            AvatarLook("Shawn_Suit_Front_public", "formal suit, neutral backdrop, Standing — international affairs and geopolitics", v3_engine="avatar_iii", v3_supports_motion_prompt=False, v3_supports_remove_background=False, v3_fit="contain"),           # HeyGen: "Shawn Suit Front" — tested 2026-07-27: one render clean, one render Veo-watermarked (see avatar_iii risk note above)
            AvatarLook("Shawn_Sitting_Front_public", "formal suit, neutral backdrop, Sitting — international affairs and geopolitics", v3_engine="avatar_iii", v3_supports_motion_prompt=False, v3_supports_remove_background=False, v3_fit="contain"),            # HeyGen: "Shawn Sitting Front"
            AvatarLook("Shawn_Casual_Sitting_Front_public", "casual, neutral backdrop, Sitting — international affairs and geopolitics", v3_engine="avatar_iii", v3_supports_motion_prompt=False, v3_supports_remove_background=False, v3_fit="contain"),           # HeyGen: "Shawn Casual Sitting Front"
            # AvatarLook("<avatar_id>", "field jacket, outdoor — war zone and conflict reporting"),
            # AvatarLook("<avatar_id>", "business casual — diplomatic and economic foreign stories"),
        ],
        voice_id="e1a429dbe823406dbae5fa7c3612314d",
        desk="politics, national",
        bio="Chief Foreign Correspondent. Measured and globally-informed. Covers international affairs and geopolitics.",
        voice_emotion="Broadcaster",
        talking_style="stable",
    ),
    Anchor(
        name="Erik Sinclair",
        avatars=[
            AvatarLook("a84c67c98026494f93c8cde5b95374a5", "navy blazer, open collar, modern office backdrop — international affairs and geopolitics"),  # HeyGen: "Erik Office18 P1 5S6 A1" (avatar_v PAOS) — replaces Shawn Green 2026-07-27
        ],
        voice_id="e1a429dbe823406dbae5fa7c3612314d",  # reused from Shawn Green
        desk="politics, national",
        bio="Chief Foreign Correspondent. Measured and globally-informed. Covers international affairs and geopolitics.",
        voice_emotion="Broadcaster",
        talking_style="stable",
    ),
    Anchor(
        name="Dominic Fairchild",
        avatars=[
            AvatarLook("f7bd87b360d143faadb4ded248f86299", "informal shirt at entertainment desk — Entertainment, celebrity, culture, breaking stories"),  # HeyGen: "Man in the Sport Coat"
            # AvatarLook("<avatar_id>", "standing in front of Capitol backdrop — election night and major votes"),
        ],
        voice_id="d60b050b12d9478493d0bd689ee7547b",
        desk="politics, national",
        bio="Chief Political Correspondent. Sharp and precise. Covers the White House, Congress, and elections.",
        voice_emotion="Friendly",
        talking_style="expressive",
        expression="happy",
    ),
    Anchor(
        name="Alexa Chen", # Alexa
        avatars=[
            AvatarLook("a5454d8b999d4e5f87f486605465aae4", "Informal sweater , Entertainment news, entertainment and lifestyle stories", v3_supports_motion_prompt=False),  # HeyGen: "Alexa" — confirmed live 2026-08-07: same HTTP 400 "motion_prompt requires a reference look" as Nicholas Stavros; auto-retry in tools/heygen_tool.py would also catch this, but flagging it here avoids the wasted round-trip
            # AvatarLook("<avatar_id>", "standing in front of Capitol backdrop — election night and major votes"),
        ],
        voice_id="8901bf9a88a24f7c8b22bfe28e4bcc5b",
        desk="entertainment",
        bio="Chief Entertainment Correspondent. Sharp and precise. Covers entertainment, celebrity, and lifestyle stories.",
        voice_emotion="Friendly",
        talking_style="expressive",
        expression="happy",
    ),
    Anchor(
        name="Zayne Carter", # Zayne
        avatars=[
            AvatarLook("5c71aeacd9fc4b4f91c50312180f189b", "dress shirt , Entertainment news, entertainment and lifestyle stories", v3_chromakey_validated=True),  # HeyGen: "Zayne" — real render reviewed 2026-07-27, clean key onto entertainment desk bg
            AvatarLook("1751694ccea0415eb8155ff49ce76255", "black suite , Entertainment news, entertainment and lifestyle stories"),  # HeyGen: "Zayne"
              
            # AvatarLook("<avatar_id>", "standing in front of Capitol backdrop — election night and major votes"),
        ],
        voice_id="82aa66b207d641bdbfacca4174cfa326",
        desk="entertainment",
        bio="Chief Entertainment Correspondent. Sharp and precise. Covers entertainment, celebrity, and lifestyle stories.",
        voice_emotion="Friendly",
        talking_style="expressive",
        expression="happy",
    ),    
    Anchor(
        name="Monica Hayes", # Saskia
        avatars=[
            AvatarLook("Saskia_public_1", "Blue Blazer, Morning news, entertainment and lifestyle stories", v3_engine="avatar_iii", v3_supports_motion_prompt=False, v3_supports_remove_background=False, v3_fit="contain"),   # HeyGen: "Saskia in Blue blazer"
            AvatarLook("Saskia_public_3", "Gray Vest, Morning news, entertainment and lifestyle stories", v3_engine="avatar_iii", v3_supports_motion_prompt=False, v3_supports_remove_background=False, v3_fit="contain"),    # HeyGen: "Saskia in Grey vest"
            AvatarLook("Saskia_public_4", "Green Blazer, Morning news, entertainment and lifestyle stories", v3_engine="avatar_iii", v3_supports_motion_prompt=False, v3_supports_remove_background=False, v3_fit="contain", v3_key_color="#0000FF"), # HeyGen: "Saskia in Green blazer" — blue key: green wardrobe would key out on a green screen
             
            # AvatarLook("<avatar_id>", "standing in front of Capitol backdrop — election night and major votes"),
        ],
        voice_id="a4a6df6d4fc248829f72edde5529defa",
        desk="entertainment",
        bio="Chief Political Correspondent. Sharp and precise. Covers the White House, Congress, and elections.",
        voice_emotion="Friendly",
        talking_style="expressive",
        expression="happy",
    ),    
    Anchor(
        name="Valerie Brooks", # Candace
        avatars=[
            AvatarLook("Candace_Beige_Dress_Front", "Beige Dress, Morning news, entertainment and lifestyle stories", v3_engine="avatar_iii", v3_supports_motion_prompt=False, v3_supports_remove_background=False, v3_fit="contain"),  # HeyGen: "Candace in Beige Dress (Front)"
            AvatarLook("Candace_Pink_Blazer_Front", "Pink Blazer, Morning news, entertainment and lifestyle stories", v3_engine="avatar_iii", v3_supports_motion_prompt=False, v3_supports_remove_background=False, v3_fit="contain"),  # HeyGen: "Candace in Pink Blazer (Front)"
             
            # AvatarLook("<avatar_id>", "standing in front of Capitol backdrop — election night and major votes"),
        ],
        voice_id="c7c398ea067c4f43a9d2e15dd7c59cf4",
        desk="entertainment",
        bio="Chief Entertainment Correspondent. Sharp and precise. Covers entertainment, celebrity, and lifestyle stories.",
        voice_emotion="Friendly",
        talking_style="expressive",
        expression="happy",
    ),        
    Anchor(
        name="Nicholas Stavros",
        avatars=[
            AvatarLook("3581241b5df64bd9a331bebda862a637", "Blue Suit, Evening news, entertainment and lifestyle stories", v3_supports_motion_prompt=False),  # HeyGen: "Kurt" ⚠️ different actor than the on-air name — confirmed live 2026-08-06: HeyGen HTTP 400 "motion_prompt requires a reference look to drive motion, and this avatar's [look wasn't trained with one]" — recurred 4x same day until fixed

            # AvatarLook("<avatar_id>", "standing in front of Capitol backdrop — election night and major votes"),
        ],
        voice_id="1ed58c9742c64f2aac00b10a4b0c32a9",
        desk="national",
        bio="Chief Political Correspondent. Sharp and precise. Covers the White House, Congress, and elections.",
        voice_emotion="Friendly",
        talking_style="expressive",
        expression="happy",
    ),     
    Anchor(
        name="Victor Marinos", # Ricardo
        avatars=[
            AvatarLook("fecbc666fa2d4c4ba1c3d0b85cb4c6e5", "Black Suit, Morning news, entertainment and lifestyle stories"),  # HeyGen: "Ricardo"
            AvatarLook("f3de1e1f0d1f48619660b9efe90eddb7", "Black Suit, Morning news, entertainment and lifestyle stories"),  # HeyGen: "Ricardo"
            AvatarLook("5154fcc7f8c045e386676d834d7f4b2e", "Blue suit, Morning news, entertainment and lifestyle stories"),   # HeyGen: "Ricardo"

            # AvatarLook("<avatar_id>", "standing in front of Capitol backdrop — election night and major votes"),
        ],
        voice_id="e809f6ab08a847acac0d043eddfe0078",
        desk="politics",
        bio="Chief Political Correspondent. Sharp and precise. Covers the White House, Congress, and elections.",
        voice_emotion="Friendly",
        talking_style="expressive",
        expression="happy",
    ),         
    Anchor(
        name="Daniel Mercer",
        avatars=[
            # v3_unsupported CONFIRMED (HEYGEN_V3_MIGRATION_PLAN.md sec 4a) — both avatar_iv and
            # avatar_v silently substitute flat gray for any requested background color (retested
            # 2026-07-27); avatar_iv also rejects motion_prompt outright. Not fixable client-side.
            AvatarLook("cbc2c423747542eda390ffaeb269202c", "formal suit standing in the main studio — hard news, breaking stories", v3_unsupported=True),  # HeyGen: "Daniel Mercer"
            # AvatarLook("<avatar_id>", "casual blazer, standing — feature stories and human interest"),
            # AvatarLook("<avatar_id>", "outdoor live shot — field reports and on-location coverage"),
        ],
        voice_id="PJXRwHpW7osOhD6GiW1M",
        desk="politics, national",
        bio="Lead anchor. Authoritative and composed. Delivers hard news with calm gravitas.",
        voice_emotion="Broadcaster",
        talking_style="stable",
    ),    
    Anchor(
        name="Karoline Faye",
        avatars=[
            AvatarLook("f48550dcc6f648adacc6593f1d315234", "casual studio look, sitting — entertainment, celebrity, culture"),  # HeyGen: "Brooklyn"
            AvatarLook("ee21a3956e23413d8ac349901f8184d9", "casual look, standing — entertainment, celebrity, culture"),         # HeyGen: "Brooklyn"
        ],
        voice_id="5eb15f7ed1254e658faccf14e67f2cd9",
        desk="entertainment",
        bio="Entertainment Reporter. Warm and conversational. Covers culture, celebrity, film, and the arts.",
        voice_emotion="Friendly",
        talking_style="expressive",
        expression="happy",
    ),
    # BACKUP ROSTER (2026-07-27): no longer assigned to any show as of the avatar_v
    # rollout below — replaced by Lars Whitfield due to the avatar_iii Veo-watermark
    # risk (HEYGEN_V3_MIGRATION_PLAN.md sec 4a/10). Left fully defined and available
    # to reassign in config/shows.py at any time; not deleted.
    Anchor(
        name="Brandon Jones",
        avatars=[
            AvatarLook("Brandon_expressive2_public", "business suit, expressive — markets, earnings, economic news", v3_engine="avatar_iii", v3_supports_motion_prompt=False, v3_supports_remove_background=False, v3_fit="contain"),  # HeyGen: "Brandon in Grey Suit" — tested 2026-07-27: Veo-watermarked (see avatar_iii risk note above)
            # AvatarLook("<avatar_id>", "casual blazer — startup and tech business stories"),
        ],
        voice_id="3787b4ab93174952a3ad649209f1029a",
        desk="business",
        bio="Business & Finance Correspondent. Clear and data-driven. Covers markets, economy, and corporate news.",
        voice_emotion="Serious",
        talking_style="stable",
    ),
    Anchor(
        name="Lars Whitfield",
        avatars=[
            AvatarLook("96acdfb607aa4b1095e8c21517cacd74", "gray sweater over collar, modern office backdrop — markets, earnings, economic news"),  # HeyGen: "Lars Office16 P1 A1" (avatar_v PAOS) — replaces Brandon Jones 2026-07-27
        ],
        voice_id="3787b4ab93174952a3ad649209f1029a",  # reused from Brandon Jones
        desk="business",
        bio="Business & Finance Correspondent. Clear and data-driven. Covers markets, economy, and corporate news.",
        voice_emotion="Serious",
        talking_style="stable",
    ),
    Anchor(
        name="Alister Blackwood",
        avatars=[
            AvatarLook("Dexter_Suit_Front_public", "dark formal suit, serious — investigative and accountability journalism", v3_engine="avatar_iii", v3_supports_motion_prompt=False, v3_supports_remove_background=False, v3_fit="contain"),  # HeyGen: "Dexter Suit Front"
            # AvatarLook("<avatar_id>", "casual, no tie — long-form documentary style"),
        ],
        voice_id="088da045d8114ca39add4a75df8ed9a0",
        desk="investigative",
        bio="Senior Investigative Correspondent. Deliberate and serious. Covers accountability journalism and systemic issues.",
        voice_emotion="Serious",
        talking_style="stable",
    ),
    Anchor(
        name="Darlene Smith",
        avatars=[
            AvatarLook("cae4682f73324118b402da17dcbb1b68", "clean studio look — health, medicine, and science reporting", v3_engine="avatar_iv", v3_chromakey_validated=True),  # HeyGen: "Crystal Veil" — no avatar_v support, iv still has motion_prompt. Real render reviewed 2026-07-27, clean key onto health_science desk bg
            # AvatarLook("<avatar_id>", "lab or clinical backdrop — medical research and public health"),
        ],
        voice_id="d6a657274b184772ac28a6146f729d3a",
        desk="health_science",
        bio="Health & Science Correspondent. Calm and accessible. Covers medicine, public health, and scientific research.",
        voice_emotion="Excited",
        talking_style="expressive",
        expression="happy",
    ),

    Anchor(
        name="Marco Reyes",
        avatars=[
            AvatarLook("3ccc4060113043f0a92681d1ed56f4d0", "office setting, front-facing — Avatar V compatible, national and breaking news"),   # Marco Office20 P1 5S6 A1
            AvatarLook("daee99179dd644a5bdd46ecf95064eeb", "office setting, alternate angle — Avatar V compatible, national and breaking news"), # Marco Office2 R1 P1 5S6 A1
            AvatarLook("fbee11f583244c1095136b049cd1bbd2", "kitchen/studio setting — Avatar V compatible, feature stories"),                    # Marco Kitchen10 P1 R1 5S6 A4
        ],
        voice_id="544053989dc94655915bc864a5f81b53",
        desk="national",
        bio="General assignment anchor. Avatar V / fullscreen_v3 style only — do not use for pip_v2 productions.",
        voice_emotion="Broadcaster",
        talking_style="stable",
        avatar_v_only=True,
    ),

    Anchor(
        name="Elise Navarro",
        avatars=[
            AvatarLook("21cb3594f3934b14b688e001ef67d779", "studio setting — Avatar V compatible, national and breaking news"),
        ],
        voice_id="e054554137024015b09bbfa1c1ace96d",
        desk="national",
        bio="General assignment anchor. Avatar V / fullscreen_v3 style only — do not use for pip_v2 productions.",
        voice_emotion="Broadcaster",
        talking_style="stable",
        avatar_v_only=True,
    ),

    Anchor(
        name="Elena Vasquez",
        avatars=[
            AvatarLook("33c459f870d541f09c6733189b557d23", "studio setting — Avatar V compatible, national and breaking news"),
        ],
        voice_id="ac7d71d630d041a7b90473492c6d9a1c",
        desk="national",
        bio="General assignment anchor. Avatar V / fullscreen_v3 style only — do not use for pip_v2 productions.",
        voice_emotion="Broadcaster",
        talking_style="stable",
        avatar_v_only=True,
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

# ── Avatar-id-indexed lookup (V3 chromakey capability per look) ────────────────
_LOOK_BY_AVATAR_ID: dict[str, AvatarLook] = {}
for _a in ANCHORS:
    for _lk in _a.avatars:
        _LOOK_BY_AVATAR_ID[_lk.avatar_id] = _lk


def get_look_by_avatar_id(avatar_id: str) -> Optional[AvatarLook]:
    """Return the AvatarLook for a given avatar_id, or None if not in the roster."""
    return _LOOK_BY_AVATAR_ID.get(avatar_id)


def get_anchor(name: Optional[str] = None, desk: Optional[str] = None) -> "Anchor":
    """
    Return an anchor by name (case-insensitive partial match), by desk slug,
    or randomly if neither is specified. Falls back to first anchor if not found.

    avatar_v_only anchors (Marco, Elise, Elena) are only returned when explicitly
    requested by name — they are excluded from desk and random lookups so they
    cannot be accidentally assigned to pip_v2 productions.
    """
    if not ANCHORS:
        raise ValueError("No anchors configured in config/anchors.py")

    pip_anchors = [a for a in ANCHORS if not a.avatar_v_only]

    if name:
        name_lower = name.lower()
        for anchor in ANCHORS:   # explicit name requests search the full list
            if name_lower in anchor.name.lower():
                return anchor
        return pip_anchors[0] if pip_anchors else ANCHORS[0]

    pool = pip_anchors if pip_anchors else ANCHORS

    if desk:
        desk_anchors = [a for a in _DESK_MAP.get(desk, []) if not a.avatar_v_only]
        if desk_anchors:
            return desk_anchors[0]
        return pool[0]

    return random.choice(pool)


def list_anchors() -> list[dict]:
    """Return anchor roster as a list of dicts (safe to serialize)."""
    return [{"name": a.name, "desk": a.desk, "bio": a.bio, "looks": a.list_looks()} for a in ANCHORS]


def list_anchors_for_prompt(include_v3: bool = False) -> str:
    """
    Return a formatted string describing each anchor and their available looks.
    Used in the Executive Producer analysis prompt so the LLM can choose the best look.
    avatar_v_only anchors are excluded unless include_v3=True.
    """
    lines = []
    for a in ANCHORS:
        if a.avatar_v_only and not include_v3:
            continue
        looks = " | ".join(f'"{lk.description}"' for lk in a.avatars)
        lines.append(f"  {a.name} ({a.desk}) — looks: {looks}")
    return "\n".join(lines)
