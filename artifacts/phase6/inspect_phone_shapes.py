import asyncio, json, re
from pathlib import Path
from dotenv import dotenv_values
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

async def main():
    metadata = {x['candidate_id']+'.pdf': x for x in json.loads(Path(r'C:\Abhyudit_Files\Infinite\CV-Benchmark-Dataset-Generator\output\candidates.json').read_text())}
    engine = create_async_engine(make_url(dotenv_values('docker/.env')['DATABASE_URL']).set(host='127.0.0.1', port=5432))
    async with engine.connect() as db:
        rows = (await db.execute(text("SELECT source_filename,redacted_text FROM campaign_cvs WHERE campaign_id='ec8bbc11-e38c-4d06-99ec-adeecf426c33' AND stage0_status='SUCCEEDED'"))).mappings().all()
    samples = []
    for row in rows:
        phone = metadata[row['source_filename']]['phone']
        core = re.split(r'(?:x|ext\.?\s*)', phone, flags=re.I)[0]
        digits = re.sub(r'\D','',core)
        stored = row['redacted_text'] or ''
        if len(digits)>=8 and digits in re.sub(r'\D','',stored):
            samples.append({'file':row['source_filename'],'phone_shape':re.sub(r'\d','#',phone),'literal_core_retained':core in stored})
    print(json.dumps({'processed':len(rows),'matches':len(samples),'first_examples':samples[:8]},indent=2))
    await engine.dispose()
asyncio.run(main())
