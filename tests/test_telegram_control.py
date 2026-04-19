from aitrader.telegram_control import TelegramControl
from aitrader.types import SystemMode


def test_killswitch_has_highest_priority():
    tg = TelegramControl(mode=SystemMode.RUNNING)
    tg.handle_command("/pause")
    assert tg.mode == SystemMode.PAUSED
    tg.handle_command("/killswitch")
    assert tg.mode == SystemMode.KILLED
    tg.handle_command("/resume")
    assert tg.mode == SystemMode.KILLED
