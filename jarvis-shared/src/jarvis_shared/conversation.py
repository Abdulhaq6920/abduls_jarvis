"""The conversation lifecycle state machine states (shared vocabulary)."""
from __future__ import annotations

from enum import Enum


class ConversationState(str, Enum):
    """
    IDLE -> WAKE_DETECTED -> LISTENING -> PROCESSING -> RESPONDING
         -> ACTIVE_CONVERSATION -> (back to LISTENING without wake word) -> IDLE

    While in ACTIVE_CONVERSATION the user does not need to repeat the wake word until
    the configurable conversation timeout elapses, which returns the FSM to IDLE.
    """

    IDLE = "IDLE"
    WAKE_DETECTED = "WAKE_DETECTED"
    LISTENING = "LISTENING"
    PROCESSING = "PROCESSING"
    RESPONDING = "RESPONDING"
    ACTIVE_CONVERSATION = "ACTIVE_CONVERSATION"
    PAUSED = "PAUSED"  # microphone paused by the user
