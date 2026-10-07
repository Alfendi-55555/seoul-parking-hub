// app/fee.py 와 같은 요금 계산. (GitHub Pages 버전은 서버가 없어서 브라우저에서 계산한다)
// 두 구현이 같은 값을 내는지는 tests/test_fee_js.py 가 확인한다.
(function (root) {
  const DAY_MIN = 24 * 60;

  const known = (p) => !!(p.is_free || p.basic_fee > 0 || p.add_fee > 0);

  function elapsedMinutes(startMs, endMs) {
    return Math.max(0, Math.ceil((endMs - startMs) / 60000));
  }

  function feeWithinDay(p, minutes) {
    let fee;
    if (minutes <= p.basic_min || p.add_min <= 0) fee = p.basic_fee;
    else fee = p.basic_fee + Math.ceil((minutes - p.basic_min) / p.add_min) * p.add_fee;
    if (p.day_max > 0) fee = Math.min(fee, p.day_max);
    return fee;
  }

  function feeForMinutes(p, minutes) {
    if (p.is_free || !known(p)) return 0;
    if (p.day_max <= 0) return feeWithinDay(p, minutes);
    const fullDays = Math.floor(minutes / DAY_MIN), rest = minutes % DAY_MIN;
    let fee = fullDays * p.day_max;
    if (rest > 0 || fullDays === 0) fee += feeWithinDay(p, rest);
    return fee;
  }

  function nextIncreaseMinute(p, minutes) {
    if (p.is_free || !known(p)) return null;
    const current = feeForMinutes(p, minutes);
    const day = Math.floor(minutes / DAY_MIN);
    for (const d of [day, day + 1]) {
      const base = d * DAY_MIN;
      const candidates = [base + p.basic_min];
      if (p.add_min > 0) {
        for (let k = 1; p.basic_min + k * p.add_min < DAY_MIN; k++) {
          candidates.push(base + p.basic_min + k * p.add_min);
        }
      }
      candidates.push(base + DAY_MIN);
      for (const t of candidates) {
        if (t >= minutes && feeForMinutes(p, t + 1) > current) return t;
      }
    }
    return null;
  }

  const api = { known, elapsedMinutes, feeForMinutes, nextIncreaseMinute };
  if (typeof module !== "undefined") module.exports = api;
  else root.Fee = api;
})(this);
