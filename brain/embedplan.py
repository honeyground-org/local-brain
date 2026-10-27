"""Token-bounded source spans. The caller supplies the selected model's exact tokenizer."""
from __future__ import annotations

import hashlib
import re

VERSION = "egm-retrieval-v1"
MAX_TOKENS = 2048
DIMS = (128, 256, 512, 768)


def formatted(text, side="doc", title=""):
    if side == "query":
        return "task: search result | query: " + text
    if side != "doc":
        raise ValueError("unknown embedding side")
    return "title: " + (title or "none") + " | text: " + text


def digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def plan(text, title, count, budget=768):
    """Cover every source character, cutting at a paragraph/line/sentence when possible.

    No overlap in the initial reference policy. Each span carries bounded title context.
    Token counts include the exact prompt and special tokens. Source offsets never come
    from decoding token IDs, so Unicode and whitespace survive unchanged.
    """
    if not isinstance(text, str) or not isinstance(title, str):
        raise ValueError("text and title must be strings")
    if not 32 <= budget <= MAX_TOKENS:
        raise ValueError("token budget must be between 32 and 2048")
    # Metadata may not consume the body budget. This is an administrative limit, not relevance.
    while title and count(formatted("", title=title)) > budget // 2:
        title = title[:len(title)//2]
    if count(formatted("", title=title)) >= budget:
        raise ValueError("prompt alone exceeds token budget")
    out, start = [], 0
    while start < len(text):
        # Cap work on enormous paragraphs; this is not a character-to-token conversion.
        end = min(len(text), start + budget * 8)
        if count(formatted(text[start:end], title=title)) > budget:
            lo, hi = start, end
            while lo + 1 < hi:
                mid = (lo + hi) // 2
                if count(formatted(text[start:mid], title=title)) <= budget:
                    lo = mid
                else:
                    hi = mid
            end = lo
        if end <= start:
            raise ValueError("one source character cannot fit the selected token budget")
        if end < len(text):
            piece = text[start:end]
            # Prefer a complete paragraph, then line, then sentence. Avoid tiny fragments.
            for pattern in (r"\n\s*\n", r"\n", r"[.!?]\s+"):
                edges = [m.end() for m in re.finditer(pattern, piece)]
                edges = [n for n in edges if n >= len(piece)//2]
                if edges:
                    end = start + edges[-1]
                    break
        value = formatted(text[start:end], title=title)
        tokens = count(value)
        if tokens > budget:
            raise ValueError("tokenizer boundary changed the input budget")
        out.append({"start": start, "end": end, "text": text[start:end], "title": title,
                    "tokens": tokens, "input_hash": digest(value)})
        start = end
    return out
