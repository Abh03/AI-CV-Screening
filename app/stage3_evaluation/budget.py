"""Request-local schemas and conservative provider token estimates."""
import copy
import json
import math
import re
from pathlib import Path
from functools import lru_cache
from xml.etree import ElementTree as ET
from app.stage3_evaluation.schemas import SentenceSelectionOutput, CompactSentenceSelectionOutput


def selection_schema(user_prompt=None):
    root = ET.fromstring(user_prompt) if user_prompt is not None else None
    compact = root is not None and root.attrib.get('transport') == 'handles-v1'
    schema = (CompactSentenceSelectionOutput if compact else SentenceSelectionOutput).model_json_schema()
    if root is not None:
        ids = {}
        for category, prefix in zip(('skills', 'experience', 'projects', 'education'), 'SEPD'):
            ids[category] = ([handle for node in root.findall('.//evidence/p/e')
                              for handle in node.attrib['ids'].split() if handle.startswith(prefix)]
                             if compact else [n.attrib['evidence_id'] for n in root.findall(
                                 f'.//category[@name="{category.upper()}"]/snippet') if 'evidence_id' in n.attrib])
            node = copy.deepcopy(schema['$defs']['SentenceSelectionAssessment'])
            constrain(node['properties']['citations'], ids[category])
            schema['properties'][category] = node
        constrain(schema['$defs']['SentenceSelectionFlag']['properties']['citations'],
                  [value for values in ids.values() for value in values])
        if compact:
            schema['properties']['evidence_scope']['enum'] = [root.attrib['evidence_scope']]
        # Inlined assessments no longer need their unused definition.
        schema['$defs'].pop('SentenceSelectionAssessment', None)
    def compact_schema(node):
        if isinstance(node, dict):
            node.pop('title', None)
            for value in node.values():
                compact_schema(value)
        elif isinstance(node, list):
            for value in node:
                compact_schema(value)
    compact_schema(schema)
    return schema


def constrain(node, ids):
    if ids:
        node['items'] = {'type': 'string', 'enum': ids}
    else:
        node['maxItems'] = 0



TOKENIZER_SHA256 = '0614fe83cadab421296e664e1f48f4261fa8fef6e03e63bb75c20f38e37d07d3'


@lru_cache(maxsize=4)
def load_groq_tokenizer(path):
    import hashlib
    from tokenizers import Tokenizer
    try:
        data = Path(path).read_bytes()
    except OSError as error:
        raise ValueError("Configured Groq tokenizer is unavailable") from error
    if hashlib.sha256(data).hexdigest() != TOKENIZER_SHA256:
        raise ValueError('Unexpected Groq tokenizer content')
    return Tokenizer.from_str(data.decode('utf-8'))


def groq_tokenizer():
    from app.config import settings
    # Never download during an evaluation. Docker provisions this at build time.
    if settings.GROQ_TOKENIZER_PATH:
        return load_groq_tokenizer(settings.GROQ_TOKENIZER_PATH)
    local = Path('models/gpt-oss/tokenizer.json')
    return load_groq_tokenizer(str(local.resolve())) if local.is_file() else None


def request_budget(prompt, provider):
    from app.config import settings
    from app.stage3_evaluation.prompts import SYSTEM_PROMPT_STAGE3
    schema = json.dumps(selection_schema(prompt), separators=(',', ':'))
    texts = [('system', SYSTEM_PROMPT_STAGE3), ('user', prompt), ('schema', schema)]
    tokenizer = groq_tokenizer() if provider == 'groq' and settings.GROQ_MODEL.startswith('openai/gpt-oss-') else None
    if tokenizer is not None:
        components = {name: len(tokenizer.encode(value, add_special_tokens=False).ids)
                      for name, value in texts}
        method = 'gpt-oss-tokenizer-plus-framing-estimate'
    else:
        components = {name: math.ceil(len(value.encode('utf-8')) / 3) for name, value in texts}
        components['evidence_ids'] = sum(math.ceil(len(value) / 2) for value in
            re.findall(r'ev_[0-9a-f]+', prompt + schema))
        method = 'utf8-bytes/3-conservative'
    completion = (settings.GROQ_MAX_COMPLETION_TOKENS if provider == 'groq' else
                  settings.OPENROUTER_MAX_TOKENS if provider == 'openrouter' else 8192)
    context_limit = getattr(settings, f'{provider.upper()}_CONTEXT_TOKENS', 32768)
    input_tokens = sum(components.values()) + 128
    # Groq's observed admission checks reject oversized input requests. A
    # completion ceiling is model context allowance, not prepaid minute usage.
    # The shared minute limiter reserves the full bucket and prevents overlap.
    input_limit = min(context_limit, settings.PROVIDER_TOKENS_PER_MINUTE) if provider == 'groq' else context_limit
    return dict(components, input=input_tokens, completion=completion, framing=128,
                total=input_tokens + completion, limit=context_limit, input_limit=input_limit,
                fits=input_tokens <= input_limit and input_tokens + completion <= context_limit,
                minute_reservation=min(input_tokens + completion, settings.PROVIDER_TOKENS_PER_MINUTE),
                method=method)
