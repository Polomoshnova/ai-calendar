class SchedulePlanError(Exception):
    pass


class SchedulePlanValidationError(SchedulePlanError):
    pass


class SchedulePlanNotFoundError(SchedulePlanError):
    pass


class SchedulePlanUserNotFoundError(SchedulePlanError):
    pass


class InvalidPlanTransitionError(SchedulePlanError):
    pass


class SchedulePlanImmutableError(SchedulePlanError):
    pass
