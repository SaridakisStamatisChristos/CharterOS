from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256

from charteros.domain.shared.exceptions import DomainValidationError
from charteros.simulation.types import PPM


@dataclass(frozen=True, slots=True)
class DeterministicSampler:
    """Stateless SHA-256 sampler keyed by seed, policy version, and semantic labels.

    Each outcome is addressed by a label instead of consuming mutable RNG state. Adding a sample
    in one part of the simulator therefore cannot shift unrelated outcomes elsewhere.
    """

    seed: int
    policy_version: str

    def __post_init__(self) -> None:
        if not isinstance(self.seed, int) or isinstance(self.seed, bool):
            raise DomainValidationError("simulation seed must be an integer")
        if not 0 <= self.seed <= 2**63 - 1:
            raise DomainValidationError("simulation seed must fit a non-negative signed int64")
        if not self.policy_version:
            raise DomainValidationError("policy_version cannot be empty")

    def u64(self, label: str) -> int:
        if not label:
            raise DomainValidationError("sampler label cannot be empty")
        payload = f"{self.policy_version}\x1f{self.seed}\x1f{label}".encode()
        return int.from_bytes(sha256(payload).digest()[:8], "big", signed=False)

    def integer(self, label: str, *, lower: int, upper: int) -> int:
        if upper < lower:
            raise DomainValidationError("sampler upper bound must be >= lower bound")
        span = upper - lower + 1
        return lower + self.u64(label) % span

    def sample_ppm(self, label: str) -> int:
        return self.u64(label) % PPM

    def probability_hit(self, label: str, probability_ppm: int) -> bool:
        if not 0 <= probability_ppm <= PPM:
            raise DomainValidationError(f"probability_ppm must be between 0 and {PPM}")
        if probability_ppm == 0:
            return False
        if probability_ppm == PPM:
            return True
        return self.sample_ppm(label) < probability_ppm
