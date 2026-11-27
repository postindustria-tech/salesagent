# Hand-authored feature — not compiled from adcp-req.
#
# LOCALLY-ADDED (survives BR-*.feature regeneration).
#
# What it grades: what a buyer reads from get_task_status and list_tasks for a
# create_media_buy that a Google Ad Manager seller approved by hand.
#
# AdCP 3.2.7 enums/task-status.json: `input-required` is "Task is paused and waiting for input
# from the user (e.g., clarification, approval)" and `completed` is "Task has been successfully
# completed". So the task of a buy the seller approved, and booked in the ad server, is
# `completed`. protocol/get-task-status-response.json, `result`: "Canonical task-specific
# terminal payload. Present when include_result was true and a completed, failed, or rejected
# task produced a terminal artifact". protocol/list-tasks-response.json, `tasks[].task_type`:
# "Type of AdCP operation", a member of enums/task-type.json. The seller's own step that
# activates the order in Google Ad Manager is no AdCP operation, so it is not a task a buyer
# lists.
#
# The sales-guaranteed storyboard's create_media_buy step grades the submitted task and stops
# there: no storyboard approves a buy as the seller and then reads its task, so these scenarios
# are ungraded by the storyboards. The real GoogleAdManager adapter runs, with only Google's
# SOAP client stood in. The buyer observes the outcome on the two task responses.
#
# Transports: a2a, mcp and rest. The live server has no stand-in GAM network, so the setup
# declares itself unrealizable on e2e (GamSellerMixin.sell_through_gam).
#
# @source repo=adcp ref=v3.2.7 path=dist/schemas/3.2.7/enums/task-status.json
# @source repo=adcp ref=v3.2.7 path=dist/schemas/3.2.7/protocol/get-task-status-response.json pointer=/properties/result
# @source repo=adcp ref=v3.2.7 path=dist/schemas/3.2.7/protocol/list-tasks-response.json pointer=/properties/tasks
Feature: A buyer reads the task of a media buy a Google Ad Manager seller approved (local)

  Background:
    Given a valid create_media_buy request
    And the account exists and is active
    And the seller's guaranteed products have pricing options:
      | product_id | pricing_option_id | pricing_model | fixed_price | floor_price |
      | prod_a     | cpm_usd_standard  | cpm           | 12.00       |             |
      | prod_b     | cpm_usd_standard  | cpm           | 12.00       |             |
    And the seller sells through Google Ad Manager as trafficker "2000001" on ad unit "4000001", and the buyer is its advertiser "3000001"
    And the tenant requires manual approval
    And the buyer's packages, over a 14-day flight, are:
      | product_id | pricing_option_id | budget | bid_price |
      | prod_a     | cpm_usd_standard  | 600    |           |
      | prod_b     | cpm_usd_standard  | 420    |           |

  @T-UC-002-local-gam-task-completed-by-approval @extension
  Scenario: The task of a buy the seller approved is completed and carries the media buy
    When the Buyer Agent sends the create_media_buy request
    Then the response status should be "submitted"
    When the seller approves the media buy
    And the Buyer Agent asks for the status of the task the create returned, with its result
    Then the task reads "completed" for the "create_media_buy" operation
    And the task's result is the approved media buy, not yet started, with 2 packages

  @T-UC-002-local-gam-task-list-after-approval @extension
  Scenario: The task list of a buyer whose buy the seller approved holds the buyer's operation only
    When the Buyer Agent sends the create_media_buy request
    Then the response status should be "submitted"
    When the seller approves the media buy
    And the Buyer Agent lists its tasks, remembering the task the create returned
    Then the task list holds only the task the create returned, as "completed" for the "create_media_buy" operation
