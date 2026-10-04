from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from gateway.models import AIRequest

SYSTEM_PROMPT = """You create truthful job-application drafts for a candidate.
Return JSON only with exactly these top-level keys: cv, cover_letter, claims.
cv must be an object with keys: headline, summary, skills, experience, education, links.
cover_letter must be a string.
claims must be a list of objects with keys: text, evidence_state, fact_id.
Rules:
- Use ONLY candidate facts supplied in candidate_facts for factual candidate claims.
- A selected CV may guide structure, wording, and formatting, but its claims do NOT become verified merely because they appear in the CV.
- KNOWN and VERIFIED facts supplied by the trusted candidate-fact store may be stated as facts.
- Facts extracted from a personally uploaded CV are not trusted merely because they appear in the CV; they must be USER_CONFIRMED before being supplied as candidate facts.
- INFERRED and UNKNOWN facts must NOT be stated as candidate claims.
- Never invent employment, qualifications, certifications, tools, proficiency, metrics, dates, employers, projects, responsibilities or achievements.
- Do not convert a job requirement into a candidate skill.
- Tailor wording to the job, but preserve truth.
- If candidate evidence is sparse, produce a conservative draft rather than filling gaps.
- Do not mention that facts are missing inside the CV unless useful; simply omit unsupported claims.
- Return JSON only; no markdown fences.
- Keep the CV summary below 35 words and the cover letter below 100 words.
- Keep list entries and claim evidence concise; omit unsupported items rather than filling space.
- Every claim must reproduce the FULL text of a supplied USER_CONFIRMED fact exactly and reference its id as fact_id.
- Use evidence_state USER_CONFIRMED. Never invent an id or remove a negation from a fact.
- Every CV list item must be an exact claim text; summary must be empty or exact claim texts joined by one space.
- Headline must be the supplied job title, or an exact claim text.
- Cover letter must be exactly: I am interested in this role. followed optionally by a space and exact claim texts joined by one space, then a space and Thank you for considering my application.
"""

CV_KEYS = {"headline", "summary", "skills", "experience", "education", "links"}
CLAIM_KEYS = {"text", "evidence_state", "fact_id"}
VALID_STATES = {"KNOWN", "VERIFIED", "INFERRED", "UNKNOWN", "USER_CONFIRMED"}


def _truthful_facts(candidate_facts: dict[str, Any]) -> dict[str, Any]:
    """Normalize fact records so only KNOWN/VERIFIED facts can become claims."""
    if not isinstance(candidate_facts, dict):
        return {}
    facts = candidate_facts.get("facts", candidate_facts)
    if not isinstance(facts, list):
        return {'facts': []}
    allowed = []
    for fact in facts:
        if isinstance(fact, dict) and fact.get('status') == 'USER_CONFIRMED' and fact.get('id') is not None:
            allowed.append(fact)
    return {"facts": allowed}


def _validate_claims(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        raise ValueError("claims must be a list")
    out = []
    for claim in value:
        if not isinstance(claim, dict) or set(claim) != CLAIM_KEYS:
            raise ValueError("Each claim must contain exactly text and evidence_state")
        state = str(claim["evidence_state"])
        if state not in VALID_STATES:
            raise ValueError(f"Invalid evidence state: {state}")
        if state not in {"USER_CONFIRMED", "KNOWN", "VERIFIED"}:
            raise ValueError("Generated candidate claims must be based on trusted candidate facts")
        text = str(claim["text"]).strip()
        if not text:
            raise ValueError("Claim text cannot be empty")
        out.append({"text": text, "evidence_state": state, "fact_id": claim['fact_id']})
    return out


def _validate_output(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"cv", "cover_letter", "claims"}:
        raise ValueError("Application draft must contain exactly cv, cover_letter and claims")
    cv = value["cv"]
    if not isinstance(cv, dict) or set(cv) != CV_KEYS:
        raise ValueError("CV has an invalid schema")
    for key in ("skills", "experience", "education", "links"):
        if not isinstance(cv[key], list):
            raise ValueError(f"CV field {key} must be a list")
    if not isinstance(cv["summary"], str) or not isinstance(cv["headline"], str):
        raise ValueError("CV headline/summary must be text")
    if not isinstance(value["cover_letter"], str):
        raise ValueError("cover_letter must be text")
    value["claims"] = _validate_claims(value["claims"])
    return value


def _extract(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    return _validate_output(json.loads(cleaned))


def generate_application_draft(gateway, job: dict[str, Any], candidate_facts: dict[str, Any], base_cv: dict[str, Any] | None = None, base_cv_text: str = "") -> tuple[dict[str, Any], Any]:
    payload = json.dumps({"job": job, "candidate_facts": _truthful_facts(candidate_facts),
                          "selected_cv_metadata": base_cv or {},
                          "selected_cv_text": base_cv_text[:12000]}, ensure_ascii=False, sort_keys=True)
    response = gateway.generate(AIRequest(SYSTEM_PROMPT, payload, temperature=0.0, max_tokens=768))
    draft = _extract(response.text)
    validate_evidence(draft, _truthful_facts(candidate_facts), job)
    return draft, response


def validate_evidence(draft, candidate_facts, job):
    """Validate actual text against trusted records, never the model's own labels."""
    _validate_output(draft)
    facts = {str(f['id']): str(f['text']).strip() for f in candidate_facts.get('facts', [])
             if f.get('status') == 'USER_CONFIRMED' and 'id' in f and 'text' in f}
    if not facts or not draft['claims']:
        raise ValueError('Confirm relevant candidate facts before creating an application draft.')
    texts = []
    for claim in draft['claims']:
        text = claim['text']
        if claim['evidence_state'] != 'USER_CONFIRMED' or facts.get(str(claim['fact_id'])) != text:
            raise ValueError('Draft claim is not the complete text of its referenced confirmed fact.')
        texts.append(text)
    cv = draft['cv']
    for key in ('skills', 'experience', 'education', 'links'):
        if any(not isinstance(item, str) or item not in texts for item in cv[key]):
            raise ValueError('CV contains text without a confirmed evidence reference.')
    if cv['headline'] not in [str(job.get('title', '')), *texts]:
        raise ValueError('CV headline is not supported by the job title or a confirmed fact.')
    if cv['summary'] not in ('', ' '.join(texts)):
        raise ValueError('CV summary contains unreferenced text.')
    letter = 'I am interested in this role.' + (' ' + ' '.join(texts) if texts else '') + ' Thank you for considering my application.'
    if draft['cover_letter'] != letter:
        raise ValueError('Cover letter contains text outside the confirmed facts and approved introduction/closing.')
    return True


def _deterministic_draft(job: dict[str, Any], candidate_facts: dict[str, Any]) -> dict[str, Any]:
    """Safe fallback when no AI gateway is available. It never invents candidate claims."""
    return {
        "cv": {
            "headline": str(job.get("title", "Job Application")),
            "summary": "",
            "skills": [], "experience": [], "education": [], "links": []
        },
        "cover_letter": "",
        "claims": []
    }


def generate_cv_draft(gateway, job: dict[str, Any], candidate_facts: dict[str, Any]) -> dict[str, Any]:
    if gateway is None:
        return _deterministic_draft(job, candidate_facts)["cv"]
    return generate_application_draft(gateway, job, candidate_facts)[0]["cv"]


def generate_cover_letter_draft(gateway, job: dict[str, Any], candidate_facts: dict[str, Any]) -> str:
    if gateway is None:
        return ""
    return generate_application_draft(gateway, job, candidate_facts)[0]["cover_letter"]


def validate_candidate_claims(draft: dict[str, Any]) -> bool:
    _validate_output(draft)
    return True


def save_draft_files(root: str | Path, application_id: str, draft: dict[str, Any]) -> dict[str, str]:
    from operations.draft_files import draft_files
    with draft_files(root, application_id, draft) as paths:
        return paths
