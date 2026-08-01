"""Robust JSON extraction module for agent output parsing.

Extracts the outermost balanced JSON object from raw LLM completions,
handling fences, stray backticks, and leading/trailing prose.
"""

import json

def extract_json(raw: str) -> str:
    """Extract and validate the outermost balanced JSON object from a raw completion string.

    Args:
        raw: Raw text completion from LLM.

    Returns:
        Extracted valid JSON string.

    Raises:
        ValueError: If no valid balanced JSON object can be extracted or parsed.
    """
    if not raw or not raw.strip():
        raise ValueError("Raw text is empty")

    text = raw.strip()

    # 1. Strip known fence lines (```json at start, ``` at end)
    lines = text.splitlines()
    if lines and lines[0].strip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]

    text = "\n".join(lines).strip()

    # 2. Also strip any remaining stray backticks at end or start
    if text.endswith("```"):
        text = text[:-3].strip()
    if text.startswith("```"):
        first_line_end = text.find("\n")
        if first_line_end != -1:
            text = text[first_line_end + 1 :].strip()

    # 3. Find outermost balanced {...} using brace matching with string escaping awareness
    first_brace = text.find("{")
    if first_brace == -1:
        raise ValueError("No opening brace '{' found in text")

    in_string = False
    escape = False
    brace_count = 0
    start_idx = -1
    end_idx = -1

    for idx in range(first_brace, len(text)):
        char = text[idx]

        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
            continue

        if char == "{":
            if brace_count == 0:
                start_idx = idx
            brace_count += 1
        elif char == "}":
            if brace_count > 0:
                brace_count -= 1
                if brace_count == 0:
                    end_idx = idx
                    break

    if start_idx == -1 or end_idx == -1 or brace_count != 0:
        raise ValueError("No balanced JSON object {...} found in text")

    candidate = text[start_idx : end_idx + 1].strip()

    # 4. Strip single-line JS comments (e.g. // comment) outside quotes
    import re
    lines_clean = []
    for line in candidate.splitlines():
        if "//" in line:
            in_q = False
            comment_start = -1
            for i, c in enumerate(line):
                if c == '"' and (i == 0 or line[i - 1] != "\\"):
                    in_q = not in_q
                elif c == "/" and not in_q and i + 1 < len(line) and line[i + 1] == "/":
                    comment_start = i
                    break
            if comment_start != -1:
                line = line[:comment_start]
        lines_clean.append(line)
    candidate_clean = "\n".join(lines_clean).strip()

    # 5. Validate with json.loads
    try:
        json.loads(candidate_clean)
        return candidate_clean
    except json.JSONDecodeError:
        try:
            json.loads(candidate)
            return candidate
        except json.JSONDecodeError as exc:
            raise ValueError(f"Extracted string is not valid JSON: {exc}") from exc
