import uuid
from datetime import UTC, datetime, timedelta
from typing import Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.calendar_sync import ConsistencyIssueCode, ConsistencyStatus


class ConsistencySession(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: uuid.UUID
    order: int = Field(gt=0)
    start: datetime
    end: datetime
    externally_deleted: bool = False

    @model_validator(mode="after")
    def validate_interval(self) -> Self:
        for name, value in (("start", self.start), ("end", self.end)):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"{name} must be timezone-aware")
        if self.start.astimezone(UTC) >= self.end.astimezone(UTC):
            raise ValueError("start must be before end")
        return self


class ConsistencyIssue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: ConsistencyIssueCode
    session_ids: tuple[uuid.UUID, ...]


class ConsistencyResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: ConsistencyStatus
    issues: tuple[ConsistencyIssue, ...]


class ConsistencyChecker(Protocol):
    def check(
        self,
        sessions: list[ConsistencySession],
        *,
        minimum_break_minutes: int = 0,
        deadline: datetime | None = None,
    ) -> ConsistencyResult: ...


class DefaultConsistencyChecker:
    def check(
        self,
        sessions: list[ConsistencySession],
        *,
        minimum_break_minutes: int = 0,
        deadline: datetime | None = None,
    ) -> ConsistencyResult:
        if minimum_break_minutes < 0:
            raise ValueError("minimum_break_minutes must not be negative")
        if deadline is not None and (
            deadline.tzinfo is None or deadline.utcoffset() is None
        ):
            raise ValueError("deadline must be timezone-aware")

        issues: list[ConsistencyIssue] = []
        active = [item for item in sessions if not item.externally_deleted]
        chronological = sorted(
            active,
            key=lambda item: (
                item.start.astimezone(UTC),
                item.end.astimezone(UTC),
                item.order,
                str(item.id),
            ),
        )
        ordered = sorted(active, key=lambda item: (item.order, str(item.id)))

        if [item.id for item in chronological] != [item.id for item in ordered]:
            issues.append(
                ConsistencyIssue(
                    code=ConsistencyIssueCode.invalid_session_order,
                    session_ids=tuple(item.id for item in ordered),
                )
            )

        required_break = timedelta(minutes=minimum_break_minutes)
        for previous, current in zip(chronological, chronological[1:], strict=False):
            previous_end = previous.end.astimezone(UTC)
            current_start = current.start.astimezone(UTC)
            ids = (previous.id, current.id)
            if current_start < previous_end:
                issues.append(
                    ConsistencyIssue(
                        code=ConsistencyIssueCode.session_overlap,
                        session_ids=ids,
                    )
                )
            elif required_break and current_start - previous_end < required_break:
                issues.append(
                    ConsistencyIssue(
                        code=ConsistencyIssueCode.minimum_break_violation,
                        session_ids=ids,
                    )
                )

        if deadline is not None and chronological:
            latest = max(
                chronological,
                key=lambda item: item.end.astimezone(UTC),
            )
            if latest.end.astimezone(UTC) > deadline.astimezone(UTC):
                issues.append(
                    ConsistencyIssue(
                        code=ConsistencyIssueCode.latest_session_after_deadline,
                        session_ids=(latest.id,),
                    )
                )

        issues.extend(
            ConsistencyIssue(
                code=ConsistencyIssueCode.externally_deleted_session,
                session_ids=(item.id,),
            )
            for item in sorted(
                (value for value in sessions if value.externally_deleted),
                key=lambda value: (value.order, str(value.id)),
            )
        )
        return ConsistencyResult(
            status=(
                ConsistencyStatus.inconsistent
                if issues
                else ConsistencyStatus.consistent
            ),
            issues=tuple(issues),
        )
