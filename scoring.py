import config


def combine_scores(llm_score: float, stylometry_score: float, repetition_score: float = None) -> float:
    """Weighted combination of signals into a single 0-1 'combined_score'
    where 1.0 = confidently AI-generated, 0.0 = confidently human-written."""
    if repetition_score is None:
        # Two-signal mode: renormalize the two required weights.
        total = config.WEIGHT_LLM + config.WEIGHT_STYLOMETRY
        return (
            config.WEIGHT_LLM * llm_score + config.WEIGHT_STYLOMETRY * stylometry_score
        ) / total
    return (
        config.WEIGHT_LLM * llm_score
        + config.WEIGHT_STYLOMETRY * stylometry_score
        + config.WEIGHT_REPETITION * repetition_score
    )


def score_confidence(combined_score: float) -> dict:
    """Maps a combined_score to (attribution band, reported confidence).

    See planning.md section 2 for the reasoning behind these formulas.
    """
    if combined_score >= config.AI_THRESHOLD:
        attribution = "likely_ai"
        confidence = combined_score
    elif combined_score <= config.HUMAN_THRESHOLD:
        attribution = "likely_human"
        confidence = 1 - combined_score
    else:
        attribution = "uncertain"
        confidence = 1 - abs(combined_score - 0.5) * 2

    return {
        "attribution": attribution,
        "confidence": round(confidence, 4),
        "combined_score": round(combined_score, 4),
    }


LABELS = {
    "likely_ai": (
        "This content shows strong indicators of AI generation (confidence: {pct}%). "
        "If you believe this is your original work, you can appeal this classification."
    ),
    "likely_human": (
        "This content appears to be human-written, with no strong indicators of AI "
        "generation (confidence: {pct}%)."
    ),
    "uncertain": (
        "We're not confident whether this content is AI-generated or human-written. "
        "Treat the attribution as inconclusive."
    ),
}


def generate_label(attribution: str, confidence: float) -> str:
    pct = round(confidence * 100)
    return LABELS[attribution].format(pct=pct)
