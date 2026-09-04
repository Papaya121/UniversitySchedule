from dataclasses import dataclass
from datetime import date, time


@dataclass(frozen=True, slots=True)
class Lesson:
    starts_at: time
    ends_at: time
    subject: str
    subgroup: int | None
    room: str | None = None
    teacher: str | None = None

    def fingerprint(self) -> tuple[str, ...]:
        return (
            self.starts_at.isoformat(timespec="minutes"),
            self.ends_at.isoformat(timespec="minutes"),
            self.subject,
            str(self.subgroup or 0),
            self.room or "",
            self.teacher or "",
        )


@dataclass(frozen=True, slots=True)
class DaySchedule:
    day: date
    lessons: tuple[Lesson, ...]

    def for_subgroup(self, subgroup: int) -> "DaySchedule":
        return DaySchedule(
            self.day,
            tuple(x for x in self.lessons if x.subgroup in (None, subgroup)),
        )

