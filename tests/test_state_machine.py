from aitrader.state_machine import OrderStateMachine
from aitrader.types import OrderState


def test_valid_state_progression():
    sm = OrderStateMachine()
    sm.transition(OrderState.RISK_APPROVED)
    sm.transition(OrderState.SUBMIT_PENDING)
    sm.transition(OrderState.ACKED)
    sm.transition(OrderState.FILLED)
    sm.transition(OrderState.EXIT_MANAGED)
    sm.transition(OrderState.CLOSED)
    assert sm.state == OrderState.CLOSED


def test_invalid_transition_rejected():
    sm = OrderStateMachine()
    try:
        sm.transition(OrderState.FILLED)
        raised = False
    except ValueError:
        raised = True
    assert raised
