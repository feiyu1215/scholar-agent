"""handlers/write_ops.py — Rewrite, voice profile, and author profile handlers."""

from core.state import WORKSPACE


async def handle_rewrite_section(section_id: str, custom_instructions: str = "",
                                 provider: str = None, model: str = None) -> str:
    """Legacy monolithic rewrite. Prefer generate_rewrite -> commit_rewrite -> verify_rewrite_quality."""
    from tools.write_engine import rewrite_section
    return await rewrite_section(section_id, provider=provider, model=model,
                                 custom_instructions=custom_instructions)


async def handle_generate_rewrite(section_id: str, custom_instructions: str = "",
                                  provider: str = None, model: str = None) -> str:
    from tools.write_engine import generate_rewrite
    from core.state import session_provider, session_model
    p = provider or session_provider
    m = model or session_model
    return await generate_rewrite(section_id, custom_instructions=custom_instructions,
                                  provider=p, model=m)


def handle_commit_rewrite(section_id: str, proposed_text: str, changes_summary: str = "") -> str:
    from tools.write_engine import commit_rewrite
    return commit_rewrite(section_id, proposed_text, changes_summary)


async def handle_verify_rewrite_quality(section_id: str,
                                        provider: str = None, model: str = None) -> str:
    from tools.write_engine import verify_rewrite_quality
    from core.state import session_provider, session_model
    p = provider or session_provider
    m = model or session_model
    return await verify_rewrite_quality(section_id, provider=p, model=m)


async def handle_parallel_rewrite(section_ids: list, custom_instructions: str = "",
                                  provider: str = None, model: str = None) -> str:
    from tools.parallel_rewrite import parallel_rewrite
    return await parallel_rewrite(
        section_ids, provider=provider, model=model,
        custom_instructions=custom_instructions
    )


def handle_build_voice_profile() -> str:
    from utils.voice_profile import build_voice_profile_from_paper, get_voice_constraints
    fp = build_voice_profile_from_paper()
    if fp and fp.total_words_analyzed > 0:
        constraints = get_voice_constraints(fp)
        return (
            "Voice profile built from " + str(len(fp.sections_analyzed)) + " sections "
            "(" + str(fp.total_words_analyzed) + " words analyzed).\n\n"
            "Key metrics:\n"
            "  Avg sentence length: " + str(fp.avg_sentence_length) + " words\n"
            "  Passive voice ratio: " + f"{fp.passive_ratio:.0%}" + "\n"
            "  Hedge frequency: " + str(fp.hedge_frequency) + "/100 words\n"
            "  Preferred hedges: " + ", ".join(fp.preferred_hedges[:3]) + "\n\n"
            "Constraints (auto-injected into rewrites):\n" + constraints
        )
    return "No paper sections found. Run parse_paper first."


def handle_show_author_profile() -> str:
    from utils.author_profile import load_profile, format_profile_summary
    profile = load_profile()
    return format_profile_summary(profile)
