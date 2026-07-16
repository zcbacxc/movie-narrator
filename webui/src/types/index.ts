export interface FormSubmitData {
  movie: string;
  style: string;
  duration: number;
  voice: string;
  format: "16:9" | "9:16";
  library_dir: string;
  research: boolean;
  no_bgm: boolean;
  no_clips: boolean;
  strict: boolean;
  subtitle_lang: string;
  subtitle_mode: "original" | "translated" | "bilingual";
  scene_threshold?: number;
  match_min_score?: number;
  translate_provider: string;
  translate_retries?: number;
}

export type TaskStatus = "running" | "done" | "failed" | "cancelled";

export interface TaskCreateResponse {
  task_id: string;
  status: string;
}

export interface TaskStatusResponse {
  task_id: string;
  status: TaskStatus;
  current_step?: string;
  error?: string;
  artifacts?: string[];
  video_path?: string;
}

export interface WsMessage {
  type: "progress" | "terminal";
  step?: string;
  version?: number;
  log?: string;
  status?: TaskStatus;
  error?: string;
  artifacts?: string[];
  video_path?: string;
}

// Pipeline steps for progress timeline — must match runner.py STEPS list
export const PIPELINE_STEPS = [
  "resolve_video",
  "prepare_assets",
  "research_plot",
  "generate_script",
  "export_script_md",
  "generate_voice",
  "align_audio",
  "detect_scenes",
  "match_clips",
  "mix_bgm",
  "translate_subtitles",
  "generate_subtitle",
  "render_video",
  "export_clips",
] as const;

export type PipelineStep = typeof PIPELINE_STEPS[number];
