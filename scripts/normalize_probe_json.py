"""Strip startup notices from completed probe artifacts and preserve JSON values."""
import argparse,json
from pathlib import Path

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('paths',nargs='+',type=Path)
args=parser.parse_args()
for path in args.paths:
    data=path.read_bytes()
    source=data.decode('utf-16' if data.startswith((b'\xff\xfe',b'\xfe\xff')) else 'utf-8-sig')
    value,offset=json.JSONDecoder().raw_decode(source[source.index('{'):])
    path.write_text(json.dumps(value,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'input':str(path),'output':'valid UTF-8 JSON; payload preserved'}))
