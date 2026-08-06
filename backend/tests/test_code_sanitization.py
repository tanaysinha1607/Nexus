"""Unit tests for generated code artifact Unicode sanitization."""

import pytest
from orchestrator.agents.parsing import parse_agent_output, sanitize_code_artifact
from orchestrator.agents.roles import BACKEND_ENGINEER_ROLE


def test_sanitize_code_artifact_replaces_unicode_chars():
    """Verify non-standard Unicode characters are replaced with ASCII equivalents."""
    raw_code = (
        "def hello():\n"
        "    # Non-breaking hyphen: ‑\n"
        "    # En dash: –\n"
        "    # Em dash: —\n"
        "    msg = ‘smart single quotes’\n"
        "    desc = “smart double quotes”\n"
        "    space = 'hello\u00a0world'\n"
        "    zero_width = 'zero\u200bwidth'\n"
        "    return msg\n"
    )

    clean = sanitize_code_artifact(raw_code)

    assert "‑" not in clean  # U+2011 gone
    assert "–" not in clean  # U+2013 gone
    assert "—" not in clean  # U+2014 gone
    assert "‘" not in clean and "’" not in clean
    assert "“" not in clean and "”" not in clean
    assert "\u00a0" not in clean
    assert "\u200b" not in clean

    assert "Non-breaking hyphen: -" in clean
    assert "En dash: -" in clean
    assert "Em dash: -" in clean
    assert "msg = 'smart single quotes'" in clean
    assert 'desc = "smart double quotes"' in clean
    assert "space = 'hello world'" in clean
    assert "zero_width = 'zerowidth'" in clean

    # Ensure python compile() succeeds on sanitized code
    compile(clean, "main.py", "exec")


def test_sanitize_code_artifact_leaves_ascii_unchanged():
    """Verify standard ASCII code is completely untouched."""
    ascii_code = (
        "from fastapi import FastAPI\n\n"
        "app = FastAPI()\n\n"
        "@app.get('/health')\n"
        "def health():\n"
        "    return {'status': 'ok'}\n"
    )
    assert sanitize_code_artifact(ascii_code) == ascii_code


def test_parse_agent_output_sanitizes_generated_main_py():
    """Verify parse_agent_output automatically sanitizes U+2011 in generated main.py."""
    raw_text = """=== FILE: main.py ===
```python
from fastapi import FastAPI

app = FastAPI()

# Key-value store description with non-breaking hyphen: user‑id
@app.get('/health')
def health():
    return {'status': 'ok'}
```

=== FILE: requirements.txt ===
```text
fastapi==0.115.0
uvicorn==0.30.0
```"""

    is_valid, artifacts, log_reason = parse_agent_output(
        raw_text, BACKEND_ENGINEER_ROLE.outputs, role=BACKEND_ENGINEER_ROLE
    )

    assert is_valid is True, f"Failed with log_reason: {log_reason}"
    main_art = next(a for a in artifacts if a.filename == "main.py")
    assert "user-id" in main_art.content
    assert "user‑id" not in main_art.content
