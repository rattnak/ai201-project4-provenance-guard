# Provenance Guard

A backend system that classifies submitted creative text as likely AI-generated, likely human-written, or uncertain — with a confidence score, a plain-language transparency label, an appeals workflow, rate limiting, and a structured audit log.

Full design rationale lives in [planning.md](planning.md) (detection signal choices, uncertainty-mapping formulas, edge cases, architecture diagram, AI tool plan). This README documents what was built, why, and the evidence graders need.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then fill in GROQ_API_KEY
python app.py          # runs on http://localhost:5000
```

If `GROQ_API_KEY` is unset or the Groq call fails, `signals_llm.py` degrades gracefully to a neutral 0.5 score instead of crashing, so the rest of the pipeline still runs.

## Architecture Overview

A submission's path from input to label:

1. `POST /submit {text, creator_id}` — assigns a `content_id` (uuid4).
2. **Signal 1 (Groq LLM judgment)** — `signals_llm.py` sends the text to `llama-3.3-70b-versatile` and gets back an `ai_probability` (0-1) plus one-sentence reasoning.
3. **Signal 2 (stylometric heuristics)** — `signals_stylometry.py` computes sentence-length variance, type-token ratio, and punctuation-density irregularity in pure Python, combined into a `stylometry_score` (0-1).
4. **Signal 3, stretch (repetition/formulaic-phrasing heuristic)** — `signals_repetition.py` measures trigram repetition and transition-word density into a `repetition_score` (0-1).
5. **Scoring** — `scoring.py` combines the three signals with weights `0.5 / 0.3 / 0.2` into a `combined_score`, then maps that score to an attribution band (`likely_ai` / `uncertain` / `likely_human`) and a reported `confidence`, using asymmetric thresholds (0.70 / 0.30) described in planning.md.
6. **Label generation** — `scoring.generate_label()` renders the exact label text for the resolved band.
7. **Audit log** — `auditor.py` appends a structured JSON line (all signal scores, combined score, attribution, confidence, label) to `logs/audit.jsonl`; `store.py` keeps an in-memory index by `content_id` for appeal lookups.
8. The endpoint returns `{content_id, attribution, confidence, label, signals: {...}, status}`.

Appeals: `POST /appeal {content_id, creator_reasoning}` looks up the original submission, flips its `status` to `under_review`, and appends a separate `"type": "appeal"` audit-log entry referencing the original decision. `GET /appeals` joins log + store into a reviewer queue. `GET /log` and `GET /dashboard` expose the raw log and aggregate metrics respectively.

See the ASCII diagram in [planning.md § Architecture](planning.md#architecture) for the full flow with data labels on each arrow.

## Detection Signals

**Signal 1 — LLM semantic judgment (Groq `llama-3.3-70b-versatile`).** Captures holistic semantic/stylistic coherence: does the passage read like generic, hedged, uniformly-structured LLM output, or like a specific human voice with idiosyncratic word choice and concrete personal detail? Chosen because it's the only signal that reasons about *meaning*, not just surface statistics. Blind spot: it's a trained prior, not ground truth — it can be fooled by lightly-edited AI text, and it tends to flag formal/technical/ESL human writing as AI-like because that writing shares surface features (uniform structure, hedging) with LLM output.

**Signal 2 — Stylometric heuristics (pure Python, no external libraries).** Captures structural/statistical regularity: sentence-length variance, type-token ratio (vocabulary diversity), and punctuation-density irregularity (ALL CAPS, `...`, `--`, repeated punctuation). AI text tends toward uniform sentence length and a narrow, "measured" vocabulary band; human text is bursty — mixing short and long sentences, repeating words, using irregular punctuation. Chosen because it's a genuinely independent axis from Signal 1 (no model in the loop, purely computed). Blind spot: unreliable on short passages (under ~15 words, variance is noise) and penalizes naturally uniform human writers (technical writers, some poetry).

**Signal 3, stretch — Repetition/formulaic-phrasing heuristic.** Captures trigram repetition rate and density of AI-associated transition words (`furthermore`, `moreover`, `it is important to note`, etc.). Chosen as a third, cheap, purely lexical signal that's independent of both the LLM's semantic judgment and the stylometric variance measures. Blind spot: short texts have too few trigrams to measure meaningfully, and human academic/technical writers also lean on transition words.

Two signals are the required minimum; the third (repetition) is the **ensemble detection stretch feature**, using documented weights `0.5 (LLM) / 0.3 (stylometry) / 0.2 (repetition)` — LLM weighted highest because it's the only semantic signal, stylometry next because it's the most reliable structural signal at typical submission lengths, repetition weighted lowest because it's the noisiest on short text.

## Confidence Scoring

`combined_score` (0 = confidently human, 1 = confidently AI) is a weighted average of the three signal scores. It's mapped to a band and a *reported* confidence as follows (see `scoring.py` and planning.md § 2 for the full reasoning):

- `combined_score >= 0.70` → **likely_ai**, confidence = `combined_score`
- `combined_score <= 0.30` → **likely_human**, confidence = `1 - combined_score`
- otherwise → **uncertain**, confidence = `1 - abs(combined_score - 0.5) * 2` (peaks near the midpoint, decays toward either boundary)

This means a score of 0.51 lands in the *uncertain* band and gets the hedged "we're not confident either way" label — it never gets treated as a decisive AI call just because it crossed 0.5. A score of 0.95 clears the 0.70 threshold decisively and is labeled **likely_ai** with confidence 0.95. This directly tests the requirement that 0.51 and 0.95 must produce meaningfully different labels.

**Validation approach:** tested against 4 inputs spanning the range (clearly AI, clearly human, and two borderline cases — formal human writing, and lightly-edited AI-style writing). Actual results from this run:

| Input | llm_score | stylometry_score | repetition_score | combined_score | attribution | confidence |
|---|---|---|---|---|---|---|
| Clearly AI ("Artificial intelligence represents a transformative...") | 0.90 | 0.6416 | 0.40 | 0.7225 | **likely_ai** | **0.7225** (high) |
| Clearly human (ramen review, casual voice) | 0.20 | 0.4817 | 0.00 | 0.2445 | **likely_human** | **0.7555** (high) |
| Borderline: formal human (monetary policy passage) | 0.80 | 0.7130 | 0.00 | 0.6139 | uncertain | 0.7722 (of being uncertain) |
| Borderline: lightly-edited AI (remote work reflection) | 0.70 | 0.6287 | 0.00 | 0.5537 | uncertain | 0.9228 (of being uncertain) |

Two example submissions with noticeably different confidence, both from this run: the clearly-AI passage scored **0.7225 confidence, likely_ai**, while the formal-human passage scored **0.6139 combined** (landing in uncertain, confidence 0.7722 *of uncertainty*, not of being AI) — demonstrating the scoring doesn't collapse everything into one bucket, and that the formal-writing edge case (anticipated in planning.md) is correctly routed to the hedged label rather than a false "likely AI" call.

This also validates the false-positive-asymmetry design goal from planning.md: the formal human writing that superficially resembles AI structure lands in **uncertain**, not **likely_ai** — the system hedges rather than confidently mislabeling a human writer.

## Transparency Label

Exact label text per band (from `scoring.py: LABELS`):

| Band | Label text (verbatim) |
|---|---|
| High-confidence AI | `"This content shows strong indicators of AI generation (confidence: {pct}%). If you believe this is your original work, you can appeal this classification."` |
| High-confidence human | `"This content appears to be human-written, with no strong indicators of AI generation (confidence: {pct}%)."` |
| Uncertain | `"We're not confident whether this content is AI-generated or human-written. Treat the attribution as inconclusive."` |

`{pct}` is `round(confidence * 100)`. All three were reached in testing above (likely_ai, likely_human, uncertain all appear in the table).

## Rate Limiting

`POST /submit` is limited to **10 requests per minute and 100 per day** per client (via Flask-Limiter, in-memory storage, keyed by remote address — see `config.py: RATE_LIMITS`).

**Reasoning:** a genuine creator submitting their own work rarely submits more than a handful of pieces in a sitting — 10/minute comfortably covers someone submitting several drafts or revisions in quick succession without feeling throttled. The 100/day ceiling caps sustained abuse (e.g. a script probing the classifier or scraping label output) without affecting normal usage, since no individual writer submits 100 distinct pieces of content in a day.

**Evidence** — 12 rapid `POST /submit` requests in a row (status codes, from an actual test run):

```
200
200
200
200
200
200
200
200
429
429
429
429
```

The first 8 succeeded before the per-minute window's request budget (shared with prior test traffic in this session) was exhausted; all subsequent requests returned `429 Too Many Requests` as expected, confirming the limiter is active on `/submit`.

## Audit Log

Every submission and appeal is appended as a structured JSON line to `logs/audit.jsonl`, readable via `GET /log`. Example entries (redacted to key fields) from an actual test run:

```json
{
  "type": "submission",
  "content_id": "1bdc8040-49f9-4045-bd47-10b2568341f8",
  "creator_id": "test-ai",
  "timestamp": "2026-07-01T02:55:40.367772+00:00",
  "attribution": "likely_ai",
  "confidence": 0.7225,
  "combined_score": 0.7225,
  "llm_score": 0.9,
  "stylometry_score": 0.6416,
  "repetition_score": 0.4,
  "status": "classified"
}
{
  "type": "submission",
  "content_id": "f4aa956e-fc70-4a14-ab47-736523711c01",
  "creator_id": "test-human",
  "timestamp": "2026-07-01T02:55:40.803400+00:00",
  "attribution": "likely_human",
  "confidence": 0.7555,
  "combined_score": 0.2445,
  "llm_score": 0.2,
  "stylometry_score": 0.4817,
  "repetition_score": 0.0,
  "status": "classified"
}
{
  "type": "appeal",
  "appeal_id": "44ee0bdc-1438-4632-9422-f99b1bc0a604",
  "content_id": "1325225a-df98-42a0-af36-ef32fa422f09",
  "creator_id": "test-formal",
  "timestamp": "2026-07-01T03:06:12.172664+00:00",
  "creator_reasoning": "I wrote this myself as an economics grad student; my formal training explains the style.",
  "original_attribution": "uncertain",
  "original_confidence": 0.7722,
  "status": "under_review"
}
```

`GET /appeals` renders these into a reviewer-facing queue with the original text, all signal scores, and creator reasoning attached.

## Known Limitations

1. **Formal/technical human writing is the most likely false positive.** Uniform sentence structure and a "measured" vocabulary band — the exact stylometric signature associated with AI output — is also the signature of careful, formal human prose (academic writing, legal writing, non-native English speakers writing carefully). In testing, a genuinely human-authored passage about monetary policy landed in the "uncertain" band rather than "likely human," because both the LLM signal and stylometry signal read its formality as AI-adjacent. This is the core reason the appeals workflow exists, and it's the asymmetry the label/threshold design tries to hedge against — but it isn't solved, only mitigated.

2. **Short-form content (a haiku, a one-line caption, a tweet-length excerpt) breaks the stylometry and repetition signals.** Sentence-length variance and trigram repetition are statistically meaningless under roughly 10-15 words, so both signals fall back to neutral 0.5 scores, leaving the combined score almost entirely dependent on the LLM signal — which is itself less reliable on very short inputs. Expect short submissions to cluster in "uncertain" more than their true label distribution would suggest.

## Spec Reflection

**How the spec helped:** writing out the exact three label strings and the 0.70/0.30 thresholds in planning.md *before* touching `scoring.py` made the implementation almost mechanical — there was no ambiguity to resolve mid-coding about what a 0.6 combined score should produce, because that decision was already made and written down. It also surfaced the "0.51 vs 0.95 must differ" requirement early enough to design the uncertain-band confidence formula deliberately, rather than discovering post-hoc that a naive implementation returned the same confidence for both.

**Where implementation diverged:** the original plan used a fixed two-signal weighted average (`0.6 * llm + 0.4 * stylometry`). While building the ensemble stretch feature, this was refactored so `combine_scores()` renormalizes to two signals when no repetition score is available, but defaults to the three-signal `0.5/0.3/0.2` weighting once the repetition signal was added — the plan didn't originally anticipate needing both a 2-signal and 3-signal code path, since the stretch feature was written up after the required-features spec was locked in.

## AI Usage

1. **Directed:** "Given this spec section (detection signals: LLM + stylometry, exact output shapes) and the architecture diagram, generate a Flask route for POST /submit and a standalone `llm_signal(text)` function calling the Groq API with a strict-JSON response format." **Produced:** a working function, but the first version didn't handle the case where Groq wrapped the JSON in prose/markdown fences. **Revised:** added `_extract_json()` in `signals_llm.py` to regex out the JSON object from the raw response, and added a try/except fallback to a neutral 0.5 score so a Groq outage or malformed response doesn't crash the endpoint (verified by testing with `GROQ_API_KEY` temporarily unset).

2. **Directed:** "Generate the confidence-scoring function per this uncertainty-representation spec, including the uncertain-band formula that makes 0.51 read as low-confidence rather than reusing the raw combined score." **Produced:** an initial version that returned `combined_score` directly as the confidence value even in the uncertain band, which would have made 0.51 look like moderate-high confidence in "likely AI" direction — the opposite of the spec's intent. **Overrode:** replaced that branch with the `1 - abs(combined_score - 0.5) * 2` formula from planning.md, then re-verified against the 4 test inputs to confirm 0.51-range scores now report low confidence rather than a misleadingly high one.

## Stretch Features Implemented

- **Ensemble detection (3+ signals):** `signals_repetition.py` adds a third, independent lexical signal (trigram repetition + transition-word density), combined via documented weights `0.5 (LLM) / 0.3 (stylometry) / 0.2 (repetition)` in `scoring.combine_scores()`.
- **Analytics dashboard:** `GET /dashboard` returns submission counts per band, average confidence per band, total appeals, and appeal rate — see `app.py: dashboard()`.
