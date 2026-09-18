from typing import List, Optional
from pydantic import BaseModel, Field


class SkillCluster(BaseModel):
    canonical: str = Field(
        description="The primary name of the required skill/technology (e.g., 'Kubernetes')."
    )
    aliases: List[str] = Field(
        default_factory=list,
        description="Exact acronyms, synonyms, or alternative spellings (e.g., ['k8s', 'kubectl'])."
    )
    substitutes: List[str] = Field(
        default_factory=list,
        description="Acceptable domain substitutes scored at partial weight (e.g., ['docker swarm'])."
    )


class JDProfile(BaseModel):
    job_id: str = Field(description="Unique database identifier for the job posting.")
    job_title: str = Field(description="Target role title.")
    must_have_skills: List[SkillCluster] = Field(default_factory=list)
    nice_to_have_skills: List[SkillCluster] = Field(default_factory=list)
    min_years_experience: int = Field(default=0)
    required_degree_level: Optional[str] = Field(default=None)
    min_match_threshold: float = Field(
        default=0.50, description="Minimum normalized match ratio required to pass Stage 1 (0.0 to 1.0)."
    )


def encapsulate_jd_data(jd_text: str) -> str:
    """
    Sanitizes structural delimiter collisions and encapsulates raw JD text within XML boundaries.
    """
    if not jd_text:
        return "<job_description>\n</job_description>"

    safe_content = (
        jd_text.replace("</job_description>", "&lt;/job_description&gt;")
               .replace("<job_description>", "&lt;job_description&gt;")
    )
    return f"<job_description>\n{safe_content.strip()}\n</job_description>"