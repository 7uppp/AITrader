from __future__ import annotations

from dataclasses import dataclass

from .types import OrderState


VALID_TRANSITIONS: dict[OrderState, set[OrderState]] = {
    OrderState.INTENT_CREATED: {OrderState.RISK_APPROVED, OrderState.REJECTED},
    OrderState.RISK_APPROVED: {OrderState.SUBMIT_PENDING, OrderState.REJECTED},
    OrderState.SUBMIT_PENDING: {OrderState.ACKED, OrderState.REJECTED, OrderState.RECOVERED},
    OrderState.ACKED: {OrderState.PARTIALLY_FILLED, OrderState.FILLED, OrderState.REJECTED},
    OrderState.PARTIALLY_FILLED: {OrderState.FILLED, OrderState.REJECTED, OrderState.RECOVERED},
    OrderState.FILLED: {OrderState.EXIT_MANAGED, OrderState.CLOSED},
    OrderState.EXIT_MANAGED: {OrderState.CLOSED, OrderState.RECOVERED},
    OrderState.REJECTED: set(),
    OrderState.CLOSED: set(),
    OrderState.RECOVERED: {OrderState.CLOSED, OrderState.REJECTED},
}


@dataclass(slots=True)
class OrderStateMachine:
    state: OrderState = OrderState.INTENT_CREATED

    def can_transition(self, to_state: OrderState) -> bool:
        return to_state in VALID_TRANSITIONS[self.state]

    def transition(self, to_state: OrderState) -> None:
        if not self.can_transition(to_state):
            raise ValueError(f"invalid state transition: {self.state} -> {to_state}")
        self.state = to_state
