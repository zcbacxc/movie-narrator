# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

from importlib.metadata import version

__version__ = version("movie-narrator")

# ── Public SDK surface (v0.5+) ─────────────────────────────
# These symbols allow external code to extend the engine:
#
#   from movie_narrator import register_step, Context
#   from movie_narrator import register_tts, register_vision
#   from movie_narrator import register_llm, register_research
#   from movie_narrator import Plugin, PluginContext, load_plugin
#   from movie_narrator import discover_plugins  # entry_points discovery
#
# The contract module is the single import surface. We re-export
# here for convenience so plugins don't need to know the internal
# module path.

from .contract import (  # noqa: F401
    CONTRACT_VERSION,
    check_version,
    Context,
    Plugin,
    PluginContext,
    ProviderRegistry,
    ResearchInfo,
    Services,
    Step,
    StepRegistry,
    build_context,
    discover_plugins,
    get_preset,
    list_available_plugins,
    list_presets,
    load_plugin,
    register_step,
    register_tts,
    register_vision,
    register_llm,
    register_research,
    run_pipeline,
    step,
    step_registry,
    tts_registry,
    vision_registry,
    llm_registry,
    research_registry,
    # Cloud / Task Queue (v0.6.0)
    CancelController,
    LocalTaskQueue,
    ProgressConsole,
    Task,
    TaskProgress,
    TaskQueue,
    TaskRequest,
    TaskResult,
    TaskStatus,
    TaskStorage,
    run_task,
    # Cloud / Remote Inference (v0.6.1)
    RemoteTaskQueue,
    RemoteQueueError,
    TaskAPIServer,
    WorkerDaemon,
    download_artifact,
    download_all_artifacts,
    list_artifacts,
    register_remote_llm,
    register_remote_tts,
    run_daemon,
)
