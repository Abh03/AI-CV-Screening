import json, subprocess
from dotenv import dotenv_values
root=dotenv_values('.env'); docker=dotenv_values('docker/.env')
for name in ['docker-web-1','docker-evaluation-worker-1']:
    info=json.loads(subprocess.run(['docker','inspect',name],capture_output=True,text=True,check=True).stdout)[0]
    env=dict(value.split('=',1) for value in info['Config']['Env'] if '=' in value)
    print(json.dumps({'container':name,'key_present':bool(env.get('GROQ_API_KEY')),'matches_docker_env':env.get('GROQ_API_KEY')==docker.get('GROQ_API_KEY'),'matches_root_env':env.get('GROQ_API_KEY')==root.get('GROQ_API_KEY'),'limits':{k:env.get(k) for k in ['GROQ_MODEL','PROVIDER_REQUESTS_PER_MINUTE','PROVIDER_TOKENS_PER_MINUTE','GROQ_MAX_COMPLETION_TOKENS']}}))
