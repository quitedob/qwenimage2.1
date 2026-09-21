"""Submit an API-format prompt to the SCRATCH ComfyUI on 127.0.0.1:8199.

Never point this at 8188 -- that is the H3 production instance.
Usage: python submit_8199.py <workflow.json> [timeout_seconds]
"""
import json
import sys
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8199"


def call(path, data=None):
    body = None if data is None else json.dumps(data).encode()
    headers = {"Content-Type": "application/json"} if body else {}
    req = urllib.request.Request(BASE + path, data=body, headers=headers)
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.load(r)


def main():
    wf = sys.argv[1]
    timeout = int(sys.argv[2]) if len(sys.argv) > 2 else 3600
    payload = json.load(open(wf, encoding="utf-8"))
    print("submitting", wf, flush=True)
    t0 = time.time()
    try:
        r = call("/prompt", payload)
    except urllib.error.HTTPError as e:
        print("HTTP", e.code, flush=True)
        print(e.read().decode("utf-8", "replace")[:4000], flush=True)
        return 1
    pid = r["prompt_id"]
    print("prompt_id:", pid, flush=True)
    while time.time() - t0 < timeout:
        h = call("/history/" + pid)
        if pid in h:
            st = h[pid].get("status", {})
            print("elapsed: %.1fs" % (time.time() - t0), flush=True)
            print("status_str:", st.get("status_str"), flush=True)
            if st.get("status_str") != "success":
                print(json.dumps(st, ensure_ascii=False)[:3000], flush=True)
            print("--- history outputs ---", flush=True)
            print(json.dumps(h[pid].get("outputs", {}), ensure_ascii=False)[:2000], flush=True)
            return 0 if st.get("status_str") == "success" else 2
        time.sleep(5)
    print("TIMEOUT after %ss" % timeout, flush=True)
    return 3


if __name__ == "__main__":
    sys.exit(main())
