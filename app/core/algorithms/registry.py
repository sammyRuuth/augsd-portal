"""Algorithm registry for timetable generation"""

from typing import Type

from app.core.algorithms.backtrack import BacktrackAlgorithm
from app.core.algorithms.backtrack_optimized import BacktrackOptimizedAlgorithm
from app.core.algorithms.base import (
    AlgorithmType,
    GenerationConstraints,
    TimetableAlgorithm,
)
from app.core.algorithms.genetic import GeneticAlgorithm
from app.core.algorithms.greedy import GreedyAlgorithm
from app.core.algorithms.hybrid import HybridAlgorithm, ParallelBestAlgorithm
from app.core.algorithms.parallel_race import ParallelRaceAlgorithm
from app.core.algorithms.random_algorithms import (
    RandomAlgorithm,
    RandomRestartAlgorithm,
    SimulatedAnnealingAlgorithm,
)


class AlgorithmRegistry:
    """Registry of available timetable generation algorithms"""

    _algorithms: dict[str, Type[TimetableAlgorithm]] = {
        AlgorithmType.GREEDY: GreedyAlgorithm,
        AlgorithmType.BACKTRACK: BacktrackAlgorithm,
        AlgorithmType.BACKTRACK_OPTIMIZED: BacktrackOptimizedAlgorithm,
        AlgorithmType.GENETIC: GeneticAlgorithm,
        AlgorithmType.RANDOM: RandomAlgorithm,
        AlgorithmType.RANDOM_RESTART: RandomRestartAlgorithm,
        AlgorithmType.SIMULATED_ANNEALING: SimulatedAnnealingAlgorithm,
        AlgorithmType.HYBRID: HybridAlgorithm,
        AlgorithmType.PARALLEL_BEST: ParallelBestAlgorithm,
        AlgorithmType.PARALLEL_RACE: ParallelRaceAlgorithm,
    }

    @classmethod
    def get(
        cls,
        algorithm_type: str | AlgorithmType,
        constraints: GenerationConstraints | None = None,
    ) -> TimetableAlgorithm:
        """Get an instance of the specified algorithm"""
        if isinstance(algorithm_type, str):
            algorithm_type = algorithm_type.lower()

        if algorithm_type not in cls._algorithms:
            raise ValueError(
                f"Unknown algorithm: {algorithm_type}. "
                f"Available: {list(cls._algorithms.keys())}"
            )

        return cls._algorithms[algorithm_type](constraints)

    @classmethod
    def list_algorithms(cls) -> list[dict[str, str]]:
        """List available algorithms with descriptions"""
        result = []
        for name, algo_class in cls._algorithms.items():
            result.append(
                {
                    "id": name if isinstance(name, str) else name.value,
                    "name": algo_class.name,
                    "description": algo_class.description,
                }
            )
        return result

    @classmethod
    def register(cls, name: str, algorithm_class: Type[TimetableAlgorithm]):
        """Register a new algorithm"""
        cls._algorithms[name] = algorithm_class


def get_algorithm(
    algorithm_type: str = "backtrack_optimized",
    constraints: GenerationConstraints | None = None,
) -> TimetableAlgorithm:
    """Convenience function to get an algorithm instance"""
    return AlgorithmRegistry.get(algorithm_type, constraints)
