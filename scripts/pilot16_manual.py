"""Explicit, resumable 16-pair pilot commands. Never print service credentials."""
import argparse
import hashlib
import json
from pathlib import Path
import urllib.error
import urllib.request
import uuid
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SELECTION = {
    "JD01": ["CV0125", "CV0130", "CV0175", "CV0054"],
    "JD02": ["CV0347", "CV0363", "CV0326", "CV0377"],
    "JD03": ["CV0553", "CV0591", "CV0713", "CV0731"],
    "JD04": ["CV0981", "CV0755", "CV0828", "CV0482"],
}


def save(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2), encoding="utf-8")
    temporary.replace(path)


def prepare(folder):
    if folder.exists():
        raise SystemExit("Output already exists; use its saved state or choose another --output.")
    source = ROOT / "artifacts/pilot36"
    manifest = json.loads((source / "CV_36_pilot_20260927.manifest.json").read_text())
    archive = source / manifest["zip_filename"]
    if hashlib.sha256(archive.read_bytes()).hexdigest() != manifest["zip_sha256"]:
        raise SystemExit("Historical archive checksum mismatch.")
    candidates = {c["candidate_id"]: c for c in manifest["candidates"]}
    approved = json.loads((source / "jobs.json").read_text())
    if len(approved) != 4:
        raise SystemExit("Expected four saved approved JD references.")
    folder.mkdir(parents=True)
    state = {"run_id": uuid.uuid4().hex, "groups": {}}
    with zipfile.ZipFile(archive) as incoming:
        for index, (jd, identifiers) in enumerate(SELECTION.items()):
            selected = [candidates[c] for c in identifiers]
            with zipfile.ZipFile(folder / f"{jd}.zip", "w", zipfile.ZIP_DEFLATED) as outgoing:
                for c in selected:
                    data = incoming.read(c["filename"])
                    if hashlib.sha256(data).hexdigest() != c["sha256"]:
                        raise SystemExit(f"CV checksum mismatch: {c['candidate_id']}")
                    outgoing.writestr(c["filename"], data)
            state["groups"][jd] = {"approved_jd_id": approved[index],
                                   "candidates": identifiers, "campaign_id": None,
                                   "uploaded": False}
    save(folder / "state.json", state)
    print(f"Prepared {folder}: four archives, 16 unique CVs, 16 target pairs. No API calls.")


def request(method, route, *, payload=None, content=None):
    from dotenv import dotenv_values
    values = dotenv_values(ROOT / "docker/.env")
    credentials = json.loads(values.get("API_TOKENS_JSON") or "[]")
    if not credentials:
        raise SystemExit("docker/.env has no configured service principal.")
    headers = {"Authorization": "Bearer " + credentials[0]["token"]}
    if payload is not None:
        content = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    elif content is not None:
        headers["Content-Type"] = "application/zip"
    req = urllib.request.Request("http://127.0.0.1:8000" + route,
                                 data=content, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=120) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        raise SystemExit(f"API returned HTTP {error.code} for {route}; no credentials displayed.") from None
    except urllib.error.URLError:
        raise SystemExit("API unreachable; check /ready and Docker status.") from None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["prepare", "start", "status"])
    parser.add_argument("--output", default="artifacts/pilot16/v14-manual")
    parser.add_argument("--jd", choices=list(SELECTION))
    args = parser.parse_args()
    folder = ROOT / args.output
    if args.command == "prepare":
        prepare(folder)
        return
    state_path = folder / "state.json"
    state = json.loads(state_path.read_text())
    if args.command == "start" and not args.jd:
        parser.error("start requires --jd; campaigns are started explicitly one at a time")
    for jd, group in state["groups"].items():
        if args.jd and jd != args.jd:
            continue
        if args.command == "start":
            if not group["campaign_id"]:
                result = request("POST", "/api/v1/campaigns", payload={
                    "name": f"pilot16-v14-{state['run_id']}-{jd}",
                    "approved_jd_ids": [group["approved_jd_id"]],
                    "idempotency_key": f"pilot16-v14-{state['run_id']}-{jd}"})
                group["campaign_id"] = result["campaign_id"]
                save(state_path, state)
                save(folder / f"{jd}.created.json", result)
            if not group["uploaded"]:
                result = request("POST", f"/api/v1/campaigns/{group['campaign_id']}/archive",
                                 content=(folder / f"{jd}.zip").read_bytes())
                save(folder / f"{jd}.intake.json", result)
                group["uploaded"] = True
                save(state_path, state)
            print(f"{jd}: started/resumed {group['campaign_id']}")
        else:
            if not group["campaign_id"]:
                print(f"{jd}: not started")
                continue
            base = f"/api/v1/campaigns/{group['campaign_id']}"
            result = request("GET", base)
            save(folder / f"{jd}.status.json", result)
            definitions = request("GET", base + "/jds")
            save(folder / f"{jd}.jds.json", definitions)
            for definition in definitions["jds"]:
                route = base + "/jds/" + definition["jd_key"]
                save(folder / f"{jd}.rankings.json", request("GET", route + "/rankings"))
                for outcome in ["REVIEW_REQUIRED", "EVALUATION_FAILED", "FILTER_REJECTED",
                                "PROCESSING_FAILED", "CUTOFF_EXCLUDED", "EXTRACTION_FAILED"]:
                    save(folder / f"{jd}.{outcome}.json", request(
                        "GET", route + "/outcomes?status=" + outcome))
            print(json.dumps({"jd": jd, "campaign_id": group["campaign_id"],
                              "status": result.get("status"), "counts": result.get("counts"),
                              "stage0": result.get("stage0"),
                              "stage3_retry_waiting": result.get("stage3_retry_waiting")}))


if __name__ == "__main__":
    main()
