"""Job model and state machine for the batch queue. No I/O — pure data."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from video_batch.core.verify import QualityScore


class JobState(str, Enum):
    QUEUED = "Queued"
    PROBING = "Probing"
    ENCODING = "Encoding"
    VERIFYING = "Verifying"
    DONE = "Done"
    SKIPPED = "Skipped"
    FAILED = "Failed"
    CANCELLED = "Cancelled"
    QUALITY_CHECK_FAILED = "Quality check failed"


# States that mean the job is finished and won't transition further.
TERMINAL_STATES = {
    JobState.DONE,
    JobState.SKIPPED,
    JobState.FAILED,
    JobState.CANCELLED,
    JobState.QUALITY_CHECK_FAILED,
}

# Allowed forward transitions. QUEUED can also be reached again from
# ENCODING/VERIFYING when a quality-check retry restarts the encode.
_ALLOWED_TRANSITIONS: dict[JobState, set[JobState]] = {
    JobState.QUEUED: {JobState.PROBING, JobState.CANCELLED},
    JobState.PROBING: {JobState.ENCODING, JobState.SKIPPED, JobState.FAILED, JobState.CANCELLED},
    JobState.ENCODING: {
        JobState.VERIFYING, JobState.DONE, JobState.FAILED, JobState.CANCELLED,
    },
    JobState.VERIFYING: {
        JobState.DONE, JobState.QUALITY_CHECK_FAILED, JobState.ENCODING,
        JobState.FAILED, JobState.CANCELLED,
    },
    JobState.DONE: set(),
    JobState.SKIPPED: set(),
    JobState.FAILED: set(),
    JobState.CANCELLED: set(),
    JobState.QUALITY_CHECK_FAILED: set(),
}


def can_transition(current: JobState, target: JobState) -> bool:
    return target in _ALLOWED_TRANSITIONS.get(current, set())


@dataclass
class Job:
    id: str
    source_path: Path
    state: JobState = JobState.QUEUED
    progress: float = 0.0
    output_path: Path | None = None
    quality: QualityScore | None = None
    warnings: list[str] = field(default_factory=list)
    error: str | None = None
    attempts: int = 0
    # Display-only fields, filled in by the runner once the source is probed.
    source_codec: str = ""
    resolution: str = ""

    def set_state(self, target: JobState) -> None:
        if not can_transition(self.state, target):
            raise ValueError(f"Illegal job transition: {self.state} -> {target}")
        self.state = target


class JobQueue:
    """An ordered collection of Jobs, keyed by id for O(1) lookup/update."""

    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._order: list[str] = []

    def add(self, source_path: Path) -> Job:
        job_id = str(source_path)
        if job_id in self._jobs:
            return self._jobs[job_id]
        job = Job(id=job_id, source_path=source_path)
        self._jobs[job_id] = job
        self._order.append(job_id)
        return job

    def remove(self, job_id: str) -> None:
        if job_id in self._jobs:
            del self._jobs[job_id]
            self._order.remove(job_id)

    def clear(self) -> None:
        self._jobs.clear()
        self._order.clear()

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def jobs(self) -> list[Job]:
        return [self._jobs[jid] for jid in self._order]

    def pending_jobs(self) -> list[Job]:
        return [j for j in self.jobs() if j.state == JobState.QUEUED]

    def is_finished(self) -> bool:
        return all(j.state in TERMINAL_STATES for j in self.jobs())

    def __len__(self) -> int:
        return len(self._order)
