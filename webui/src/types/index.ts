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
  narration_preset: string;
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
  "validate_deliverable",
  "export_clips",
] as const;

export type PipelineStep = typeof PIPELINE_STEPS[number];

// Narration presets — must match presets/registry.py
// Note: "" (empty) and "douyin-fast" are functionally equivalent
// (douyin-fast is the default). We only show "douyin-fast" to avoid
// confusion; empty value is used internally as "no selection".
export const NARRATION_PRESETS = [
  { value: "douyin-fast", label: "抖音快节奏", description: "18句×3.3s, 快切镜, 深度闪避" },
  { value: "mainstream-dry", label: "主流干讲", description: "12句×5s, 慢切镜, 从容叙事" },
  { value: "bilibili-long", label: "B站长解说", description: "8句×7.5s, 大场景合并, 书面风格" },
] as const;
