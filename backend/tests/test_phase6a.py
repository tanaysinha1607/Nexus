"""Phase 6a Unit Tests: Prompt Generality, Domain Un-hardcoding, and Dynamic Contract Adaptation."""

import json
import pytest
from orchestrator.agents.roles import MAX_MVP_ENDPOINTS, ROLES
from orchestrator.handlers.real_agent import assemble_user_message
from app.models import Artifact


def test_no_crypto_or_hardcoded_endpoints_in_prompts():
    """Assert that NO agent role system prompt contains domain-specific hardcoded text."""
    forbidden_terms = [
        "crypto",
        "portfolio",
        "auth/register",
        "auth/login",
        "portfolio/summary",
    ]

    for role_name, role in ROLES.items():
        prompt_lower = role.system_prompt.lower()
        for term in forbidden_terms:
            assert term not in prompt_lower, (
                f"Role '{role_name}' system prompt contains hardcoded domain term '{term}'!"
            )


def test_max_mvp_endpoints_cap_in_api_designer():
    """Assert that MAX_MVP_ENDPOINTS constant exists and is referenced in ApiDesigner's prompt."""
    assert MAX_MVP_ENDPOINTS == 5
    prompt = ROLES["api_designer"].system_prompt
    assert f"MAX_MVP_ENDPOINTS ({MAX_MVP_ENDPOINTS})" in prompt


def test_backend_prompt_assembly_adapts_to_contract():
    """Assert Backend engineer prompt assembly includes arbitrary contract endpoints."""
    custom_contract = {
        "endpoints": [
            {"method": "POST", "path": "/api/v1/shorten", "summary": "Create short URL", "status_code": 201},
            {"method": "GET", "path": "/{short_code}", "summary": "Redirect to long URL", "status_code": 302},
            {"method": "GET", "path": "/api/v1/analytics", "summary": "Click analytics", "status_code": 200},
            {"method": "POST", "path": "/api/v1/keys", "summary": "Generate API Key", "status_code": 201},
        ]
    }

    contract_artifact = Artifact(
        filename="api_contract.json",
        kind="api_contract",
        content=json.dumps(custom_contract, indent=2),
    )

    inputs = {"api_contract": contract_artifact}
    assembled_user_msg = assemble_user_message(inputs, ROLES["backend_engineer"])

    assert "/api/v1/shorten" in assembled_user_msg
    assert "/{short_code}" in assembled_user_msg
    assert "/api/v1/analytics" in assembled_user_msg
    assert "/api/v1/keys" in assembled_user_msg
    assert "Implement EVERY endpoint defined in api_contract.json" in ROLES["backend_engineer"].system_prompt


def test_qa_prompt_assembly_adapts_to_contract():
    """Assert QA engineer prompt assembly adapts to arbitrary contracts including redirects."""
    contract_2_endpoints = {
        "endpoints": [
            {"method": "GET", "path": "/health", "summary": "Health Check", "status_code": 200},
            {"method": "POST", "path": "/api/v1/items", "summary": "Create item", "status_code": 201},
        ]
    }

    contract_redirect = {
        "endpoints": [
            {"method": "POST", "path": "/api/v1/shorten", "summary": "Create short URL", "status_code": 201},
            {
                "method": "GET",
                "path": "/{short_code}",
                "summary": "Redirect link",
                "status_code": 302,
                "headers": {"Location": {"type": "string"}},
            },
        ]
    }

    art2 = Artifact(filename="api_contract.json", kind="api_contract", content=json.dumps(contract_2_endpoints))
    art_redir = Artifact(filename="api_contract.json", kind="api_contract", content=json.dumps(contract_redirect))

    assembled_2 = assemble_user_message({"api_contract": art2}, ROLES["qa_engineer"])
    assembled_redir = assemble_user_message({"api_contract": art_redir}, ROLES["qa_engineer"])

    assert "/api/v1/items" in assembled_2
    assert "/{short_code}" in assembled_redir
    assert "follow_redirects=False" in ROLES["qa_engineer"].system_prompt
    assert "Location" in ROLES["qa_engineer"].system_prompt
