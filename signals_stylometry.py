import re
import statistics


def _sentences(text: str) -> list:
    parts = re.split(r"[.!?]+", text)
    return [p.strip() for p in parts if p.strip()]


def _words(text: str) -> list:
    return re.findall(r"[a-zA-Z']+", text.lower())


def _sentence_length_variance_score(sentences: list) -> float:
    """Low variance (uniform sentences) -> high score (AI-like)."""
    if len(sentences) < 2:
        return 0.5
    lengths = [len(_words(s)) for s in sentences]
    if len(lengths) < 2 or max(lengths) == 0:
        return 0.5
    stdev = statistics.pstdev(lengths)
    mean = statistics.mean(lengths) or 1
    coeff_var = stdev / mean
    # High coeff_var (bursty/irregular) -> human-like -> low score.
    # Clamp typical range [0, 1.2] onto [0, 1], inverted.
    normalized = max(0.0, min(1.0, coeff_var / 1.2))
    return 1.0 - normalized


def _type_token_ratio_score(words: list) -> float:
    """Very high vocabulary diversity for short text is mixed signal;
    AI text on longer passages tends toward moderate, measured TTR.
    We score toward AI-like (1.0) when TTR sits in a narrow "measured" band,
    and toward human-like (0.0) at the extremes (very repetitive or very diverse)."""
    if len(words) < 5:
        return 0.5
    ttr = len(set(words)) / len(words)
    # Human writing on short/medium passages skews toward the extremes
    # (repetitive casual speech, or unusually diverse tangents).
    # AI writing tends to sit around 0.55-0.75 TTR for this length range.
    distance_from_band_center = abs(ttr - 0.65)
    normalized = max(0.0, min(1.0, distance_from_band_center / 0.35))
    return 1.0 - normalized


def _punctuation_density_score(text: str) -> float:
    """AI text tends toward measured, consistent punctuation (commas for
    clause structure); human casual text is bursty (dashes, ellipses,
    ALL CAPS, repeated punctuation, or terse fragments)."""
    if not text:
        return 0.5
    irregular = len(re.findall(r"(\.\.\.|--|!!|\?\?|[A-Z]{3,})", text))
    words = _words(text)
    if not words:
        return 0.5
    irregular_rate = irregular / max(1, len(words))
    normalized = max(0.0, min(1.0, irregular_rate * 20))
    return 1.0 - normalized


def stylometry_signal(text: str) -> dict:
    """Returns {"stylometry_score": float, "metrics": {...}}.

    stylometry_score: 0 = irregular/bursty (human-like), 1 = uniform (AI-like).
    """
    sentences = _sentences(text)
    words = _words(text)

    sentence_variance_score = _sentence_length_variance_score(sentences)
    ttr_score = _type_token_ratio_score(words)
    punctuation_score = _punctuation_density_score(text)

    combined = (
        0.5 * sentence_variance_score
        + 0.3 * ttr_score
        + 0.2 * punctuation_score
    )

    return {
        "stylometry_score": round(combined, 4),
        "metrics": {
            "sentence_count": len(sentences),
            "word_count": len(words),
            "sentence_length_variance_score": round(sentence_variance_score, 4),
            "type_token_ratio_score": round(ttr_score, 4),
            "punctuation_density_score": round(punctuation_score, 4),
        },
    }
