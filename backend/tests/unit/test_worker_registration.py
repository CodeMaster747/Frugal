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
