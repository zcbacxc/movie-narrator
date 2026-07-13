"""Gradio Blocks UI for Movie Narrator.

Launch with ``mn web`` or ``python -m movie_narrator.web``.

Design principles (from spec):
- Thin shell over ``build_context`` + ``run_pipeline`` — no second implementation.
- ``empty = no override``: form fields left blank do NOT shadow Settings.
- Cancel is cooperative (step boundaries only), via ``GradioController``.
- Uploads go to ``mn_web_*`` temp dirs, never to ``output/``.
- Single-job per session (re-entrancy guard via ``WebRun.status``).
"""

from __future__ import annotations

import gradio as gr

from .bridge import run_bridge
from .form import FormData
from .models import RunStatus, WebRun
from .utils import save_upload


def create_app() -> gr.Blocks:
    """Build and return the Gradio Blocks app."""

    with gr.Blocks(title="Movie Narrator", theme=gr.themes.Soft()) as app:
        gr.Markdown("# 🎬 Movie Narrator")
        gr.Markdown("One prompt → one narrated movie video.")

        web_run_state = gr.State(WebRun())

        with gr.Row():
            # ── Left column: form inputs ────────────────────
            with gr.Column(scale=2):
                movie_input = gr.Textbox(
                    label="Movie Name",
                    placeholder="e.g. 飞驰人生",
                    info="Required.",
                )
                style_input = gr.Textbox(label="Style", value="热血搞笑")
                with gr.Row():
                    duration_input = gr.Slider(
                        10, 300, value=60, step=5, label="Duration (s)"
                    )
                    format_input = gr.Radio(
                        ["16:9", "9:16"], value="16:9", label="Format"
                    )
                voice_input = gr.Textbox(
                    label="Voice (optional)",
                    placeholder="zh-CN-YunxiNeural",
                )

                with gr.Accordion("Video & Assets", open=False):
                    video_input = gr.File(
                        label="Source video (optional)",
                        file_types=["video"],
                    )
                    library_input = gr.Textbox(
                        label="Library dir (optional)",
                        placeholder="/path/to/movies",
                    )
                    bgm_input = gr.File(
                        label="BGM (optional)",
                        file_types=["audio"],
                    )
                    no_bgm_input = gr.Checkbox(
                        label="Disable BGM (overrides settings default)",
                        value=False,
                    )

                with gr.Accordion("Subtitles", open=False):
                    subtitle_lang_input = gr.Textbox(
                        label="Subtitle lang (e.g. en, ja, zh-TW)",
                        placeholder="empty = feature off",
                    )
                    subtitle_mode_input = gr.Radio(
                        ["original", "translated", "bilingual"],
                        value="original",
                        label="Subtitle mode",
                    )

                with gr.Accordion("Advanced", open=False):
                    research_input = gr.Checkbox(
                        label="Enable plot research", value=False
                    )
                    no_clips_input = gr.Checkbox(
                        label="Skip clips export", value=False
                    )
                    strict_input = gr.Checkbox(
                        label="Strict mode (abort on soft failure)",
                        value=False,
                    )
                    scene_threshold_input = gr.Number(
                        label="Scene threshold",
                        value=None,
                        placeholder="Settings default",
                    )
                    match_min_score_input = gr.Number(
                        label="Match min score",
                        value=None,
                        placeholder="Settings default",
                    )
                    translate_provider_input = gr.Textbox(
                        label="Translate provider",
                        value="",
                        placeholder="llm",
                    )
                    translate_retries_input = gr.Number(
                        label="Translate retries",
                        value=None,
                        placeholder="Settings default",
                    )

            # ── Right column: output ─────────────────────────
            with gr.Column(scale=3):
                status_md = gr.Markdown("Ready.")
                log_md = gr.Markdown(
                    "",
                    elem_classes=["log-output"],
                )
                video_output = gr.Video(label="Output Video")
                files_output = gr.File(
                    label="Artifacts",
                    file_count="multiple",
                )

                with gr.Row():
                    generate_btn = gr.Button(
                        "Generate", variant="primary"
                    )
                    cancel_btn = gr.Button(
                        "Cancel", variant="stop", interactive=False
                    )

        # ── Event handlers ─────────────────────────────────

        def _generate(
            movie, style, duration, format, voice,
            video, library, bgm, no_bgm,
            subtitle_lang, subtitle_mode,
            research, no_clips, strict,
            scene_threshold, match_min_score,
            translate_provider, translate_retries,
            web_run,
        ):
            # Re-entrancy guard: refuse if already running
            if web_run.status == RunStatus.RUNNING:
                return (
                    "⚠ A run is already in progress. Cancel it first.",
                    "", None, None, web_run,
                    gr.update(interactive=True),
                )

            # Save uploads to managed temp dirs
            video_path = save_upload(video) if video else None
            bgm_path = save_upload(bgm) if bgm else None

            form_data = FormData(
                movie=movie or "",
                style=style or "热血搞笑",
                duration=int(duration) if duration else 60,
                voice=voice or "",
                format=format or "16:9",
                video_path=video_path,
                library_dir=library or "",
                research=bool(research),
                bgm_path=bgm_path,
                no_bgm=bool(no_bgm),
                no_clips=bool(no_clips),
                strict=bool(strict),
                subtitle_lang=subtitle_lang or "",
                subtitle_mode=subtitle_mode or "original",
                scene_threshold=scene_threshold,
                match_min_score=match_min_score,
                translate_provider=translate_provider or "",
                translate_retries=(
                    int(translate_retries)
                    if translate_retries is not None
                    else None
                ),
            )

            # Reset web_run
            web_run.status = RunStatus.IDLE
            web_run.context = None
            web_run.controller = None
            web_run.current_step = ""
            web_run.error = ""
            web_run.video_path = None
            web_run.audio_path = None
            web_run.subtitle_path = None
            web_run.script_md_path = None
            web_run.output_dir = None

            yield from run_bridge(form_data, web_run)

        def _cancel(web_run):
            if web_run.controller is not None:
                web_run.controller.cancel()
            return web_run

        generate_btn.click(
            _generate,
            inputs=[
                movie_input, style_input, duration_input, format_input,
                voice_input, video_input, library_input, bgm_input,
                no_bgm_input, subtitle_lang_input, subtitle_mode_input,
                research_input, no_clips_input, strict_input,
                scene_threshold_input, match_min_score_input,
                translate_provider_input, translate_retries_input,
                web_run_state,
            ],
            outputs=[
                status_md, log_md, video_output, files_output,
                web_run_state, cancel_btn,
            ],
        )

        cancel_btn.click(_cancel, inputs=[web_run_state], outputs=[web_run_state])

    return app


def launch_web(
    host: str = "127.0.0.1",
    port: int = 7860,
    share: bool = False,
) -> None:
    """Build and launch the Gradio app."""
    app = create_app()
    app.launch(
        server_name=host,
        server_port=port,
        share=share,
        inbrowser=True,
        prevent_thread_lock=False,
    )
