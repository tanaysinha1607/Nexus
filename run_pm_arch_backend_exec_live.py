import urllib.request
import json
import time
import sys
import difflib
import subprocess

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

BASE_URL = "http://localhost:8000"
PROMPT = "Build a cryptocurrency paper trading platform with authentication, a dashboard, charts, portfolio management, and an admin panel."


def run_phase_14a_live():
    print("=" * 80)
    print("NEXUS PHASE 1.4a LIVE REWORK VERIFICATION RUN")
    print("=" * 80)

    print("\nCreating project...")
    req = urllib.request.Request(
        f"{BASE_URL}/api/projects",
        data=json.dumps({"name": "Phase 1.4a Live Rework Verification", "user_prompt": PROMPT}).encode(),
        headers={"Content-Type": "application/json"}
    )
    res = json.loads(urllib.request.urlopen(req).read().decode())
    project_id = res["id"]
    print(f"Project created: {project_id}")

    print("\nTriggering pm_arch_backend_exec run...")
    req_run = urllib.request.Request(
        f"{BASE_URL}/api/projects/{project_id}/runs?graph=pm_arch_backend_exec",
        method="POST"
    )
    res_run = json.loads(urllib.request.urlopen(req_run).read().decode())
    run_id = res_run["id"]
    print(f"Run started: {run_id}")

    print("\nPolling run status...")
    snap = None
    for i in range(180):
        time.sleep(3)
        snap_res = urllib.request.urlopen(f"{BASE_URL}/api/runs/{run_id}/snapshot")
        snap = json.loads(snap_res.read().decode())
        run_status = snap["run"]["status"]
        node_summary = [f"{n['name']}(attempt {n['attempt']}): {n['status']}" for n in snap["nodes"]]
        print(f"[{i*3}s] Run status: {run_status} | Nodes: {node_summary}")
        if run_status in ("completed", "failed"):
            break

    print("\n" + "=" * 80)
    print(f"LIVE VERIFICATION RUN METRICS (Run ID: {run_id})")
    print("=" * 80)
    print(f"Final Run Status: {snap['run']['status']}")

    # Group nodes by attempt
    attempts = {}
    for n in snap["nodes"]:
        att = n["attempt"]
        attempts.setdefault(att, []).append(n)

    print(f"Total Attempts Created: {max(attempts.keys())}")

    # Track code artifacts across attempts for diffing
    code_by_attempt = {}

    for att in sorted(attempts.keys()):
        print("\n" + "-" * 80)
        print(f"=== ATTEMPT {att} ===")
        print("-" * 80)

        att_nodes = attempts[att]
        backend_node = next((n for n in att_nodes if n.get("agent_role") == "backend_engineer"), None)

        for n in att_nodes:
            rework_info = f", rework_of: {n.get('rework_of')}" if n.get("rework_of") else ""
            print(f"Node: {n['name']} (ID: {n['id']}, Role: {n.get('agent_role')}, Type: {n.get('node_type')}{rework_info}) -> Status: {n['status']}")

        # Fetch prompt & failure_context artifacts for this attempt's backend engineer
        if backend_node:
            prompt_art = next((a for a in snap["artifacts"] if a["node_id"] == backend_node["id"] and a["kind"] == "prompt"), None)
            if prompt_art:
                char_len = len(prompt_art["content"])
                est_tokens = char_len // 4
                has_fc = "## INPUT: failure_context" in prompt_art["content"]
                print(f"\n[Attempt {att} Backend Engineer Prompt Metrics]")
                print(f"  - Prompt Character Count: {char_len}")
                print(f"  - Estimated Input Tokens: ~{est_tokens}")
                print(f"  - TPM Budget Check: ~{est_tokens} + 3200 max_tokens = {est_tokens + 3200} tokens (Budget: 7500) -> {'SAFE' if est_tokens + 3200 <= 7500 else 'TPM EXCEEDED'}")
                print(f"  - Failure context present in prompt: {has_fc}")
                if has_fc:
                    print("  - CONFIRMED: failure_context (with container log traceback) appended in prompt!")

        # Source code artifacts for this attempt
        att_code = {a["filename"]: a["content"] for a in snap["artifacts"] if a["kind"] == "source_code" and a["attempt"] == att}
        code_by_attempt[att] = att_code

        if att > 1 and (att - 1) in code_by_attempt:
            prev_code = code_by_attempt[att - 1]
            print(f"\n[CODE DIFF VS ATTEMPT {att - 1}]")
            all_files = set(prev_code.keys()) | set(att_code.keys())
            for fn in sorted(all_files):
                p_text = prev_code.get(fn, "")
                c_text = att_code.get(fn, "")
                if p_text != c_text:
                    print(f"--- File changed: {fn} ---")
                    diff = difflib.unified_diff(
                        p_text.splitlines(), c_text.splitlines(),
                        fromfile=f"attempt_{att-1}/{fn}", tofile=f"attempt_{att}/{fn}", lineterm=""
                    )
                    print("\n".join(diff))
                else:
                    print(f"--- File unchanged: {fn} ---")

        # Execution report for this attempt
        exec_node = next((n for n in att_nodes if n.get("node_type") == "executor"), None)
        if exec_node:
            exec_art = next((a for a in snap["artifacts"] if a["node_id"] == exec_node["id"] and a["kind"] == "execution_report"), None)
            if exec_art:
                print(f"\n[Attempt {att} execution_report.json]")
                print(exec_art["content"])

        # Verdict for this attempt
        val_node = next((a for a in att_nodes if a.get("node_type") == "validator"), None)
        if val_node:
            v_art = next((a for a in snap["artifacts"] if a["node_id"] == val_node["id"] and a["kind"] == "verdict"), None)
            if v_art:
                print(f"\n[Attempt {att} verdict.json (Objective Validator)]")
                print(v_art["content"])

        # Senior Reviewer node and review.md for this attempt
        rev_node = next((a for a in att_nodes if a.get("agent_role") == "senior_reviewer"), None)
        if rev_node:
            print(f"\n[Attempt {att} Senior Reviewer Node Status: {rev_node['status']}]")
            rev_prompt_art = next((a for a in snap["artifacts"] if a["node_id"] == rev_node["id"] and a["kind"] == "prompt"), None)
            if rev_prompt_art:
                rev_char_len = len(rev_prompt_art["content"])
                rev_tokens = rev_char_len // 4
                print(f"  - Reviewer Prompt Chars: {rev_char_len}")
                print(f"  - Reviewer Estimated Tokens: ~{rev_tokens}")
                print(f"  - TPM Check: ~{rev_tokens} + 1500 max_tokens = {rev_tokens + 1500} (Budget: 7500) -> {'SAFE' if rev_tokens + 1500 <= 7500 else 'EXCEEDED'}")

            rev_art = next((a for a in snap["artifacts"] if (a["node_id"] == rev_node["id"] or a["attempt"] == att) and a["kind"] == "review"), None)
            if rev_art:
                print(f"\n[Attempt {att} review.md (Verbatim Reviewer Output)]")
                print(rev_art["content"])

    print("\n" + "=" * 80)
    print("DOCKER TEARDOWN & CONTAINER LEAK AUDIT")
    print("=" * 80)
    try:
        ps_out = subprocess.check_output(["docker", "ps", "-a", "--filter", "name=nexus-sb-"], text=True)
        print("Active/Leaked sandbox containers (filter: nexus-sb-):")
        print(ps_out.strip() if ps_out.strip() else "NONE (0 leaked containers!)")
    except Exception as e:
        print(f"Could not check docker ps: {e}")

    try:
        img_out = subprocess.check_output(["docker", "images", "--filter", "reference=nexus-sb-*"], text=True)
        print("\nLeftover sandbox images (filter: nexus-sb-*):")
        print(img_out.strip() if img_out.strip() else "NONE (0 leaked images!)")
    except Exception as e:
        print(f"Could not check docker images: {e}")

if __name__ == "__main__":
    run_phase_14a_live()
