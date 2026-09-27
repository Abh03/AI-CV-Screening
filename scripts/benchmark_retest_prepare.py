"""Prepare isolated benchmark JD drafts and a deterministic representative pilot."""
import asyncio
import hashlib
import json
import zipfile
from pathlib import Path
from datetime import datetime, timezone

import fitz
import httpx
from dotenv import dotenv_values

ROOT = Path('artifacts/retest-20260927')
SOURCE = Path('C:/Abhyudit_Files/Infinite/CV-Benchmark-Dataset-Generator/output')


async def main():
    ROOT.mkdir(parents=True, exist_ok=True)
    truth = json.loads((SOURCE / 'ground_truth.json').read_text())
    selected = {'CV0526', 'CV0888'}
    for jd in ('JD01', 'JD02', 'JD03', 'JD04'):
        rows = [r for r in truth if r['jd_id'] == jd]
        for band in sorted({r['overall_relevance_band'] for r in rows}):
            selected.update(r['candidate_id'] for r in sorted(
                [r for r in rows if r['overall_relevance_band'] == band],
                key=lambda r: r['candidate_id'])[:3])
    with zipfile.ZipFile(SOURCE / 'cvs/CV_1000.zip') as src:
        names = [n for n in src.namelist() if n.lower().endswith('.pdf')]
        with zipfile.ZipFile(ROOT / 'pilot.zip', 'w', zipfile.ZIP_DEFLATED) as dst:
            for name in names:
                if Path(name).stem in selected:
                    dst.writestr(Path(name).name, src.read(name))
    manifest = {'started_at': datetime.now(timezone.utc).isoformat(),
                'pilot_candidates': sorted(selected), 'archive_pdf_count': len(names),
                'ground_truth_sha256': hashlib.sha256((SOURCE / 'ground_truth.json').read_bytes()).hexdigest()}
    (ROOT / 'manifest.json').write_text(json.dumps(manifest, indent=2))
    token = json.loads(dotenv_values('docker/.env')['API_TOKENS_JSON'])[0]['token']
    async with httpx.AsyncClient(base_url='http://127.0.0.1:8000',
                                headers={'Authorization': 'Bearer ' + token}, timeout=300) as client:
        (await client.get('/ready')).raise_for_status()
        old = await client.get('/api/v1/jds/approved')
        old.raise_for_status()
        (ROOT / 'baseline-approved-jds.json').write_text(json.dumps(old.json(), indent=2))
        for path in sorted((SOURCE / 'jds').glob('*.pdf')):
            saved_path = ROOT / (path.stem + '-draft.json')
            if saved_path.exists() and json.loads(saved_path.read_text())['status'] == 'REVIEW':
                continue
            # New metadata forces fresh JD extraction without changing source text.
            with fitz.open(path) as doc:
                doc.set_metadata({**doc.metadata, 'subject': 'benchmark retest 20260927'})
                data = doc.tobytes()
                (ROOT / (path.stem + '-source.txt')).write_text(''.join(p.get_text() for p in doc))
            response = await client.post('/api/v1/jds/extract?retry=true', content=data,
                                         headers={'Content-Type': 'application/pdf'})
            response.raise_for_status()
            draft = response.json()
            (ROOT / (path.stem + '-draft.json')).write_text(json.dumps(draft, indent=2))
            print(json.dumps({'jd': path.stem, 'status': draft['status'], 'error': draft.get('error_code')}), flush=True)
            await asyncio.sleep(55)


if __name__ == '__main__':
    asyncio.run(main())
