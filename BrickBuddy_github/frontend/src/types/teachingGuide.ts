export interface StepGuide {
  step_index: number
  image_file: string
  parts_needed: string
  placement_instructions: string
}

export interface GuidesData {
  project_id: string
  total_steps: number
  steps: StepGuide[]
}

export interface LessonStep extends StepGuide {
  cultural_knowledge: string
  teaching_notes: string
}

export interface LessonPlansData {
  project_id: string
  total_steps: number
  teaching_materials: string
  steps: LessonStep[]
}

export interface ProgressStatus {
  status: 'not_started' | 'processing' | 'completed' | 'error'
  current_step: number
  total_steps: number
  error?: string
}

export interface ProjectMetadata {
  total_pages: number
  total_steps: number
  image_files: string[]
  has_step_data: boolean
}

export interface UploadFile {
  uri: string
  name: string
  type?: string
  file?: unknown
}
