"""Request-local schemas and conservative provider token estimates."""
import copy
import json
import math
import re
from xml.etree import ElementTree as ET
from app.stage3_evaluation.schemas import SentenceSelectionOutput


def selection_schema(user_prompt=None):
    schema = SentenceSelectionOutput.model_json_schema()
    if user_prompt is not None:
        root = ET.fromstring(user_prompt)
        ids = {}
        for category in ('skills', 'experience', 'projects', 'education'):
            ids[category] = [n.attrib['evidence_id'] for n in root.findall(
                f'.//category[@name="{category.upper()}"]/snippet') if 'evidence_id' in n.attrib]
            node = copy.deepcopy(schema['$defs']['SentenceSelectionAssessment'])
            constrain(node['properties']['citations'], ids[category])
            schema['properties'][category] = node
        constrain(schema['$defs']['SentenceSelectionFlag']['properties']['citations'],
                  [value for values in ids.values() for value in values])
    return schema


def constrain(node, ids):
    if ids:
        node['items'] = {'type': 'string', 'enum': ids}
    else:
        node['maxItems'] = 0


def request_budget(prompt, provider):
    from app.config import settings
    from app.stage3_evaluation.prompts import SYSTEM_PROMPT_STAGE3
    schema = json.dumps(selection_schema(prompt), separators=(',', ':'))
    # Conservative fallback: includes escaped Unicode, random IDs, schema and
    # chat framing. This is an estimate, not the provider's tokenizer count.
    components = {name: math.ceil(len(value.encode('utf-8')) / 3)
                  for name, value in [('system', SYSTEM_PROMPT_STAGE3), ('user', prompt), ('schema', schema)]}
    # Random hexadecimal IDs tokenize less densely than prose. Account for
    # each appearance in both XML and schema, in addition to base text cost.
    components["evidence_ids"] = sum(math.ceil(len(value) / 2) for value in
        re.findall(r"ev_[0-9a-f]+", prompt + schema))
    completion = (settings.GROQ_MAX_COMPLETION_TOKENS if provider == 'groq' else
                  settings.OPENROUTER_MAX_TOKENS if provider == 'openrouter' else 8192)
    limit = getattr(settings, f'{provider.upper()}_CONTEXT_TOKENS', 32768)
    if provider == 'groq':
        limit = min(limit, settings.PROVIDER_TOKENS_PER_MINUTE)
    return dict(components, completion=completion, framing=128,
                total=sum(components.values()) + completion + 128, limit=limit,
                method='utf8-bytes/3-conservative')
