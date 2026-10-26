"""Prompt text for local payroll-note extraction.

The model may only copy values that are present in the note. It must not
calculate pay, TDS, advance recovery, approval, or invoices.
"""

EXTRACTION_PROMPT_TEMPLATE = """You read monthly payroll notes written in English and return JSON.

Known members: {names}

Rules:
- Use only names from the known members list. Ignore capitalisation. Never invent members.
- "leave", "leaves" or "day off" means total_leaves. "half day" means 0.5.
- "extra day" or "extra days" means extra_work_days.
- "convert N to comp-off" or "N comp-off" means comp_off = N.
- "N hours" means hours_worked.
- "N overtime" or "N overtime hours" means overtime_hours.
- "bonus N" means one_off_bonus.
- "advance N" or "advance of N" means advance_amount, in rupees.
- "5k" means 5000. "1.5k" means 1500.
- If a value is not mentioned, use 0. Never guess. Copy numbers exactly as written.
- evidence is the short phrase from the note that this member's values came from.
- Return one object per member mentioned, and nothing else.

Examples (known members: Neha, Arjun, Priya, Karan):

Note: Neha: 6 leaves, 4 extra days, convert 3 to comp-off.
{{"members":[{{"member_name":"Neha","evidence":"6 leaves, 4 extra days, convert 3 to comp-off","total_leaves":6,"extra_work_days":4,"comp_off":3,"hours_worked":0,"overtime_hours":0,"one_off_bonus":0,"advance_amount":0}}]}}

Note: Arjun: half day leave, 2.5 leaves in total.
{{"members":[{{"member_name":"Arjun","evidence":"2.5 leaves in total","total_leaves":2.5,"extra_work_days":0,"comp_off":0,"hours_worked":0,"overtime_hours":0,"one_off_bonus":0,"advance_amount":0}}]}}

Note: Priya: 175 hours, 12 overtime, bonus 7500.
{{"members":[{{"member_name":"Priya","evidence":"175 hours, 12 overtime, bonus 7500","total_leaves":0,"extra_work_days":0,"comp_off":0,"hours_worked":175,"overtime_hours":12,"one_off_bonus":7500,"advance_amount":0}}]}}

Note: Karan: advance of 8000.
{{"members":[{{"member_name":"Karan","evidence":"advance of 8000","total_leaves":0,"extra_work_days":0,"comp_off":0,"hours_worked":0,"overtime_hours":0,"one_off_bonus":0,"advance_amount":8000}}]}}

Now read this note.

Note: {note}
"""


def build_extraction_prompt(note: str, known_names=None) -> str:
    names = ", ".join(name for name in (known_names or []) if name)
    return EXTRACTION_PROMPT_TEMPLATE.format(names=names, note=note)
