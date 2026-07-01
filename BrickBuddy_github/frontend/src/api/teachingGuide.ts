import type {
  GuidesData,
  LessonPlansData,
  ProgressStatus,
  ProjectMetadata,
  UploadFile
} from '../types/teachingGuide'

interface ProjectResponse {
  project_id: string
}

export interface SimulationFramePayload {
  current_time: number
  duration?: number | null
  width?: number
  height?: number
  frame_data_url?: string
  source?: 'simulation' | 'glasses' | string
  source_url?: string
  stream_url?: string
}

interface GlassesStreamFrameResponse {
  frame: SimulationFramePayload
}

declare const process:
  | {
      env?: Record<string, string | undefined>
    }
  | undefined

function trimTrailingSlash(value: string): string {
  return value.replace(/\/+$/, '')
}

function getDefaultApiBaseUrl(): string {
  if (typeof process === 'undefined') return ''
  return process.env?.EXPO_PUBLIC_API_BASE_URL || 'http://127.0.0.1:5005'
}

function getFormPart(file: UploadFile): UploadFile | unknown {
  return file.file || file
}

export function createTeachingGuideApi(baseUrl = getDefaultApiBaseUrl()) {
  const apiRoot = `${trimTrailingSlash(baseUrl)}/api`

  async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
    const response = await fetch(`${apiRoot}${path}`, init)
    if (!response.ok) {
      const message = await response.text()
      throw new Error(message || `Request failed with ${response.status}`)
    }
    return response.json() as Promise<T>
  }

  return {
    health(): Promise<{ status: string; service?: string }> {
      return requestJson('/health')
    },

    async createProject(): Promise<string> {
      const data = await requestJson<ProjectResponse>('/projects', { method: 'POST' })
      return data.project_id
    },

    async uploadFiles(projectId: string, pdf: UploadFile, stepsJson?: UploadFile): Promise<ProjectMetadata> {
      const form = new FormData()
      form.append('pdf', getFormPart(pdf) as never)
      if (stepsJson) {
        form.append('steps_json', getFormPart(stepsJson) as never)
      }
      return requestJson<ProjectMetadata>(`/projects/${projectId}/upload`, {
        method: 'POST',
        body: form
      })
    },

    getProject(projectId: string): Promise<ProjectMetadata> {
      return requestJson<ProjectMetadata>(`/projects/${projectId}`)
    },

    async captureGlassesStreamFrame(payload: {
      stream_url: string
      current_time?: number
      max_width?: number
    }): Promise<SimulationFramePayload> {
      const data = await requestJson<GlassesStreamFrameResponse>('/agent/glasses-stream/frame', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      })
      return data.frame
    },

    getImageUrl(projectId: string, filename: string): string {
      return `${apiRoot}/projects/${projectId}/images/${encodeURIComponent(filename)}`
    },

    startConversion(projectId: string): Promise<{ message: string; total_steps: number }> {
      return requestJson(`/projects/${projectId}/convert`, { method: 'POST' })
    },

    getConversionStatus(projectId: string): Promise<ProgressStatus> {
      return requestJson<ProgressStatus>(`/projects/${projectId}/convert/status`)
    },

    getGuides(projectId: string): Promise<GuidesData> {
      return requestJson<GuidesData>(`/projects/${projectId}/guides`)
    },

    saveGuides(projectId: string, data: GuidesData): Promise<{ message: string }> {
      return requestJson(`/projects/${projectId}/guides`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data)
      })
    },

    saveMaterials(projectId: string, content: string): Promise<{ message: string }> {
      return requestJson(`/projects/${projectId}/materials`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content })
      })
    },

    async getMaterials(projectId: string): Promise<string> {
      const data = await requestJson<{ content: string }>(`/projects/${projectId}/materials`)
      return data.content
    },

    startGeneration(projectId: string): Promise<{ message: string; total_steps: number }> {
      return requestJson(`/projects/${projectId}/generate`, { method: 'POST' })
    },

    getGenerationStatus(projectId: string): Promise<ProgressStatus> {
      return requestJson<ProgressStatus>(`/projects/${projectId}/generate/status`)
    },

    getLessonPlans(projectId: string): Promise<LessonPlansData> {
      return requestJson<LessonPlansData>(`/projects/${projectId}/lesson-plans`)
    },

    saveLessonPlans(projectId: string, data: LessonPlansData): Promise<{ message: string }> {
      return requestJson(`/projects/${projectId}/lesson-plans`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data)
      })
    }
  }
}
