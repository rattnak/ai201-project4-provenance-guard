# Provenance Guard — Planning

## 1. Detection Signals

Two independent signals, combined into one confidence score.

**Signal 1 — LLM semantic judgment (Groq, `llama-3.3-70b-versatile`)**
- Measures: holistic semantic/stylistic coherence — does the passage read like something an LLM would generate (hedged, uniformly structured, generically "helpful" phrasing) vs. something a specific human voice would produce (idiosyncratic word choice, tangents, uneven register).
- Output: the model is prompted to return strict JSON `{"ai_probability": 0.0-1.0, "reasoning": "..."}`. `ai_probability` is used directly as `llm_score` (0 = confidently human, 1 = confidently AI).
- Blind spot: LLMs judging LLM output is circular — a model can be fooled by lightly-edited AI text, and can be biased against formal/technical human writing (academic, legal, ESL) that "sounds like AI" structurally without being AI. It also has no ground truth; it's a trained-prior guess, not a measurement.

**Signal 2 — Stylometric heuristics (pure Python)**
- Measures: statistical/structural properties of the text: sentence-length variance, type-token ratio (vocabulary diversity), average sentence length, and punctuation/burstiness variance.
- Output: each metric is normalized to 0-1 and averaged into `stylometry_score` (0 = high variance / human-like irregularity, 1 = low variance / uniform, AI-like).
- Rationale: AI text tends toward uniform sentence length and measured vocabulary; human text is "bursty" — mixing short and long sentences, repeating words, going off on tangents.
- Blind spot: short passages (under ~50 words) don't have enough tokens for variance stats to be meaningful. Also fooled by human writers with naturally uniform style (technical writers, some poets) and by AI text that's been heavily post-edited by a human.

These are genuinely distinct: one is a semantic/holistic judgment from a model, the other is a structural/statistical measurement with no model in the loop. Combination: `combined = 0.6 * llm_score + 0.4 * stylometry_score`. LLM signal weighted higher because it directly assesses content semantics; stylometry acts as a structural check/tiebreaker, especially valuable when the LLM signal is itself uncertain (near 0.5).

## 2. Uncertainty Representation

- `confidence` returned to the user is **not** "probability of AI" — it's a single 0-1 axis, where the *label* (below) carries the direction (AI vs human) and the *distance from 0.5* carries the certainty.
- Internally: `combined_score` (0-1, 1 = AI) is computed as above. We define:
  - `combined_score >= 0.70` → **likely AI**, confidence reported = `combined_score`
  - `combined_score <= 0.30` → **likely human**, confidence reported = `1 - combined_score`
  - `0.30 < combined_score < 0.70` → **uncertain**, confidence reported = `1 - abs(combined_score - 0.5) * 2` (peaks near 1.0 exactly at 0.5, → 0 near the boundaries) — this makes the "uncertain" band explicitly communicate low confidence rather than reusing the raw score.
- A score of 0.51 (barely over the midpoint) lands in the **uncertain** band with a low reported confidence (~0.96 low? — see below) — actually to keep this monotonic and simple: reported confidence in the uncertain band is `1 - abs(combined_score - 0.5) * 2`, so 0.51 → confidence ≈ 0.98 *of being uncertain*, not of being AI. We surface this distinction explicitly in the label text ("we're not confident either way") rather than a single ambiguous number, so a 0.51 never gets a token in the "high confidence" bucket. A 0.95 clears the 0.70 threshold decisively and gets labeled **likely AI** with confidence 0.95.
- False-positive asymmetry: because mislabeling a human as AI is worse than the reverse, the human-side threshold is intentionally symmetric but the label copy (below) is written to hedge harder on the AI side ("may include," "some indicators") than the human side, and appeals are surfaced prominently only on AI-leaning labels.

## 3. Transparency Label (exact text)

| Band | Condition | Label text shown to user |
|---|---|---|
| High-confidence AI | `combined_score >= 0.70` | `"This content shows strong indicators of AI generation (confidence: {pct}%). If you believe this is your original work, you can appeal this classification."` |
| High-confidence human | `combined_score <= 0.30` | `"This content appears to be human-written, with no strong indicators of AI generation (confidence: {pct}%)."` |
| Uncertain | `0.30 < combined_score < 0.70` | `"We're not confident whether this content is AI-generated or human-written. Treat the attribution as inconclusive."` |

`{pct}` = `round(confidence * 100)`.

## 4. Appeals Workflow

- Any creator (identified by `creator_id`, the same one used at submission) can appeal a classification via `POST /appeal` by providing `content_id` and `creator_reasoning` (free text explaining why the classification is wrong).
- On receipt: the system looks up the original submission by `content_id`, sets its `status` to `"under_review"`, and writes a new audit log entry of type `"appeal"` containing the appeal reasoning, a reference to the original decision (`content_id`, original `attribution`, `confidence`), and a timestamp. No automated re-classification occurs.
- Response: confirmation JSON with `content_id`, new `status`, and `appeal_id`.
- A human reviewer opening the appeal queue (`GET /appeals`) would see: content_id, original text, original attribution + confidence + signal breakdown, creator's reasoning, and current status — everything needed to make a manual call without re-running detection.

## 5. Anticipated Edge Cases

1. **Short-form content (e.g. a haiku or a one-line caption).** Stylometric variance metrics are meaningless on fewer than ~10-15 words (variance over 1-2 sentences is noise), so the combined score leans entirely on the LLM signal, which itself is less reliable on very short inputs. Expect these to land disproportionately in the "uncertain" band, which is the correct honest behavior but may frustrate creators of short-form work.
2. **Formal/technical human writing (academic abstracts, legal writing, non-native English).** Uniform sentence structure and measured vocabulary — the exact stylometric signature we associate with AI — is also the signature of careful, formal human prose. This is our most likely false-positive source and is called out explicitly to justify the appeals workflow's existence.
3. **Heavily-edited AI output.** A human who takes AI-drafted text and substantially rewrites it will show mixed signals (structurally uniform base, but with human vocabulary/tangents layered in) — likely landing in "uncertain," which is arguably correct, but the system has no way to detect or represent "hybrid" authorship as a distinct category.

## Architecture

### Submission flow
```
POST /submit {text, creator_id}
        |
        v
generate content_id (uuid4), store raw submission
        |
        v
  +-----------------+       +--------------------------+
  | Signal 1: Groq   |      | Signal 2: Stylometry      |
  | LLM judgment     |      | (sentence-length variance, |
  | -> llm_score     |      |  TTR, punctuation density) |
  |    (0-1)         |      | -> stylometry_score (0-1) |
  +-----------------+       +--------------------------+
        \                         /
         \                       /
          v                     v
        combine: 0.6*llm + 0.4*stylometry = combined_score
                        |
                        v
        map combined_score -> band (AI / human / uncertain)
        + confidence value (see uncertainty rules)
                        |
                        v
        generate transparency label text
                        |
                        v
        write audit log entry (content_id, creator_id, timestamp,
        llm_score, stylometry_score, combined_score, confidence,
        attribution band, label, status="classified")
                        |
                        v
        response: {content_id, attribution, confidence, label,
                    signals: {llm_score, stylometry_score}}
```

### Appeal flow
```
POST /appeal {content_id, creator_reasoning}
        |
        v
  look up original submission by content_id
        |
        v
  update stored status -> "under_review"
        |
        v
  write audit log entry (type="appeal", content_id, creator_reasoning,
  original attribution/confidence, timestamp)
        |
        v
  response: {content_id, status: "under_review", appeal_id}
```

Both flows write to the same structured audit log (`logs/audit.jsonl`), which `GET /log` reads and returns as JSON. `GET /appeals` filters/joins that log into a reviewer-facing queue view.

## AI Tool Plan

- **M3 (submission endpoint + first signal):** Provide the "Detection Signals" section (Signal 1 only) + the architecture diagram. Ask for: Flask app skeleton with `POST /submit` stub, and a standalone `llm_signal(text) -> {"ai_probability": float, "reasoning": str}` function calling Groq with a strict-JSON prompt. Verify: call `llm_signal` directly on 2-3 hand-picked texts and print the output before wiring into the route.
- **M4 (second signal + confidence scoring):** Provide "Detection Signals" (both) + "Uncertainty Representation" + diagram. Ask for: `stylometry_signal(text) -> {"stylometry_score": float, "metrics": {...}}` and a `score_confidence(llm_score, stylometry_score) -> {"combined_score", "attribution", "confidence"}` function implementing the exact thresholds (0.70 / 0.30) and the uncertain-band formula above. Verify: run the 4 test inputs (clearly AI, clearly human, 2 borderline) from the assignment and confirm the bands and rough score ordering match intuition; correct any silent threshold drift from what's specified here.
- **M5 (production layer):** Provide "Transparency Label" + "Appeals Workflow" sections + diagram. Ask for: `generate_label(band, confidence) -> str` matching the exact table text above, and the `POST /appeal` route + `GET /appeals` route. Verify: submit 3 texts engineered to hit each band and diff the returned label string against the table verbatim; submit an appeal and confirm `GET /log` shows `status: "under_review"` and populated `appeal_reasoning`.

## Stretch Features Plan

- **Ensemble detection (3rd signal):** add a perplexity-adjacent heuristic — repeated-phrase / n-gram repetition rate — as a third, differently-weighted signal, with documented weights (e.g. 0.5 LLM / 0.3 stylometry / 0.2 repetition) and a note on why weights were chosen.
- **Analytics dashboard:** `GET /dashboard` (JSON, no frontend framework) summarizing: count by attribution band, appeal rate (% of submissions appealed), and average confidence by band.
