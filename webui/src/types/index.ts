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

// Pipeline steps for progress timeline
export const PIPELINE_STEPS = [
  "resolve_video",
  "generate_script",
  "synthesize_tts",
  "detect_scenes",
  "match_clips",
  "align_audio",
  "generate_subtitle",
  "translate_subtitles",
  "export_clips",
  "render_video",
] as const;

export type PipelineStep = typeof PIPELINE_STEPS[number];
