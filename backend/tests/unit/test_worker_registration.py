"""Every task module is registered, and every scheduled name resolves.

This file exists because the failure it catches is silent and has now cost this
project twice. `celery_app.autodiscover_tasks(..., related_name=X)` matches a
single filename, so a task module missing from that tuple is **never
registered** -- the task queues fine, the dispatcher returns an id, and nothing
ever runs it. The file's own comment records the first occurrence; the second
was `"scraping"`, which shipped absent while `beat_schedule` published
`app.workers.tasks.scraping.refresh_scrapes` nightly to a worker that had never
imported it.

Nothing else in the suite can see this. Integration tests call the task
functions directly, which works whether or not Celery knows about them, and the
scraper's runtime is unreachable by design while `ALLOWED_HOSTS` is empty. These
three assertions are the only detector.
"""

from __future__ import annotations

import ast
import pathlib

BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[2]
TASKS_DIR = BACKEND_ROOT / "app" / "workers" / "tasks"
RUNNER = BACKEND_ROOT / "scripts" / "run_jobs.py"


def _registered_modules() -> set[str]:
    """The names in the `autodiscover_tasks` loop, read from the source.

    Parsed rather than imported: importing `celery_app` pulls in the whole
    worker entrypoint, and the thing under test is a literal in that file.
    `ast` also survives `ruff format` reflowing the tuple across lines -- which
    is exactly how the `"scraping"` edit was lost.
    """
    source = (BACKEND_ROOT / "app" / "workers" / "celery_app.py").read_text()
    tree = ast.parse(source)

    for node in ast.walk(tree):
        if not isinstance(node, ast.For):
            continue
        if not isinstance(node.target, ast.Name) or node.target.id != "_module":
            continue
        return {
            element.value
            for element in node.iter.elts  # type: ignore[attr-defined]
            if isinstance(element, ast.Constant) and isinstance(element.value, str)
        }

    raise AssertionError("could not find the `for _module in (...)` loop in celery_app.py")


def _task_modules_on_disk() -> set[str]:
    return {path.stem for path in TASKS_DIR.glob("*.py") if path.stem != "__init__"}


def _scheduled_task_names() -> set[str]:
    """Task names referenced by `beat_schedule`, and by the dispatch constants."""
    source = (BACKEND_ROOT / "app" / "core" / "queue.py").read_text()
    tree = ast.parse(source)

    names: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and (node.value.startswith("app.workers.tasks."))
        ):
            names.add(node.value)
    return names


def _split(names: set[str]) -> tuple[set[str], set[str]]:
    """Routing globs (`...module.*`) and concrete task names.

    `task_routes` keys are globs and name no function; `beat_schedule` entries
    and the dispatch constants are concrete. Both must resolve to a real
    module, but only the concrete ones name a function.
    """
    globs = {n for n in names if n.endswith(".*")}
    return globs, names - globs


def test_every_task_module_is_registered():
    """A module on disk but not in the tuple is a task that never runs."""
    missing = _task_modules_on_disk() - _registered_modules()
    assert not missing, (
        f"task modules absent from `autodiscover_tasks` in celery_app.py: {sorted(missing)}. "
        "Their tasks will queue and never run, and nothing else in this suite can tell."
    )


def test_every_registered_module_exists():
    """The other direction: a name in the tuple with no file is a silent no-op."""
    missing = _registered_modules() - _task_modules_on_disk()
    assert not missing, f"`autodiscover_tasks` names modules that do not exist: {sorted(missing)}"


def test_every_scheduled_task_resolves_to_a_registered_module():
    """A beat entry pointing at an unregistered module fires into nothing.

    Checked against the *module* rather than the task object because importing
    every task module here would pull Prophet and OpenCV into a unit test.
    """
    registered = _registered_modules()

    unresolved = set()
    for name in _scheduled_task_names():
        # app.workers.tasks.<module>.<task-or-glob>
        parts = name.split(".")
        if len(parts) < 5 or parts[3] not in registered:
            unresolved.add(name)

    assert not unresolved, (
        f"scheduled or dispatched task names whose module is not registered: {sorted(unresolved)}"
    )


def test_every_scheduled_task_function_exists():
    """The name in `beat_schedule` matches a function that is actually defined.

    A typo here is as silent as a missing module: Celery accepts any string.
    Read with `ast` so no task module is imported.
    """
    _, concrete = _split(_scheduled_task_names())

    missing = set()
    for name in concrete:
        module_name, _, function_name = name.rpartition(".")
        module_file = TASKS_DIR / f"{module_name.split('.')[-1]}.py"
        if not module_file.exists():
            missing.add(name)
            continue

        tree = ast.parse(module_file.read_text())
        defined = {
            node.name
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        }
        if function_name not in defined:
            missing.add(name)

    assert not missing, f"task names with no matching function: {sorted(missing)}"


def _runner_sweeps() -> set[tuple[str, str]]:
    """The `(module, function)` pairs `scripts/run_jobs.py` will call.

    Read with `ast` for the same reason as everything else in this file:
    importing the runner would import every task module it names, and the thing
    under test is a literal in that file.
    """
    tree = ast.parse(RUNNER.read_text())

    pairs: set[tuple[str, str]] = set()
    for node in ast.walk(tree):
        targets = (
            node.targets
            if isinstance(node, ast.Assign)
            else [node.target]
            if isinstance(node, ast.AnnAssign)
            else []
        )
        named = any(isinstance(t, ast.Name) and t.id in {"HOURLY", "NIGHTLY"} for t in targets)
        if not named or node.value is None:
            continue
        for element in getattr(node.value, "elts", []):
            if (
                isinstance(element, ast.Tuple)
                and len(element.elts) == 2
                and all(isinstance(e, ast.Constant) for e in element.elts)
            ):
                pairs.add((element.elts[0].value, element.elts[1].value))
    return pairs


def test_every_runner_sweep_resolves_to_a_real_function():
    """The runner calls private functions by name, so a typo is silent.

    `getattr(module, "_run")` on a name that does not exist raises inside a cron
    job nobody is watching. This is the same class of failure the rest of this
    file exists for, moved to the mechanism that now actually executes the
    scheduled work.
    """
    pairs = _runner_sweeps()
    assert pairs, "no HOURLY/NIGHTLY sweeps found in scripts/run_jobs.py"

    missing = set()
    for module_name, function_name in pairs:
        module_file = TASKS_DIR / f"{module_name}.py"
        if not module_file.exists():
            missing.add(f"{module_name}.{function_name} (no such module)")
            continue

        tree = ast.parse(module_file.read_text())
        defined = {
            node.name
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        }
        if function_name not in defined:
            missing.add(f"{module_name}.{function_name}")

    assert not missing, f"runner sweeps with no matching function: {sorted(missing)}"


def _beat_schedule_task_names() -> set[str]:
    """Only the names `beat_schedule` actually puts on a clock.

    `_scheduled_task_names` above deliberately sweeps up *every* task-name
    constant in queue.py, which includes the ones dispatched on demand from an
    HTTP request -- `process_receipt`, `generate_forecast`, `promote_receipt`.
    Those have no schedule, so asking whether a periodic runner covers them
    asserts something that was never true.

    Found by shape rather than by variable name: a dict whose values are dicts
    carrying a `"task"` key is the beat schedule, and matching on the shape
    survives the constant being renamed or reflowed.
    """
    tree = ast.parse((BACKEND_ROOT / "app" / "core" / "queue.py").read_text())

    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        for entry in node.values:
            if not isinstance(entry, ast.Dict):
                continue
            for key, value in zip(entry.keys, entry.values, strict=False):
                if (
                    isinstance(key, ast.Constant)
                    and key.value == "task"
                    and isinstance(value, ast.Constant)
                    and isinstance(value.value, str)
                ):
                    names.add(value.value)
    return names


def test_the_runner_covers_every_scheduled_module():
    """Nothing that beat used to run may be left with no executor.

    `beat_schedule` is documentation rather than a mechanism now -- no beat
    process is deployed anywhere -- so a module scheduled there and absent from
    `scripts/run_jobs.py` is a sweep that has silently stopped happening. That
    is this repository's recurring failure, moved to the mechanism that now
    actually runs the work.

    Compared at module level because beat names the *task* while the runner
    calls the *private* function beneath it, and no static check can map one to
    the other.
    """
    scheduled = _beat_schedule_task_names()
    assert scheduled, "no beat_schedule entries found in queue.py"

    scheduled_modules = {n.split(".")[3] for n in scheduled if len(n.split(".")) >= 5}
    runner_modules = {module for module, _ in _runner_sweeps()}

    orphaned = scheduled_modules - runner_modules
    assert not orphaned, (
        f"modules with a beat schedule but no runner sweep: {sorted(orphaned)}. "
        "Nothing executes them: no beat process is deployed."
    )
