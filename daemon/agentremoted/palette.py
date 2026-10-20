"""Provider brand colours - the daemon is the single source of truth.

Clients keep a built-in palette as a fallback (older daemon, offline share
page), but when /api/ping carries a palette they use it: one place to change
a harness colour, no four-way drift between web, Android, BlackBerry and the
pager.

Roles:
  accent        chip / title-bar / bar colour
  heading       rich-text headings (dark surfaces)
  inline        inline code + links (dark surfaces)
  heading_light inline/heading on light surfaces (Android light theme)
  surface       dark card/background tint for this accent
  banner_bg     live-status banner background
  banner_text   live-status banner text
  live_well     live transcript item tint
  thought       thinking / meta line colour
"""

PALETTES = {
    "claude": {
        "label": "Claude", "accent": "#d97757", "heading": "#e08a5c",
        "inline": "#e0a183", "heading_light": "#a9532f",
        "inline_light": "#a9532f", "surface": "#20120c",
        "banner_bg": "#2a1f1a", "banner_text": "#e8a088",
        "live_well": "#20180f", "thought": "#8a92e0",
    },
    "grok": {
        "label": "Grok", "accent": "#00d4ff", "heading": "#b9a2f0",
        "inline": "#67e8f9", "heading_light": "#0b7c93",
        "inline_light": "#0b7c93", "surface": "#04222a",
        "banner_bg": "#0d1a1f", "banner_text": "#00d4ff",
        "live_well": "#17171c", "thought": "#9a8fb0",
    },
    "codex": {
        "label": "Codex", "accent": "#484cf4", "heading": "#7b7ef7",
        "inline": "#a9abfa", "heading_light": "#3b3fc4",
        "inline_light": "#3b3fc4", "surface": "#15162e",
        "banner_bg": "#1b1c3a", "banner_text": "#9a9df9",
        "live_well": "#16172c", "thought": "#8e90b8",
    },
    "deepseek": {
        "label": "DeepSeek", "accent": "#4d6bfe", "heading": "#7b93ff",
        "inline": "#93a8ff", "heading_light": "#5b3fa8",
        "inline_light": "#5b3fa8", "surface": "#0b1024",
        "banner_bg": "#12162a", "banner_text": "#7b93ff",
        "live_well": "#14161f", "thought": "#8a92e0",
    },
    "cursor": {
        "label": "Cursor", "accent": "#d946ef", "heading": "#e879f9",
        "inline": "#f0abfc", "heading_light": "#a21caf",
        "inline_light": "#a21caf", "surface": "#24082a",
        "banner_bg": "#24122a", "banner_text": "#e879f9",
        "live_well": "#1a1220", "thought": "#a98fb5",
    },
    "copilot": {
        "label": "Copilot", "accent": "#8957e5", "heading": "#b392f0",
        "inline": "#d2a8ff", "heading_light": "#6e40c9",
        "inline_light": "#6e40c9", "surface": "#160b2a",
        "banner_bg": "#1a1228", "banner_text": "#b392f0",
        "live_well": "#17131f", "thought": "#9a8fb0",
    },
    "codebuddy": {
        "label": "CodeBuddy", "accent": "#58c08b", "heading": "#7fd4a8",
        "inline": "#a8e3c8", "heading_light": "#268253",
        "inline_light": "#268253", "surface": "#10281b",
        "banner_bg": "#0d211a", "banner_text": "#8fdcb4",
        "live_well": "#131f1a", "thought": "#7a9a8a",
    },
}

# Aliases clients may send (provider ids that share a palette).
ALIASES = {"dsh": "deepseek", "cursor-agent": "cursor",
           "github-copilot": "copilot", "cbc": "codebuddy"}


def canonical(name: str) -> str:
    key = str(name or "").strip().lower()
    return ALIASES.get(key, key)


def palette_for(name: str) -> dict:
    """The palette for a provider id ({} when unknown)."""
    return dict(PALETTES.get(canonical(name), {}))


def palettes_for(names) -> dict:
    """{provider_id: palette} for the given ids, aliases resolved."""
    out = {}
    for name in names or []:
        pal = palette_for(name)
        if pal:
            out[str(name)] = pal
    return out
