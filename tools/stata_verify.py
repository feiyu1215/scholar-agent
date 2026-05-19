"""
tools/stata_verify.py — Stata MCP Statistical Verification (Mental Loop).

Generates Stata .do code from methodology issues, executes via MCP,
and interprets results to verify/challenge paper's statistical claims.

Design choices:
- Graceful degradation: if Stata MCP unavailable, outputs .do code as guidance
- Mental Loop pattern: generate code → simulate/execute → interpret results
- Only triggered for methodology issues flagged needs_statistical_verification
- Red Line 1: even if Stata contradicts paper claims, action_type = guidance (never auto-modify)
- Timeout: 30s max for Stata execution; falls back to manual guidance on timeout

Integration:
- Called by the agent loop when processing issues with needs_statistical_verification=True
- Results stored in revision_state via record_stata_result()
"""

from __future__ import annotations

import json
import asyncio
from pathlib import Path
from typing import Dict, Optional, Tuple

from llm.client import LLMClient

WORKSPACE = Path(".workspace")
STATA_TIMEOUT = 30  # seconds

# ─── Stata Availability ──────────────────────────────────────────────────────

_stata_available: Optional[bool] = None


async def check_stata_availability() -> bool:
    """
    Check if Stata MCP server is reachable.
    Caches result for the session.
    """
    global _stata_available
    if _stata_available is not None:
        return _stata_available

    try:
        # Try to import and ping the MCP client
        # In production, this would connect to the Stata MCP server
        # For now, we check if the MCP config exists
        mcp_config = Path.home() / ".mcp" / "stata" / "config.json"
        if mcp_config.exists():
            # TODO: Actually ping the server
            _stata_available = False  # Conservative: treat as unavailable until proven
        else:
            _stata_available = False
    except Exception:
        _stata_available = False

    return _stata_available


# ─── Code Generation ─────────────────────────────────────────────────────────

STATA_CODEGEN_PROMPT = """You are generating Stata .do file code to verify a statistical claim in a research paper.

The methodology reviewer has flagged an issue. Your job:
1. Generate Stata code that would test/verify the claim
2. Include comments explaining each step
3. Assume the data is already loaded (use generic variable names from the paper)
4. Output only the .do file content — no markdown, no explanation outside comments

## Issue to verify:
{issue_json}

## Paper context (methods/data section excerpt):
{methods_context}

## Requirements:
- Include clear `* COMMENT` lines explaining the logic
- End with a `* INTERPRETATION` comment block explaining what the output means
- Use standard Stata syntax (version 17+)
- Include error handling where appropriate (capture noisily)
- For sample size issues: use power analysis (power twomeans, power twoproportions)
- For test choice issues: run both the paper's test and the appropriate alternative
- For robustness: include at least 2 robustness variations"""


async def generate_stata_code(
    issue: Dict,
    methods_context: str = "",
    provider: str = None,
    model: str = None,
) -> str:
    """Generate Stata .do code to verify a methodology issue."""
    client = LLMClient(model=model, max_concurrent=3, provider=provider)

    system = STATA_CODEGEN_PROMPT.format(
        issue_json=json.dumps(issue, indent=2, ensure_ascii=False),
        methods_context=methods_context[:2000],
    )

    response = await client.chat(
        system=system,
        user="Generate the .do file now.",
        max_tokens=2000,
        temperature=0.1,
    )

    # Clean response
    code = response.strip()
    if code.startswith("```"):
        code = code.split("```")[1]
        if code.startswith("stata") or code.startswith("do"):
            code = code.split("\n", 1)[1] if "\n" in code else ""
    
    return code


# ─── Execution ───────────────────────────────────────────────────────────────

async def execute_stata(do_code: str) -> Dict:
    """
    Execute Stata .do code via MCP server.
    Returns execution result or timeout/error status.
    """
    if not await check_stata_availability():
        return {
            "status": "unavailable",
            "message": "Stata MCP server not configured or unreachable.",
            "code": do_code,
        }

    try:
        # TODO: Actual MCP call to Stata server
        # result = await mcp_client.call("stata.execute", {"code": do_code}, timeout=STATA_TIMEOUT)
        
        # Placeholder — in production this calls the real MCP server
        return {
            "status": "unavailable",
            "message": "Stata MCP execution not yet implemented.",
            "code": do_code,
        }
    except asyncio.TimeoutError:
        return {
            "status": "timeout",
            "message": f"Stata execution timed out after {STATA_TIMEOUT}s.",
            "code": do_code,
        }
    except Exception as e:
        return {
            "status": "execution_error",
            "message": f"Stata error: {type(e).__name__}: {e}",
            "code": do_code,
        }


# ─── Interpretation ──────────────────────────────────────────────────────────

INTERPRET_PROMPT = """You are interpreting Stata output to verify a paper's statistical claim.

## Original claim from paper:
{claim}

## Stata output:
{output}

## Your job:
1. Compare Stata results to the paper's claims
2. Report any discrepancies factually (do NOT editorialize)
3. If results are consistent: state so clearly
4. If results differ: report exact numbers from both sources

Output format (JSON):
{{
  "consistent": true/false,
  "paper_claims": "<what the paper says>",
  "stata_result": "<what Stata found>",
  "discrepancy": "<description of difference, or null if consistent>",
  "confidence": <0.0-1.0 how confident in the comparison>,
  "recommendation": "<specific next step for the author>"
}}"""


async def interpret_stata_output(
    issue: Dict,
    stata_output: str,
    provider: str = None,
    model: str = None,
) -> Dict:
    """Interpret Stata execution results against paper claims."""
    client = LLMClient(model=model, max_concurrent=3, provider=provider)

    claim = issue.get("description", "") + " | " + issue.get("suggestion", "")
    
    system = INTERPRET_PROMPT.format(
        claim=claim,
        output=stata_output[:3000],
    )

    response = await client.chat(
        system=system,
        user="Interpret now.",
        max_tokens=1000,
        temperature=0.0,
    )

    try:
        response = response.strip()
        if response.startswith("```"):
            response = response.split("```")[1]
            if response.startswith("json"):
                response = response[4:]
        return json.loads(response)
    except (json.JSONDecodeError, ValueError):
        return {
            "consistent": None,
            "parse_error": True,
            "raw": response[:500],
        }


# ─── Main Entry Point ────────────────────────────────────────────────────────

async def stata_verify(
    issue: Dict,
    methods_context: str = "",
    provider: str = None,
    model: str = None,
) -> Dict:
    """
    Full verification pipeline for a methodology issue.
    
    Returns a verification result dict that includes:
    - status: "verified" | "discrepancy" | "unavailable" | "timeout" | "execution_error"
    - do_code: generated Stata code
    - interpretation: result comparison (if execution succeeded)
    - guidance: recommended next steps
    
    Per DESIGN.md §6.4: results NEVER auto-modify the paper. 
    If Stata contradicts the paper, action_type is forced to "guidance".
    """
    # Step 1: Generate .do code
    do_code = await generate_stata_code(
        issue, methods_context=methods_context, 
        provider=provider, model=model
    )

    # Save .do file
    stata_dir = WORKSPACE / "stata"
    stata_dir.mkdir(parents=True, exist_ok=True)
    issue_id = issue.get("id", "unknown")
    do_path = stata_dir / f"{issue_id}.do"
    do_path.write_text(do_code, encoding="utf-8")

    # Step 2: Execute (with graceful degradation)
    exec_result = await execute_stata(do_code)

    if exec_result["status"] == "unavailable":
        # Graceful degradation: output code as guidance
        return {
            "status": "unavailable",
            "do_code": do_code,
            "do_path": str(do_path),
            "interpretation": None,
            "guidance": (
                f"Stata MCP not available. Generated .do code saved to {do_path}.\n"
                f"Please run manually and report results. The code tests: "
                f"{issue.get('description', '')[:100]}"
            ),
        }

    if exec_result["status"] in ("timeout", "execution_error"):
        return {
            "status": exec_result["status"],
            "do_code": do_code,
            "do_path": str(do_path),
            "error_message": exec_result["message"],
            "interpretation": None,
            "guidance": (
                f"Stata execution failed ({exec_result['status']}): {exec_result['message']}\n"
                f"Code saved to {do_path}. Please review and run manually."
            ),
        }

    # Step 3: Interpret results (only reached if execution succeeded)
    stata_output = exec_result.get("output", "")
    interpretation = await interpret_stata_output(
        issue, stata_output, provider=provider, model=model
    )

    # Determine final status
    if interpretation.get("consistent"):
        status = "verified"
        guidance = "Stata verification confirms the paper's statistical claims."
    else:
        status = "discrepancy"
        discrepancy = interpretation.get("discrepancy", "Unknown discrepancy")
        guidance = (
            f"⚠️ Stata results differ from paper claims: {discrepancy}\n"
            f"Paper says: {interpretation.get('paper_claims', 'N/A')}\n"
            f"Stata found: {interpretation.get('stata_result', 'N/A')}\n"
            f"Recommendation: {interpretation.get('recommendation', 'Review manually')}\n"
            f"\n[NOTE: Per Red Line 1, this issue remains as GUIDANCE — "
            f"the agent will NOT auto-modify the paper's claims.]"
        )

    return {
        "status": status,
        "do_code": do_code,
        "do_path": str(do_path),
        "stata_output": stata_output,
        "interpretation": interpretation,
        "guidance": guidance,
    }


def format_stata_result(result: Dict) -> str:
    """Format Stata verification result for display."""
    lines = []
    status = result.get("status", "unknown")
    
    status_icons = {
        "verified": "✅",
        "discrepancy": "⚠️",
        "unavailable": "📋",
        "timeout": "⏱️",
        "execution_error": "❌",
    }
    icon = status_icons.get(status, "❓")
    
    lines.append(f"{icon} Stata Verification: {status}")
    lines.append(f"  .do file: {result.get('do_path', 'N/A')}")
    
    guidance = result.get("guidance", "")
    if guidance:
        lines.append(f"  {guidance}")
    
    interpretation = result.get("interpretation")
    if interpretation and not interpretation.get("parse_error"):
        lines.append(f"\n  Paper claims: {interpretation.get('paper_claims', 'N/A')}")
        lines.append(f"  Stata result: {interpretation.get('stata_result', 'N/A')}")
        if interpretation.get("discrepancy"):
            lines.append(f"  ⚠️ Discrepancy: {interpretation['discrepancy']}")

    return "\n".join(lines)
