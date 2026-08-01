import urllib.request
import json
import time

def main():
    req_proj = urllib.request.Request(
        'http://localhost:8000/api/projects',
        data=json.dumps({'name': 'Demo Seed Project', 'user_prompt': 'Test seed graph'}).encode(),
        headers={'Content-Type': 'application/json'}
    )
    proj = json.loads(urllib.request.urlopen(req_proj).read().decode())
    proj_id = proj['id']
    print('Created Project:', proj_id)

    req_run = urllib.request.Request(f'http://localhost:8000/api/projects/{proj_id}/runs', method='POST')
    run_data = json.loads(urllib.request.urlopen(req_run).read().decode())
    run_id = run_data['id']
    print('Started Run:', run_id)

    for _ in range(25):
        time.sleep(0.5)
        snap_res = urllib.request.urlopen(f'http://localhost:8000/api/runs/{run_id}/snapshot')
        snap = json.loads(snap_res.read().decode())
        statuses = {n['name']: n['status'] for n in snap['nodes']}
        print(f"Seq: {snap['seq_counter']} | Run: {snap['run']['status']} | Nodes: {statuses}")
        if snap['run']['status'] in ('completed', 'failed'):
            break

    print("\nFinal Artifacts:")
    for a in snap['artifacts']:
        print(f"  - Kind: {a['kind']} | Filename: {a['filename']} | Version: {a['version']} | Role: {a['produced_by_role']}")

if __name__ == "__main__":
    main()
