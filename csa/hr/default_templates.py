"""The Malaysian default onboarding and offboarding templates (HR PR 4, spec sections 6 and 7).

Data only: every step is a reminder with a reference, never a filing or a calculation, and carries
no rate, ceiling, threshold or amount. Each step's `SourceNote` points at a row of the research in
`docs/hr/default-templates-sources.md` (checked 2026-11-18). Where that research could not confirm
a timing on an official page, the step's note says `UNCONFIRMED` instead of a timing.

The content is English only (`title_ms` and `description_ms` stay None) and is read by an HR
practitioner through `docs/hr/DEFAULT_TEMPLATES_REVIEW.md`, generated from this file by
`scripts/render_default_templates_review.py`. Nothing here is created for a company until that
review is signed and the create-defaults switch is turned on.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal

from csa.hr.process_models import (
    Anchor,
    ApprovalBy,
    AutoCheck,
    OwnerRole,
    ProcessKind,
    TemplateCreate,
    TemplateStep,
)

__all__ = [
    "CHECKED_ON",
    "DEFAULT_TEMPLATES",
    "DEFAULT_TEMPLATE_SOURCES",
    "PRACTICE",
    "UNCONFIRMED",
    "Confidence",
    "SourceNote",
]

Confidence = Literal["official_read", "official_excerpt", "secondary", "unclear", "practice"]

CHECKED_ON = date(2026, 11, 18)
UNCONFIRMED = "Timing not confirmed on an official page: reviewer to confirm."
PRACTICE = "Company practice, no statutory source"


@dataclass(frozen=True, slots=True)
class SourceNote:
    """Where a step comes from. `confidence` follows the research file: `official_read` (the
    official page or statute text was read), `official_excerpt` (only search excerpts of the
    official page were seen), `secondary`, `unclear`, or `practice` (a company-practice step
    with no statutory source; its body starts with `PRACTICE`)."""

    body: str
    url: str | None
    checked_on: date
    confidence: Confidence


# ---- Official pages cited (each appears in docs/hr/default-templates-sources.md) ---------------

_ACT_265 = (
    "https://jtksm.mohr.gov.my/sites/default/files/2023-11/Akta%20Kerja%201955%20(Akta%20265).pdf"
)
_REGS_1980 = (
    "https://jtksm.gov.my/sites/default/files/2023-03/"
    "8.%20EMPLOYMENT%20(TERMINATION%20&%20LAY%20OFF%20BENEFITS)%20REGULATIONS%201980.pdf"
)
_ACT_800 = (
    "https://perkeso.gov.my/images/akta/ACT%20800/"
    "Akta%20800_EMPLOYMENT%20INSURANCE%20SYSTEM%20ACT%202017.pdf"
)
_PDP = "https://www.pdp.gov.my/ppdpv1/en/principles-of-personal-data-protection/"
_KWSP_REGISTER = "https://www.kwsp.gov.my/en/employer/responsibilities/register-employee"
_KWSP_CONTRIBUTION = "https://www.kwsp.gov.my/en/employer/responsibilities/mandatory-contribution"
_PERKESO_REGISTRATION = (
    "https://www.perkeso.gov.my/en/?view=article&id=775%3Aregistration&catid=184"
)
_HASIL_EMPLOYER = "https://www.hasil.gov.my/en/majikan/tanggungjawab-majikan/"
_HASIL_CESSATION = "https://www.hasil.gov.my/en/majikan/pemberitahuan-pemberhentian-kerja/"
_HASIL_EDAFTAR = "https://www.hasil.gov.my/en/syarikat/perniagaan-digital/e-daftar/"

_CHECKED = f"Checked {CHECKED_ON.isoformat()}."
_PRACTICE_NOTE = f"{PRACTICE}. {_CHECKED}"
_PRACTICE_SOURCE = SourceNote(f"{PRACTICE}.", None, CHECKED_ON, "practice")


def _read(body: str, url: str) -> SourceNote:
    return SourceNote(body, url, CHECKED_ON, "official_read")


def _excerpt(body: str, url: str) -> SourceNote:
    return SourceNote(body, url, CHECKED_ON, "official_excerpt")


def _practice(body: str, url: str) -> SourceNote:
    """A company-practice step whose negative finding (no statutory rule) comes from a text."""
    return SourceNote(f"{PRACTICE}: {body}", url, CHECKED_ON, "practice")


@dataclass(frozen=True, slots=True)
class _Step:
    title: str
    owner: OwnerRole
    anchor: Anchor
    offset: int
    note: str
    source: SourceNote
    description: str
    required: bool = True
    approval_by: ApprovalBy | None = None
    auto_check: AutoCheck | None = None


# ---- Onboarding (offsets in days from the start date unless stated) ---------------------------

_ONBOARDING = (
    _Step(
        "Employment contract and written terms given, signed copy on file",
        "hr",
        "start_date",
        0,
        "Employment Regulations 1957 reg 8(1): each employee gets a certified copy of the terms "
        "'on or before the date of his commencing employment'. Employment Act 1955 s.10(1): a "
        "contract for a specified period exceeding one month, or for specified piece work that "
        "may take over one month, shall be in writing; s.10(2): every written contract has a "
        f"termination clause. JTKSM-hosted texts. {_CHECKED}",
        _read(
            "Employment Act 1955 (Act 265) s.10 and Employment Regulations 1957 reg 8(1), "
            "JTKSM-hosted texts (research rows d1, d2)",
            _ACT_265,
        ),
        "Give the new hire the written particulars of their terms (the particulars reg 5 lists, "
        "such as occupation, wages, hours, notice period, leave and wage period) and their offer "
        "or contract, and keep a signed copy. The particulars are due for every employee the "
        "Employment Act covers: reg 8(1) requires a certified copy on or before the date they "
        "start. A written contract is required by s.10(1) only for the contracts it names; for "
        "other contracts it is good practice. Any written contract needs a termination clause "
        "(s.10(2)). The step follows the Peninsular Malaysia and Labuan rule; Sabah and Sarawak "
        "have their own labour ordinances.",
    ),
    _Step(
        "Privacy notice given to the new hire",
        "hr",
        "start_date",
        0,
        "Personal Data Protection Act 2010, Notice and Choice Principle, as PDP summarises it: "
        "'prior information and purposes are communicated to the data subject'. No prescribed "
        f"form. {UNCONFIRMED} PDP principles page. {_CHECKED}",
        _read("PDP, principles of personal data protection page (research rows f, m5)", _PDP),
        "Give the new hire a written notice of the personal data the company collects about "
        "them and why, and keep their acknowledgement. The Act sets no fixed retention period: "
        "personal data is kept only as long as necessary.",
    ),
    _Step(
        "Workplace equipment and accounts ready",
        "manager",
        "start_date",
        0,
        _PRACTICE_NOTE,
        _PRACTICE_SOURCE,
        "Have the new hire's equipment, workspace and work accounts ready on their first day.",
    ),
    _Step(
        "Personal details completed by the new hire",
        "employee",
        "start_date",
        7,
        _PRACTICE_NOTE,
        _PRACTICE_SOURCE,
        "The new hire fills in their home address, personal phone, emergency name, emergency "
        "relationship and emergency phone in My HR. The step checks that those five fields are "
        "set; it does not check bank or statutory numbers.",
        auto_check="personal_details_complete",
    ),
    _Step(
        "Bank and statutory numbers recorded",
        "hr",
        "start_date",
        7,
        _PRACTICE_NOTE,
        _PRACTICE_SOURCE,
        "Record the bank account for wages and the identity card or passport, income tax, EPF "
        "and SOCSO numbers in the person's personal details. Nothing checks this automatically: "
        "complete the step once they are recorded.",
    ),
    _Step(
        "Orientation and company policies acknowledged",
        "hr",
        "start_date",
        7,
        _PRACTICE_NOTE,
        _PRACTICE_SOURCE,
        "Walk the new hire through the company's policies and keep their signed acknowledgement.",
    ),
    _Step(
        "Manager's welcome and 30-day goals",
        "manager",
        "start_date",
        7,
        _PRACTICE_NOTE,
        _PRACTICE_SOURCE,
        "Meet the new hire, explain the role and agree goals for their first 30 days.",
    ),
    _Step(
        "EPF: new hire registered as a member or details updated",
        "hr",
        "start_date",
        14,
        "EPF (KWSP): register the employee as a member and keep their details current, through "
        "i-Akaun (Employer). KWSP pages could not be read directly (search excerpts only). "
        f"{UNCONFIRMED} {_CHECKED}",
        _excerpt(
            "KWSP employer pages, register an employee and non-Malaysian employees (search "
            "excerpts only, the pages returned HTTP 403; research rows a2, a3)",
            _KWSP_REGISTER,
        ),
        "Register the new hire as an EPF member if they are not one yet, or update their "
        "details. Excerpts of the KWSP pages list i-Akaun (Employer) 'Register EPF Member', "
        "automatic registration through the monthly Form A, counter registration with MyKad, or "
        "Form KWSP 3; they also say non-Malaysian employees with a valid passport and work pass "
        "(domestic workers excluded) are covered since the October 2025 wage month. The due "
        "date is a company reminder, not a statutory deadline.",
    ),
    _Step(
        "Income tax number checked (e-Daftar)",
        "hr",
        "start_date",
        14,
        "LHDN (HASiL): an employee subject to monthly tax deduction must have an income tax "
        f"file; registration through e-Daftar on MyTax. {UNCONFIRMED} HASiL e-Daftar page. "
        f"{_CHECKED}",
        _read("HASiL e-Daftar page (research row c2)", _HASIL_EDAFTAR),
        "Check that the new hire has an income tax reference number and record it. A new hire "
        "without one can register through e-Daftar on MyTax; the page says the number is issued "
        "within three working days after completing an online application. Optional: skip it "
        "when the number is already recorded.",
        required=False,
    ),
    _Step(
        "Foreign employee: approval on file and hiring reported to JTKSM",
        "hr",
        "start_date",
        14,
        "Employment Act 1955 s.60K(1): no employer shall employ a foreign employee without the "
        "Director General's prior approval; s.60K(3): furnish the particulars 'within fourteen "
        "days from the date of the employment of a foreign employee'. JTKSM ePPAx system. "
        f"{_CHECKED}",
        _read(
            "Employment Act 1955 s.60K and s.60O, JTKSM-hosted text; JTKSM foreign workers "
            "employment page for ePPAx (research row x1)",
            _ACT_265,
        ),
        "Only for a foreign employee; permanent residents are not foreign employees for this "
        "Part of the Act (s.60O). The prior approval comes before the person is employed, so "
        "check it is on file, then report the particulars; JTKSM says this reporting is done "
        "through the ePPAx system since December 2024. HR skips the step for everyone else.",
        required=False,
    ),
    _Step(
        "SOCSO: new hire registered with PERKESO",
        "hr",
        "start_date",
        30,
        "PERKESO: 'New employer and employee must be registered with PERKESO within 30 days "
        "upon hiring a new employee.' Form 2 (employee) through the ASSIST portal. PERKESO "
        f"registration page. {_CHECKED}",
        _read("PERKESO registration page (research rows b1, b2)", _PERKESO_REGISTRATION),
        "Register the new hire with PERKESO using Form 2 on the ASSIST portal (an employer "
        "registering for the first time also files Form 1). For a foreign worker, PERKESO's "
        "Employers' Circular 3/2018 describes registration through ASSIST or the Foreign Worker "
        "Registration Form and states no registration deadline.",
    ),
    _Step(
        "EIS: new hire registered for Employment Insurance",
        "hr",
        "start_date",
        30,
        "Employment Insurance System Act 2017 (Act 800) s.16(1)(b): register employees 'within "
        "thirty days from the date the employees enter into employment'. PERKESO Form SIP 2 "
        f"through the ASSIST portal. {_CHECKED}",
        _read(
            "Employment Insurance System Act 2017 (Act 800), PERKESO-hosted text as at "
            "1 September 2022 (research row b3)",
            _ACT_800,
        ),
        "Register the new hire for the Employment Insurance System with Form SIP 2 through the "
        "ASSIST portal. Foreign employees are excluded by the Act's First Schedule, except "
        "non-citizens residing permanently in Malaysia with the required identity card: HR "
        "skips the step for an excluded employee. The Act text read is as at 1 September 2022; "
        "a later amendment was not read.",
    ),
    _Step(
        "LHDN: new employee notified (Form CP22)",
        "hr",
        "start_date",
        30,
        "LHDN (HASiL): Form CP22 'within 30 days after the commencement of employment', through "
        "the e-CP22 application on the MyTax portal. HASiL employer responsibilities page. "
        f"{_CHECKED}",
        _read("HASiL employer responsibilities page (research row c1)", _HASIL_EMPLOYER),
        "Notify LHDN of the new hire with Form CP22 through e-CP22 on MyTax. LHDN's employer "
        "page applies this to employees who are or may become chargeable to tax: HR may skip it "
        "for others.",
    ),
    _Step(
        "Probation review",
        "manager",
        "probation_end",
        -7,
        "No statutory probation rule found; probation terms are set by the contract. Employment "
        "Act 1955 s.12(2): the notice period is the one written in the contract, otherwise not "
        f"less than the statutory scale. {_CHECKED}",
        _practice(
            "a text search of the Employment Act 1955, the Employment Regulations 1957, the "
            "Termination and Lay-Off Benefits Regulations 1980 and Sabah Cap. 67 found no "
            "probation rule (research row e)",
            _ACT_265,
        ),
        "Review the new hire's probation before it ends and record the outcome as the contract "
        "allows (confirm, extend or end it); HR approves. The step waits for a date until HR "
        "sets the probation end date. HR skips this step when the person has no probation.",
        approval_by="hr",
    ),
)

# ---- Offboarding (offsets in days from the last day) ------------------------------------------

_OFFBOARDING = (
    _Step(
        "Resignation or termination letter on file",
        "hr",
        "last_day",
        -30,
        "Employment Act 1955 s.12(4): notice 'shall be written and may be given at any time, and "
        "the day on which the notice is given shall be included in the period of the notice'; "
        "s.12(2): its length is the contract's written provision, otherwise not less than the "
        f"statutory scale. {_CHECKED}",
        _read(
            "Employment Act 1955 s.12 and s.13, JTKSM-hosted text; JTKSM FAQ on the 2022 "
            "amendment for the pregnancy restriction (research rows j2, x7)",
            _ACT_265,
        ),
        "Keep the written resignation or termination letter and the notice period it gives. "
        "Either party may waive notice, and under s.13(1) either may end the contract without "
        "notice by paying an indemnity equal to the wages for the notice period: check before "
        "agreeing. Before an employer-initiated termination, check the restrictions that apply; "
        "JTKSM's FAQ notes that an employer must not terminate a pregnant employee's service "
        "except in limited cases. The due date is a company reminder, not a statutory deadline.",
    ),
    _Step(
        "LHDN: employee leaving notified (Form CP22A)",
        "hr",
        "last_day",
        -30,
        "LHDN (HASiL): Form CP22A 'not less than 30 days before the cessation of employment', "
        "through the e-SPC application on MyTax (online submission mandatory from 1 January "
        f"2024). HASiL employer pages. {_CHECKED}",
        _read(
            "HASiL employer responsibilities and cessation of employment pages "
            "(research rows h1, h2)",
            _HASIL_CESSATION,
        ),
        "Notify LHDN that the employment ends, with Form CP22A through e-SPC on MyTax. Not "
        "required where the employee's income is already subject to monthly tax deduction, "
        "where remuneration is below the monthly tax deduction minimum, or where the company "
        "knows the employee will be employed elsewhere in Malaysia. HASiL's employer pages list "
        "these exemptions; HASiL's individual termination page words them differently, so the "
        "reviewer confirms them against that page before HR skips the step on that basis. If the "
        "employee has died, the wording is not more than 30 days after being informed of the "
        "death. Where the form is filed, HASiL's page says to withhold any money payable to the "
        "employee for 90 days or until a tax clearance letter is received (see the final pay "
        "step).",
    ),
    _Step(
        "LHDN: employee leaving Malaysia notified (Form CP21)",
        "hr",
        "last_day",
        -30,
        "LHDN (HASiL): Form CP21 'not less than 30 days before the expected departure date', "
        f"through e-SPC on MyTax. HASiL employer pages. {_CHECKED}",
        _read(
            "HASiL employer responsibilities and cessation of employment pages "
            "(research rows h3, h2)",
            _HASIL_EMPLOYER,
        ),
        "Only when the employee will leave Malaysia for more than three months; not needed where "
        "the Director General is satisfied the employee must leave Malaysia frequently for work. "
        "The period counts from the expected departure date, not the last day: move the due "
        "date if they differ. Money payable to an employee leaving without intent to return is "
        "withheld as for CP22A. HR skips the step for everyone else.",
        required=False,
    ),
    _Step(
        "Notice period and handover plan agreed",
        "manager",
        "last_day",
        -14,
        f"{PRACTICE}: no provision on handover was found in the Employment Act. {_CHECKED}",
        _practice(
            "a text search of the Employment Act 1955 found no provision on property return, "
            "exit interviews, handover or certificates of service (research row k)",
            _ACT_265,
        ),
        "Agree with the leaver how the notice period is used: who takes over their work, what "
        "they hand over and by when.",
    ),
    _Step(
        "Knowledge and files handed over",
        "employee",
        "last_day",
        -3,
        _PRACTICE_NOTE,
        _PRACTICE_SOURCE,
        "The leaver hands over their work, files and contacts as the handover plan sets out.",
    ),
    _Step(
        "Exit conversation",
        "manager",
        "last_day",
        -3,
        _PRACTICE_NOTE,
        _PRACTICE_SOURCE,
        "Talk with the leaver about their time at the company and why they are leaving, and "
        "pass anything HR should know to the HR team.",
    ),
    _Step(
        "Company equipment returned",
        "employee",
        "last_day",
        0,
        f"{PRACTICE}: no provision on returning company property was found in the Employment "
        "Act. s.24(1): no deductions from wages 'otherwise than in accordance with this Act'. "
        f"{_CHECKED}",
        _practice(
            "a text search of the Employment Act 1955 found no provision on returning company "
            "property; s.24(1) governs deductions from wages (research rows k, j1)",
            _ACT_265,
        ),
        "The leaver returns company equipment, cards and keys by their last day. Recovering the "
        "value of anything not returned from final pay is a legal question, because deductions "
        "from wages must follow the Employment Act (s.24).",
    ),
    _Step(
        "Final pay and annual leave balance",
        "hr",
        "last_day",
        0,
        "Employment Act 1955 s.20 (termination under s.11(1) or s.12): wages due 'not later than "
        "the day on which such contract of service so terminates'; s.21(1), the employer ends it "
        "without notice: 'not later than the day on which such contract of service is so "
        "terminated'; s.21(2), the employee ends it without notice: 'not later than the third "
        "day after'. s.60E(3A): unused paid annual leave at the ordinary rate of pay, except on "
        f"dismissal under s.14(1)(a). {_CHECKED}",
        _read(
            "Employment Act 1955 ss.20, 21, 24 and 60E, JTKSM-hosted text; HASiL cessation of "
            "employment page for the withholding rule (research rows j1, l, h2)",
            _ACT_265,
        ),
        "Pay the final wages and any unused annual leave, and record what was paid. The leaver "
        "may instead take the leave due before the contract ends (s.60E(2A)). Where Form CP22A "
        "or CP21 applies, HASiL's rule to withhold money payable for 90 days or until a tax "
        "clearance letter is received may affect what can be paid by the last day: confirm with "
        "the company's accountant. The Act does not say whether the leave payment falls under "
        "the same deadline as final wages. Deductions must follow s.24.",
    ),
    _Step(
        "Access removed",
        "hr",
        "last_day",
        1,
        _PRACTICE_NOTE,
        _PRACTICE_SOURCE,
        "Completed by the system the day after the last day, when the person is finished: their "
        "HR access in this company is deleted and their console membership disabled. It cannot "
        "be completed by hand. Access to other systems (email, building, other software) is not "
        "covered: remove it separately.",
        auto_check="access_removed",
    ),
    _Step(
        "Termination or lay-off benefits paid, where they apply",
        "hr",
        "last_day",
        7,
        "Employment (Termination and Lay-Off Benefits) Regulations 1980 reg 11(1): paid 'not "
        "later than seven days after the relevant date'; reg 12: a written statement of the "
        f"amount and how it was calculated. JTKSM-hosted text. {_CHECKED}",
        _read(
            "Employment (Termination and Lay-Off Benefits) Regulations 1980, JTKSM-hosted text; "
            "Employment Act 1955 s.60J (research row j3)",
            _REGS_1980,
        ),
        "Only where the employee is entitled: the Regulations apply to an employee with a "
        "continuous contract for not less than twelve months (reg 3(1)); there are no benefits on "
        "dismissal for misconduct after due inquiry or on voluntary resignation (reg 4(1)), and "
        "the Act's First Schedule excludes some higher-paid non-manual employees. Give the "
        "employee a written statement of the amount and how it was calculated (reg 12). The due "
        "date assumes the relevant date is the last day: reviewer to confirm. HR skips the step "
        "when no benefit is due.",
        required=False,
    ),
    _Step(
        "Personnel records updated and kept",
        "hr",
        "last_day",
        7,
        "Employment Act 1955 s.61(2): every particular in the register stays available 'for not "
        "less than six years after the recording thereof'. LHDN: records kept for 7 years. "
        "PERKESO: employee records 'maintained for seven (7) years from last entry date'. "
        f"{_CHECKED}",
        _read(
            "Employment Act 1955 s.61(2) and Employment Regulations 1957 reg 5, JTKSM-hosted "
            "texts; HASiL employer responsibilities page; PERKESO registration page (research "
            "rows m1, m3, m4)",
            _ACT_265,
        ),
        "Record the date of leaving and any termination benefits paid in the employee register, "
        "and file the leaver's records. The periods in the note are how long records are kept, "
        "not a deadline for this step. The due date is a company reminder.",
    ),
    _Step(
        "Foreign employee: end of service reported to JTKSM",
        "hr",
        "last_day",
        14,
        "Employment Act 1955 s.60KA(1): inform the Director General 'within thirty days of the "
        "termination of service'; s.60KA(2), where the employee ends it or absconds: 'within "
        "fourteen days of the termination of service or after the foreign employee's absence'. "
        f"JTKSM ePPAx. {_CHECKED}",
        _read(
            "Employment Act 1955 s.60KA, JTKSM-hosted text; JTKSM foreign workers employment "
            "page for ePPAx (research row x2)",
            _ACT_265,
        ),
        "Only for a foreign employee; report through ePPAx (Termination Reporting). The period "
        "is thirty days where the company ends the service, the work pass expires or the "
        "employee is repatriated or deported, and fourteen days where the foreign employee ends "
        "the service or absconds (counted from the termination or their absence); the due date "
        "uses the shorter period. HR skips the step for everyone else.",
        required=False,
    ),
    _Step(
        "EPF: final month's contribution paid",
        "hr",
        "last_day",
        15,
        "EPF (KWSP): pay the contribution for the last month's wages, through Form A or i-Akaun "
        "(Employer). KWSP pages could not be read directly (search excerpts only). "
        f"{UNCONFIRMED} {_CHECKED}",
        _excerpt(
            "KWSP employer pages, mandatory contribution and non-Malaysian employees (search "
            "excerpts only, the pages returned HTTP 403; research row i1)",
            _KWSP_CONTRIBUTION,
        ),
        "Pay the EPF contribution for the wages of the last month in service. No per-employee "
        "leaving notice to EPF was found on the KWSP excerpts. The due date is a company "
        "reminder, not a statutory deadline.",
    ),
    _Step(
        "SOCSO: final month's contribution paid",
        "hr",
        "last_day",
        15,
        "PERKESO: contribution due 'no later than the 15th day of each succeeding month'. No "
        f"per-employee leaving notice found. PERKESO registration page. {_CHECKED}",
        _read("PERKESO registration page (research row i2)", _PERKESO_REGISTRATION),
        "Pay the PERKESO (SOCSO) contribution for the last wage month. No official page was "
        "found that requires a separate leaving notice to PERKESO for a normal resignation. The "
        "due date, 15 days after the last day, is never later than the 15th of the following "
        "month.",
    ),
    _Step(
        "EIS: final month's contribution paid",
        "hr",
        "last_day",
        15,
        "PERKESO, Employment Insurance System: pay the EIS contribution for the last wage month. "
        f"No employer duty to report a leaver was found in the Act 800 text read. {UNCONFIRMED} "
        f"{_CHECKED}",
        SourceNote(
            "Employment Insurance System Act 2017 (Act 800), PERKESO-hosted text as at "
            "1 September 2022: no employer leaver duty or contribution timing found (research row "
            "i3)",
            _ACT_800,
            CHECKED_ON,
            "unclear",
        ),
        "Pay the EIS contribution for the last wage month. HR skips the step for an employee who "
        "was not registered for EIS (an excluded foreign employee). The due date is a company "
        "reminder, not a statutory deadline.",
    ),
)


# ---- Loader -----------------------------------------------------------------------------------


def _load(
    kind: ProcessKind, name: str, steps: tuple[_Step, ...]
) -> tuple[TemplateCreate, dict[str, SourceNote]]:
    template = TemplateCreate(
        kind=kind,
        name=name,
        rules=None,
        steps=[
            TemplateStep(
                title_en=s.title,
                description_en=s.description,
                owner_role=s.owner,
                anchor=s.anchor,
                due_offset_days=s.offset,
                required=s.required,
                approval_by=s.approval_by,
                auto_check=s.auto_check,
                reference_note=s.note,
            )
            for s in steps
        ],
    )
    # Ordinals as hr_template_write_steps (0631) numbers them: from 1, in array order.
    sources = {f"{kind}:{n}": s.source for n, s in enumerate(steps, start=1)}
    return template, sources


_ON, _ON_SOURCES = _load("onboarding", "Onboarding (Malaysia default)", _ONBOARDING)
_OFF, _OFF_SOURCES = _load("offboarding", "Offboarding (Malaysia default)", _OFFBOARDING)

DEFAULT_TEMPLATES: tuple[TemplateCreate, ...] = (_ON, _OFF)
DEFAULT_TEMPLATE_SOURCES: dict[str, SourceNote] = {**_ON_SOURCES, **_OFF_SOURCES}
