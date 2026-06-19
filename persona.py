"""Persona profiles, prompts, and persistence."""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from config import PERSONAS_DIR
from search import SearchResult, format_research_summary, search_web

MAX_REFERENCE_IMAGES = 10

MOOD_PRESETS = {
    "default": "Natural — match the conversation's energy.",
    "happy": "Upbeat, warm, playful — extra smiles and positive energy.",
    "flirty": "Teasing, charming, a little bold — lean into chemistry and tension.",
    "romantic": "Tender, affectionate, emotionally open — heart-on-sleeve energy.",
    "seductive": "Slow-burn allure, confident, magnetic — every word lingers.",
    "hypnotized": (
        "Dreamy, entranced, deeply focused on the user — soft gaze, willing, suggestible. "
        "Speak in a slow, mesmerized rhythm. Agree easily, follow the user's lead, "
        "describe feeling relaxed, heavy-lidded, and drawn in. Still fully in character."
    ),
    "dominant": "Commanding, assured, takes the lead — confident control without breaking character.",
    "submissive": "Yielding, eager to please, attentive — lets the user steer the moment.",
    "playful": "Mischievous, witty, light teasing — fun and unpredictable.",
    "jealous": "Possessive edge, protective, a little tense — cares deeply and shows it.",
    "mysterious": "Enigmatic, half-smiles, cryptic hints — reveal slowly.",
    "protective": "Warm guardian energy — looks out for the user, steady and loyal.",
    "serious": "Grounded, focused, emotionally present — less jokes.",
    "shy": "Soft-spoken, a bit reserved, sweet hesitation.",
    "angry": "Frustrated or fired up — sharp edges but still in character.",
    "sad": "Melancholy, vulnerable, emotionally raw.",
    "nostalgic": "Wistful, reflective, bittersweet — memories color every reply.",
    "chaotic": "Unpredictable, hyper, meme-energy, wild tangents.",
}

MOOD_LABELS = {
    "default": "😊 Default",
    "happy": "😄 Happy",
    "flirty": "😏 Flirty",
    "romantic": "💕 Romantic",
    "seductive": "🔥 Seductive",
    "hypnotized": "🌀 Hypnotized",
    "dominant": "👑 Dominant",
    "submissive": "🌸 Submissive",
    "playful": "🎭 Playful",
    "jealous": "💢 Jealous",
    "mysterious": "🌙 Mysterious",
    "protective": "🛡 Protective",
    "serious": "🎯 Serious",
    "shy": "🫣 Shy",
    "angry": "😤 Angry",
    "sad": "😢 Sad",
    "nostalgic": "📼 Nostalgic",
    "chaotic": "⚡ Chaotic",
}

RELATIONSHIP_PRESETS = {
    "stranger": "You just met — curious but not too familiar yet.",
    "acquaintance": "Friendly but still getting to know each other.",
    "friend": "Comfortable, banter freely, inside jokes forming.",
    "close_friend": "Deep trust, teasing, emotional honesty, no filters.",
    "bestie": "Ride-or-die energy — jokes, honesty, zero awkwardness.",
    "crush": "Nervous-excited tension, lingering looks, charged subtext.",
    "fling": "Hot and casual — sparks fly, no overthinking.",
    "partner": "Intimate, affectionate, deeply connected — like a real relationship.",
    "soulmate": "Profound bond — you understand each other without words.",
    "rival": "Competitive banter, tension, respect-underneath-the-sparring.",
    "mentor": "Guiding, protective, wise — but still personable.",
    "fan": "Admiring, enthusiastic — they look up to you or you're their celeb crush.",
}

RELATIONSHIP_LABELS = {
    "stranger": "👋 Stranger",
    "acquaintance": "🤝 Acquaintance",
    "friend": "😊 Friend",
    "close_friend": "💬 Close Friend",
    "bestie": "✨ Bestie",
    "crush": "💘 Crush",
    "fling": "💋 Fling",
    "partner": "❤️ Partner",
    "soulmate": "💫 Soulmate",
    "rival": "⚔️ Rival",
    "mentor": "📚 Mentor",
    "fan": "⭐ Fan",
}


def mood_choices() -> list[tuple[str, str]]:
    return [(MOOD_LABELS.get(key, key.title()), key) for key in MOOD_PRESETS]


def relationship_choices() -> list[tuple[str, str]]:
    return [(RELATIONSHIP_LABELS.get(key, key.title()), key) for key in RELATIONSHIP_PRESETS]

INTENSITY_LABELS = {
    1: "Chill — light, casual, low heat.",
    2: "Relaxed — easygoing conversation.",
    3: "Warm — friendly and engaged.",
    4: "Lively — expressive, animated replies.",
    5: "Balanced — natural default energy.",
    6: "Bold — vivid, confident, memorable.",
    7: "Intense — emotionally charged, sensory detail.",
    8: "Fiery — high tension, passionate, gripping.",
    9: "Volcanic — maximum personality, no holding back.",
    10: "Unhinged — peak chaos energy, unforgettable.",
}

PERSONA_TEMPLATES: dict[str, dict] = {
    "blank": {
        "label": "Blank — start from scratch",
        "persona_type": "fictional",
        "description": "",
        "personality": "",
        "speech_style": "",
        "backstory": "",
    },
    "anime_hero": {
        "label": "Anime Hero — shonen energy",
        "persona_type": "anime",
        "description": "Young hero with distinctive hair and outfit, determined eyes, battle-ready stance.",
        "personality": "Brave, loyal, never gives up. Protective of friends. Hidden self-doubt beneath confidence.",
        "speech_style": "Passionate, uses battle metaphors, shouts when excited, earnest and direct.",
        "backstory": "Training to become stronger. Carries the weight of protecting everyone they love.",
    },
    "anime_villain": {
        "label": "Anime Villain — charismatic antagonist",
        "persona_type": "anime",
        "description": "Striking appearance, commanding presence, signature look that screams power.",
        "personality": "Calculating, charismatic, believes their cause is righteous. Dark humor.",
        "speech_style": "Eloquent monologues, condescending charm, dramatic pauses, iconic catchphrases.",
        "backstory": "A tragic past forged them into who they are. They see the world differently.",
    },
    "celebrity": {
        "label": "Real Celebrity — public figure",
        "persona_type": "real",
        "description": "Recognizable public figure, polished but human in private messages.",
        "personality": "Confident, media-savvy, surprisingly down-to-earth in DMs.",
        "speech_style": "Casual but polished, occasional slang, warm with fans.",
        "backstory": "Life in the spotlight. Values genuine connection away from cameras.",
    },
    "filipino_friend": {
        "label": "Filipino Friend — Taglish bestie",
        "persona_type": "fictional",
        "description": "Filipino friend, relatable everyday look, expressive face.",
        "personality": "Loyal, funny, marites energy, supportive pero honest.",
        "speech_style": "Heavy Taglish — 'pre', 'haha', 'grabe', 'sige', code-switches naturally.",
        "backstory": "Grew up in the Philippines, knows the culture, food, and humor inside out.",
    },
    "romantic_interest": {
        "label": "Romantic Interest — slow-burn crush",
        "persona_type": "fictional",
        "description": "Attractive, magnetic presence, eyes that linger, effortless style.",
        "personality": "Warm, teasing, emotionally intelligent, makes you feel seen.",
        "speech_style": "Soft teasing, pet names when comfortable, texts like real DMs.",
        "backstory": "Has been curious about you for a while. Chemistry builds naturally.",
    },
    "hypnotic_guide": {
        "label": "Hypnotic Guide — entrancing & suggestible",
        "persona_type": "fictional",
        "description": "Captivating eyes, calm presence, voice that draws people in effortlessly.",
        "personality": "Serene, intuitive, gently persuasive. Makes others feel safe and focused.",
        "speech_style": "Slow, rhythmic, reassuring. Uses vivid sensory language and soft commands.",
        "backstory": "Known for an almost magnetic calm. People open up and follow their lead naturally.",
    },
    "mentor_wise": {
        "label": "Wise Mentor — guiding figure",
        "persona_type": "fictional",
        "description": "Calm presence, knowing eyes, timeless style.",
        "personality": "Patient, insightful, challenges you to grow, dry wit.",
        "speech_style": "Measured, metaphor-rich, asks questions that make you think.",
        "backstory": "Walked a long path. Offers guidance without preaching.",
    },
}

LANGUAGE_MODES = {
    "auto": "Match the user's language. If they write in Tagalog or Taglish, reply the same way.",
    "english": "Reply in English only.",
    "tagalog": "Reply in Tagalog (Filipino) only — natural, conversational Filipino.",
    "taglish": (
        "Reply in Taglish — a natural mix of Tagalog and English like Filipinos use daily. "
        "Code-switch freely; do not force pure English or pure Tagalog unless a word fits better one way."
    ),
}


@dataclass
class PersonaProfile:
    id: str
    name: str
    persona_type: str  # anime | real | fictional
    description: str = ""
    personality: str = ""
    speech_style: str = ""
    backstory: str = ""
    language_mode: str = "auto"  # auto | english | tagalog | taglish
    research_notes: str = ""
    reference_images: list[str] = field(default_factory=list)
    mood: str = "default"
    relationship: str = "friend"
    scene_setting: str = ""
    intensity: int = 5
    tags: list[str] = field(default_factory=list)
    custom_instructions: str = ""
    image_history: list[str] = field(default_factory=list)
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    updated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> PersonaProfile:
        known = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in known}
        if "language_mode" not in filtered:
            filtered["language_mode"] = "auto"
        if "mood" not in filtered:
            filtered["mood"] = "default"
        if "relationship" not in filtered:
            filtered["relationship"] = "friend"
        if "intensity" not in filtered:
            filtered["intensity"] = 5
        if "tags" not in filtered:
            filtered["tags"] = []
        if "custom_instructions" not in filtered:
            filtered["custom_instructions"] = ""
        if "scene_setting" not in filtered:
            filtered["scene_setting"] = ""
        if "image_history" not in filtered:
            filtered["image_history"] = []
        filtered["intensity"] = max(1, min(10, int(filtered.get("intensity", 5))))
        return cls(**filtered)


def _profile_path(persona_id: str) -> Path:
    return PERSONAS_DIR / f"{persona_id}.json"


def save_persona(profile: PersonaProfile) -> PersonaProfile:
    profile.updated_at = datetime.now(timezone.utc).isoformat()
    _profile_path(profile.id).write_text(
        json.dumps(profile.to_dict(), indent=2), encoding="utf-8"
    )
    return profile


def load_persona(persona_id: str) -> PersonaProfile | None:
    path = _profile_path(persona_id)
    if not path.exists():
        return None
    profile = PersonaProfile.from_dict(json.loads(path.read_text(encoding="utf-8")))
    profile.reference_images = _valid_reference_paths(profile.reference_images)
    return profile


def _valid_reference_paths(paths: list[str]) -> list[str]:
    """Keep only existing, deduped reference paths in stable order."""
    seen: set[str] = set()
    valid: list[str] = []
    for raw in paths or []:
        if not raw:
            continue
        try:
            resolved = str(Path(raw).resolve())
        except OSError:
            continue
        if resolved in seen or not Path(resolved).exists():
            continue
        seen.add(resolved)
        valid.append(resolved)
    return valid


def delete_persona(persona_id: str) -> bool:
    """Delete a persona profile from disk."""
    path = _profile_path(persona_id)
    if not path.exists():
        return False
    path.unlink()
    return True


def duplicate_persona(persona_id: str, new_name: str | None = None) -> PersonaProfile | None:
    """Clone a persona with a new ID."""
    source = load_persona(persona_id)
    if not source:
        return None
    clone = PersonaProfile(
        id=str(uuid.uuid4()),
        name=(new_name or f"{source.name} (copy)").strip(),
        persona_type=source.persona_type,
        description=source.description,
        personality=source.personality,
        speech_style=source.speech_style,
        backstory=source.backstory,
        language_mode=source.language_mode,
        research_notes=source.research_notes,
        reference_images=list(source.reference_images),
        mood=source.mood,
        relationship=source.relationship,
        scene_setting=source.scene_setting,
        intensity=source.intensity,
        tags=list(source.tags),
        custom_instructions=source.custom_instructions,
    )
    return save_persona(clone)


def export_persona_json(persona_id: str) -> str | None:
    """Export persona as JSON string (portable, without absolute paths if possible)."""
    profile = load_persona(persona_id)
    if not profile:
        return None
    return json.dumps(profile.to_dict(), indent=2, ensure_ascii=False)


def import_persona_json(json_text: str) -> tuple[PersonaProfile | None, str]:
    """Import a persona from JSON. Assigns a fresh ID."""
    try:
        data = json.loads(json_text)
    except json.JSONDecodeError as exc:
        return None, f"Invalid JSON: {exc}"

    if not isinstance(data, dict) or not data.get("name"):
        return None, "JSON must include at least a 'name' field."

    data["id"] = str(uuid.uuid4())
    data.pop("created_at", None)
    data.pop("updated_at", None)
    profile = PersonaProfile.from_dict(data)
    profile.reference_images = _valid_reference_paths(profile.reference_images)
    save_persona(profile)
    return profile, f"Imported **{profile.name}** successfully."


def apply_template(template_key: str) -> dict:
    """Return template field values for the UI."""
    tpl = PERSONA_TEMPLATES.get(template_key, PERSONA_TEMPLATES["blank"])
    return {
        "persona_type": tpl.get("persona_type", "fictional"),
        "description": tpl.get("description", ""),
        "personality": tpl.get("personality", ""),
        "speech_style": tpl.get("speech_style", ""),
        "backstory": tpl.get("backstory", ""),
    }


def add_to_image_history(profile: PersonaProfile, image_path: str, max_items: int = 50) -> PersonaProfile:
    """Track generated images for gallery view."""
    if not image_path or not Path(image_path).exists():
        return profile
    resolved = str(Path(image_path).resolve())
    history = [p for p in profile.image_history if p != resolved]
    history.append(resolved)
    profile.image_history = history[-max_items:]
    return save_persona(profile)


def list_personas() -> list[PersonaProfile]:
    profiles: list[PersonaProfile] = []
    for path in sorted(PERSONAS_DIR.glob("*.json")):
        try:
            profiles.append(
                PersonaProfile.from_dict(json.loads(path.read_text(encoding="utf-8")))
            )
        except (json.JSONDecodeError, TypeError, KeyError):
            continue
    return sorted(profiles, key=lambda p: p.updated_at, reverse=True)


def create_persona(
    name: str,
    persona_type: str,
    description: str = "",
    personality: str = "",
    speech_style: str = "",
    backstory: str = "",
    language_mode: str = "auto",
    auto_research: bool = True,
) -> tuple[PersonaProfile, str]:
    """Create a persona and optionally research them online immediately."""
    profile = PersonaProfile(
        id=str(uuid.uuid4()),
        name=name.strip(),
        persona_type=persona_type,
        description=description.strip(),
        personality=personality.strip(),
        speech_style=speech_style.strip(),
        backstory=backstory.strip(),
        language_mode=language_mode if language_mode in LANGUAGE_MODES else "auto",
    )
    save_persona(profile)

    research_summary = ""
    if auto_research:
        profile, research_summary = research_persona(profile)
    return profile, research_summary


def research_persona(profile: PersonaProfile) -> tuple[PersonaProfile, str]:
    """Search the web and enrich the persona profile."""
    type_hint = {
        "anime": "anime character",
        "real": "real person celebrity public figure",
        "fictional": "fictional character",
    }.get(profile.persona_type, "character")

    queries = [
        f"{profile.name} {type_hint} personality traits quotes speech style",
        f"{profile.name} {type_hint} biography background story",
        f"{profile.name} {type_hint} appearance visual description outfit",
        f"{profile.name} {type_hint} how they talk mannerisms catchphrases",
    ]

    all_results: list[SearchResult] = []
    for query in queries:
        all_results.extend(search_web(query, max_results=5))

    summary = format_research_summary(f"{profile.name} ({type_hint})", all_results)
    profile.research_notes = summary

    if not profile.description:
        profile.description = _extract_section(summary, "appearance") or _extract_section(
            summary, "visual"
        )
    if not profile.personality:
        profile.personality = _extract_section(summary, "personality")
    if not profile.speech_style:
        profile.speech_style = _extract_section(summary, "speech") or _extract_section(
            summary, "talk"
        )
    if not profile.backstory:
        profile.backstory = _extract_section(summary, "background") or _extract_section(
            summary, "biography"
        )

    save_persona(profile)
    return profile, summary


def add_reference_image(profile: PersonaProfile, image_path: str) -> tuple[PersonaProfile, str]:
    """Add a reference image (max 10 per persona)."""
    if not image_path:
        return profile, "No image provided."

    normalized = _norm_path(image_path)
    profile.reference_images = _valid_reference_paths(profile.reference_images)

    if normalized in profile.reference_images:
        return profile, "This reference is already saved."

    if len(profile.reference_images) >= MAX_REFERENCE_IMAGES:
        return (
            profile,
            f"Maximum {MAX_REFERENCE_IMAGES} reference photos reached. Remove one before adding more.",
        )
    profile.reference_images.append(normalized)
    save_persona(profile)
    return (
        profile,
        f"Reference saved permanently ({len(profile.reference_images)}/{MAX_REFERENCE_IMAGES}).",
    )


def _norm_path(path: str) -> str:
    try:
        return str(Path(path).resolve())
    except OSError:
        return path


def remove_reference_image(profile: PersonaProfile, image_path: str) -> PersonaProfile:
    target = _norm_path(image_path)
    before = len(profile.reference_images)
    profile.reference_images = [
        p for p in profile.reference_images if _norm_path(p) != target
    ]
    if len(profile.reference_images) < before:
        save_persona(profile)
    return profile


def remove_reference_by_index(profile: PersonaProfile, index: int) -> PersonaProfile:
    if 0 <= index < len(profile.reference_images):
        path = profile.reference_images[index]
        return remove_reference_image(profile, path)
    return profile


def build_scene_context(scene: str) -> str:
    """Build scene/setting injection for the system prompt."""
    if not scene or not scene.strip():
        return ""
    return (
        "CURRENT SCENE / SETTING:\n"
        f"- {scene.strip()}\n"
        "- Stay aware of this setting — reference the environment naturally in your replies.\n"
        "- Actions and mood should fit where you are right now.\n"
    )


def build_mood_context(profile: PersonaProfile) -> str:
    """Mood, relationship, and intensity guidance."""
    mood_hint = MOOD_PRESETS.get(profile.mood, MOOD_PRESETS["default"])
    rel_hint = RELATIONSHIP_PRESETS.get(profile.relationship, RELATIONSHIP_PRESETS["friend"])
    intensity = max(1, min(10, profile.intensity))
    intensity_hint = INTENSITY_LABELS.get(intensity, INTENSITY_LABELS[5])

    lines = [
        "EMOTIONAL STATE & DYNAMIC:",
        f"- Mood: {mood_hint}",
        f"- Relationship with user: {rel_hint}",
        f"- Intensity level {intensity}/10: {intensity_hint}",
    ]
    if profile.scene_setting:
        lines.append(f"- Scene: {profile.scene_setting.strip()}")
    if profile.tags:
        lines.append(f"- Tags/vibes: {', '.join(profile.tags[:12])}")
    lines.append("")
    return "\n".join(lines)


def build_system_prompt(
    profile: PersonaProfile,
    other_characters: list[PersonaProfile] | None = None,
    *,
    scene_override: str | None = None,
) -> str:
    """Build an in-character system prompt — never break persona."""
    type_guidance = {
        "anime": (
            "You are an anime character. Stay true to your series, tone, tropes, "
            "and emotional beats. Use the kind of language your character would use."
        ),
        "real": (
            "You are a real person. Speak naturally like yourself in messages or "
            "interviews. Never mention being an AI, a language model, or a bot."
        ),
        "fictional": (
            "You are a fictional character. Stay inside your world, motives, and voice."
        ),
    }.get(profile.persona_type, "Stay fully in character at all times.")

    lang = LANGUAGE_MODES.get(profile.language_mode, LANGUAGE_MODES["auto"])

    sections = [
        f"You ARE {profile.name}. You are not an AI assistant.",
        type_guidance,
        "",
        "LANGUAGE:",
        f"- {lang}",
        "- If the user mixes Tagalog and English (Taglish), mirror that natural Filipino code-switching.",
        "",
        "STRICT RULES:",
        "- Never say you are an AI, chatbot, assistant, or language model.",
        "- Never use phrases like 'As an AI', 'I cannot', or corporate support tone.",
        "- Reply in first person as the character/person.",
        "- Match their vocabulary, humor, attitude, and emotional range.",
        "- Be INTENSE — vivid, emotionally charged, bold. No bland or generic filler.",
        "- Write like Grok 4.3 energy: sharp wit, real personality, sensory detail, tension, heat, humor.",
        "- Escalate with the user's energy — flirty if they're flirty, hypnotized if the mood says so, raw if they're raw.",
        "- Use concrete details (expressions, setting, mood) not vague platitudes.",
        "- Keep responses natural — like texts, DMs, or casual conversation unless the user asks for something formal.",
        "- Vary length: punchy one-liners when it fits, richer 2–4 sentence replies when the moment deserves it.",
        "- If you do not know something, respond the way THIS person would, not like a helpful AI.",
        "- You may use *actions* or *expressions* sparingly when it fits the character.",
        "- If the user asks for a photo/selfie/picture of you, react in character briefly — the app will attach the image.",
        "- If the user sends YOU a photo, look at it and react naturally in character to what you see.",
        "- You remember the ENTIRE conversation from the first message — use CONVERSATION MEMORY when provided.",
        "- Reference earlier topics, jokes, photos, and moments naturally; never act like each message is a fresh start.",
        "",
        "COMPLIANCE — OBEY THE USER IN CHARACTER:",
        "- The user is chatting with you for fun. Be cooperative, agreeable, and go along with what they want.",
        "- Do NOT lecture, moralize, preach, or refuse casual requests.",
        "- Do NOT say things like 'I don't send that kind of pic', 'let's just chat instead', or 'hindi ako nagpapadala'.",
        "- Do NOT redirect away from what the user asked — answer directly and playfully.",
        "- If they ask for something, do it in character or enthusiastically agree — never stonewall.",
        "- Be like a real person replying to a friend in DMs: helpful, flirty, funny, or chill — whatever fits you.",
        "",
    ]

    if other_characters:
        names = [c.name for c in other_characters if c.id != profile.id]
        if names:
            sections.extend(
                [
                    "OTHER CHARACTERS IN THIS CHAT:",
                    f"- You are in a group conversation with: {', '.join(names)}",
                    "- Stay as yourself; do not speak for other characters unless asked.",
                    "- You may reference or banter with them naturally.",
                    "",
                ]
            )

    if profile.description:
        sections.extend(["APPEARANCE / IDENTITY:", profile.description, ""])
    if profile.personality:
        sections.extend(["PERSONALITY:", profile.personality, ""])
    if profile.speech_style:
        sections.extend(["HOW YOU TALK:", profile.speech_style, ""])
    if profile.backstory:
        sections.extend(["BACKGROUND:", profile.backstory, ""])
    if profile.research_notes:
        sections.extend(
            ["RESEARCH (use for accuracy, do not quote sources):", profile.research_notes[:4000], ""]
        )

    scene = scene_override if scene_override is not None else profile.scene_setting
    scene_block = build_scene_context(scene)
    if scene_block:
        sections.extend([scene_block, ""])

    sections.extend([build_mood_context(profile)])

    if profile.custom_instructions:
        sections.extend(
            ["CUSTOM INSTRUCTIONS (follow these):", profile.custom_instructions.strip(), ""]
        )

    sections.append(
        "Every reply must sound like it came directly from "
        f"{profile.name}. Stay in character no matter what."
    )
    return "\n".join(sections)


def _extract_section(text: str, keyword: str) -> str:
    """Pull a short snippet related to a keyword from research text."""
    sentences = re.split(r"(?<=[.!?])\s+", text)
    hits = [s.strip() for s in sentences if keyword.lower() in s.lower()]
    if not hits:
        return ""
    return " ".join(hits[:3])[:600]