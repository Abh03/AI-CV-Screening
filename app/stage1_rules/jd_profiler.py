from typing import List, Optional, Annotated
from pydantic import BaseModel, Field, field_validator
from app.stage1_rules.contracts import DegreeRequirement, HardFilterRules, StrictModel

SkillTerm = Annotated[str, Field(min_length=1, max_length=120, pattern=r"\S")]


class SkillCluster(StrictModel):
    canonical: str = Field(min_length=1, max_length=120, pattern=r"\S",
        description="The primary name of the required skill/technology (e.g., 'Kubernetes')."
    )
    aliases: List[SkillTerm] = Field(
        default_factory=list, max_length=30,
        description="Exact acronyms, synonyms, or alternative spellings (e.g., ['k8s', 'kubectl'])."
    )
    substitutes: List[SkillTerm] = Field(
        default_factory=list, max_length=30,
        description="Acceptable domain substitutes scored at partial weight (e.g., ['docker swarm'])."
    )

    @field_validator("canonical")
    @classmethod
    def trim_name(cls, value):
        return value.strip()

    @field_validator("aliases", "substitutes")
    @classmethod
    def trim_terms(cls, values):
        return list(dict.fromkeys(value.strip() for value in values))


class JDProfile(HardFilterRules):
    job_id: str = Field(description="Unique database identifier for the job posting.")
    job_title: str = Field(description="Target role title.")
    must_have_skills: List[SkillCluster] = Field(default_factory=list)
    nice_to_have_skills: List[SkillCluster] = Field(default_factory=list)
    min_match_threshold: float = Field(
        default=0.50, ge=0, le=1, allow_inf_nan=False, description="Minimum normalized match ratio required to pass Stage 1 (0.0 to 1.0)."
    )


def encapsulate_jd_data(jd_text: str) -> str:
    """Sanitizes structural delimiter collisions and encapsulates raw JD text within XML boundaries."""
    if not jd_text:
        return "<job_description>\n</job_description>"

    safe_content = (
        jd_text.replace("</job_description>", "&lt;/job_description&gt;")
               .replace("<job_description>", "&lt;job_description&gt;")
    )
    return f"<job_description>\n{safe_content.strip()}\n</job_description>"
