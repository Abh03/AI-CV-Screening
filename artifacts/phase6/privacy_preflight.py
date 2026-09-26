"""Audit every source PDF through production ingestion before provider evaluation."""
import json, re, zipfile, time
from pathlib import Path
from collections import Counter
from app.stage0_extraction.pipeline import ingest_pdf

started=time.monotonic()
counts=Counter()
examples=[]
try:
    metadata={row['candidate_id']+'.pdf':row for row in json.loads(Path('/tmp/capacity-metadata.json').read_text())}
    with zipfile.ZipFile('/tmp/capacity-archive.zip') as archive:
        for index,name in enumerate(archive.namelist()):
            if not name.lower().endswith('.pdf'):
                continue
            result=ingest_pdf(archive.read(name))
            counts['documents']+=1
            counts['status_'+result.status]+=1
            if result.status=='success':
                stored=result.redacted_text+json.dumps(result.pages)
                known=metadata[name]
                leaks=[]
                for field in ('name','email','location'):
                    if known[field].casefold() in stored.casefold():
                        counts[field+'_leaks']+=1
                        leaks.append(field)
                core=re.split(r'(?:x|ext\.?\s*)',known['phone'],flags=re.I)[0]
                digits=re.sub(r'\D','',core)
                if len(digits)>=8 and digits in re.sub(r'\D','',result.redacted_text):
                    counts['phone_leaks']+=1
                    leaks.append('phone')
                if leaks and len(examples)<10:
                    examples.append({'file':name,'fields':leaks})
                counts['ocr_pages']+=sum(page['ocr_used'] for page in result.pages)
            else:
                counts['code_'+result.code]+=1
            if counts['documents']%100==0:
                print(json.dumps({'step':'privacy_preflight','processed':counts['documents'],'counts':dict(counts)}),flush=True)
    print(json.dumps({'input':{'pdfs':1000,'redaction_version':'pii-mask-v4'},'output':dict(counts),'first_failure_files_without_pii':examples,'runtime_seconds':round(time.monotonic()-started,3)}),flush=True)
    assert not any(counts[field+'_leaks'] for field in ('name','email','location','phone'))
finally:
    Path('/tmp/capacity-archive.zip').unlink(missing_ok=True)
    Path('/tmp/capacity-metadata.json').unlink(missing_ok=True)
