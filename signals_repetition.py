import re
from collections import Counter


def _words(text: str) -> list:
    return re.findall(r"[a-zA-Z']+", text.lower())


def repetition_signal(text: str) -> dict:
    """Measures n-gram/phrase repetition and transition-word density.

    AI-generated text tends to reuse structural transition phrases
    ("furthermore", "it is important to note", "in conclusion") and
    repeat trigrams more than human writing, which tends to vary phrasing
    even when repeating an idea.

    Output: repetition_score in [0, 1], 1 = highly repetitive/formulaic (AI-like).
    """
    words = _words(text)
    if len(words) < 6:
        return {"repetition_score": 0.5, "metrics": {"trigram_repeat_ratio": None, "transition_word_rate": None}}

    trigrams = [tuple(words[i : i + 3]) for i in range(len(words) - 2)]
    counts = Counter(trigrams)
    repeated = sum(c - 1 for c in counts.values() if c > 1)
    trigram_repeat_ratio = repeated / len(trigrams) if trigrams else 0.0

    transition_words = {
        "furthermore", "moreover", "additionally", "however", "therefore",
        "consequently", "importantly", "notably", "ultimately", "overall",
        "essential", "crucial", "significant", "paramount",
    }
    transition_hits = sum(1 for w in words if w in transition_words)
    transition_word_rate = transition_hits / len(words)

    trigram_component = max(0.0, min(1.0, trigram_repeat_ratio / 0.15))
    transition_component = max(0.0, min(1.0, transition_word_rate / 0.04))

    combined = 0.6 * trigram_component + 0.4 * transition_component

    return {
        "repetition_score": round(combined, 4),
        "metrics": {
            "trigram_repeat_ratio": round(trigram_repeat_ratio, 4),
            "transition_word_rate": round(transition_word_rate, 4),
        },
    }
