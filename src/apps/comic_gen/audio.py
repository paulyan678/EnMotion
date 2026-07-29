from typing import Any, Dict, List

# PR-3k · BGM preset catalog. Each entry maps a stable id → human label,
# mood tag, and a relative path under output/presets/bgm/. Presets remain
# unavailable until EnMotion ships a licensed audio file for that entry;
# the UI must never imply that a missing track will be present in exports.
BGM_PRESETS: List[Dict[str, Any]] = [
    {"id": "calm_warm",      "label": "温暖治愈",   "mood": "warm",      "url": "presets/bgm/calm_warm.mp3",      "available": False},
    {"id": "uplifting_pop",  "label": "明朗轻快",   "mood": "uplifting", "url": "presets/bgm/uplifting_pop.mp3",  "available": False},
    {"id": "epic_cinematic", "label": "史诗电影感", "mood": "epic",      "url": "presets/bgm/epic_cinematic.mp3", "available": False},
    {"id": "mystery_ambient","label": "悬疑氛围",   "mood": "mystery",   "url": "presets/bgm/mystery_ambient.mp3","available": False},
    {"id": "sad_piano",      "label": "忧伤钢琴",   "mood": "sad",       "url": "presets/bgm/sad_piano.mp3",      "available": False},
    {"id": "tension_drama",  "label": "紧张戏剧",   "mood": "tense",     "url": "presets/bgm/tension_drama.mp3",  "available": False},
    {"id": "lofi_chill",     "label": "Lo-Fi 慵懒", "mood": "chill",     "url": "presets/bgm/lofi_chill.mp3",     "available": False},
    {"id": "fantasy_dreamy", "label": "奇幻梦境",   "mood": "dreamy",    "url": "presets/bgm/fantasy_dreamy.mp3", "available": False},
]


def get_bgm_presets() -> List[Dict[str, Any]]:
    """PR-3k · Return BGM preset list. UI displays these in the Mix phase
    picker; selected entry's url is stored on Script.bgm_url."""
    return list(BGM_PRESETS)
