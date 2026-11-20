"""The word rules behind the assistant's fact reads, in English and Bahasa Melayu.

They live here, not in the turn service, so the area selector can use the same words without
importing the service (the service imports the selector).
"""

from __future__ import annotations

import re

# "my boss" in English and Bahasa Melayu; resolved from the caller's own HR record.
_MANAGER_WORDS = re.compile(
    r"\b(boss|manager|supervisor|line manager|bos|pengurus|penyelia|ketua)\b", re.IGNORECASE
)
# The message is about email (a reply, a thread, a sender, the inbox) in English or Malay.
_MAIL_WORDS = re.compile(
    r"\b(e-?mails?|e-?mel|inbox|gmail|mail|repl(?:y|ies|ied|ying)|respond|threads?|senders?"
    r"|attention|balas|balasan|peti masuk)\b",
    re.IGNORECASE,
)
_TEAM_WORDS = re.compile(r"\b(team|my reports|direct reports|pasukan|anak buah)\b", re.IGNORECASE)
# The message is about training in English or Malay; resolves the caller's own HR readiness.
_TRAINING_WORDS = re.compile(
    r"\b(trainings?|courses?|workshops?|certifications?|seminars?|latihan|kursus|bengkel"
    r"|pensijilan)\b",
    re.IGNORECASE,
)
# The message is about people records (adding, changing, leaving, who reports to whom) in
# English or Malay; resolves the caller's People view (people_access).
_PEOPLE_WORDS = re.compile(
    r"\b(add|hire[sd]?|hiring|new staff|chang(?:e|es|ed|ing)|managers?|departments?|leaving"
    r"|resign(?:s|ed|ing|ation)?|last day|reports? to|who is in|tambah|pekerja baru|tukar"
    r"|pengurus|jabatan|berhenti|letak jawatan|hari terakhir|melapor kepada|siapa dalam)\b",
    re.IGNORECASE,
)
