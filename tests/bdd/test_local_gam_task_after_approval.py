"""BDD binding for the locally-added feature on a buyer's task after the seller's approval.

Grades get_task_status and list_tasks for a create_media_buy a Google Ad Manager seller
approved by hand: the task is completed and carries the media buy, and the task list holds
the buyer's operation only. The real GoogleAdManager adapter runs, with only Google's SOAP
client stood in (``GamSellerMixin.sell_through_gam``). Graded on a2a, mcp and rest.
"""

from __future__ import annotations

from pytest_bdd import scenarios

scenarios("features/local-gam-task-after-approval.feature")
