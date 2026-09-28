from pathlib import Path

import pytest

from video_batch.core.job_queue import Job, JobQueue, JobState, can_transition


def test_add_creates_queued_job():
    queue = JobQueue()

    job = queue.add(Path("clip.mp4"))

    assert job.state == JobState.QUEUED
    assert len(queue) == 1


def test_add_is_idempotent_for_same_path():
    queue = JobQueue()

    job1 = queue.add(Path("clip.mp4"))
    job2 = queue.add(Path("clip.mp4"))

    assert job1 is job2
    assert len(queue) == 1


def test_jobs_preserve_insertion_order():
    queue = JobQueue()
    queue.add(Path("b.mp4"))
    queue.add(Path("a.mp4"))

    assert [j.source_path.name for j in queue.jobs()] == ["b.mp4", "a.mp4"]


def test_remove_drops_job():
    queue = JobQueue()
    job = queue.add(Path("clip.mp4"))

    queue.remove(job.id)

    assert len(queue) == 0
    assert queue.get(job.id) is None


def test_pending_jobs_only_returns_queued():
    queue = JobQueue()
    job1 = queue.add(Path("a.mp4"))
    job2 = queue.add(Path("b.mp4"))
    job1.set_state(JobState.PROBING)

    assert queue.pending_jobs() == [job2]


def test_valid_transition_succeeds():
    job = Job(id="x", source_path=Path("clip.mp4"))

    job.set_state(JobState.PROBING)
    job.set_state(JobState.ENCODING)
    job.set_state(JobState.VERIFYING)
    job.set_state(JobState.DONE)

    assert job.state == JobState.DONE


def test_invalid_transition_raises():
    job = Job(id="x", source_path=Path("clip.mp4"))

    with pytest.raises(ValueError):
        job.set_state(JobState.DONE)


def test_terminal_state_has_no_further_transitions():
    job = Job(id="x", source_path=Path("clip.mp4"), state=JobState.DONE)

    with pytest.raises(ValueError):
        job.set_state(JobState.QUEUED)


def test_verifying_can_loop_back_to_encoding_for_retry():
    assert can_transition(JobState.VERIFYING, JobState.ENCODING) is True


def test_is_finished_true_when_all_jobs_terminal():
    queue = JobQueue()
    job1 = queue.add(Path("a.mp4"))
    job2 = queue.add(Path("b.mp4"))
    job1.set_state(JobState.PROBING)
    job1.set_state(JobState.SKIPPED)
    job2.set_state(JobState.PROBING)
    job2.set_state(JobState.FAILED)

    assert queue.is_finished() is True


def test_is_finished_false_while_a_job_is_queued():
    queue = JobQueue()
    queue.add(Path("a.mp4"))

    assert queue.is_finished() is False
