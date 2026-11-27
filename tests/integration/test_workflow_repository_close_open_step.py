"""Integration tests for ``WorkflowRepository.close_open_step_for_object``.

The repository contract behind a buyer's task after the seller's approval: the approval
writer ends the open step of the buyer's operation that is linked to the media buy. BDD
grades the outcome on the wire (``local-gam-task-after-approval.feature``) but reaches the
writer directly, so the step is still ``requires_approval`` there. In a deployment the admin
route has already written ``approved``, and which stored statuses count as open is a rule
of this repository method that only a row-level test can pin.
"""

from __future__ import annotations

import pytest

from src.core.database.repositories.workflow import WorkflowRepository
from src.core.schemas import CreateMediaBuySuccess, Package
from tests.harness._base import BareIntegrationEnv

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

_MEDIA_BUY = "mb_close_step_1"


def _result() -> CreateMediaBuySuccess:
    from datetime import UTC, datetime

    return CreateMediaBuySuccess.sync_success(
        media_buy_id=_MEDIA_BUY,
        packages=[Package(package_id="pkg_1")],
        confirmed_at=datetime(2026, 11, 27, tzinfo=UTC),
        revision=2,
    )


def _close(env: BareIntegrationEnv, tenant_id: str, **overrides):
    arguments = {
        "object_type": "media_buy",
        "object_id": _MEDIA_BUY,
        "tool_name": "create_media_buy",
        "status": "completed",
        "result": _result(),
    }
    arguments.update(overrides)
    return WorkflowRepository(env.get_session(), tenant_id).close_open_step_for_object(**arguments)


def _linked_step(tenant_id: str, *, status: str, tool_name: str = "create_media_buy"):
    from tests.factories import (
        ContextFactory,
        ObjectWorkflowMappingFactory,
        PrincipalFactory,
        TenantFactory,
        WorkflowStepFactory,
    )

    principal = PrincipalFactory(tenant=TenantFactory(tenant_id=tenant_id))
    step = WorkflowStepFactory(context=ContextFactory(principal=principal), status=status, tool_name=tool_name)
    ObjectWorkflowMappingFactory(step=step, object_id=_MEDIA_BUY)
    return step


class TestCloseOpenStepForObject:
    @pytest.mark.parametrize("stored", ["approved", "requires_approval", "pending", "in_progress"])
    def test_an_open_step_is_completed_with_the_result(self, integration_db, stored):
        with BareIntegrationEnv() as env:
            step = _linked_step("close_t1", status=stored)

            closed = _close(env, "close_t1")

            assert closed is not None and closed.step_id == step.step_id
            assert (closed.status, closed.response_data["media_buy_id"]) == ("completed", _MEDIA_BUY)
            assert closed.completed_at is not None

    def test_a_failed_close_stores_the_error_and_no_result(self, integration_db):
        with BareIntegrationEnv() as env:
            _linked_step("close_t2", status="approved")

            closed = _close(env, "close_t2", status="failed", result=None, error_message="order not created")

            assert closed is not None
            assert (closed.status, closed.error_message, closed.response_data) == ("failed", "order not created", None)

    @pytest.mark.parametrize("ended", ["completed", "failed", "rejected"])
    def test_a_step_that_already_ended_is_left_as_it_is(self, integration_db, ended):
        with BareIntegrationEnv() as env:
            step = _linked_step("close_t3", status=ended)

            assert _close(env, "close_t3") is None
            env.get_session().refresh(step)
            assert (step.status, step.response_data) == (ended, None)

    def test_the_step_of_another_tool_on_the_same_object_is_left_open(self, integration_db):
        with BareIntegrationEnv() as env:
            step = _linked_step("close_t4", status="approval", tool_name="activate_gam_order")

            assert _close(env, "close_t4") is None
            env.get_session().refresh(step)
            assert step.status == "approval"

    def test_another_tenants_step_is_out_of_reach(self, integration_db):
        from tests.factories import TenantFactory

        with BareIntegrationEnv() as env:
            step = _linked_step("close_t5", status="approved")
            TenantFactory(tenant_id="close_t6")

            assert _close(env, "close_t6") is None
            env.get_session().refresh(step)
            assert step.status == "approved"
