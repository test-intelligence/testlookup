# EXP-BUG-109 — Failed digests falsified delivery history

**Mission / severity:** M18, P0.

The scheduled dispatcher advanced the successful watermark and count before
calling a provider. A failed send was lost and the following delta omitted its
window. Claims now advance only the next slot; success owns the watermark and
count, while a known provider failure retains both and schedules a bounded
retry.
