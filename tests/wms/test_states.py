import pytest

from wms.states import (
    APPROVAL_TRANSITIONS,
    ORDER_TRANSITIONS,
    ApprovalStatus,
    InvalidTransition,
    OrderStatus,
    transition,
)


def test_order_happy_path() -> None:
    s = OrderStatus.OPEN
    for nxt in (
        OrderStatus.ASSIGNED,
        OrderStatus.PICKING,
        OrderStatus.PICKED,
        OrderStatus.PACKED,
        OrderStatus.SHIPPED,
    ):
        s = transition("order", ORDER_TRANSITIONS, s, nxt)
    assert s is OrderStatus.SHIPPED


@pytest.mark.parametrize(
    ("cur", "nxt"),
    [
        (OrderStatus.PICKING, OrderStatus.CANCELLED),
        (OrderStatus.SHIPPED, OrderStatus.OPEN),
        (OrderStatus.CANCELLED, OrderStatus.OPEN),
    ],
)
def test_order_invalid(cur: OrderStatus, nxt: OrderStatus) -> None:
    with pytest.raises(InvalidTransition):
        transition("order", ORDER_TRANSITIONS, cur, nxt)


def test_approval_terminal_states() -> None:
    for terminal in (
        ApprovalStatus.EXECUTED,
        ApprovalStatus.REJECTED,
        ApprovalStatus.EXPIRED,
        ApprovalStatus.FAILED,
    ):
        assert APPROVAL_TRANSITIONS[terminal] == set()
    with pytest.raises(InvalidTransition):
        transition("approval", APPROVAL_TRANSITIONS, ApprovalStatus.PENDING, ApprovalStatus.EXECUTED)
