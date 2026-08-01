import pytest
import json
from orchestrator.agents.json_extract import extract_json

def test_clean_json_no_fences():
    raw = '{"endpoints": [{"method": "GET", "path": "/api/v1/health"}]}'
    extracted = extract_json(raw)
    assert extracted == raw
    assert json.loads(extracted)["endpoints"][0]["method"] == "GET"

def test_fenced_json_with_lang():
    raw = """```json
{
  "endpoints": [{"method": "POST", "path": "/api/v1/auth/login"}]
}
```"""
    extracted = extract_json(raw)
    parsed = json.loads(extracted)
    assert parsed["endpoints"][0]["path"] == "/api/v1/auth/login"

def test_fenced_json_no_lang():
    raw = """```
{
  "endpoints": [{"method": "GET", "path": "/api/v1/orders"}]
}
```"""
    extracted = extract_json(raw)
    parsed = json.loads(extracted)
    assert parsed["endpoints"][0]["path"] == "/api/v1/orders"

def test_unfenced_with_stray_trailing_backticks():
    """Exact failure mode from Phase 1.2 run: unfenced JSON block followed by stray closing backticks."""
    raw = """{
  "endpoints": [
    {
      "method": "POST",
      "path": "/api/v1/auth/register"
    }
  ]
}
```"""
    extracted = extract_json(raw)
    parsed = json.loads(extracted)
    assert parsed["endpoints"][0]["method"] == "POST"

def test_leading_and_trailing_prose():
    raw = """Here is the API contract requested for Phase 1.2:

{
  "endpoints": [
    {
      "method": "DELETE",
      "path": "/api/v1/orders/123"
    }
  ]
}

Please let me know if you need any additional endpoints defined!"""
    extracted = extract_json(raw)
    parsed = json.loads(extracted)
    assert parsed["endpoints"][0]["method"] == "DELETE"

def test_string_escapes_with_braces_inside():
    """Ensure strings containing braces or quotes inside JSON don't break brace matching."""
    raw = '{"endpoints": [{"method": "POST", "path": "/api/v1/echo", "summary": "Returns {hello: \\"world\\"}"}]}'
    extracted = extract_json(raw)
    parsed = json.loads(extracted)
    assert parsed["endpoints"][0]["summary"] == 'Returns {hello: "world"}'

def test_no_opening_brace_raises_value_error():
    with pytest.raises(ValueError, match="No opening brace"):
        extract_json("Just plain text with no JSON object")

def test_unbalanced_braces_raises_value_error():
    with pytest.raises(ValueError, match="No balanced JSON object"):
        extract_json('{"endpoints": [{"method": "GET"')

def test_invalid_json_content_raises_value_error():
    with pytest.raises(ValueError, match="not valid JSON"):
        extract_json('{"endpoints": [{"method": "GET",}]}')
