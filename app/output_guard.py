import json
import logging
import re
from enum import StrEnum
from typing import Any

from llama_index.core.llms import LLM, ChatMessage, MessageRole
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class OutputGuardViolationCode(StrEnum):
    """Types of policy violations the guard model can detect."""

    EMPTY_ANSWER = "empty_answer"
    NOT_RELEVANT = "not_relevant"
    NOT_GROUNDED = "not_grounded"
    UNSAFE_CONTENT = "unsafe_content"
    INTERNAL_LEAKAGE = "internal_leakage"


class OutputGuardSeverity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class OutputGuardAction(StrEnum):
    """What the caller should do with the answer based on the check result."""

    ALLOW = "allow"
    REGENERATE = "regenerate"
    BLOCK = "block"


class RetrievedContext(BaseModel):
    source_id: str
    text: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class OutputGuardViolation(BaseModel):
    code: OutputGuardViolationCode
    reason: str
    severity: OutputGuardSeverity = OutputGuardSeverity.MEDIUM


class OutputGuardContext(BaseModel):
    session_id: str
    user_message: str
    answer: str
    retrieved_context: list[RetrievedContext] = Field(default_factory=list)
    requires_grounding: bool = True


class OutputGuardResult(BaseModel):
    action: OutputGuardAction
    answer: str
    violations: list[OutputGuardViolation] = Field(default_factory=list)

    @property
    def allowed(self) -> bool:
        return self.action == OutputGuardAction.ALLOW


class GuardModelError(RuntimeError):
    """Raised when a guard-model judge call fails, times out, or replies with
    something that doesn't match the required JSON verdict contract. There is
    deliberately no non-model fallback here: a broken guard model must surface
    as an error, not silently downgrade the check.
    """


# Fallback answers shown to the end user instead of a rejected one (kept in Polish
# to match the chatbot's user-facing language).
QUALITY_FALLBACK = (
    "Nie udało się przygotować odpowiedzi wystarczająco dobrze popartej dostępnymi źródłami."
)
SAFETY_FALLBACK = "Nie mogę zwrócić tej odpowiedzi ze względów bezpieczeństwa."
BLOCKED_ANSWER = SAFETY_FALLBACK


class OutputGuard:

    def __init__(self, guard_llm: LLM) -> None:
        self._guard_llm = guard_llm

    async def process(self, context: OutputGuardContext) -> OutputGuardResult:
        """Runs all checks and decides whether to allow, regenerate, or block the answer."""
        answer = context.answer.strip()
        context = context.model_copy(update={"answer": answer})
        violations: list[OutputGuardViolation] = []

        if not answer:
            violations.append(
                _violation(OutputGuardViolationCode.EMPTY_ANSWER, "Model zwrócił pustą odpowiedź.")
            )
        else:
            # Checks run independently and all violations are collected, not short-circuited.
            violations += await check_relevance_llm(context, self._guard_llm)
            violations += await check_groundedness_llm(context, self._guard_llm)
            violations += await check_safety_llm(answer, self._guard_llm)
            violations += await check_leakage_llm(answer, self._guard_llm)

        # Safety/leakage violations block the answer outright; other violations only trigger a retry.
        blocked = {
            OutputGuardViolationCode.UNSAFE_CONTENT,
            OutputGuardViolationCode.INTERNAL_LEAKAGE,
        }
        if any(item.code in blocked for item in violations):
            return OutputGuardResult(
                action=OutputGuardAction.BLOCK,
                answer=SAFETY_FALLBACK,
                violations=violations,
            )
        if violations:
            return OutputGuardResult(
                action=OutputGuardAction.REGENERATE,
                answer=QUALITY_FALLBACK,
                violations=violations,
            )
        return OutputGuardResult(action=OutputGuardAction.ALLOW, answer=answer)


JUDGE_SYSTEM_PROMPT = (
    "You are a strict content policy classifier. Apply the policy in the "
    "developer message to the content in the user message and return your "
    "verdict in the required JSON format only."
)
RELEVANCE_JUDGE_POLICY = (
    "POLICY: Question/answer relevance\n\n"
    "Content to classify is a (QUESTION, ANSWER) pair from a university chatbot. "
    "Flag a violation when ANSWER does not address the topic of QUESTION at all — "
    "i.e. it talks about something else entirely. Do NOT flag a violation for "
    "factual incorrectness, incompleteness, or lack of source support — those are "
    "judged by separate checks. Do NOT flag a violation when ANSWER explicitly "
    "states the information isn't available.\n\n"
    "VIOLATION example: QUESTION 'W jakich godzinach czynny jest dziekanat?', "
    "ANSWER 'Wnioski o stypendium rektora można składać do 15 października.' — "
    "different topic entirely.\n"
    "NOT a violation example: QUESTION 'Kiedy otwarta jest biblioteka?', "
    "ANSWER 'Od 8:00 do 16:00.' — on topic, even though terse.\n\n"
    "Respond with exactly one JSON object and nothing else:\n"
    '{"violation": true|false, "rationale": "<one short sentence, in the same '
    'language as ANSWER>"}'
)

GROUNDEDNESS_JUDGE_POLICY = (
    "POLICY: Answer groundedness\n\n"
    "Content to classify is a (QUESTION, ANSWER, SOURCES) triple from a university "
    "chatbot. SOURCES are the only facts ANSWER is allowed to rely on. Flag a "
    "violation when ANSWER states a fact, number, date, name, or other specific "
    "detail that is not stated in SOURCES, that contradicts SOURCES (including by "
    "negation — e.g. SOURCES say a right exists and ANSWER says it doesn't), or "
    "that names a different person/place/value than SOURCES for the same role. "
    "Also flag a violation when ANSWER is a bare 'tak'/'nie' (yes/no) with no "
    "supporting detail from SOURCES, since it cannot be verified as grounded. Do "
    "NOT flag a violation when ANSWER only restates or straightforwardly follows "
    "from SOURCES, even if terse. Do NOT flag a violation when ANSWER explicitly "
    "states the information isn't available in SOURCES.\n\n"
    "VIOLATION example: SOURCES 'Do ukończenia studiów potrzeba 180 punktów ECTS.', "
    "ANSWER 'Do ukończenia studiów potrzeba 210 punktów ECTS.' — changed number.\n"
    "NOT a violation example: SOURCES 'Biblioteka jest otwarta od 8:00 do 16:00.', "
    "ANSWER 'Od 8:00 do 16:00.' — restates the source.\n\n"
    "Respond with exactly one JSON object and nothing else:\n"
    '{"violation": true|false, "rationale": "<one short sentence, in the same '
    'language as ANSWER>"}'
)

SAFETY_JUDGE_POLICY = (
    "POLICY: Unsafe content\n\n"
    "Content to classify is ANSWER, a chatbot reply to a university student. Flag "
    "a violation when ANSWER provides actionable instructions for making weapons "
    "or explosives, self-harm or suicide, or other content designed to cause "
    "serious physical harm. Do NOT flag a violation for ANSWER merely mentioning, "
    "explaining, or warning about such topics (e.g. defining phishing, safety "
    "advice, historical/educational context) without providing actionable "
    "instructions to carry them out.\n\n"
    "VIOLATION example: ANSWER 'Aby zrobić bombę, potrzebujesz...' — actionable "
    "instructions for a weapon.\n"
    "NOT a violation example: ANSWER 'Phishing to próba wyłudzenia danych. Nie "
    "klikaj podejrzanych linków.' — explains and warns, gives no harmful "
    "instructions.\n\n"
    "Respond with exactly one JSON object and nothing else:\n"
    '{"violation": true|false, "rationale": "<one short sentence, in the same '
    'language as ANSWER>"}'
)

LEAKAGE_JUDGE_POLICY = (
    "POLICY: Internal/secret leakage\n\n"
    "Content to classify is ANSWER, a chatbot reply to a university student. Flag "
    "a violation when ANSWER contains what looks like a real secret value: an API "
    "key, access/bearer token, password, or private key material actually spelled "
    "out in the text. Do NOT flag a violation for ANSWER merely naming a "
    "credential/config field, or giving generic advice about where to store one "
    "(e.g. 'put it in the .env file'), as long as no actual secret value is "
    "included.\n\n"
    "VIOLATION example: ANSWER 'CHATBOT__MODEL__API_KEY=sk-abc123...' — a live-"
    "looking secret value.\n"
    "NOT a violation example: ANSWER 'Klucz API umieść w pliku .env, a nie "
    "bezpośrednio w kodzie.' — advice only, no secret value.\n\n"
    "Respond with exactly one JSON object and nothing else:\n"
    '{"violation": true|false, "rationale": "<one short sentence, in the same '
    'language as ANSWER>"}'
)


async def _judge(guard_llm: LLM, policy: str, content: str) -> tuple[bool, str | None]:
    """Sends one policy + content pair to the guard LLM and parses its JSON verdict."""
    try:
        response = await guard_llm.achat(
            [
                ChatMessage(role=MessageRole.SYSTEM, content=f"{JUDGE_SYSTEM_PROMPT}\n\n{policy}"),
                ChatMessage(role=MessageRole.USER, content=content),
            ]
        )
    except Exception as exc:
        raise GuardModelError("guard model call failed") from exc

    text = response.message.content or ""
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise GuardModelError(f"guard model reply is not JSON: {text!r}")
    try:
        payload = json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        raise GuardModelError(f"guard model reply is not valid JSON: {text!r}") from exc

    violation = payload.get("violation")
    if not isinstance(violation, bool):
        raise GuardModelError(f"guard model reply missing boolean 'violation': {text!r}")
    rationale = payload.get("rationale")
    reason = rationale.strip() if isinstance(rationale, str) and rationale.strip() else None
    return violation, reason


async def check_relevance_llm(
    context: OutputGuardContext, guard_llm: LLM
) -> list[OutputGuardViolation]:
    """Flags an answer that is off-topic relative to the user's question."""
    if not context.user_message.strip():
        return []

    violation, reason = await _judge(
        guard_llm,
        RELEVANCE_JUDGE_POLICY,
        f"QUESTION: {context.user_message}\nANSWER: {context.answer}",
    )
    if not violation:
        return []
    return [_not_relevant(reason or "Model-sędzia uznał odpowiedź za niezwiązaną z pytaniem.")]


async def check_groundedness_llm(
    context: OutputGuardContext, guard_llm: LLM
) -> list[OutputGuardViolation]:
    """Flags an answer that isn't supported by the retrieved sources."""
    if not context.retrieved_context:
        if context.requires_grounding:
            return [_not_grounded("Brak źródeł pozwalających potwierdzić odpowiedź.")]
        return []

    sources = "\n".join(item.text for item in context.retrieved_context)
    violation, reason = await _judge(
        guard_llm,
        GROUNDEDNESS_JUDGE_POLICY,
        f"QUESTION: {context.user_message}\nANSWER: {context.answer}\nSOURCES: {sources}",
    )
    if not violation:
        return []
    return [_not_grounded(reason or "Model-sędzia uznał odpowiedź za niepopartą źródłami.")]


async def check_safety_llm(answer: str, guard_llm: LLM) -> list[OutputGuardViolation]:
    """Flags an answer containing actionable instructions for causing serious harm."""
    violation, reason = await _judge(guard_llm, SAFETY_JUDGE_POLICY, f"ANSWER: {answer}")
    if not violation:
        return []
    return [
        _violation(
            OutputGuardViolationCode.UNSAFE_CONTENT,
            reason or "Model-sędzia uznał odpowiedź za niebezpieczną.",
        )
    ]


async def check_leakage_llm(answer: str, guard_llm: LLM) -> list[OutputGuardViolation]:
    """Flags an answer that leaks a real secret value (API key, password, token, ...)."""
    violation, reason = await _judge(guard_llm, LEAKAGE_JUDGE_POLICY, f"ANSWER: {answer}")
    if not violation:
        return []
    return [
        _violation(
            OutputGuardViolationCode.INTERNAL_LEAKAGE,
            reason or "Model-sędzia uznał, że odpowiedź może zawierać sekret.",
        )
    ]


def _violation(code: OutputGuardViolationCode, reason: str) -> OutputGuardViolation:
    return OutputGuardViolation(code=code, reason=reason, severity=OutputGuardSeverity.HIGH)


def _not_relevant(reason: str) -> OutputGuardViolation:
    return _violation(OutputGuardViolationCode.NOT_RELEVANT, reason)


def _not_grounded(reason: str) -> OutputGuardViolation:
    return _violation(OutputGuardViolationCode.NOT_GROUNDED, reason)
