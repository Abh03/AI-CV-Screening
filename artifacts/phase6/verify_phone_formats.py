import json
import re
from pathlib import Path
from tests.test_phone_extensions import phone_pattern

source = Path(r'C:\Abhyudit_Files\Infinite\CV-Benchmark-Dataset-Generator\output\candidates.json')
rows = json.loads(source.read_text())
pattern = phone_pattern()
failures = [{'file': row['candidate_id']+'.pdf', 'shape': re.sub(r'\d', '#', row['phone'])}
            for row in rows if re.search(r'\d', pattern.sub('[REDACTED_PHONE]', row['phone']))]
result = {'input': str(source), 'output': {'phone_formats_checked': len(rows),
          'numbers_with_unmasked_digits':len(failures),'first_failures':failures[:5]}}
Path('artifacts/phase6/phone-format-check.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result,indent=2))
assert not failures
