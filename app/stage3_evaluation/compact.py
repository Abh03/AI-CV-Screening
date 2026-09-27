"""Lossless provider serialization; trusted IDs/provenance remain in the registry."""
import re
from dataclasses import dataclass
from types import MappingProxyType
from app.stage3_evaluation.evidence import CATEGORIES, stable_identity
from app.stage3_evaluation.schemas import SentenceSelectionOutput

PREFIXES = dict(zip(CATEGORIES, 'SEPD'))


class SelectionScopeMismatch(ValueError):
    pass


@dataclass(frozen=True)
class EvidenceManifest:
    scope: str
    rows: tuple
    handles: object
    deduplicated_ids: tuple


def evidence_manifest(registry, candidate_id=None):
    owners = {ref.candidate_id for ref in registry.values()}
    if candidate_id is not None and owners and owners != {candidate_id}:
        raise ValueError('Cross-candidate registry')
    if candidate_id is not None:
        owners.add(candidate_id)
    if len(owners) > 1:
        raise ValueError('Cross-candidate registry')
    # Each row is one physical sentence, with separate category handles.
    groups = {}
    for ref in registry.values():
        key = (ref.document_id, ref.chunk_id, ref.sentence_start, ref.sentence_end, ref.text)
        groups.setdefault(key, {})[ref.category] = ref
    context = [(key, refs) for key, refs in groups.items()
               if str(key[0] or '').startswith('context:')]
    normalize = lambda text: ' '.join(re.sub(r'^\s*\[Section: [^]]+\]\s*', '', text).split())
    kept, deduplicated = [], []
    for key, refs in groups.items():
        if not str(key[0] or '').startswith('context:'):
            # Drop only a retrieval copy exactly contained in a retained full-CV
            # sentence of the same category and provenance category. Never drop
            # CV occurrences: identical text in two roles must remain separate.
            if all(any(category in other and
                       ref.source_category == other[category].source_category and
                       normalize(ref.text) and re.search(r"(?<!\w)" + re.escape(normalize(ref.text)) + r"(?!\w)",
                                 normalize(other[category].text))
                       for _, other in context) for category, ref in refs.items()):
                deduplicated.extend(ref.evidence_id for ref in refs.values())
                continue
        kept.append((key, refs))
    # Source order retains role/date/responsibility context, ahead of retrieval.
    kept.sort(key=lambda item: (
        not str(item[0][0] or '').startswith('context:'),
        next(iter(item[1].values())).source_location.char_start or 0,
        item[0][2] or 0))
    rows, handles = [], {}
    for index, (_, refs) in enumerate(kept, 1):
        local = {}
        for category in CATEGORIES:
            if category in refs:
                handle = PREFIXES[category] + str(index)
                handles[handle] = refs[category]
                local[category] = handle
        rows.append((next(iter(refs.values())), MappingProxyType(local)))
    # Scope binds owner, exact source identities, handles and supplied registry.
    # Ordinals are never accepted without this request-specific ownership proof.
    scope = stable_identity('selection-handles-v1', sorted(owners),
        [(handle, ref.evidence_id) for handle, ref in handles.items()],
        sorted(ref.evidence_id for ref in registry.values()))[:32]
    return EvidenceManifest(scope, tuple(rows), MappingProxyType(handles), tuple(deduplicated))


def resolve_compact_selection(selection, registry, candidate_id):
    manifest = evidence_manifest(registry, candidate_id)
    if selection.evidence_scope != manifest.scope:
        raise SelectionScopeMismatch('Evidence selection belongs to another request')
    if any(ref.candidate_id != candidate_id for ref in registry.values()):
        raise ValueError('Cross-candidate registry')
    data = selection.model_dump()
    data.pop('evidence_scope')
    for assessment in [*(data[category.lower()] for category in CATEGORIES), *data['flags']]:
        assessment['citations'] = [manifest.handles[handle].evidence_id if handle in manifest.handles
                                   else 'UNKNOWN_HANDLE:' + handle for handle in assessment['citations']]
    from app.stage3_evaluation.evidence import resolve_sentence_selection
    return resolve_sentence_selection(SentenceSelectionOutput.model_validate(data), registry, candidate_id)
