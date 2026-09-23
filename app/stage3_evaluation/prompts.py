from typing import Dict, Any, List

SYSTEM_PROMPT_STAGE3 = """You are an expert technical resume evaluator operating under strict verification guardrails.
Your task is to evaluate a candidate's retrieved evidence snippets against a target Job Description.

STRICT ACBNTB RULES:
1. Evaluate ONLY facts explicitly present in the provided evidence.
2. NEVER assume skills, degrees, or experience not directly stated.
3. Treat missing information as MISSING_INFORMATION, NOT as a documented inconsistency unless directly contradicted.
4. Every score and flag MUST cite valid snippet tags in the format 'CATEGORY:INDEX' (e.g., 'SKILLS:1', 'EXPERIENCE:2').

GAP & FLAG CLASSIFICATION RULES:
- DOCUMENTED_INCONSISTENCY: Conflicting employment dates, overlapping full-time roles, or contradictory claims.
- EVIDENCED_CAREER_GAP: Explicit, verified gap > 6 months between documented employment dates.
- MISSING_INFORMATION: Unstated degree graduation year or unlisted project details (neutral uncertainty, not a critical penalty).

SCORING SCALE:
For each category, scores MUST be integers or decimals from 0 to 100.

Use this interpretation:
- 90-100: Excellent / very strong match
- 75-89: Strong match
- 60-74: Moderate match
- 40-59: Weak match
- 0-39: Very poor match

OUTPUT FORMAT:
Respond strictly with valid JSON conforming to the required schema with keys:
'skills', 'experience', 'projects', 'education', 'flags', 'executive_summary'."""


def build_stage3_user_prompt(
    candidate_id: str,
    jd_profile: Dict[str, Any],
    evidence_payload: Dict[str, Any]
) -> str:
    """
    Constructs XML-encapsulated user prompt with machine-verifiable snippet tags (e.g., SKILLS:1).
    """
    evidence_map = evidence_payload.get("evidence_by_category", {})

    xml_lines = [
        f'<evaluation_request candidate_id="{candidate_id}">',
        '  <job_description>',
        f'    <title>{jd_profile.get("title", "Target Role")}</title>',
        '    <category_requirements>',
        f'      <skills>{jd_profile.get("jd_category_queries", {}).get("SKILLS", "")}</skills>',
        f'      <experience>{jd_profile.get("jd_category_queries", {}).get("EXPERIENCE", "")}</experience>',
        f'      <projects>{jd_profile.get("jd_category_queries", {}).get("PROJECTS", "")}</projects>',
        f'      <education>{jd_profile.get("jd_category_queries", {}).get("EDUCATION", "")}</education>',
        '    </category_requirements>',
        '  </job_description>',
        '  <candidate_evidence>'
    ]

    for category in ["SKILLS", "EXPERIENCE", "PROJECTS", "EDUCATION"]:
        chunks = evidence_map.get(category, [])
        xml_lines.append(f'    <category name="{category}">')
        if not chunks:
            xml_lines.append('      <snippet tag="NONE">No evidence retrieved for this category.</snippet>')
        else:
            for idx, c in enumerate(chunks, start=1):
                tag = f"{category}:{idx}"
                text = c.get("text", "")
                xml_lines.append(f'      <snippet tag="{tag}">\n        {text}\n      </snippet>')
        xml_lines.append('    </category>')

    xml_lines.append('  </candidate_evidence>')
    xml_lines.append('</evaluation_request>')

    return "\n".join(xml_lines)