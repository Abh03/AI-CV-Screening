"""Read-only campaign export; execute inside the web container via stdin."""
import asyncio
import json
import os
from sqlalchemy import text
from app.models.database import engine


async def main():
    campaign = os.environ.get('BENCHMARK_CAMPAIGN')
    async with engine.connect() as db:
        if not campaign:
            rows = (await db.execute(text('SELECT * FROM campaigns ORDER BY created_at'))).mappings().all()
            print(json.dumps({'campaigns': [dict(r) for r in rows]}, default=str))
        else:
            result = {}
            for table in ('campaigns', 'campaign_jds', 'campaign_cvs', 'campaign_pairs'):
                column = 'id' if table == 'campaigns' else 'campaign_id'
                fields = '*' if table != 'campaign_cvs' else (
                    'id,campaign_id,candidate_id,source_filename,content_hash,stage0_status,'
                    'redacted_text,source_locations,extraction_error_code,created_at,updated_at')
                rows = (await db.execute(text(f'SELECT {fields} FROM {table} WHERE {column}=:id'),
                                         {'id': campaign})).mappings().all()
                result[table] = [dict(r) for r in rows]
            print(json.dumps(result, default=str))
    await engine.dispose()

asyncio.run(main())
