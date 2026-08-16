"""Tests for classifying service-control failures."""

from __future__ import annotations

from conftest import result
from yakg.backend.classify import NO_AGENT_MESSAGE, classify_failure, outcome_from_result
from yakg.backend.process import RETURNCODE_TIMED_OUT

FAILURE = "Could not start the Kerio VPN service."


class TestClassifyFailure:
    def test_interactive_authorization_is_no_agent_not_cancelled(self) -> None:
        outcome = classify_failure(
            result(
                returncode=1,
                stderr="org.freedesktop.DBus.Error.InteractiveAuthorizationRequired",
            ),
            FAILURE,
        )

        assert not outcome.ok
        assert not outcome.cancelled
        assert outcome.message == NO_AGENT_MESSAGE
        assert "InteractiveAuthorizationRequired" in outcome.detail

    def test_access_denied_is_cancelled(self) -> None:
        outcome = classify_failure(result(returncode=1, stderr="Access denied\n"), FAILURE)

        assert not outcome.ok
        assert outcome.cancelled
        assert outcome.message == ""

    def test_pkexec_dismissed_is_cancelled(self) -> None:
        outcome = classify_failure(
            result(
                returncode=126,
                stderr="Error executing command as another user: Request dismissed",
            ),
            FAILURE,
        )

        assert not outcome.ok
        assert outcome.cancelled
        assert outcome.message == ""

    def test_pkexec_not_authorized_is_a_failure(self) -> None:
        outcome = classify_failure(
            result(
                returncode=127,
                stderr="Error executing command as another user: Not authorized",
            ),
            FAILURE,
        )

        assert not outcome.ok
        assert not outcome.cancelled
        assert outcome.message == FAILURE

    def test_pkexec_no_agent_is_no_agent(self) -> None:
        outcome = classify_failure(
            result(
                returncode=127,
                stderr="Error executing command as another user: No authentication agent found.",
            ),
            FAILURE,
        )

        assert not outcome.ok
        assert not outcome.cancelled
        assert outcome.message == NO_AGENT_MESSAGE

    def test_timeout_mentions_how_long_it_waited(self) -> None:
        outcome = classify_failure(
            result(
                returncode=RETURNCODE_TIMED_OUT,
                timed_out=True,
                error="The command did not finish within 120 seconds.",
            ),
            FAILURE,
        )

        assert not outcome.ok
        assert not outcome.cancelled
        assert outcome.message == FAILURE
        assert "120" in outcome.detail

    def test_unrecognised_stderr_still_fails_and_keeps_the_output(self) -> None:
        outcome = classify_failure(
            result(returncode=1, stderr="Job for kerio-kvc.service failed.\n"),
            FAILURE,
        )

        assert not outcome.ok
        assert not outcome.cancelled
        assert outcome.message == FAILURE
        assert "Job for kerio-kvc.service failed." in outcome.detail

    def test_missing_executable_uses_the_runner_error(self) -> None:
        outcome = classify_failure(
            result(
                returncode=-1,
                executed=False,
                error="systemctl is not installed or not on PATH.",
            ),
            FAILURE,
        )

        assert not outcome.ok
        assert "systemctl" in outcome.detail


class TestOutcomeFromResult:
    def test_success(self) -> None:
        outcome = outcome_from_result(
            result(returncode=0),
            success_message="Started the Kerio VPN service.",
            failure_message=FAILURE,
        )

        assert outcome.ok
        assert not outcome.cancelled
        assert outcome.message == "Started the Kerio VPN service."
