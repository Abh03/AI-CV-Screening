"""Versioned lexical representation and concept plans shared by both retrievers."""
from dataclasses import dataclass
import re
import unicodedata

from app.stage1_rules.jd_matcher import IDENTITY_ALIASES, identity_terms, term_pattern

LEXICAL_VERSION = "concept-v1"
# Freeze these mappings for persisted concept-v1 indexes and migration backfills.
TECHNICAL_IDENTITIES_V1 = (
    ("techjavascript", ("JavaScript", "Java Script", "JS")),
    ("techtypescript", ("TypeScript", "Type Script", "TS")),
    ("techkubernetes", ("Kubernetes", "K8s")),
    ("techpostgresql", ("PostgreSQL", "Postgres")),
    ("technodejs", ("Node.js", "NodeJS", "Node JS")),
    ("techreactjs", ("React.js", "ReactJS", "React")),
    ("techcsharp", ("C#", "C Sharp", "CSharp")),
    ("techcplusplus", ("C++", "CPP", "C Plus Plus")),
    ("techdotnet", (".NET", "dotnet", "dot net")),
    ("techaws", ("Amazon Web Services", "AWS")),
    ("techgcp", ("Google Cloud Platform", "GCP")),
)
STOP_WORDS = set("a an and or the of in on to for with by as at from is are be have has required preferred requirement requirements experience experienced demonstrating hands hands-on projects project skills skill knowledge strong good excellent ability working work developer development years year explicit no not minimum must should candidate candidates including using proficiency proficient familiarity familiar relevant degree education section".split())


def normalize_lexical_v1(text):
    text = unicodedata.normalize("NFKC", text).casefold()
    # Longest forms first; match symbols before punctuation is stripped.
    variants = sorted(((alias, token) for token, group in TECHNICAL_IDENTITIES_V1
                       for alias in group), key=lambda item: len(item[0]), reverse=True)
    for alias, token in variants:
        leading = "." if alias.startswith(".") else ""
        parts = re.split(r"[\s._-]+", alias[len(leading):])
        pattern = (r"(?<!\w)" + re.escape(leading)
                   + r"[\s._-]*".join(re.escape(part) for part in parts) + r"(?!\w)")
        text = re.sub(pattern, lambda _: token, text, flags=re.I)
    return " ".join(re.findall(r"[^\W_]+", text, re.UNICODE))


@dataclass(frozen=True)
class SparseConcept:
    name: str
    alternatives: tuple[str, ...]
    priority: str = "context"
    substitutes: tuple[str, ...] = ()

    @property
    def tsquery(self):
        # Only normalized alphanumeric lexemes enter the syntax; values remain bound.
        phrases = [" <-> ".join(value.split()) for value in
                   (*self.alternatives, *self.substitutes) if value]
        return "(" + " | ".join(dict.fromkeys(phrases)) + ")"

    def matches(self, normalized_text):
        return any(" " + value + " " in " " + normalized_text + " "
                   for value in (*self.alternatives, *self.substitutes) if value)


@dataclass(frozen=True)
class SparsePlan:
    concepts: tuple[SparseConcept, ...]
    version: str = LEXICAL_VERSION

    @property
    def tsquery(self):
        return " | ".join(concept.tsquery for concept in self.concepts)

    def coverage(self, text):
        normalized = normalize_lexical_v1(text)
        return tuple(sum(c.matches(normalized) for c in self.concepts if c.priority == priority)
                     for priority in ("required", "preferred", "context"))


def retrieval_diagnostics(plan, hits, ranked):
    """Counts before/after reranking; full branch union is retained before reranking."""
    return {
        "lexical_version": plan.version,
        "concept_count": len(plan.concepts),
        "dense_hits": sum(hit.get("dense_rank") is not None for hit in hits),
        "sparse_hits": sum(hit.get("sparse_rank") is not None for hit in hits),
        "fused_hits": len(hits),
        "returned_hits": len(ranked),
        "returned_sparse_hits": sum(hit.get("sparse_rank") is not None for hit in ranked),
    }


def build_sparse_plan(query, category="SKILLS", required_skills=None,
                      preferred_skills=None, degree_requirement=None):
    concepts = []
    seen = set()

    def add(name, aliases=(), substitutes=(), priority="context"):
        terms = tuple(dict.fromkeys(normalize_lexical_v1(term)
                      for term in identity_terms([name, *aliases]) if term.strip()))
        terms = tuple(term for term in terms if term)
        if not terms or terms in seen:
            return
        seen.add(terms)
        concepts.append(SparseConcept(name, terms, priority,
            tuple(dict.fromkeys(normalize_lexical_v1(term)
                for term in identity_terms(list(substitutes)) if term.strip()))))

    def data(value):
        return value if isinstance(value, dict) else value.model_dump()

    if category != "EDUCATION":
        for clusters, priority in ((required_skills, "required"), (preferred_skills, "preferred")):
            for cluster in clusters or []:
                cluster = data(cluster)
                add(cluster["canonical"], cluster.get("aliases", ()),
                    cluster.get("substitutes", ()), priority)
    elif degree_requirement:
        degree = data(degree_requirement)
        if degree.get("level", "NONE") != "NONE":
            add(degree["level"], degree.get("level_aliases", ()), priority="required")
            fields = degree.get("fields", ())
            if fields:
                add(fields[0], [*fields[1:], *degree.get("field_aliases", ())], priority="required")

    if "no explicit requirement" not in query.casefold():
        # Recognized identities and common compound concepts remain phrases.
        phrases = [group[0] for group in IDENTITY_ALIASES
                   if any(re.search(term_pattern(alias), query, re.I) for alias in group)]
        phrases += re.findall(r"\b(?:spring boot|computer science|software engineering|machine learning|data engineering|quality assurance|test automation|rest apis?)\b", query, re.I)
        phrases += re.findall(r'"([^"\n]{1,120})"', query)
        if "," in query or ";" in query:
            # Legacy list queries often contain compound product names unknown
            # to the identity registry. Keep each short list item together.
            for item in re.split(r"[,;]|\b(?:and|or)\b", query, flags=re.I):
                words = normalize_lexical_v1(item).split()
                while words and (words[0] in STOP_WORDS or words[0].isdecimal()):
                    words.pop(0)
                while words and (words[-1] in STOP_WORDS or words[-1].isdecimal()):
                    words.pop()
                if 1 <= len(words) <= 6:
                    phrases.append(" ".join(words))
        for phrase in phrases:
            add(phrase)
        covered = {word for concept in concepts
                   for term in (*concept.alternatives, *concept.substitutes) for word in term.split()}
        for word in normalize_lexical_v1(query).split():
            if word not in covered and word not in STOP_WORDS and not word.isdecimal():
                add(word)
                covered.add(word)
            if len(concepts) >= 64:
                break
    return SparsePlan(tuple(concepts[:128]))
