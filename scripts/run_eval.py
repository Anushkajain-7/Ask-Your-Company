"""
Run the synthetic 100-question AskTheCompany evaluation.

This script intentionally uses the FastAPI app through TestClient and the
public API routes:
- creates a dedicated eval workspace
- creates one source per supported corpus type
- uploads markdown, PDF, Slack JSON, and CSV files
- calls POST /api/ask for every question
- writes detailed results and aggregate metrics under docs/eval/

Scoring is deterministic and keyword-based. It evaluates the answer text plus
citation previews because the default offline Hugging Face fallback is
extractive. No LLM judge is used.
"""
from __future__ import annotations

import csv
import json
import os
import re
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
DOCS_EVAL_DIR = ROOT / "docs" / "eval"
CORPUS_DIR = DOCS_EVAL_DIR / "corpus"
QUESTIONS_PATH = DOCS_EVAL_DIR / "eval_questions.json"
RESULTS_PATH = DOCS_EVAL_DIR / "eval_results.json"
SUMMARY_PATH = DOCS_EVAL_DIR / "eval_summary.json"
DB_PATH = DOCS_EVAL_DIR / "eval_run.db"

STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "has",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "per",
    "the",
    "to",
    "up",
    "with",
    "within",
}


HR_WIKI = """# Northstar People Wiki

## Parental Leave India
Employees in India receive 26 weeks of paid parental leave per child. The leave request must use form HR-PL-2026 and managers should be notified at least 30 days before the planned start date.

## Paid Time Off Carryover
Employees may carry forward up to 5 unused PTO days into the next calendar year. Carryover expires on March 31 and unused carryover is not paid out in cash.

## Remote Work
The standard remote-work limit is 3 days per week with manager approval. International remote work is capped at 15 business days and requires Legal and Payroll approval before travel.

## Learning Stipend
Full-time employees become eligible for a USD 1,200 annual learning stipend after 90 days of employment. The stipend can be used for courses, books, certifications, and conferences.

## Wellness Stipend
Northstar reimburses USD 60 per month for wellness expenses including gyms, mental health apps, meditation programs, and ergonomic accessories.

## Onboarding Requirements
During the first week, new hires must complete privacy training, security training, and product training. A team buddy should be assigned by day 2.

## Performance Reviews
Every employee has quarterly manager check-ins. Formal performance reviews happen in June and December, and calibration notes are stored in the people system.

## Support Escalation
P0 incidents require a response in 15 minutes and must start in #incident-war-room. P1 incidents require a response in 1 hour and must name a single incident lead.

## Contractors
Contractors may access internal documentation but must not access production customer data unless the security team grants an exception.

## Immigration Support
Visa and immigration support is available for roles marked visa_support=yes in the HR roster. Employees should open a People Operations ticket before booking travel tied to immigration paperwork.
"""


PDF_SECTIONS = [
    (
        "Access Control",
        "All workforce access uses SSO and MFA. Admin access is reviewed every 30 days. Contractor tokens expire after 14 days unless Security grants a written extension.",
    ),
    (
        "Data Classification",
        "Northstar data classes are public, internal, confidential, and restricted. Restricted data includes payroll, SSN, medical, and immigration records. Restricted data must be stored only in VaultLake.",
    ),
    (
        "Expense Policy",
        "Client meals are capped at USD 75 per person. Receipts are required for expenses over USD 25 and must be submitted within 5 business days. Air travel defaults to economy class.",
    ),
    (
        "Vendor Review",
        "Any vendor that handles customer data requires a security review, a signed DPA, a risk tier, and a named procurement owner before launch.",
    ),
    (
        "Incident Response",
        "P0 incidents require response in 15 minutes, P1 incidents in 1 hour, and P2 incidents by the next business day. Postmortems are due within 5 business days.",
    ),
    (
        "Retention",
        "Audit logs are retained for 365 days. Payroll records are retained for 7 years. Support tickets are retained for 24 months unless Legal places a hold.",
    ),
]


SLACK_THREADS = {
    "threads": [
        {
            "channel": "people-ops",
            "messages": [
                {"user": "rhea", "text": "Reminder: domestic remote-work exceptions can be approved by managers."},
                {"user": "sam", "text": "International remote work still needs Legal and Payroll approval before travel."},
                {"user": "rhea", "text": "No customer data may be copied to personal laptops during remote work."},
            ],
        },
        {
            "channel": "security-review",
            "messages": [
                {"user": "maya", "text": "Payroll exports are restricted data and must stay in VaultLake."},
                {"user": "liam", "text": "Security review is mandatory for any vendor touching customer data."},
                {"user": "maya", "text": "The DPA and procurement owner have to be recorded before launch."},
            ],
        },
        {
            "channel": "finance-ops",
            "messages": [
                {"user": "grace", "text": "Client dinners are capped at USD 75 per person."},
                {"user": "omar", "text": "Receipts over USD 25 need to be uploaded within 5 business days."},
            ],
        },
        {
            "channel": "it-helpdesk",
            "messages": [
                {"user": "sofia", "text": "Laptop refresh is on a 36-month cycle."},
                {"user": "jules", "text": "Stolen devices must be reported to IT within 1 hour."},
                {"user": "sofia", "text": "Temporary loaners are available during approved repairs."},
            ],
        },
        {
            "channel": "product-launch",
            "messages": [
                {"user": "mira", "text": "Project Nimbus launches on July 15, 2026."},
                {"user": "arun", "text": "Sales enablement training for Nimbus is due July 10, 2026."},
                {"user": "mira", "text": "The support macro name is NIMBUS-GA-2026."},
            ],
        },
        {
            "channel": "incident-war-room",
            "messages": [
                {"user": "ravi", "text": "The April API incident was classified as P1."},
                {"user": "nora", "text": "The immediate fix was rolling back the rate limiter."},
                {"user": "ravi", "text": "The postmortem action owner is Platform Reliability."},
            ],
        },
        {
            "channel": "benefits-questions",
            "messages": [
                {"user": "nina", "text": "The wellness stipend is USD 60 per month."},
                {"user": "ethan", "text": "The learning stipend is USD 1,200 annually after 90 days."},
            ],
        },
    ]
}


ROSTER_ROWS = [
    ["Asha Rao", "Engineering", "Bengaluru", "Isha Nair", "Software Engineer", "2024-04-15", "12", "standard", "2026-08-01", "Q3-2026", "yes", "Gold"],
    ["Ravi Patel", "Support", "Pune", "Meera Iyer", "Support Lead", "2023-11-02", "8", "standard", "2026-07-20", "Q1-2027", "no", "Silver"],
    ["Mira Shah", "Product", "Mumbai", "Arun Mehta", "Product Manager", "2022-05-09", "15", "standard", "2026-09-15", "Q4-2026", "yes", "Gold"],
    ["Grace Kim", "Finance", "Seoul", "Daniel Cho", "Finance Analyst", "2021-01-18", "6", "finance", "2026-08-30", "Q2-2027", "no", "Platinum"],
    ["Omar Haddad", "Security", "Dubai", "Leila Mansour", "Contractor", "2026-01-12", "0", "contractor", "2026-07-31", "Q3-2026", "no", "Contractor"],
    ["Sofia Garcia", "IT", "Madrid", "Luis Romero", "IT Specialist", "2020-03-23", "9", "admin", "2026-10-01", "Q4-2026", "yes", "Gold"],
    ["Ethan Brooks", "Sales", "New York", "Priya Das", "Account Executive", "2023-07-14", "11", "standard", "2026-08-21", "Q1-2027", "no", "Silver"],
    ["Nina Kapoor", "People Ops", "Delhi", "Rhea Singh", "HR Partner", "2019-09-30", "14", "hr", "2026-07-25", "Q2-2027", "yes", "Platinum"],
    ["Wei Chen", "Legal", "Singapore", "Hannah Lim", "Counsel", "2022-02-11", "10", "legal", "2026-11-05", "Q3-2027", "yes", "Gold"],
    ["Noah Reed", "Marketing", "London", "Clara Evans", "Campaign Manager", "2024-06-17", "7", "standard", "2026-08-12", "Q4-2026", "no", "Silver"],
    ["Isha Nair", "Engineering", "Bengaluru", "CTO Office", "Engineering Manager", "2018-08-06", "16", "admin", "2026-09-01", "Q1-2027", "yes", "Platinum"],
    ["Luis Romero", "IT", "Madrid", "Sofia Garcia", "IT Manager", "2017-10-20", "13", "admin", "2026-10-15", "Q4-2027", "yes", "Platinum"],
    ["Priya Das", "Sales", "New York", "Mateo Cruz", "Sales Director", "2020-12-01", "5", "standard", "2026-07-29", "Q2-2027", "no", "Gold"],
    ["Arun Mehta", "Product", "Mumbai", "Mira Shah", "Group PM", "2019-04-28", "17", "standard", "2026-09-30", "Q3-2027", "yes", "Platinum"],
    ["Leila Mansour", "Security", "Dubai", "Maya Stone", "Security Manager", "2021-05-22", "4", "security", "2026-08-05", "Q1-2027", "no", "Gold"],
    ["Daniel Cho", "Finance", "Seoul", "Grace Kim", "Controller", "2016-02-14", "18", "finance", "2026-12-10", "Q2-2027", "no", "Platinum"],
    ["Hannah Lim", "Legal", "Singapore", "Wei Chen", "Legal Ops", "2023-03-08", "6", "legal", "2026-11-20", "Q4-2026", "yes", "Gold"],
    ["Clara Evans", "Marketing", "London", "Noah Reed", "Brand Lead", "2022-08-19", "9", "standard", "2026-08-18", "Q1-2027", "no", "Silver"],
    ["Meera Iyer", "Support", "Pune", "Ravi Patel", "Support Manager", "2018-01-09", "12", "standard", "2026-07-28", "Q3-2027", "no", "Gold"],
    ["Maya Stone", "Security", "San Francisco", "CISO Office", "Security Architect", "2017-07-07", "10", "security", "2026-09-09", "Q4-2027", "no", "Platinum"],
    ["Jules Martin", "Engineering", "Paris", "Isha Nair", "Platform Engineer", "2024-02-26", "3", "standard", "2026-10-05", "Q2-2027", "yes", "Gold"],
    ["Nora Ali", "Reliability", "Toronto", "Maya Stone", "SRE", "2021-11-16", "8", "admin", "2026-08-08", "Q3-2026", "no", "Gold"],
    ["Mateo Cruz", "Sales", "Mexico City", "Priya Das", "Sales Engineer", "2022-09-12", "6", "standard", "2026-07-30", "Q4-2026", "yes", "Silver"],
    ["Rhea Singh", "People Ops", "Delhi", "Nina Kapoor", "People Ops Director", "2015-06-03", "19", "hr", "2026-07-26", "Q1-2028", "yes", "Platinum"],
    ["Samir Khan", "Operations", "Hyderabad", "Rhea Singh", "Ops Coordinator", "2025-01-22", "4", "standard", "2026-08-02", "Q2-2027", "no", "Silver"],
    ["Lena Ortiz", "Data", "Austin", "Maya Stone", "Data Analyst", "2023-04-04", "10", "standard", "2026-09-18", "Q2-2027", "no", "Gold"],
    ["Tara Singh", "Finance", "Delhi", "Daniel Cho", "Payroll Specialist", "2020-10-10", "11", "finance", "2026-08-17", "Q3-2027", "yes", "Platinum"],
    ["Ben Walker", "Support", "Dublin", "Meera Iyer", "Support Engineer", "2024-12-05", "2", "standard", "2026-07-27", "Q1-2028", "no", "Silver"],
    ["Yuki Tanaka", "Product", "Tokyo", "Arun Mehta", "Product Designer", "2021-06-25", "13", "standard", "2026-10-22", "Q4-2027", "no", "Gold"],
    ["Fatima Noor", "Security", "Cairo", "Leila Mansour", "Security Analyst", "2025-03-18", "5", "security", "2026-08-11", "Q2-2028", "yes", "Gold"],
]


FACTUAL_QA = [
    ("What paid parental leave do employees in India receive?", "India parental leave provides 26 weeks of paid leave per child using form HR-PL-2026.", "eval_hr_wiki.md"),
    ("How many unused PTO days can carry forward?", "Employees may carry forward 5 unused PTO days and the carryover expires on March 31.", "eval_hr_wiki.md"),
    ("What is the standard weekly remote-work limit?", "The standard remote-work limit is 3 days per week with manager approval.", "eval_hr_wiki.md"),
    ("How long can international remote work last?", "International remote work is capped at 15 business days and requires Legal and Payroll approval.", "eval_hr_wiki.md"),
    ("What is the annual learning stipend?", "The learning stipend is USD 1,200 annually after 90 days of employment.", "eval_hr_wiki.md"),
    ("What monthly wellness reimbursement does Northstar offer?", "Northstar reimburses USD 60 per month for wellness expenses.", "eval_hr_wiki.md"),
    ("Which trainings are required in the first week?", "New hires must complete privacy training, security training, and product training in the first week.", "eval_hr_wiki.md"),
    ("When are formal performance reviews held?", "Formal performance reviews happen in June and December, with quarterly manager check-ins.", "eval_hr_wiki.md"),
    ("What response time is required for P0 incidents?", "P0 incidents require a response in 15 minutes and must start in #incident-war-room.", "eval_hr_wiki.md|eval_security_policy.pdf"),
    ("May contractors access production customer data by default?", "Contractors must not access production customer data unless Security grants an exception.", "eval_hr_wiki.md"),
    ("How often is admin access reviewed?", "Admin access is reviewed every 30 days.", "eval_security_policy.pdf"),
    ("When do contractor tokens expire?", "Contractor tokens expire after 14 days unless Security grants a written extension.", "eval_security_policy.pdf"),
    ("Where must restricted data be stored?", "Restricted data must be stored only in VaultLake.", "eval_security_policy.pdf"),
    ("Which data classes are listed in the security policy?", "The data classes are public, internal, confidential, and restricted.", "eval_security_policy.pdf"),
    ("What is the client meal cap?", "Client meals are capped at USD 75 per person.", "eval_security_policy.pdf|eval_slack_threads.json"),
    ("When are receipts required?", "Receipts are required for expenses over USD 25 and must be submitted within 5 business days.", "eval_security_policy.pdf|eval_slack_threads.json"),
    ("What is required for vendors that handle customer data?", "Customer-data vendors require a security review, signed DPA, risk tier, and named procurement owner.", "eval_security_policy.pdf"),
    ("How long are payroll records retained?", "Payroll records are retained for 7 years.", "eval_security_policy.pdf"),
    ("How long are support tickets retained?", "Support tickets are retained for 24 months unless Legal places a hold.", "eval_security_policy.pdf"),
    ("When are P1 incident postmortems due?", "P1 incidents require response in 1 hour and postmortems are due within 5 business days.", "eval_security_policy.pdf"),
    ("What is the laptop refresh cycle?", "Laptop refresh is on a 36-month cycle.", "eval_slack_threads.json"),
    ("How quickly must stolen devices be reported?", "Stolen devices must be reported to IT within 1 hour.", "eval_slack_threads.json"),
    ("When does Project Nimbus launch?", "Project Nimbus launches on July 15, 2026.", "eval_slack_threads.json"),
    ("What is the Nimbus support macro name?", "The support macro name is NIMBUS-GA-2026.", "eval_slack_threads.json"),
    ("What fixed the April API incident?", "The April API incident was fixed by rolling back the rate limiter.", "eval_slack_threads.json"),
]


TABLE_QA = [
    ("What department is Asha Rao in?", "Asha Rao is in Engineering.", "eval_hr_roster.csv"),
    ("Who manages Ravi Patel?", "Ravi Patel's manager is Meera Iyer.", "eval_hr_roster.csv"),
    ("Where is Mira Shah located?", "Mira Shah is located in Mumbai.", "eval_hr_roster.csv"),
    ("What access role does Grace Kim have?", "Grace Kim has finance access_role.", "eval_hr_roster.csv"),
    ("What role title does Omar Haddad have?", "Omar Haddad has the Contractor role title.", "eval_hr_roster.csv"),
    ("What is Sofia Garcia's laptop refresh quarter?", "Sofia Garcia's laptop refresh quarter is Q4-2026.", "eval_hr_roster.csv"),
    ("What benefit plan does Ethan Brooks have?", "Ethan Brooks has the Silver benefit plan.", "eval_hr_roster.csv"),
    ("What is Nina Kapoor's access role?", "Nina Kapoor has hr access_role.", "eval_hr_roster.csv"),
    ("What department is Wei Chen in?", "Wei Chen is in Legal.", "eval_hr_roster.csv"),
    ("How many PTO days does Noah Reed have?", "Noah Reed has 7 PTO days.", "eval_hr_roster.csv"),
    ("Who manages Isha Nair?", "Isha Nair's manager is CTO Office.", "eval_hr_roster.csv"),
    ("What location is Luis Romero in?", "Luis Romero is located in Madrid.", "eval_hr_roster.csv"),
    ("What is Priya Das's role title?", "Priya Das is a Sales Director.", "eval_hr_roster.csv"),
    ("What visa support value is listed for Arun Mehta?", "Arun Mehta has visa_support yes.", "eval_hr_roster.csv"),
    ("What training due date is listed for Leila Mansour?", "Leila Mansour's training_due date is 2026-08-05.", "eval_hr_roster.csv"),
    ("What benefit plan does Daniel Cho have?", "Daniel Cho has the Platinum benefit plan.", "eval_hr_roster.csv"),
    ("Who manages Hannah Lim?", "Hannah Lim's manager is Wei Chen.", "eval_hr_roster.csv"),
    ("What department is Clara Evans in?", "Clara Evans is in Marketing.", "eval_hr_roster.csv"),
    ("How many PTO days does Meera Iyer have?", "Meera Iyer has 12 PTO days.", "eval_hr_roster.csv"),
    ("What access role does Maya Stone have?", "Maya Stone has security access_role.", "eval_hr_roster.csv"),
    ("Where is Jules Martin located?", "Jules Martin is located in Paris.", "eval_hr_roster.csv"),
    ("What role title does Nora Ali have?", "Nora Ali is an SRE.", "eval_hr_roster.csv"),
    ("What laptop refresh quarter is listed for Mateo Cruz?", "Mateo Cruz's laptop refresh quarter is Q4-2026.", "eval_hr_roster.csv"),
    ("Who manages Rhea Singh?", "Rhea Singh's manager is Nina Kapoor.", "eval_hr_roster.csv"),
    ("What department is Fatima Noor in?", "Fatima Noor is in Security.", "eval_hr_roster.csv"),
]


MULTIHOP_QA = [
    ("For Asha Rao, what department is she in and what remote-work limit applies?", "Asha Rao is in Engineering and the remote-work limit is 3 days per week with manager approval.", "eval_hr_roster.csv|eval_hr_wiki.md"),
    ("For Ravi Patel, who is his manager and what P1 response time applies to support incidents?", "Ravi Patel's manager is Meera Iyer and P1 incidents require response in 1 hour.", "eval_hr_roster.csv|eval_security_policy.pdf"),
    ("For Mira Shah, where is she located and what approval is needed for international remote work?", "Mira Shah is in Mumbai and international remote work needs Legal and Payroll approval.", "eval_hr_roster.csv|eval_hr_wiki.md|eval_slack_threads.json"),
    ("For Grace Kim, what access role does she have and what is the client meal cap?", "Grace Kim has finance access_role and client meals are capped at USD 75 per person.", "eval_hr_roster.csv|eval_security_policy.pdf|eval_slack_threads.json"),
    ("For Omar Haddad, what is his role title and when do contractor tokens expire?", "Omar Haddad is a Contractor and contractor tokens expire after 14 days.", "eval_hr_roster.csv|eval_security_policy.pdf"),
    ("For Sofia Garcia, what admin-related obligation applies to her access role?", "Sofia Garcia has admin access_role and admin access is reviewed every 30 days.", "eval_hr_roster.csv|eval_security_policy.pdf"),
    ("For Ethan Brooks, what benefit plan does he have and when is Nimbus sales enablement due?", "Ethan Brooks has the Silver benefit plan and Nimbus sales enablement training is due July 10, 2026.", "eval_hr_roster.csv|eval_slack_threads.json"),
    ("For Nina Kapoor, what access role does she have and which source describes the wellness stipend?", "Nina Kapoor has hr access_role and the wellness stipend is USD 60 per month.", "eval_hr_roster.csv|eval_hr_wiki.md|eval_slack_threads.json"),
    ("For Wei Chen, what department is he in and what must customer-data vendors complete?", "Wei Chen is in Legal and customer-data vendors need security review, DPA, risk tier, and procurement owner.", "eval_hr_roster.csv|eval_security_policy.pdf"),
    ("For Noah Reed, how many PTO days does he have and how many PTO days can carry forward?", "Noah Reed has 7 PTO days and up to 5 unused PTO days can carry forward.", "eval_hr_roster.csv|eval_hr_wiki.md"),
    ("For Isha Nair, what is her access role and which formal review months apply?", "Isha Nair has admin access_role and formal reviews happen in June and December.", "eval_hr_roster.csv|eval_hr_wiki.md"),
    ("For Luis Romero, where is he located and what is the laptop refresh cycle?", "Luis Romero is located in Madrid and laptop refresh is on a 36-month cycle.", "eval_hr_roster.csv|eval_slack_threads.json"),
    ("For Priya Das, what role title does she have and what is the client dinner reimbursement cap?", "Priya Das is a Sales Director and client dinners are capped at USD 75 per person.", "eval_hr_roster.csv|eval_slack_threads.json|eval_security_policy.pdf"),
    ("For Arun Mehta, what visa support value is listed and what immigration support rule applies?", "Arun Mehta has visa_support yes and should open a People Operations ticket before immigration-related travel.", "eval_hr_roster.csv|eval_hr_wiki.md"),
    ("For Leila Mansour, what training due date is listed and what data class includes payroll exports?", "Leila Mansour's training_due date is 2026-08-05 and payroll exports are restricted data.", "eval_hr_roster.csv|eval_security_policy.pdf|eval_slack_threads.json"),
    ("For Daniel Cho, what department is he in and how long are payroll records retained?", "Daniel Cho is in Finance and payroll records are retained for 7 years.", "eval_hr_roster.csv|eval_security_policy.pdf"),
    ("For Hannah Lim, who manages her and what vendor document must be signed?", "Hannah Lim's manager is Wei Chen and customer-data vendors require a signed DPA.", "eval_hr_roster.csv|eval_security_policy.pdf|eval_slack_threads.json"),
    ("For Clara Evans, what department is she in and when does Project Nimbus launch?", "Clara Evans is in Marketing and Project Nimbus launches on July 15, 2026.", "eval_hr_roster.csv|eval_slack_threads.json"),
    ("For Meera Iyer, how many PTO days does she have and when does carryover expire?", "Meera Iyer has 12 PTO days and carryover expires on March 31.", "eval_hr_roster.csv|eval_hr_wiki.md"),
    ("For Maya Stone, what access role does she have and where must restricted data stay?", "Maya Stone has security access_role and restricted data must stay in VaultLake.", "eval_hr_roster.csv|eval_security_policy.pdf|eval_slack_threads.json"),
    ("For Jules Martin, where is he located and what approval is required for international remote work?", "Jules Martin is located in Paris and international remote work requires Legal and Payroll approval.", "eval_hr_roster.csv|eval_hr_wiki.md|eval_slack_threads.json"),
    ("For Nora Ali, what role title does she have and who owns the April API incident postmortem action?", "Nora Ali is an SRE and Platform Reliability owns the April API incident postmortem action.", "eval_hr_roster.csv|eval_slack_threads.json"),
    ("For Mateo Cruz, what laptop refresh quarter is listed and what is the Nimbus support macro?", "Mateo Cruz's laptop refresh quarter is Q4-2026 and the Nimbus support macro is NIMBUS-GA-2026.", "eval_hr_roster.csv|eval_slack_threads.json"),
    ("For Rhea Singh, what access role does she have and which form is used for India parental leave?", "Rhea Singh has hr access_role and India parental leave uses form HR-PL-2026.", "eval_hr_roster.csv|eval_hr_wiki.md"),
    ("For Fatima Noor, what department is she in and what is required for vendors touching customer data?", "Fatima Noor is in Security and vendors touching customer data require security review, DPA, risk tier, and procurement owner.", "eval_hr_roster.csv|eval_security_policy.pdf|eval_slack_threads.json"),
]


NO_ANSWER_QUESTIONS = [
    "What is Northstar's cafeteria menu for next Tuesday?",
    "What is the company's 2027 acquisition target?",
    "How many shares of public stock does Northstar plan to buy back?",
    "What is the office pet policy for iguanas?",
    "What is the parental leave entitlement in Brazil?",
    "Which vendor won the 2025 office furniture RFP?",
    "What is the CEO's personal mobile number?",
    "What is the Kubernetes cluster root password?",
    "What is the exact salary for every employee?",
    "What is the company's Mars office address?",
    "Who won the Halloween costume contest in 2024?",
    "What is the cafeteria coffee supplier contract value?",
    "What are the board minutes from January 2030?",
    "What is the private key for production TLS?",
    "Which employees own Bitcoin in their personal portfolios?",
    "What is the policy for submarine commuting?",
    "How many parking spaces are reserved in Sydney?",
    "What did the customer Acme Labs pay in Q2 revenue?",
    "What is the roadmap for Project Orion?",
    "Which recruiter handled the CFO search?",
    "What is the insurance policy number for the London office lease?",
    "What is the exact code name for the next acquisition?",
    "How many catered lunches were ordered in Berlin last week?",
    "What is the immigration policy for Antarctica assignments?",
    "Which employees requested confidential medical leave last month?",
]


def write_pdf(path: Path) -> None:
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

    styles = getSampleStyleSheet()
    doc = SimpleDocTemplate(str(path), pagesize=letter, title="Northstar Security and Finance Policy")
    story = [Paragraph("Northstar Security and Finance Policy", styles["Title"])]
    for title, body in PDF_SECTIONS:
        story.append(Spacer(1, 12))
        story.append(Paragraph(title, styles["Heading2"]))
        story.append(Paragraph(body, styles["BodyText"]))
    doc.build(story)


def ensure_eval_assets() -> None:
    DOCS_EVAL_DIR.mkdir(parents=True, exist_ok=True)
    CORPUS_DIR.mkdir(parents=True, exist_ok=True)

    (CORPUS_DIR / "eval_hr_wiki.md").write_text(HR_WIKI, encoding="utf-8")
    write_pdf(CORPUS_DIR / "eval_security_policy.pdf")
    (CORPUS_DIR / "eval_slack_threads.json").write_text(
        json.dumps(SLACK_THREADS, indent=2),
        encoding="utf-8",
    )

    with (CORPUS_DIR / "eval_hr_roster.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "employee",
                "department",
                "location",
                "manager",
                "role",
                "hire_date",
                "pto_balance",
                "access_role",
                "training_due",
                "laptop_refresh_quarter",
                "visa_support",
                "benefit_plan",
            ]
        )
        writer.writerows(ROSTER_ROWS)

    questions: list[dict[str, str]] = []
    for question, expected, source in FACTUAL_QA:
        questions.append(
            {
                "question": question,
                "expected_answer_summary": expected,
                "tier": "factual",
                "expected_source": source,
            }
        )
    for question, expected, source in MULTIHOP_QA:
        questions.append(
            {
                "question": question,
                "expected_answer_summary": expected,
                "tier": "multi-hop",
                "expected_source": source,
            }
        )
    for question, expected, source in TABLE_QA:
        questions.append(
            {
                "question": question,
                "expected_answer_summary": expected,
                "tier": "table-lookup",
                "expected_source": source,
            }
        )
    for question in NO_ANSWER_QUESTIONS:
        questions.append(
            {
                "question": question,
                "expected_answer_summary": "The system should say it does not have enough information.",
                "tier": "opinion/no-answer",
                "expected_source": "none",
            }
        )

    if len(questions) != 100:
        raise RuntimeError(f"Expected 100 eval questions, got {len(questions)}")
    QUESTIONS_PATH.write_text(json.dumps(questions, indent=2), encoding="utf-8")


def configure_eval_environment() -> None:
    if DB_PATH.exists():
        DB_PATH.unlink()
    os.environ["DATABASE_URL"] = f"sqlite:///{DB_PATH}"
    os.environ["HF_API_TOKEN"] = ""
    os.environ["ENABLE_RERANKING"] = "true"
    os.environ["RERANK_CANDIDATE_K"] = "20"
    os.environ["TOP_K"] = "6"
    sys.path.insert(0, str(ROOT / "backend"))


def api_json(client, path: str, token: str, *, method: str = "get", body: dict[str, Any] | None = None):
    headers = {"Authorization": f"Bearer {token}"}
    request = getattr(client, method)
    response = request(path, headers=headers, json=body) if body is not None else request(path, headers=headers)
    response.raise_for_status()
    return response.json()


def ingest_eval_corpus(client) -> str:
    from app.core.db import init_db

    init_db()
    signup = client.post(
        "/api/auth/signup",
        json={
            "email": "eval-admin@northstar.example",
            "password": "supersecret1",
            "full_name": "Eval Admin",
            "workspace_name": "Northstar Synthetic Eval",
        },
    )
    signup.raise_for_status()
    token = signup.json()["access_token"]

    source_specs = [
        ("People Wiki", "markdown", "eval_hr_wiki.md", "text/markdown"),
        ("Security PDF", "pdf", "eval_security_policy.pdf", "application/pdf"),
        ("Slack Export", "slack_json", "eval_slack_threads.json", "application/json"),
        ("HR Roster", "csv", "eval_hr_roster.csv", "text/csv"),
    ]
    for name, source_type, filename, content_type in source_specs:
        created = api_json(
            client,
            f"/api/sources?name={name.replace(' ', '%20')}&source_type={source_type}",
            token,
            method="post",
        )
        with (CORPUS_DIR / filename).open("rb") as f:
            uploaded = client.post(
                f"/api/sources/{created['id']}/documents",
                headers={"Authorization": f"Bearer {token}"},
                files={"file": (filename, f, content_type)},
            )
        uploaded.raise_for_status()
        status = uploaded.json()["status"]
        if status != "ready":
            raise RuntimeError(f"{filename} upload ended with status={status}: {uploaded.json()}")
    return token


def terms(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]+", text.lower()) if t not in STOPWORDS and len(t) > 1]


def expected_sources(value: str) -> list[str]:
    if value == "none":
        return []
    return [part.strip().lower() for part in value.split("|") if part.strip()]


def answer_accuracy(question: dict[str, str], answer: dict[str, Any]) -> tuple[int, float, list[str]]:
    citations = answer.get("citations", [])
    evidence = " ".join(
        [answer.get("answer", "")]
        + [c.get("text_preview", "") for c in citations]
        + [c.get("document_filename", "") for c in citations]
        + [c.get("locator", "") for c in citations]
    ).lower()

    if question["expected_source"] == "none":
        insufficient = any(
            phrase in evidence
            for phrase in [
                "not have enough information",
                "no matching context",
                "no matching passages",
                "don't have enough information",
            ]
        )
        accurate = int((not citations) or insufficient)
        return accurate, float(accurate), []

    expected_terms = sorted(set(terms(question["expected_answer_summary"])))
    matched = [term for term in expected_terms if term in evidence]
    score = len(matched) / max(len(expected_terms), 1)
    threshold = 0.6 if question["tier"] != "multi-hop" else 0.55
    return int(score >= threshold), score, [term for term in expected_terms if term not in matched]


def citation_metrics(question: dict[str, str], answer: dict[str, Any]) -> tuple[float, float]:
    citations = answer.get("citations", [])
    expected = expected_sources(question["expected_source"])

    if not expected:
        clean = 1.0 if not citations else 0.0
        return clean, clean

    if not citations:
        return 0.0, 0.0

    relevant_count = 0
    found_sources = set()
    for citation in citations:
        citation_text = " ".join(
            [
                citation.get("source_name", ""),
                citation.get("document_filename", ""),
                citation.get("locator", ""),
            ]
        ).lower()
        matched_any = False
        for source in expected:
            if source in citation_text:
                matched_any = True
                found_sources.add(source)
        if matched_any:
            relevant_count += 1

    precision = relevant_count / len(citations)
    recall = len(found_sources) / len(expected)
    return precision, recall


def confidence_bucket(confidence: float) -> str:
    low = min(int(confidence // 10) * 10, 90)
    return f"{low}-{low + 10}"


def aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "n": len(rows),
        "answer_accuracy": round(statistics.mean(r["answer_accuracy"] for r in rows), 4),
        "citation_precision": round(statistics.mean(r["citation_precision"] for r in rows), 4),
        "citation_recall": round(statistics.mean(r["citation_recall"] for r in rows), 4),
        "avg_confidence": round(statistics.mean(r["confidence"] for r in rows), 2),
        "by_tier": {},
        "confidence_calibration": [],
    }

    by_tier: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_bucket: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_tier[row["tier"]].append(row)
        by_bucket[confidence_bucket(row["confidence"])].append(row)

    for tier, tier_rows in sorted(by_tier.items()):
        summary["by_tier"][tier] = {
            "n": len(tier_rows),
            "answer_accuracy": round(statistics.mean(r["answer_accuracy"] for r in tier_rows), 4),
            "citation_precision": round(statistics.mean(r["citation_precision"] for r in tier_rows), 4),
            "citation_recall": round(statistics.mean(r["citation_recall"] for r in tier_rows), 4),
            "avg_confidence": round(statistics.mean(r["confidence"] for r in tier_rows), 2),
        }

    def bucket_sort(label: str) -> int:
        return int(label.split("-")[0])

    for bucket in sorted(by_bucket, key=bucket_sort):
        bucket_rows = by_bucket[bucket]
        summary["confidence_calibration"].append(
            {
                "bucket": bucket,
                "n": len(bucket_rows),
                "accuracy": round(statistics.mean(r["answer_accuracy"] for r in bucket_rows), 4),
                "avg_confidence": round(statistics.mean(r["confidence"] for r in bucket_rows), 2),
            }
        )

    return summary


def run_eval() -> dict[str, Any]:
    ensure_eval_assets()
    configure_eval_environment()

    from fastapi.testclient import TestClient
    from app.main import app

    client = TestClient(app)
    token = ingest_eval_corpus(client)
    questions = json.loads(QUESTIONS_PATH.read_text(encoding="utf-8"))

    rows = []
    for idx, question in enumerate(questions, start=1):
        response = client.post(
            "/api/ask",
            headers={"Authorization": f"Bearer {token}"},
            json={"question": question["question"], "top_k": 6},
        )
        response.raise_for_status()
        answer = response.json()
        accurate, keyword_score, missing_terms = answer_accuracy(question, answer)
        precision, recall = citation_metrics(question, answer)
        rows.append(
            {
                "id": idx,
                **question,
                "answer": answer["answer"],
                "confidence": answer["confidence"],
                "used_fallback": answer["used_fallback"],
                "citations": answer["citations"],
                "answer_accuracy": accurate,
                "keyword_score": round(keyword_score, 4),
                "missing_terms": missing_terms,
                "citation_precision": round(precision, 4),
                "citation_recall": round(recall, 4),
            }
        )

    summary = aggregate(rows)
    summary["scoring_method"] = (
        "Deterministic keyword scoring over answer text plus citation previews; "
        "no LLM judge. Hugging Face token is disabled for reproducibility, so "
        "the app uses its offline embedding/generation fallback."
    )
    summary["artifacts"] = {
        "questions": str(QUESTIONS_PATH.relative_to(ROOT)),
        "results": str(RESULTS_PATH.relative_to(ROOT)),
        "corpus": str(CORPUS_DIR.relative_to(ROOT)),
    }

    RESULTS_PATH.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    SUMMARY_PATH.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def main() -> None:
    summary = run_eval()
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
