"""Code-defined capability authorization for every execution lane.

The registry describes authority. Callers provide only trusted execution
context; model/tool arguments are passed separately and never influence policy.
"""

from __future__ import annotations

import inspect
import re
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from enum import Enum
from functools import wraps
from types import MappingProxyType
from typing import Any, Callable, Iterator, Mapping

from app.logger import logger
from app.observability import increment_counter, start_span, telemetry_scope


CAPABILITY_POLICY_VERSION = 1
_CAPABILITY_ID_RE = re.compile(r"^[a-z][a-z0-9_.:-]{0,79}$")


class CapabilityCategory(str, Enum):
    INTERNAL = "internal"
    SEARCH = "search"
    IMAGE = "image"
    MEMORY = "memory"
    ATTACHMENT = "attachment"
    EMAIL = "email"


class CapabilityEffect(str, Enum):
    READ_ONLY = "read_only"
    LOCAL_MUTATION = "local_mutation"
    EXTERNAL_MUTATION = "external_mutation"


class CapabilityRisk(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ApprovalRequirement(str, Enum):
    NONE = "none"
    WORKFLOW_APPROVAL = "workflow_approval"
    REQUEST_SCOPED_AUTHORIZATION = "request_scoped_authorization"
    EXPLICIT_CONFIRMATION = "explicit_confirmation"
    FORBIDDEN = "forbidden"


class CapabilitySource(str, Enum):
    DIRECT_TOOL = "direct_tool"
    AGENT = "agent"
    WORKFLOW = "workflow"
    HTTP = "http"
    SYSTEM = "system"
    MCP = "mcp"
    AUTOMATION = "automation"
    WEBHOOK = "webhook"


class PolicyDecisionType(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_APPROVAL = "require_approval"


@dataclass(frozen=True)
class CapabilitySpec:
    capability_id: str
    category: CapabilityCategory
    effect: CapabilityEffect
    risk: CapabilityRisk
    approval: ApprovalRequirement
    requires_owner: bool
    allowed_sources: frozenset[CapabilitySource]
    network_access: bool = False
    accesses_user_data: bool = False
    idempotent: bool = False
    timeout_seconds: float | None = None


@dataclass(frozen=True)
class CapabilityContext:
    owner: str | None
    source: CapabilitySource
    job_id: str | None = None
    workflow_id: str | None = None
    workflow_status: str | None = None
    workflow_approval_state: str | None = None
    workflow_lease_valid: bool = False
    request_authorization_verified: bool = False
    cancel_requested: bool = False


@dataclass(frozen=True)
class PolicyDecision:
    decision: PolicyDecisionType
    reason: str
    capability: CapabilitySpec | None


class CapabilityRegistry:
    """Mutable only during construction, then exposed as a frozen mapping."""

    def __init__(self) -> None:
        self._specs: dict[str, CapabilitySpec] = {}
        self._frozen = False

    def register(self, spec: CapabilitySpec) -> None:
        if self._frozen:
            raise RuntimeError("capability_registry_frozen")
        capability_id = str(spec.capability_id or "").strip()
        if not _CAPABILITY_ID_RE.fullmatch(capability_id):
            raise ValueError("capability_id_required")
        if capability_id in self._specs:
            raise ValueError("duplicate_capability")
        self._specs[capability_id] = spec

    def freeze(self) -> "CapabilityRegistry":
        self._specs = MappingProxyType(dict(self._specs))  # type: ignore[assignment]
        self._frozen = True
        return self

    def get(self, capability_id: str) -> CapabilitySpec | None:
        return self._specs.get(str(capability_id or ""))

    @property
    def specs(self) -> Mapping[str, CapabilitySpec]:
        return self._specs

    @property
    def frozen(self) -> bool:
        return self._frozen

    def __len__(self) -> int:
        return len(self._specs)


class CapabilityPolicy:
    def __init__(self, registry: CapabilityRegistry) -> None:
        if not registry.frozen:
            raise ValueError("capability_registry_must_be_frozen")
        self.registry = registry

    def evaluate(self, capability_id: str, context: CapabilityContext) -> PolicyDecision:
        spec = self.registry.get(capability_id)
        if spec is None:
            return self._trace(
                PolicyDecision(PolicyDecisionType.DENY, "unknown_capability", None),
                capability_id,
                context,
            )
        if not isinstance(context.source, CapabilitySource):
            return self._trace(
                PolicyDecision(PolicyDecisionType.DENY, "unknown_source", spec),
                capability_id,
                context,
            )
        if context.source not in spec.allowed_sources:
            return self._trace(
                PolicyDecision(PolicyDecisionType.DENY, "source_not_allowed", spec),
                capability_id,
                context,
            )
        if spec.requires_owner and not str(context.owner or "").strip():
            return self._trace(
                PolicyDecision(PolicyDecisionType.DENY, "owner_required", spec),
                capability_id,
                context,
            )
        if spec.effect == CapabilityEffect.EXTERNAL_MUTATION and not str(context.owner or "").strip():
            return self._trace(
                PolicyDecision(PolicyDecisionType.DENY, "owner_required", spec),
                capability_id,
                context,
            )
        if spec.effect == CapabilityEffect.EXTERNAL_MUTATION and context.cancel_requested:
            return self._trace(
                PolicyDecision(PolicyDecisionType.DENY, "workflow_cancelled", spec),
                capability_id,
                context,
            )
        if spec.effect == CapabilityEffect.EXTERNAL_MUTATION and context.source == CapabilitySource.WORKFLOW:
            if context.workflow_status != "running" or not context.workflow_lease_valid:
                return self._trace(
                    PolicyDecision(PolicyDecisionType.DENY, "workflow_lease_invalid", spec),
                    capability_id,
                    context,
                )
        if spec.approval == ApprovalRequirement.FORBIDDEN:
            return self._trace(
                PolicyDecision(PolicyDecisionType.DENY, "capability_forbidden", spec),
                capability_id,
                context,
            )
        if spec.approval == ApprovalRequirement.WORKFLOW_APPROVAL:
            if context.source != CapabilitySource.WORKFLOW:
                return self._trace(
                    PolicyDecision(PolicyDecisionType.DENY, "source_not_allowed", spec),
                    capability_id,
                    context,
                )
            if context.workflow_approval_state != "approved":
                return self._trace(
                    PolicyDecision(PolicyDecisionType.REQUIRE_APPROVAL, "workflow_not_approved", spec),
                    capability_id,
                    context,
                )
        if spec.approval == ApprovalRequirement.REQUEST_SCOPED_AUTHORIZATION:
            if context.source == CapabilitySource.WORKFLOW and context.workflow_approval_state != "approved":
                return self._trace(
                    PolicyDecision(PolicyDecisionType.REQUIRE_APPROVAL, "workflow_not_approved", spec),
                    capability_id,
                    context,
                )
            if not context.request_authorization_verified:
                return self._trace(
                    PolicyDecision(
                        PolicyDecisionType.REQUIRE_APPROVAL,
                        "external_mutation_not_authorized",
                        spec,
                    ),
                    capability_id,
                    context,
                )
        if spec.approval == ApprovalRequirement.EXPLICIT_CONFIRMATION:
            return self._trace(
                PolicyDecision(PolicyDecisionType.REQUIRE_APPROVAL, "approval_required", spec),
                capability_id,
                context,
            )
        return self._trace(
            PolicyDecision(PolicyDecisionType.ALLOW, "allowed", spec),
            capability_id,
            context,
        )

    @staticmethod
    def _trace(
        decision: PolicyDecision,
        capability_id: str,
        context: CapabilityContext,
    ) -> PolicyDecision:
        spec = decision.capability
        source = context.source.value if isinstance(context.source, CapabilitySource) else "unknown"
        safe_capability_id = (
            spec.capability_id
            if spec
            else str(capability_id or "unknown").strip().lower()
        )
        if not _CAPABILITY_ID_RE.fullmatch(safe_capability_id):
            safe_capability_id = "unknown"
        logger.info(
            "[CapabilityTrace] capability=%s source=%s decision=%s reason=%s risk=%s effect=%s has_workflow=%s has_job=%s",
            safe_capability_id,
            source,
            decision.decision.value,
            decision.reason,
            spec.risk.value if spec else "unknown",
            spec.effect.value if spec else "unknown",
            bool(context.workflow_id),
            bool(context.job_id),
        )
        increment_counter(
            "helper.capability.invocations",
            {
                "capability": safe_capability_id,
                "source": source,
                "decision": decision.decision.value,
                "reason": decision.reason,
                "risk": spec.risk.value if spec else "unknown",
                "effect": spec.effect.value if spec else "unknown",
            },
        )
        return decision


class CapabilityDeniedError(RuntimeError):
    """Controlled failure containing policy metadata but no invocation arguments."""

    def __init__(self, capability: str, decision: PolicyDecision) -> None:
        self.capability = capability
        self.reason = decision.reason
        self.decision = decision.decision
        super().__init__(f"{decision.decision.value}:{decision.reason}")


class CapabilityGateway:
    """Immutable capability-to-handler bindings plus execution-time policy."""

    def __init__(
        self,
        bindings: Mapping[str, Callable[..., Any]],
        *,
        policy: CapabilityPolicy | None = None,
    ) -> None:
        self.policy = policy or CAPABILITY_POLICY
        reviewed: dict[str, Callable[..., Any]] = {}
        for capability_id, handler in bindings.items():
            if self.policy.registry.get(capability_id) is None:
                raise ValueError("unknown_capability_binding")
            if capability_id in reviewed:
                raise ValueError("duplicate_capability_binding")
            if not callable(handler):
                raise TypeError("capability_handler_must_be_callable")
            reviewed[capability_id] = handler
        self._bindings = MappingProxyType(reviewed)

    @property
    def bindings(self) -> Mapping[str, Callable[..., Any]]:
        return self._bindings

    def invoke(
        self,
        capability_id: str,
        *,
        context: CapabilityContext,
        arguments: Mapping[str, Any] | None = None,
    ) -> Any:
        invocation_arguments = dict(arguments or {})
        spec = self.policy.registry.get(capability_id)
        handler = self._bindings.get(capability_id)
        if handler is None:
            decision = (
                self.policy.evaluate(capability_id, context)
                if spec is None
                else self.policy._trace(
                    PolicyDecision(PolicyDecisionType.DENY, "handler_not_registered", spec),
                    capability_id,
                    context,
                )
            )
            raise CapabilityDeniedError(capability_id, decision)
        argument_owner = invocation_arguments.get("owner") or invocation_arguments.get("user_id")
        if (
            spec
            and spec.requires_owner
            and argument_owner
            and context.owner
            and str(argument_owner).strip().lower() != str(context.owner).strip().lower()
        ):
            decision = self.policy._trace(
                PolicyDecision(PolicyDecisionType.DENY, "owner_mismatch", spec),
                capability_id,
                context,
            )
            raise CapabilityDeniedError(capability_id, decision)
        decision = self.policy.evaluate(capability_id, context)
        if decision.decision != PolicyDecisionType.ALLOW:
            raise CapabilityDeniedError(capability_id, decision)
        token = _ACTIVE_CAPABILITY_CONTEXT.set(context)
        try:
            with telemetry_scope(job_id=context.job_id, workflow_id=context.workflow_id, source="capability"):
                with start_span(
                    "helper.capability.invoke",
                    {
                        "helper.capability.id": capability_id,
                        "helper.capability.source": context.source.value,
                        "helper.capability.risk": spec.risk.value if spec else "unknown",
                        "helper.capability.effect": spec.effect.value if spec else "unknown",
                    },
                ):
                    return handler(**invocation_arguments)
        finally:
            _ACTIVE_CAPABILITY_CONTEXT.reset(token)


_ACTIVE_CAPABILITY_CONTEXT: ContextVar[CapabilityContext | None] = ContextVar(
    "active_capability_context",
    default=None,
)


def current_capability_context() -> CapabilityContext | None:
    return _ACTIVE_CAPABILITY_CONTEXT.get()


@contextmanager
def capability_scope(context: CapabilityContext) -> Iterator[None]:
    token = _ACTIVE_CAPABILITY_CONTEXT.set(context)
    try:
        yield
    finally:
        _ACTIVE_CAPABILITY_CONTEXT.reset(token)


def capability_handler(
    capability_id: str,
    *,
    default_source: CapabilitySource,
    on_denied: Callable[[CapabilityDeniedError], Any] | None = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Bind one reviewed handler to one capability at definition time."""

    def decorate(handler: Callable[..., Any]) -> Callable[..., Any]:
        gateway = CapabilityGateway({capability_id: handler})
        signature = inspect.signature(handler)

        @wraps(handler)
        def wrapped(*args: Any, **kwargs: Any) -> Any:
            bound = signature.bind(*args, **kwargs)
            bound.apply_defaults()
            context = current_capability_context()
            if context is None:
                owner = bound.arguments.get("owner") or bound.arguments.get("user_id")
                if not owner:
                    try:
                        from app.logic.memory import user_context

                        owner = user_context.get()
                    except (ImportError, AttributeError):
                        owner = None
                context = CapabilityContext(owner=str(owner) if owner else None, source=default_source)
            try:
                return gateway.invoke(capability_id, context=context, arguments=bound.arguments)
            except CapabilityDeniedError as exc:
                if on_denied is None:
                    raise
                return on_denied(exc)

        wrapped.capability_id = capability_id  # type: ignore[attr-defined]
        return wrapped

    return decorate


def _sources(*values: CapabilitySource) -> frozenset[CapabilitySource]:
    return frozenset(values)


def _build_registry() -> CapabilityRegistry:
    registry = CapabilityRegistry()
    specs = (
        CapabilitySpec("assistant.respond", CapabilityCategory.INTERNAL, CapabilityEffect.READ_ONLY, CapabilityRisk.LOW, ApprovalRequirement.NONE, False, _sources(CapabilitySource.WORKFLOW)),
        CapabilitySpec("web.search", CapabilityCategory.SEARCH, CapabilityEffect.READ_ONLY, CapabilityRisk.MEDIUM, ApprovalRequirement.NONE, False, _sources(CapabilitySource.DIRECT_TOOL, CapabilitySource.AGENT, CapabilitySource.WORKFLOW), network_access=True, timeout_seconds=45),
        CapabilitySpec("image.search", CapabilityCategory.IMAGE, CapabilityEffect.READ_ONLY, CapabilityRisk.MEDIUM, ApprovalRequirement.NONE, False, _sources(CapabilitySource.DIRECT_TOOL, CapabilitySource.AGENT, CapabilitySource.WORKFLOW), network_access=True, timeout_seconds=30),
        CapabilitySpec("image.generate", CapabilityCategory.IMAGE, CapabilityEffect.READ_ONLY, CapabilityRisk.MEDIUM, ApprovalRequirement.NONE, False, _sources(CapabilitySource.DIRECT_TOOL, CapabilitySource.AGENT, CapabilitySource.WORKFLOW), network_access=True, timeout_seconds=120),
        CapabilitySpec("image.upscale", CapabilityCategory.IMAGE, CapabilityEffect.LOCAL_MUTATION, CapabilityRisk.MEDIUM, ApprovalRequirement.NONE, False, _sources(CapabilitySource.DIRECT_TOOL, CapabilitySource.AGENT, CapabilitySource.WORKFLOW, CapabilitySource.SYSTEM), network_access=True, timeout_seconds=180),
        CapabilitySpec("image.proxy.read", CapabilityCategory.IMAGE, CapabilityEffect.READ_ONLY, CapabilityRisk.MEDIUM, ApprovalRequirement.NONE, False, _sources(CapabilitySource.HTTP), network_access=True, timeout_seconds=60),
        CapabilitySpec("memory.read", CapabilityCategory.MEMORY, CapabilityEffect.READ_ONLY, CapabilityRisk.MEDIUM, ApprovalRequirement.NONE, True, _sources(CapabilitySource.DIRECT_TOOL, CapabilitySource.AGENT, CapabilitySource.HTTP, CapabilitySource.SYSTEM), accesses_user_data=True),
        CapabilitySpec("memory.write", CapabilityCategory.MEMORY, CapabilityEffect.LOCAL_MUTATION, CapabilityRisk.MEDIUM, ApprovalRequirement.NONE, True, _sources(CapabilitySource.DIRECT_TOOL, CapabilitySource.AGENT, CapabilitySource.SYSTEM), accesses_user_data=True),
        CapabilitySpec("attachment.read", CapabilityCategory.ATTACHMENT, CapabilityEffect.READ_ONLY, CapabilityRisk.MEDIUM, ApprovalRequirement.NONE, True, _sources(CapabilitySource.HTTP, CapabilitySource.WORKFLOW, CapabilitySource.SYSTEM), accesses_user_data=True),
        CapabilitySpec("attachment.write", CapabilityCategory.ATTACHMENT, CapabilityEffect.LOCAL_MUTATION, CapabilityRisk.MEDIUM, ApprovalRequirement.NONE, True, _sources(CapabilitySource.HTTP, CapabilitySource.SYSTEM), accesses_user_data=True),
        CapabilitySpec("email.draft.build", CapabilityCategory.EMAIL, CapabilityEffect.READ_ONLY, CapabilityRisk.LOW, ApprovalRequirement.NONE, False, _sources(CapabilitySource.DIRECT_TOOL, CapabilitySource.AGENT, CapabilitySource.WORKFLOW), accesses_user_data=True),
        CapabilitySpec("email.draft.update", CapabilityCategory.EMAIL, CapabilityEffect.READ_ONLY, CapabilityRisk.LOW, ApprovalRequirement.NONE, False, _sources(CapabilitySource.WORKFLOW), accesses_user_data=True),
        CapabilitySpec("email.attachment.add", CapabilityCategory.EMAIL, CapabilityEffect.READ_ONLY, CapabilityRisk.MEDIUM, ApprovalRequirement.NONE, False, _sources(CapabilitySource.WORKFLOW), accesses_user_data=True),
        CapabilitySpec("email.deliver", CapabilityCategory.EMAIL, CapabilityEffect.EXTERNAL_MUTATION, CapabilityRisk.HIGH, ApprovalRequirement.REQUEST_SCOPED_AUTHORIZATION, True, _sources(CapabilitySource.WORKFLOW, CapabilitySource.HTTP), network_access=True, accesses_user_data=True, idempotent=True, timeout_seconds=120),
    )
    for spec in specs:
        registry.register(spec)
    return registry.freeze()


CAPABILITY_REGISTRY = _build_registry()
CAPABILITY_POLICY = CapabilityPolicy(CAPABILITY_REGISTRY)


WORKFLOW_ACTION_CAPABILITY_IDS: Mapping[str, str] = MappingProxyType({
    "web_search": "web.search",
    "image_search": "image.search",
    "image_generate": "image.generate",
    "build_email_draft": "email.draft.build",
    "update_email_draft": "email.draft.update",
    "attach_image": "email.attachment.add",
    "deliver_email": "email.deliver",
    "general_response": "assistant.respond",
})

ACTIVE_TOOL_CAPABILITY_IDS: Mapping[str, str] = MappingProxyType({
    "web_search_text": "web.search",
    "build_email_draft_tool": "email.draft.build",
    "image_generate_tool": "image.generate",
    "image_search_tool": "image.search",
    "recall_memory": "memory.read",
    "archive_insight": "memory.write",
})

INTERNAL_PURE_TOOL_IDS = frozenset({"calculate_horoscope", "analyze_palm_lines"})


def workflow_capability_id(action_type: Any) -> str:
    value = getattr(action_type, "value", action_type)
    capability_id = WORKFLOW_ACTION_CAPABILITY_IDS.get(str(value or ""))
    if capability_id is None:
        raise CapabilityDeniedError(
            str(value or "unknown"),
            PolicyDecision(PolicyDecisionType.DENY, "unknown_capability", None),
        )
    return capability_id


def capability_diagnostics() -> dict[str, int]:
    return {
        "policy_version": CAPABILITY_POLICY_VERSION,
        "registered_capabilities": len(CAPABILITY_REGISTRY),
    }
