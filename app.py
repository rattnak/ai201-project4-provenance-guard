import uuid
from datetime import datetime, timezone

from flask import Flask, jsonify, request
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

import auditor
import config
import scoring
import store
from signals_llm import llm_signal
from signals_repetition import repetition_signal
from signals_stylometry import stylometry_signal

app = Flask(__name__)

limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=[],
    storage_uri="memory://",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@app.route("/submit", methods=["POST"])
@limiter.limit(";".join(config.RATE_LIMITS))
def submit():
    data = request.get_json(silent=True) or {}
    text = data.get("text", "").strip()
    creator_id = data.get("creator_id", "").strip()

    if not text or not creator_id:
        return jsonify({"error": "text and creator_id are required"}), 400

    content_id = str(uuid.uuid4())

    llm_result = llm_signal(text)
    stylometry_result = stylometry_signal(text)
    repetition_result = repetition_signal(text)

    llm_score = llm_result["ai_probability"]
    stylometry_score = stylometry_result["stylometry_score"]
    repetition_score = repetition_result["repetition_score"]

    combined_score = scoring.combine_scores(llm_score, stylometry_score, repetition_score)
    result = scoring.score_confidence(combined_score)
    label = scoring.generate_label(result["attribution"], result["confidence"])

    entry = {
        "type": "submission",
        "content_id": content_id,
        "creator_id": creator_id,
        "timestamp": _now(),
        "text": text,
        "attribution": result["attribution"],
        "confidence": result["confidence"],
        "combined_score": result["combined_score"],
        "llm_score": llm_score,
        "llm_reasoning": llm_result["reasoning"],
        "stylometry_score": stylometry_score,
        "stylometry_metrics": stylometry_result["metrics"],
        "repetition_score": repetition_score,
        "repetition_metrics": repetition_result["metrics"],
        "label": label,
        "status": "classified",
    }

    auditor.append_log(entry)
    store.save(content_id, entry)

    return jsonify(
        {
            "content_id": content_id,
            "attribution": result["attribution"],
            "confidence": result["confidence"],
            "label": label,
            "signals": {
                "llm_score": llm_score,
                "stylometry_score": stylometry_score,
                "repetition_score": repetition_score,
            },
            "status": entry["status"],
        }
    )


@app.route("/appeal", methods=["POST"])
def appeal():
    data = request.get_json(silent=True) or {}
    content_id = data.get("content_id", "").strip()
    creator_reasoning = data.get("creator_reasoning", "").strip()

    if not content_id or not creator_reasoning:
        return jsonify({"error": "content_id and creator_reasoning are required"}), 400

    original = store.get(content_id)
    if original is None:
        return jsonify({"error": "content_id not found"}), 404

    store.set_status(content_id, "under_review")

    appeal_id = str(uuid.uuid4())
    entry = {
        "type": "appeal",
        "appeal_id": appeal_id,
        "content_id": content_id,
        "creator_id": original.get("creator_id"),
        "timestamp": _now(),
        "creator_reasoning": creator_reasoning,
        "original_attribution": original.get("attribution"),
        "original_confidence": original.get("confidence"),
        "status": "under_review",
    }
    auditor.append_log(entry)

    return jsonify(
        {
            "content_id": content_id,
            "appeal_id": appeal_id,
            "status": "under_review",
        }
    )


@app.route("/appeals", methods=["GET"])
def appeals():
    entries = auditor.get_log(limit=10_000)
    appeal_entries = [e for e in entries if e.get("type") == "appeal"]

    queue = []
    for a in appeal_entries:
        original = store.get(a["content_id"]) or {}
        queue.append(
            {
                "content_id": a["content_id"],
                "appeal_id": a["appeal_id"],
                "creator_id": a.get("creator_id"),
                "creator_reasoning": a.get("creator_reasoning"),
                "original_text": original.get("text"),
                "original_attribution": a.get("original_attribution"),
                "original_confidence": a.get("original_confidence"),
                "signals": {
                    "llm_score": original.get("llm_score"),
                    "stylometry_score": original.get("stylometry_score"),
                    "repetition_score": original.get("repetition_score"),
                },
                "status": original.get("status"),
                "timestamp": a.get("timestamp"),
            }
        )
    return jsonify({"appeals": queue})


@app.route("/log", methods=["GET"])
def log():
    limit = request.args.get("limit", default=50, type=int)
    return jsonify({"entries": auditor.get_log(limit=limit)})


@app.route("/dashboard", methods=["GET"])
def dashboard():
    entries = auditor.get_log(limit=10_000)
    submissions = [e for e in entries if e.get("type") == "submission"]
    appeal_entries = [e for e in entries if e.get("type") == "appeal"]

    band_counts = {"likely_ai": 0, "likely_human": 0, "uncertain": 0}
    band_confidence_sum = {"likely_ai": 0.0, "likely_human": 0.0, "uncertain": 0.0}

    for s in submissions:
        band = s.get("attribution")
        if band in band_counts:
            band_counts[band] += 1
            band_confidence_sum[band] += s.get("confidence", 0.0)

    avg_confidence_by_band = {
        band: round(band_confidence_sum[band] / count, 4) if count else None
        for band, count in band_counts.items()
    }

    total_submissions = len(submissions)
    appeal_rate = round(len(appeal_entries) / total_submissions, 4) if total_submissions else 0.0

    return jsonify(
        {
            "total_submissions": total_submissions,
            "band_counts": band_counts,
            "avg_confidence_by_band": avg_confidence_by_band,
            "total_appeals": len(appeal_entries),
            "appeal_rate": appeal_rate,
        }
    )


if __name__ == "__main__":
    app.run(debug=True, port=5000)
