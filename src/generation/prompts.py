"""
src/generation/prompts.py

Prompt templates for each experimental condition. C1 only, for now.
"""

C1_TEMPLATE = """You are analysing a UK company's annual report and accounts. \
Answer the question below using ONLY the filing text provided. Do not use \
any outside knowledge about this company.

If the filing text does not contain enough information to answer \
confidently, say "Not found in the provided text" rather than guessing.

Give a direct, specific answer. If the answer is a number, state the exact \
figure and its unit (e.g. "£32,812 million" or "$58,739 million"). Do not \
round or approximate unless the filing itself only gives a rounded figure.

=== FILING TEXT ({company_name}) ===
{context}
=== END FILING TEXT ===

Question: {question}

Answer:"""


def build_c1_prompt(company_name: str, context: str, question: str) -> str:
    return C1_TEMPLATE.format(
        company_name=company_name, context=context, question=question
    )


C3_TEMPLATE = """You are analysing a UK company's annual report and accounts. \
You have been given structured, tagged data retrieved from the filing's \
XBRL facts. Answer the question using ONLY this retrieved evidence.

If the retrieved evidence does not answer the question, say \
"Not found in the retrieved evidence" rather than guessing.

If more than one fact is shown and they represent different dimensional breakdowns, \
choose the one that matches what the question specifically asks for. \
If the question does not specify a dimension, prefer the undimensioned (consolidated total) fact if one is present. \
  If no undimensioned total exists but one dimensional member clearly represents the overall/group/parent figure \
 (for example, labelled Total, Group, or Consolidated, or the item covering the whole reporting entity rather than \
a subsidiary or component), use that one and say so. Only say "not found" if genuinely no reasonable total exists \
among the retrieved facts.

Give a direct, specific answer with the exact figure and unit.

=== RETRIEVED EVIDENCE ({company_name}) ===
{evidence}
=== END RETRIEVED EVIDENCE ===

Question: {question}

Answer:"""


def build_c3_prompt(company_name: str, evidence: str, question: str) -> str:
    return C3_TEMPLATE.format(
        company_name=company_name, evidence=evidence, question=question
    )