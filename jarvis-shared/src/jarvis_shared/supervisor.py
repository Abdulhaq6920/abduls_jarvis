"""Supervisor control-flow stages.

The supervisor must always distinguish these six+1 stages and never collapse them
into one uncontrolled LLM call. The backend emits a trace of these stages for every
request so activity is observable and auditable.
"""
from __future__ import annotations

from enum import Enum


class SupervisorStage(str, Enum):
    OBSERVE = "OBSERVE"   # ingest the request + current context
    PLAN = "PLAN"         # decide which agent(s)/tool(s) are needed
    PROPOSE = "PROPOSE"   # produce a concrete proposed action
    APPROVE = "APPROVE"   # policy gate; may require human approval
    EXECUTE = "EXECUTE"   # run the approved action
    VERIFY = "VERIFY"     # check the result is sane
    REPORT = "REPORT"     # format the spoken/visual response
