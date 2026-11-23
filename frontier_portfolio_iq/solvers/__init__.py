"""Multi-solver layer: common interface, engines, independent validation and confidence reporting."""
from .base import MilpMatrix, NormalizedResult, SolverSettings, SolveStatus, compile_problem
from .compiled import CompiledModel, compile_portfolio, to_portfolio_result
from .orchestrate import (ENGINES, EngineRun, SolverConfidenceReport, engine_inventory, make_engine,
                          solve_validated)
from .validate import ValidationReport, validate_result, validate_solution

__all__ = ["MilpMatrix", "NormalizedResult", "SolverSettings", "SolveStatus", "compile_problem",
           "CompiledModel", "compile_portfolio", "to_portfolio_result", "ENGINES", "EngineRun",
           "SolverConfidenceReport", "engine_inventory", "make_engine", "solve_validated",
           "ValidationReport", "validate_result", "validate_solution"]
