from __future__ import annotations
from pathlib import Path
from typing import Any
import json


def choose_cv(records: list[dict[str, Any]], *, role: str = '', variant: str = '') -> dict[str, Any] | None:
    """Pick an active CV variant using metadata only; ties favor the newest record."""
    active=[r for r in records if r.get('active')]
    if not active: return None
    role=role.lower().strip(); variant=variant.lower().strip()
    def score(r):
        s=0; rr=str(r.get('role_type','')).lower(); vv=str(r.get('variant','')).lower()
        if role and role in rr: s+=5
        if variant and variant in vv: s+=5
        return s
    return sorted(active, key=lambda r:(score(r),str(r.get('updated_at',''))), reverse=True)[0]


def extract_text(path: str | Path) -> str:
    p=Path(path); ext=p.suffix.lower()
    if ext in {'.txt','.md','.json'}:
        raw=p.read_text(encoding='utf-8',errors='replace')
        if ext=='.json':
            try: return json.dumps(json.loads(raw),ensure_ascii=False,indent=2)
            except Exception: return raw
        return raw
    if ext=='.pdf':
        try:
            from pypdf import PdfReader
            return '\n'.join((page.extract_text() or '') for page in PdfReader(str(p)).pages)
        except Exception:
            return ''
    if ext=='.docx':
        try:
            from docx import Document
            return '\n'.join(x.text for x in Document(str(p)).paragraphs)
        except Exception:
            return ''
    return ''
