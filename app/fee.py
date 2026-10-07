"""주차 요금 계산.

화면의 실시간 요금 미터와 출차 시 DB 트리거가 모두 이 함수를 쓴다.
(트리거는 db.py에서 SQLite 함수로 등록한 parking_fee()를 통해 호출한다)

요금 규칙
- 경과 시간은 분 단위로 올림한다. (5분 1초 → 6분)
- 기본시간까지는 기본요금, 이후 추가단위시간마다 추가요금.
- 1일 최대요금이 있으면 24시간 단위로 그 금액을 넘지 않는다.
- 야간·주말 요금, 할인, 감면은 반영하지 않는다. (예상 요금)
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

DAY_MIN = 24 * 60


@dataclass(frozen=True)
class FeePolicy:
    is_free: bool = False
    basic_min: int = 0      # 기본시간(분)
    basic_fee: int = 0      # 기본요금(원)
    add_min: int = 0        # 추가단위시간(분)
    add_fee: int = 0        # 추가단위요금(원)
    day_max: int = 0        # 1일 최대요금(원), 0이면 없음

    @property
    def known(self) -> bool:
        """요금을 계산할 수 있는 정보가 있는지."""
        return self.is_free or self.basic_fee > 0 or self.add_fee > 0


def elapsed_minutes(started: datetime, ended: datetime) -> int:
    seconds = (ended - started).total_seconds()
    return max(0, math.ceil(seconds / 60))


def _fee_within_day(policy: FeePolicy, minutes: int) -> int:
    if minutes <= policy.basic_min or policy.add_min <= 0:
        fee = policy.basic_fee
    else:
        units = math.ceil((minutes - policy.basic_min) / policy.add_min)
        fee = policy.basic_fee + units * policy.add_fee
    if policy.day_max > 0:
        fee = min(fee, policy.day_max)
    return fee


def fee_for_minutes(policy: FeePolicy, minutes: int) -> int:
    if policy.is_free or not policy.known:
        return 0
    if policy.day_max <= 0:
        return _fee_within_day(policy, minutes)
    full_days, rest = divmod(minutes, DAY_MIN)
    fee = full_days * policy.day_max
    if rest > 0 or full_days == 0:
        fee += _fee_within_day(policy, rest)
    return fee


def next_increase_minute(policy: FeePolicy, minutes: int) -> int | None:
    """이 경과 시간(분)을 넘기면 요금이 오르는 시점. 오르지 않으면 None."""
    if policy.is_free or not policy.known:
        return None
    current = fee_for_minutes(policy, minutes)
    day = minutes // DAY_MIN
    # 오늘과 내일 블록의 요금 구간 경계를 차례로 확인한다.
    for d in (day, day + 1):
        base = d * DAY_MIN
        candidates = [base + policy.basic_min]
        if policy.add_min > 0:
            k = 1
            while policy.basic_min + k * policy.add_min < DAY_MIN:
                candidates.append(base + policy.basic_min + k * policy.add_min)
                k += 1
        candidates.append(base + DAY_MIN)
        for t in candidates:
            if t >= minutes and fee_for_minutes(policy, t + 1) > current:
                return t
    return None


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value)
