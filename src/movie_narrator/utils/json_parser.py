import json
import re


def _clean_json(text: str) -> str:
    """Clean common JSON issues from LLM output"""
    text = re.sub(r"\.\.\.", "", text)
    text = re.sub(r",\s*([}\]])", r"\1", text)
    text = re.sub(r"\n\s*\n", "\n", text)
    return text


def extract_json(raw_text: str) -> dict:
    raw_text = raw_text.strip()
    if not raw_text:
        raise ValueError("LLM returned empty response")

    try:
        return json.loads(raw_text)
    except json.JSONDecodeError:
        pass

    match = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw_text)
    if match:
        candidate = match.group(1).strip()
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            cleaned = _clean_json(candidate)
            try:
                return json.loads(cleaned)
            except json.JSONDecodeError:
                pass

    first = raw_text.find("{")
    last = raw_text.rfind("}")
    if first != -1 and last > first:
        candidate = raw_text[first:last + 1]
        cleaned = _clean_json(candidate)
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            pass

    raise ValueError(f"Cannot parse JSON from LLM response: {raw_text[:200]}...")
