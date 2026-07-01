import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Image,
  Pressable,
  SafeAreaView,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  useWindowDimensions,
  View
} from 'react-native'
import { StatusBar } from 'expo-status-bar'
import flvjs from 'flv.js'
import { createTeachingGuideApi } from '../api/teachingGuide'
import type { SimulationFramePayload } from '../api/teachingGuide'
import type { GuidesData, LessonPlansData } from '../types/teachingGuide'
import { LanguageToggle, useI18n, type TFunction } from '../i18n'

type ActivityStepStatus = 'pending' | 'active' | 'completed' | 'warning'
type SourceMode = 'simulation' | 'glasses' | 'rtmp'
type FeedbackTone = 'ok' | 'warning' | 'danger'

interface ModelOption {
  id: string
  label: string
  modelName: string
  capability: string
}

interface ModelCategory {
  id: string
  label: string
  description: string
  models: ModelOption[]
}

interface ActivityCheckpoint {
  id: string
  label: string
  expectation: string
  score: number
}

interface ActivityStep {
  id: string
  name: string
  goal: string
  estimatedMinutes: number
  checkpoints: ActivityCheckpoint[]
}

interface FeedbackItem {
  id: string
  tone: FeedbackTone
  title: string
  detail: string
  stepId: string
  timestamp: string
}

interface VlmPageEvent {
  time?: string
  frame_index?: number
  frameIndex?: number
  ifjudge?: number
  ifJudge?: number
  changepage?: number
  changePage?: number
  change_page?: number
  now_step?: number
  nowStep?: number
  state?: number
  reason?: string
}

interface VlmPageEventLogItem {
  id: string
  receivedAt: string
  event: VlmPageEvent
  applied: boolean
  targetStepIndex: number | null
}

interface VlmRunStatus {
  running?: boolean
  pid?: number | null
  started_at?: number | null
  command?: string[]
  source_mode?: SourceMode | string
  stream_url?: string | null
  video_path?: string
  default_interval_seconds?: number
  realtime?: {
    running?: boolean
    pid?: number | null
    started_at?: number | null
    command?: string[]
    source_mode?: SourceMode | string
    stream_url?: string | null
    default_video_fps?: number
    no_video?: boolean
  }
  already_running?: boolean
  error?: string
}

declare global {
  interface Window {
    __legoGlassApplyVlmOutput?: (event: VlmPageEvent) => void
  }
}

declare const process:
  | {
      env?: Record<string, string | undefined>
    }
  | undefined

const activityTitle = '乐高结构搭建与讲解活动'
const defaultProjectId = 'tiantan'
const defaultStreamUrl = 'http://172.20.10.9:8080/'
const publicRtmpPushUrl = 'rtmp://121.5.162.62/live/aiglass'
const publicRtmpFlvUrl = 'http://121.5.162.62:8000/live/aiglass.flv'
const simulationVideoFile = 'step8test.mp4'
const qwenVlmStartConfig = {
  start_realtime: true,
  include_final_frame: true,
  final_frame_offset_seconds: 0.5
}
const fallbackImageFiles = Array.from({ length: 8 }, (_, index) => `images/steps/page_${String(index + 1).padStart(2, '0')}.png`)
const vlmProgressIntervalMs = 15000
const stopLegacyAgentApi = 1
const defaultLegoBackendUrl = 'http://127.0.0.1:8765'

const modelCategories: ModelCategory[] = [
  {
    id: 'planner_model',
    label: '活动规划模型',
    description: '拆解任务、识别当前搭建阶段',
    models: [
      { id: 'gpt-5.5', label: 'gpt-5.5', modelName: 'gpt-5.5', capability: 'reasoning' },
      { id: 'o3', label: 'o3', modelName: 'o3', capability: 'reasoning' },
      { id: 'deepseek-r1', label: 'deepseek-r1', modelName: 'deepseek-r1', capability: 'reasoning' }
    ]
  },
  {
    id: 'vision_model',
    label: 'VLM 视觉模型',
    description: '分析第一视角画面和积木结构',
    models: [
      { id: 'gpt-5.5-vlm', label: 'gpt-5.5-vlm', modelName: 'gpt-5.5', capability: 'vision' },
      { id: 'gemini-2.5-pro', label: 'gemini-2.5-pro', modelName: 'gemini-2.5-pro', capability: 'vision' },
      { id: 'qwen-vl-max', label: 'qwen-vl-max', modelName: 'qwen-vl-max', capability: 'vision' }
    ]
  },
  {
    id: 'asr_model',
    label: 'ASR 语音识别',
    description: '识别学生讲解、教师口令和组内协作',
    models: [
      { id: 'doubao-streaming-asr', label: 'doubao-streaming-asr', modelName: 'Doubao Seed ASR', capability: 'streaming_asr' },
      { id: 'web-speech-recognition', label: 'web-speech-recognition', modelName: 'browser-asr', capability: 'asr' }
    ]
  },
  {
    id: 'coach_model',
    label: '实时反馈模型',
    description: '生成鼓励、纠错和下一步提示',
    models: [
      { id: 'gpt-5.5-coach', label: 'gpt-5.5-coach', modelName: 'gpt-5.5', capability: 'coaching' },
      { id: 'claude-sonnet-4-6', label: 'claude-sonnet-4-6', modelName: 'claude-sonnet-4-6', capability: 'coaching' },
      { id: 'web-speech-synthesis', label: 'web-speech-synthesis', modelName: 'browser-tts', capability: 'tts' }
    ]
  }
]

const defaultModelSelection = modelCategories.reduce<Record<string, string>>((selection, category) => {
  selection[category.id] = category.models[0]?.id || ''
  return selection
}, {})

const firstFallbackStepId = 'lesson-step-1'

function buildFallbackActivitySteps(imageFiles: string[], t: TFunction): ActivityStep[] {
  const files = imageFiles.length ? imageFiles : fallbackImageFiles
  return files.map((filename, index) => {
    const stepIndex = index + 1
    return {
      id: `lesson-step-${stepIndex}`,
      name: t('第 {{step}} 步', { step: stepIndex }),
      goal: t('等待 agent lesson plan 加载，当前对应 {{filename}}。', { filename }),
      estimatedMinutes: 4,
      checkpoints: [
        {
          id: `step-${stepIndex}-parts`,
          label: t('所需零件'),
          expectation: t('等待 agent 返回当前步骤零件。'),
          score: 4
        },
        {
          id: `step-${stepIndex}-placement`,
          label: t('摆放动作'),
          expectation: t('等待 agent 返回当前步骤摆放说明。'),
          score: 8
        },
        {
          id: `step-${stepIndex}-confirm`,
          label: t('完成确认'),
          expectation: t('等待视觉检查或学生确认。'),
          score: 4
        }
      ]
    }
  })
}

function formatClock(seconds: number): string {
  const safeSeconds = Math.max(0, Math.floor(seconds))
  const minutes = Math.floor(safeSeconds / 60)
  const remainder = safeSeconds % 60
  return `${String(minutes).padStart(2, '0')}:${String(remainder).padStart(2, '0')}`
}

function getStepScore(step: ActivityStep): number {
  return step.checkpoints.reduce((sum, checkpoint) => sum + checkpoint.score, 0)
}

function getTotalScore(steps: ActivityStep[]): number {
  return steps.reduce((sum, step) => sum + getStepScore(step), 0)
}

function getTimestamp(): string {
  return new Date().toLocaleTimeString('zh-CN', { hour12: false })
}

function compactText(text: string | undefined, limit: number): string {
  const normalized = String(text || '').replace(/\s+/g, ' ').trim()
  if (!normalized) return ''
  if (normalized.length <= limit) return normalized
  return `${normalized.slice(0, Math.max(0, limit - 1))}…`
}

function buildActivityStepsFromLesson(
  lessonPlans: LessonPlansData | null,
  imageFiles: string[],
  t: TFunction
): ActivityStep[] {
  if (!lessonPlans?.steps?.length) {
    return buildFallbackActivitySteps(imageFiles, t)
  }

  return lessonPlans.steps.map((step) => {
    const stepIndex = Number(step.step_index)
    return {
      id: `lesson-step-${stepIndex}`,
      name: t('第 {{step}} 步', { step: stepIndex }),
      goal: compactText(step.placement_instructions, 96) || t('完成第 {{step}} 步拼装。', { step: stepIndex }),
      estimatedMinutes: 4,
      checkpoints: [
        {
          id: `step-${stepIndex}-parts`,
          label: t('所需零件'),
          expectation: compactText(step.parts_needed, 120) || t('确认当前步骤零件已准备好。'),
          score: 4
        },
        {
          id: `step-${stepIndex}-placement`,
          label: t('摆放动作'),
          expectation: compactText(step.placement_instructions, 140) || t('按照当前指导图完成摆放。'),
          score: 8
        },
        {
          id: `step-${stepIndex}-knowledge`,
          label: t('教学提示'),
          expectation: compactText(step.teaching_notes || step.cultural_knowledge, 120) || t('完成后等待 agent 复核。'),
          score: 4
        }
      ]
    }
  })
}

function selectedModelLabel(selection: Record<string, string>, categoryId: string, t: TFunction): string {
  const category = modelCategories.find((item) => item.id === categoryId)
  const model = category?.models.find((item) => item.id === selection[categoryId])
  return model?.label || selection[categoryId] || t('未选择')
}

function getSourceModeLabel(sourceMode: SourceMode, t: TFunction): string {
  if (sourceMode === 'simulation') return t('模拟实时流')
  if (sourceMode === 'rtmp') return t('RTMP 公网访问')
  return t('眼镜实时流')
}

function getPayloadSourceMode(payload: SimulationFramePayload): SourceMode {
  if (payload.source === 'rtmp') return 'rtmp'
  if (payload.source === 'glasses' || payload.stream_url) return 'glasses'
  return 'simulation'
}

function trimTrailingSlash(value: string): string {
  return value.replace(/\/+$/, '')
}

function getLegoBackendUrl(): string {
  if (typeof process === 'undefined') return defaultLegoBackendUrl
  return trimTrailingSlash(
    process.env?.EXPO_PUBLIC_LEGO_BACKEND_URL ||
      process.env?.EXPO_PUBLIC_VLM_BRIDGE_URL ||
      defaultLegoBackendUrl
  )
}

function getVlmEventUrl(): string {
  if (typeof process === 'undefined') return `${defaultLegoBackendUrl}/events`
  return process.env?.EXPO_PUBLIC_VLM_EVENT_URL || `${getLegoBackendUrl()}/events`
}

function getVlmSimulationVideoUrl(): string {
  if (typeof process === 'undefined') return `${defaultLegoBackendUrl}/video/${simulationVideoFile}`
  return process.env?.EXPO_PUBLIC_SIMULATION_VIDEO_URL || `${getLegoBackendUrl()}/video/${simulationVideoFile}`
}

function getInstructionImageUrl(filename: string): string {
  if (/^https?:\/\//i.test(filename)) return filename
  const normalized = filename.replace(/^\/+/, '')
  if (normalized.startsWith('images/')) {
    return `${getLegoBackendUrl()}/rawdata/${normalized}`
  }
  return `${getLegoBackendUrl()}/rawdata/images/steps/${normalized}`
}

async function requestLegoBackend(path: string, body?: Record<string, unknown>): Promise<VlmRunStatus> {
  const response = await fetch(`${getLegoBackendUrl()}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body || {})
  })
  const text = await response.text()
  const payload = text ? JSON.parse(text) : {}
  if (!response.ok) {
    const message = typeof payload?.error === 'string' ? payload.error : text
    throw new Error(message || `Qwen-LegoAgent request failed: ${response.status}`)
  }
  return payload as VlmRunStatus
}

function buildQwenVlmStartConfig(sourceMode: SourceMode, streamUrl: string): Record<string, unknown> {
  if (sourceMode !== 'glasses') {
    return {
      ...qwenVlmStartConfig,
      source_mode: 'simulation'
    }
  }

  const liveStreamUrl = streamUrl.trim() || defaultStreamUrl
  return {
    ...qwenVlmStartConfig,
    source_mode: 'glasses',
    source: 'glasses',
    stream_url: liveStreamUrl,
    source_url: liveStreamUrl,
    video_url: liveStreamUrl
  }
}

function startQwenVlmRun(config: Record<string, unknown>): Promise<VlmRunStatus> {
  return requestLegoBackend('/vlm/start', config)
}

function stopQwenVlmRun(): Promise<VlmRunStatus> {
  return requestLegoBackend('/vlm/stop')
}

function getVlmChangePage(event: VlmPageEvent): number {
  return Number(event.changepage ?? event.changePage ?? event.change_page ?? 0)
}

function getVlmFrameIndex(event: VlmPageEvent): number | null {
  const value = Number(event.frame_index ?? event.frameIndex)
  return Number.isFinite(value) ? value : null
}

function getVlmIfJudge(event: VlmPageEvent): number | null {
  const value = Number(event.ifjudge ?? event.ifJudge)
  if (!Number.isFinite(value)) return null
  return Math.floor(value)
}

function getVlmNowStep(event: VlmPageEvent): number | null {
  const value = Number(event.now_step ?? event.nowStep)
  if (!Number.isFinite(value) || value <= 0) return null
  return Math.floor(value)
}

function getVlmEventKey(event: VlmPageEvent): string {
  const frameIndex = getVlmFrameIndex(event)
  return `${event.time || 'unknown'}:${frameIndex ?? 'unknown'}:${getVlmNowStep(event) ?? 'unknown'}:${getVlmChangePage(event)}`
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value && typeof value === 'object' && !Array.isArray(value))
}

const vlmOutputDisplayKeys = new Set([
  'time',
  'frame_index',
  'frameIndex',
  'ifjudge',
  'ifJudge',
  'now_step',
  'nowStep',
  'state',
  'reason',
  'changepage',
  'changePage',
  'change_page'
])

function formatVlmOutput(event: VlmPageEvent): string {
  const extraFields = Object.entries(event).reduce<Record<string, unknown>>((payload, [key, value]) => {
    if (!vlmOutputDisplayKeys.has(key)) {
      payload[key] = value
    }
    return payload
  }, {})

  return JSON.stringify(
    {
      time: event.time || null,
      frame_index: getVlmFrameIndex(event),
      ifjudge: getVlmIfJudge(event),
      now_step: getVlmNowStep(event),
      state: typeof event.state === 'undefined' ? null : event.state,
      reason: event.reason || '',
      changepage: getVlmChangePage(event),
      ...extraFields
    },
    null,
    2
  )
}

function formatVlmOutputLog(items: VlmPageEventLogItem[], t: TFunction): string {
  if (!items.length) {
    return t('等待 VLM output。')
  }

  return items.map((item, index) => [
    `#${items.length - index} · ${item.receivedAt}`,
    formatVlmOutput(item.event)
  ].join('\n')).join('\n\n')
}

function formatLatestVlmOutput(item: VlmPageEventLogItem | undefined, t: TFunction): string {
  if (!item) return t('等待 VLM output。')
  return formatVlmOutput(item.event)
}

function getVlmPageDecisionLabel(item: VlmPageEventLogItem | undefined, t: TFunction): string {
  if (!item) return t('等待 VLM output')
  return getVlmChangePage(item.event) === 1 ? t('翻页') : t('不翻页')
}

function ActionButton({
  label,
  onPress,
  variant = 'secondary',
  disabled = false
}: {
  label: string
  onPress: () => void
  variant?: 'primary' | 'secondary' | 'danger'
  disabled?: boolean
}) {
  return (
    <Pressable
      accessibilityRole="button"
      disabled={disabled}
      onPress={onPress}
      style={({ pressed }) => [
        styles.button,
        variant === 'primary' ? styles.buttonPrimary : null,
        variant === 'danger' ? styles.buttonDanger : null,
        disabled ? styles.buttonDisabled : null,
        pressed && !disabled ? styles.buttonPressed : null
      ]}
    >
      <Text
        style={[
          styles.buttonText,
          variant === 'primary' ? styles.buttonPrimaryText : null,
          variant === 'danger' ? styles.buttonDangerText : null,
          disabled ? styles.buttonDisabledText : null
        ]}
      >
        {label}
      </Text>
    </Pressable>
  )
}

function ImageStepButton({
  label,
  onPress,
  disabled,
  accessibilityLabel
}: {
  label: string
  onPress: () => void
  disabled: boolean
  accessibilityLabel: string
}) {
  return (
    <Pressable
      accessibilityLabel={accessibilityLabel}
      accessibilityRole="button"
      disabled={disabled}
      onPress={onPress}
      style={({ pressed }) => [
        styles.imageStepButton,
        disabled ? styles.imageStepButtonDisabled : null,
        pressed && !disabled ? styles.buttonPressed : null
      ]}
    >
      <Text style={[styles.imageStepButtonText, disabled ? styles.imageStepButtonTextDisabled : null]}>
        {label}
      </Text>
    </Pressable>
  )
}

function StatusPill({
  label,
  tone = 'neutral'
}: {
  label: string
  tone?: 'neutral' | 'online' | 'live' | 'warning'
}) {
  return (
    <View
      style={[
        styles.statusPill,
        tone === 'online' ? styles.statusOnline : null,
        tone === 'live' ? styles.statusLive : null,
        tone === 'warning' ? styles.statusWarning : null
      ]}
    >
      <Text
        style={[
          styles.statusText,
          tone === 'online' ? styles.statusOnlineText : null,
          tone === 'live' ? styles.statusLiveText : null,
          tone === 'warning' ? styles.statusWarningText : null
        ]}
      >
        {label}
      </Text>
    </View>
  )
}

function Metric({
  label,
  value,
  meta
}: {
  label: string
  value: string | number
  meta?: string
}) {
  return (
    <View style={styles.metric}>
      <Text style={styles.metricLabel}>{label}</Text>
      <Text style={styles.metricValue}>{value}</Text>
      {meta ? <Text style={styles.metricMeta}>{meta}</Text> : null}
    </View>
  )
}

function VlmEventCard({
  label,
  value,
  meta,
  children
}: {
  label: string
  value: string
  meta?: string
  children?: React.ReactNode
}) {
  return (
    <View style={styles.vlmEventCard}>
      <Text style={styles.vlmEventLabel}>{label}</Text>
      <Text selectable style={styles.vlmEventValue}>
        {value}
      </Text>
      {meta ? <Text style={styles.vlmEventMeta}>{meta}</Text> : null}
      {children}
    </View>
  )
}

function ModelSelect({
  category,
  selectedModelId,
  isOpen,
  onToggle,
  onSelect
}: {
  category: ModelCategory
  selectedModelId: string
  isOpen: boolean
  onToggle: () => void
  onSelect: (modelId: string) => void
}) {
  const { t } = useI18n()
  const selectedModel = category.models.find((model) => model.id === selectedModelId)

  return (
    <View style={[styles.modelSelectorGroup, isOpen ? styles.modelSelectorGroupOpen : null]}>
      <Text style={styles.modelCategoryLabel}>{category.label}</Text>
      <Text style={styles.modelCategoryMeta}>{category.description}</Text>
      <Pressable
        accessibilityRole="button"
        accessibilityState={{ expanded: isOpen }}
        onPress={onToggle}
        style={({ pressed }) => [
          styles.modelDropdownTrigger,
          isOpen ? styles.modelDropdownTriggerOpen : null,
          pressed ? styles.buttonPressed : null
        ]}
      >
        <View style={styles.modelDropdownTextGroup}>
          <Text numberOfLines={1} style={styles.modelDropdownLabel}>
            {selectedModel?.label || t('请选择模型')}
          </Text>
          <Text numberOfLines={1} style={styles.modelDropdownMeta}>
            {selectedModel?.modelName || t('未选择')} · {selectedModel?.capability || 'pending'}
          </Text>
        </View>
        <Text style={styles.modelDropdownChevron}>{isOpen ? 'up' : 'down'}</Text>
      </Pressable>

      {isOpen ? (
        <View style={styles.modelDropdownPopover}>
          {category.models.map((model) => {
            const selected = model.id === selectedModelId
            return (
              <Pressable
                key={model.id}
                accessibilityRole="button"
                accessibilityState={{ selected }}
                onPress={() => onSelect(model.id)}
                style={({ pressed }) => [
                  styles.modelDropdownOption,
                  selected ? styles.modelDropdownOptionSelected : null,
                  pressed ? styles.buttonPressed : null
                ]}
              >
                <View style={styles.modelDropdownOptionTextGroup}>
                  <Text
                    numberOfLines={1}
                    style={[
                      styles.modelDropdownOptionLabel,
                      selected ? styles.modelDropdownOptionLabelSelected : null
                    ]}
                  >
                    {model.label}
                  </Text>
                  <Text numberOfLines={1} style={styles.modelDropdownOptionMeta}>
                    {model.modelName} · {model.capability}
                  </Text>
                </View>
                <Text style={[styles.modelDropdownCheck, selected ? styles.modelDropdownCheckSelected : null]}>
                  {selected ? 'selected' : ''}
                </Text>
              </Pressable>
            )
          })}
        </View>
      ) : null}
    </View>
  )
}

function SourceModeButton({
  label,
  active,
  onPress
}: {
  label: string
  active: boolean
  onPress: () => void
}) {
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityState={{ selected: active }}
      onPress={onPress}
      style={({ pressed }) => [
        styles.sourceModeButton,
        active ? styles.sourceModeButtonActive : null,
        pressed ? styles.buttonPressed : null
      ]}
    >
      <Text style={[styles.sourceModeButtonText, active ? styles.sourceModeButtonTextActive : null]}>
        {label}
      </Text>
    </Pressable>
  )
}

function StepStatusBadge({ status }: { status: ActivityStepStatus }) {
  const { t } = useI18n()
  const label =
    status === 'completed'
      ? t('已完成')
      : status === 'active'
        ? t('进行中')
        : status === 'warning'
          ? t('复核')
          : t('待开始')

  return (
    <View
      style={[
        styles.stepStatusBadge,
        status === 'completed' ? styles.stepStatusDone : null,
        status === 'active' ? styles.stepStatusActive : null,
        status === 'warning' ? styles.stepStatusWarning : null
      ]}
    >
      <Text
        style={[
          styles.stepStatusText,
          status === 'completed' ? styles.stepStatusDoneText : null,
          status === 'active' ? styles.stepStatusActiveText : null,
          status === 'warning' ? styles.stepStatusWarningText : null
        ]}
      >
        {label}
      </Text>
    </View>
  )
}

function LegoPreviewSurface({
  currentStep,
  elapsedSeconds,
  isRunning,
  sourceMode
}: {
  currentStep: ActivityStep
  elapsedSeconds: number
  isRunning: boolean
  sourceMode: SourceMode
}) {
  const { t } = useI18n()
  const sourceLabel = getSourceModeLabel(sourceMode, t)

  return (
    <View style={styles.previewFrame}>
      <View style={styles.previewHudTop}>
        <Text numberOfLines={1} style={styles.previewHudText}>
          {sourceLabel} · {currentStep.name}
        </Text>
        <Text style={styles.previewHudText}>{formatClock(elapsedSeconds)}</Text>
      </View>

      <View style={styles.legoBoard}>
        <View style={[styles.brick, styles.brickLong, styles.brickBlue, { left: '12%', top: '58%' }]} />
        <View style={[styles.brick, styles.brickLong, styles.brickBlue, { right: '12%', top: '58%' }]} />
        <View style={[styles.brick, styles.brickMedium, styles.brickYellow, { left: '25%', top: '42%' }]} />
        <View style={[styles.brick, styles.brickMedium, styles.brickYellow, { right: '25%', top: '42%' }]} />
        <View style={[styles.brick, styles.brickShort, styles.brickGreen, { left: '36%', top: '28%' }]} />
        <View style={[styles.brick, styles.brickShort, styles.brickGreen, { right: '36%', top: '28%' }]} />
        <View style={[styles.brick, styles.brickHub, styles.brickRed]} />
        <View style={[styles.wheel, { left: '11%', bottom: '15%' }]} />
        <View style={[styles.wheel, { right: '11%', bottom: '15%' }]} />
      </View>

      <View style={styles.previewBottomBar}>
        <View style={[styles.liveDot, isRunning ? styles.liveDotOn : null]} />
        <Text numberOfLines={1} style={styles.previewBottomText}>
          {isRunning ? t('AI 正在抽帧识别结构与手部操作') : t('等待开始活动评测')}
        </Text>
      </View>
    </View>
  )
}

function StepTimeline({
  steps,
  activeStepId,
  completedStepIds
}: {
  steps: ActivityStep[]
  activeStepId: string
  completedStepIds: Set<string>
}) {
  const { t } = useI18n()

  return (
    <View style={styles.timelineBlock}>
      <View style={styles.timelineHeader}>
        <Text style={styles.timelineTitle}>{t('拼装步骤时间轴')}</Text>
        <Text style={styles.timelineMeta}>
          {t('{{done}}/{{total}} 步完成', { done: completedStepIds.size, total: steps.length })}
        </Text>
      </View>
      <View style={styles.timelineTrack}>
        {steps.map((step, index) => {
          const completed = completedStepIds.has(step.id)
          const active = step.id === activeStepId
          return (
            <View
              key={step.id}
              style={[
                styles.timelineSegment,
                completed ? styles.timelineSegmentDone : null,
                active ? styles.timelineSegmentActive : null,
                { flex: Math.max(1, step.estimatedMinutes) }
              ]}
            >
              <Text numberOfLines={1} style={styles.timelineSegmentText}>
                {index + 1}
              </Text>
            </View>
          )
        })}
      </View>
      <View style={styles.timelineLegend}>
        {steps.map((step, index) => (
          <View key={step.id} style={styles.timelineLegendItem}>
            <View
              style={[
                styles.timelineLegendDot,
                completedStepIds.has(step.id) ? styles.timelineLegendDotDone : null,
                step.id === activeStepId ? styles.timelineLegendDotActive : null
              ]}
            />
            <Text numberOfLines={1} style={styles.timelineLegendText}>
              {step.name} · {step.goal}
            </Text>
          </View>
        ))}
      </View>
    </View>
  )
}

function InstructionImageList({
  imageFiles,
  selectedImageFile,
  selectedImageIndex,
  onPrevious,
  onNext
}: {
  imageFiles: string[]
  selectedImageFile: string
  selectedImageIndex: number
  onPrevious: () => void
  onNext: () => void
}) {
  const { t } = useI18n()
  const currentImage = selectedImageFile || imageFiles[0]

  return (
    <View style={styles.instructionImageViewer}>
      <View style={styles.imageStepControlRow}>
        <ImageStepButton
          label="↑"
          accessibilityLabel={t('切换到上一张拼装指导图')}
          disabled={selectedImageIndex <= 0}
          onPress={onPrevious}
        />
      </View>

      <View
        accessibilityLabel={`Step ${selectedImageIndex + 1} of ${imageFiles.length}`}
        accessibilityRole="image"
        style={styles.instructionImageSingleCard}
      >
        <Image
          resizeMode="contain"
          source={{ uri: getInstructionImageUrl(currentImage) }}
          style={styles.instructionImage}
        />
        <View style={styles.instructionImageFooter}>
          <Text style={styles.instructionImageTitle}>Step {selectedImageIndex + 1}</Text>
          <Text numberOfLines={1} style={styles.instructionImageMeta}>
            {currentImage}
          </Text>
        </View>
      </View>

      <View style={styles.imageStepControlRow}>
        <ImageStepButton
          label="↓"
          accessibilityLabel={t('切换到下一张拼装指导图')}
          disabled={selectedImageIndex >= imageFiles.length - 1}
          onPress={onNext}
        />
      </View>
    </View>
  )
}

function StreamSurface({
  title,
  subtitle,
  active,
  isRunning,
  emphasis = 'normal',
  children
}: {
  title: string
  subtitle: string
  active: boolean
  isRunning: boolean
  emphasis?: 'compact' | 'normal' | 'large'
  children: React.ReactNode
}) {
  return (
    <View
      style={[
        styles.streamSurface,
        emphasis === 'compact' ? styles.streamSurfaceCompact : null,
        emphasis === 'large' ? styles.streamSurfaceLarge : null,
        active ? styles.streamSurfaceActive : null
      ]}
    >
      <View style={styles.streamSurfaceHeader}>
        <View style={styles.streamSurfaceTitleGroup}>
          <Text numberOfLines={1} style={styles.streamSurfaceTitle}>
            {title}
          </Text>
          <Text numberOfLines={1} style={styles.streamSurfaceSubtitle}>
            {subtitle}
          </Text>
        </View>
        <View style={[styles.liveDot, isRunning && active ? styles.liveDotOn : null]} />
      </View>
      <View style={styles.streamSurfaceBody}>{children}</View>
    </View>
  )
}

function SimulationVideoStream({
  currentStep,
  isPlaying,
  onProgressFrame,
  onRealtimeFrame,
  restartKey,
  videoUrl
}: {
  currentStep: ActivityStep
  isPlaying: boolean
  onProgressFrame: (payload: SimulationFramePayload) => void
  onRealtimeFrame: (payload: SimulationFramePayload) => void
  restartKey: number
  videoUrl: string
}) {
  const { t } = useI18n()
  const videoRef = useRef<HTMLVideoElement | null>(null)
  const onProgressFrameRef = useRef(onProgressFrame)
  const onRealtimeFrameRef = useRef(onRealtimeFrame)

  useEffect(() => {
    onProgressFrameRef.current = onProgressFrame
  }, [onProgressFrame])

  useEffect(() => {
    onRealtimeFrameRef.current = onRealtimeFrame
  }, [onRealtimeFrame])

  useEffect(() => {
    const video = videoRef.current
    if (!video) return undefined

    const restartVideo = () => {
      try {
        video.currentTime = 0
      } catch {
        // Some browsers delay seeking until enough metadata is available.
      }
      if (isPlaying) {
        const playPromise = video.play()
        if (playPromise) {
          playPromise.catch(() => undefined)
        }
      } else {
        video.pause()
      }
    }

    if (video.readyState >= 1) {
      restartVideo()
      return undefined
    }

    video.addEventListener('loadedmetadata', restartVideo, { once: true })
    return () => {
      video.removeEventListener('loadedmetadata', restartVideo)
    }
  }, [isPlaying, restartKey, videoUrl])

  useEffect(() => {
    const video = videoRef.current
    if (!video) return

    if (isPlaying) {
      const playPromise = video.play()
      if (playPromise) {
        playPromise.catch(() => undefined)
      }
    } else {
      video.pause()
    }
  }, [isPlaying])

  useEffect(() => {
    if (!isPlaying) return undefined

    const captureFrame = (): SimulationFramePayload | null => {
      const video = videoRef.current
      if (!video || video.readyState < 2 || !Number.isFinite(video.duration)) return null

      const naturalWidth = video.videoWidth || 640
      const naturalHeight = video.videoHeight || 360
      const targetWidth = Math.min(640, naturalWidth)
      const targetHeight = Math.max(1, Math.round((targetWidth / naturalWidth) * naturalHeight))

      let frameDataUrl: string | undefined
      try {
        const canvas = document.createElement('canvas')
        canvas.width = targetWidth
        canvas.height = targetHeight
        const context = canvas.getContext('2d')
        context?.drawImage(video, 0, 0, targetWidth, targetHeight)
        frameDataUrl = canvas.toDataURL('image/jpeg', 0.72)
      } catch {
        frameDataUrl = undefined
      }

      return {
        current_time: video.currentTime,
        duration: video.duration,
        width: targetWidth,
        height: targetHeight,
        frame_data_url: frameDataUrl,
        source: 'simulation',
        source_url: videoUrl
      }
    }

    const sendRealtimeFrame = () => {
      const payload = captureFrame()
      if (payload) onRealtimeFrameRef.current(payload)
    }

    const sendProgressFrame = () => {
      const payload = captureFrame()
      if (!payload) return false
      onProgressFrameRef.current(payload)
      return true
    }

    let bootstrapTimer: ReturnType<typeof window.setTimeout> | null = null
    let bootstrapAttempts = 0
    const sendBootstrapProgressFrame = () => {
      if (sendProgressFrame()) return
      bootstrapAttempts += 1
      if (bootstrapAttempts < 24) {
        bootstrapTimer = window.setTimeout(sendBootstrapProgressFrame, 250)
      }
    }

    sendBootstrapProgressFrame()
    const realtimeInterval = window.setInterval(sendRealtimeFrame, 1000)
    const progressInterval = window.setInterval(sendProgressFrame, vlmProgressIntervalMs)
    return () => {
      if (bootstrapTimer !== null) {
        window.clearTimeout(bootstrapTimer)
      }
      window.clearInterval(realtimeInterval)
      window.clearInterval(progressInterval)
    }
  }, [isPlaying])

  return (
    <View style={styles.simulationVideoFrame}>
      {React.createElement('video', {
        key: restartKey,
        controls: true,
        crossOrigin: 'anonymous',
        loop: true,
        muted: true,
        playsInline: true,
        preload: 'auto',
        ref: videoRef,
        src: videoUrl,
        style: styles.simulationVideo
      })}
      <View style={styles.previewBottomBar}>
        <Text numberOfLines={1} style={styles.previewBottomText}>
          {simulationVideoFile} · {t('模拟实时传输')} · {currentStep.name}
        </Text>
      </View>
    </View>
  )
}

function GlassesStreamView({
  captureGlassesStreamFrame,
  currentStep,
  isPlaying,
  onProgressFrame,
  onRealtimeFrame,
  streamUrl
}: {
  captureGlassesStreamFrame: (payload: {
    stream_url: string
    current_time?: number
    max_width?: number
  }) => Promise<SimulationFramePayload>
  currentStep: ActivityStep
  isPlaying: boolean
  onProgressFrame: (payload: SimulationFramePayload) => void
  onRealtimeFrame: (payload: SimulationFramePayload) => void
  streamUrl: string
}) {
  const { t } = useI18n()
  const imageRef = useRef<HTMLImageElement | null>(null)
  const onProgressFrameRef = useRef(onProgressFrame)
  const onRealtimeFrameRef = useRef(onRealtimeFrame)
  const captureInFlightRef = useRef(false)
  const streamStartedAtRef = useRef(Date.now())
  const [captureStatus, setCaptureStatus] = useState('等待直播画面')

  useEffect(() => {
    onProgressFrameRef.current = onProgressFrame
  }, [onProgressFrame])

  useEffect(() => {
    onRealtimeFrameRef.current = onRealtimeFrame
  }, [onRealtimeFrame])

  useEffect(() => {
    streamStartedAtRef.current = Date.now()
    setCaptureStatus('等待直播画面')
  }, [streamUrl])

  useEffect(() => {
    if (isPlaying) {
      streamStartedAtRef.current = Date.now()
    }
  }, [isPlaying, streamUrl])

  useEffect(() => {
    if (!isPlaying || !streamUrl.trim()) return undefined

    let stopped = false
    const elapsedSeconds = () => Math.max(0, (Date.now() - streamStartedAtRef.current) / 1000)

    const getPreviewDimensions = () => {
      const image = imageRef.current
      if (!image || image.naturalWidth <= 0 || image.naturalHeight <= 0) {
        return { width: 640, height: 360 }
      }

      const naturalWidth = image.naturalWidth || 640
      const naturalHeight = image.naturalHeight || 360
      const targetWidth = Math.min(640, naturalWidth)
      const targetHeight = Math.max(1, Math.round((targetWidth / naturalWidth) * naturalHeight))
      return { width: targetWidth, height: targetHeight }
    }

    const captureFrame = async (): Promise<SimulationFramePayload | null> => {
      if (captureInFlightRef.current) return null
      captureInFlightRef.current = true
      try {
        const frame = await captureGlassesStreamFrame({
          stream_url: streamUrl,
          current_time: elapsedSeconds(),
          max_width: 640
        })
        const preview = getPreviewDimensions()
        setCaptureStatus('后端截帧')
        return {
          ...frame,
          current_time: frame.current_time ?? elapsedSeconds(),
          duration: frame.duration ?? null,
          width: frame.width ?? preview.width,
          height: frame.height ?? preview.height,
          source: 'glasses',
          source_url: streamUrl,
          stream_url: streamUrl
        }
      } catch {
        setCaptureStatus('后端截帧失败')
        return null
      } finally {
        captureInFlightRef.current = false
      }
    }

    const sendRealtimeFrame = async () => {
      const payload = await captureFrame()
      if (!stopped && payload) onRealtimeFrameRef.current(payload)
    }

    const sendProgressFrame = async () => {
      const payload = await captureFrame()
      if (stopped || !payload) return false
      onProgressFrameRef.current(payload)
      return true
    }

    let bootstrapTimer: ReturnType<typeof window.setTimeout> | null = null
    let bootstrapAttempts = 0
    const sendBootstrapProgressFrame = async () => {
      if ((await sendProgressFrame()) || stopped) return
      bootstrapAttempts += 1
      if (bootstrapAttempts < 24) {
        bootstrapTimer = window.setTimeout(sendBootstrapProgressFrame, 250)
      }
    }

    sendBootstrapProgressFrame()
    const realtimeInterval = window.setInterval(sendRealtimeFrame, 1000)
    const progressInterval = window.setInterval(sendProgressFrame, vlmProgressIntervalMs)

    return () => {
      stopped = true
      if (bootstrapTimer !== null) {
        window.clearTimeout(bootstrapTimer)
      }
      window.clearInterval(realtimeInterval)
      window.clearInterval(progressInterval)
    }
  }, [captureGlassesStreamFrame, isPlaying, streamUrl])

  return (
    <View style={styles.glassesStreamFrame}>
      {React.createElement('img', {
        ref: imageRef,
        src: streamUrl,
        style: styles.glassesStreamImage
      })}
      <View style={styles.previewBottomBar}>
        <Text numberOfLines={1} style={styles.previewBottomText}>
          {streamUrl} · {currentStep.name} · {t(captureStatus)}
        </Text>
      </View>
    </View>
  )
}

function PublicRtmpFlvStreamView({
  currentStep,
  flvUrl,
  isPlaying,
  onProgressFrame,
  onRealtimeFrame,
  pushUrl
}: {
  currentStep: ActivityStep
  flvUrl: string
  isPlaying: boolean
  onProgressFrame: (payload: SimulationFramePayload) => void
  onRealtimeFrame: (payload: SimulationFramePayload) => void
  pushUrl: string
}) {
  const { t } = useI18n()
  const videoRef = useRef<HTMLVideoElement | null>(null)
  const onProgressFrameRef = useRef(onProgressFrame)
  const onRealtimeFrameRef = useRef(onRealtimeFrame)
  const streamStartedAtRef = useRef(Date.now())
  const [playbackStatus, setPlaybackStatus] = useState('等待 HTTP-FLV 直播流')

  useEffect(() => {
    onProgressFrameRef.current = onProgressFrame
  }, [onProgressFrame])

  useEffect(() => {
    onRealtimeFrameRef.current = onRealtimeFrame
  }, [onRealtimeFrame])

  useEffect(() => {
    const video = videoRef.current
    if (!video) return undefined

    streamStartedAtRef.current = Date.now()

    if (!flvjs.isSupported()) {
      setPlaybackStatus('当前浏览器不支持 HTTP-FLV')
      return undefined
    }

    setPlaybackStatus('正在连接 HTTP-FLV')
    const player = flvjs.createPlayer(
      {
        cors: true,
        hasAudio: false,
        hasVideo: true,
        isLive: true,
        type: 'flv',
        url: flvUrl,
        withCredentials: false
      },
      {
        autoCleanupMaxBackwardDuration: 30,
        autoCleanupMinBackwardDuration: 10,
        autoCleanupSourceBuffer: true,
        deferLoadAfterSourceOpen: false,
        enableStashBuffer: false,
        enableWorker: false,
        isLive: true,
        lazyLoad: false,
        stashInitialSize: 128
      }
    )

    const handleCanPlay = () => {
      setPlaybackStatus('HTTP-FLV 已就绪')
      if (isPlaying) {
        const playPromise = video.play()
        if (playPromise) {
          playPromise.catch(() => setPlaybackStatus('点击视频开始播放'))
        }
      }
    }
    const handlePlaying = () => setPlaybackStatus('HTTP-FLV 播放中')
    const handleWaiting = () => setPlaybackStatus('等待直播数据')
    const handleError = (_type?: string, detail?: string) => {
      setPlaybackStatus(detail ? t('FLV 播放异常：{{detail}}', { detail }) : 'FLV 播放异常')
    }

    video.addEventListener('canplay', handleCanPlay)
    video.addEventListener('playing', handlePlaying)
    video.addEventListener('waiting', handleWaiting)
    player.on(flvjs.Events.ERROR, handleError)
    player.attachMediaElement(video)
    player.load()

    if (isPlaying) {
      const playPromise = video.play()
      if (playPromise) {
        playPromise.catch(() => setPlaybackStatus('点击视频开始播放'))
      }
    }

    return () => {
      video.removeEventListener('canplay', handleCanPlay)
      video.removeEventListener('playing', handlePlaying)
      video.removeEventListener('waiting', handleWaiting)
      player.off(flvjs.Events.ERROR, handleError)
      player.unload()
      player.detachMediaElement()
      player.destroy()
    }
  }, [flvUrl])

  useEffect(() => {
    const video = videoRef.current
    if (!video) return

    if (isPlaying) {
      streamStartedAtRef.current = Date.now()
      const playPromise = video.play()
      if (playPromise) {
        playPromise.catch(() => setPlaybackStatus('点击视频开始播放'))
      }
    } else {
      video.pause()
    }
  }, [isPlaying])

  useEffect(() => {
    if (!isPlaying) return undefined

    const elapsedSeconds = () => Math.max(0, (Date.now() - streamStartedAtRef.current) / 1000)

    const captureFrame = (): SimulationFramePayload | null => {
      const video = videoRef.current
      if (!video || video.readyState < 2 || video.videoWidth <= 0 || video.videoHeight <= 0) return null

      const naturalWidth = video.videoWidth || 640
      const naturalHeight = video.videoHeight || 360
      const targetWidth = Math.min(640, naturalWidth)
      const targetHeight = Math.max(1, Math.round((targetWidth / naturalWidth) * naturalHeight))

      let frameDataUrl: string | undefined
      try {
        const canvas = document.createElement('canvas')
        canvas.width = targetWidth
        canvas.height = targetHeight
        const context = canvas.getContext('2d')
        context?.drawImage(video, 0, 0, targetWidth, targetHeight)
        frameDataUrl = canvas.toDataURL('image/jpeg', 0.72)
        setPlaybackStatus('HTTP-FLV 播放中 · 浏览器截帧')
      } catch {
        frameDataUrl = undefined
        setPlaybackStatus('HTTP-FLV 播放中 · 跨域限制截帧')
      }

      return {
        current_time: Number.isFinite(video.currentTime) ? video.currentTime : elapsedSeconds(),
        duration: Number.isFinite(video.duration) ? video.duration : null,
        width: targetWidth,
        height: targetHeight,
        frame_data_url: frameDataUrl,
        source: 'rtmp',
        source_url: flvUrl,
        stream_url: flvUrl
      }
    }

    const sendRealtimeFrame = () => {
      const payload = captureFrame()
      if (payload) onRealtimeFrameRef.current(payload)
    }

    const sendProgressFrame = () => {
      const payload = captureFrame()
      if (!payload) return false
      onProgressFrameRef.current(payload)
      return true
    }

    let bootstrapTimer: ReturnType<typeof window.setTimeout> | null = null
    let bootstrapAttempts = 0
    const sendBootstrapProgressFrame = () => {
      if (sendProgressFrame()) return
      bootstrapAttempts += 1
      if (bootstrapAttempts < 24) {
        bootstrapTimer = window.setTimeout(sendBootstrapProgressFrame, 250)
      }
    }

    sendBootstrapProgressFrame()
    const realtimeInterval = window.setInterval(sendRealtimeFrame, 1000)
    const progressInterval = window.setInterval(sendProgressFrame, vlmProgressIntervalMs)
    return () => {
      if (bootstrapTimer !== null) {
        window.clearTimeout(bootstrapTimer)
      }
      window.clearInterval(realtimeInterval)
      window.clearInterval(progressInterval)
    }
  }, [flvUrl, isPlaying])

  return (
    <View style={styles.publicFlvFrame}>
      {React.createElement('video', {
        controls: true,
        crossOrigin: 'anonymous',
        muted: true,
        playsInline: true,
        preload: 'auto',
        ref: videoRef,
        style: styles.publicFlvVideo
      })}
      <View style={styles.previewBottomBar}>
        <Text numberOfLines={1} style={styles.previewBottomText}>
          {t('推流：{{pushUrl}} · 播放：{{flvUrl}} · {{stepName}} · {{status}}', {
            pushUrl,
            flvUrl,
            stepName: currentStep.name,
            status: t(playbackStatus)
          })}
        </Text>
      </View>
    </View>
  )
}

function LegoActivityDashboard() {
  const { t } = useI18n()
  const { width, height } = useWindowDimensions()
  const isWide = width >= 1080
  const dashboardPanelHeight = isWide ? Math.max(560, height - 32) : undefined
  const api = useMemo(() => createTeachingGuideApi(), [])

  const [isConnected, setIsConnected] = useState(false)
  const [isRunning, setIsRunning] = useState(false)
  const [sourceMode, setSourceMode] = useState<SourceMode>('glasses')
  const [streamUrl, setStreamUrl] = useState(defaultStreamUrl)
  const [projectId] = useState(defaultProjectId)
  const [imageFiles, setImageFiles] = useState<string[]>(fallbackImageFiles)
  const [selectedImageFile, setSelectedImageFile] = useState(fallbackImageFiles[0])
  const [guides, setGuides] = useState<GuidesData | null>(null)
  const [lessonPlans, setLessonPlans] = useState<LessonPlansData | null>(null)
  const [agentStatus, setAgentStatus] = useState<'loading' | 'ready' | 'offline'>('loading')
  const [activeStepId, setActiveStepId] = useState(firstFallbackStepId)
  const [selectedStepId, setSelectedStepId] = useState(firstFallbackStepId)
  const [completedStepIds, setCompletedStepIds] = useState<Set<string>>(new Set())
  const [simulationVideoVersion, setSimulationVideoVersion] = useState(0)
  const [elapsedSeconds, setElapsedSeconds] = useState(0)
  const appliedVlmEventKeysRef = useRef<Set<string>>(new Set())
  const [vlmPageEvents, setVlmPageEvents] = useState<VlmPageEventLogItem[]>([])
  const [showVlmHistory, setShowVlmHistory] = useState(false)
  const [vlmRunStatus, setVlmRunStatus] = useState<VlmRunStatus | null>(null)
  const [isStartingVlm, setIsStartingVlm] = useState(false)
  const [feedbackLog, setFeedbackLog] = useState<FeedbackItem[]>(() => [
    {
      id: 'initial-1',
      tone: 'ok',
      title: t('活动驾驶舱已就绪'),
      detail: t('左侧加载拼装指导图，中间显示模拟流、眼镜局域网流或 RTMP 公网流，右侧等待 VLM 翻页事件。'),
      stepId: firstFallbackStepId,
      timestamp: getTimestamp()
    }
  ])

  useEffect(() => {
    let mounted = true

    async function loadAgentProject() {
      try {
        const metadata = await api.getProject(projectId)
        if (!mounted) return

        if (metadata.image_files?.length) {
          setImageFiles(metadata.image_files)
          setSelectedImageFile(metadata.image_files[0])
        }

        try {
          const nextGuides = await api.getGuides(projectId)
          if (mounted) setGuides(nextGuides)
        } catch {
          if (mounted) setGuides(null)
        }

        try {
          const nextLessonPlans = await api.getLessonPlans(projectId)
          if (mounted) setLessonPlans(nextLessonPlans)
        } catch {
          if (mounted) setLessonPlans(null)
        }

        setAgentStatus('ready')
      } catch {
        if (!mounted) return
        setAgentStatus('offline')
      }
    }

    loadAgentProject()
    return () => {
      mounted = false
    }
  }, [api, projectId])

  useEffect(() => {
    if (!isRunning) return undefined

    const timer = setInterval(() => {
      setElapsedSeconds((seconds) => seconds + 1)
    }, 1000)

    return () => clearInterval(timer)
  }, [isRunning])

  const activitySteps = useMemo(
    () => buildActivityStepsFromLesson(lessonPlans, imageFiles, t),
    [lessonPlans, imageFiles, t]
  )

  useEffect(() => {
    if (!activitySteps.length) return

    setActiveStepId((current) => (
      activitySteps.some((step) => step.id === current) ? current : activitySteps[0].id
    ))
    setSelectedStepId((current) => (
      activitySteps.some((step) => step.id === current) ? current : activitySteps[0].id
    ))
  }, [activitySteps])

  const activeStep = activitySteps.find((step) => step.id === activeStepId) || activitySteps[0]
  const totalScore = useMemo(() => getTotalScore(activitySteps), [activitySteps])
  const currentScore = useMemo(() => {
    return activitySteps.reduce((sum, step) => {
      const stepScore = getStepScore(step)
      if (completedStepIds.has(step.id)) return sum + stepScore
      if (step.id === activeStepId && isRunning) return sum + Math.round(stepScore * 0.45)
      return sum
    }, 0)
  }, [activitySteps, activeStepId, completedStepIds, isRunning])
  const completedCheckpointCount = useMemo(() => {
    return activitySteps.reduce((sum, step) => {
      if (!completedStepIds.has(step.id)) return sum
      return sum + step.checkpoints.length
    }, 0)
  }, [activitySteps, completedStepIds])
  const totalCheckpointCount = useMemo(() => {
    return activitySteps.reduce((sum, step) => sum + step.checkpoints.length, 0)
  }, [activitySteps])
  const activeStepIndex = activitySteps.findIndex((step) => step.id === activeStepId)
  const simulationVideoUrl = getVlmSimulationVideoUrl()
  const sourceLabel = getSourceModeLabel(sourceMode, t)
  const streamInputValue =
    sourceMode === 'rtmp'
      ? publicRtmpPushUrl
      : sourceMode === 'simulation'
        ? simulationVideoUrl
        : streamUrl
  const activeStreamSubtitle =
    sourceMode === 'rtmp'
      ? publicRtmpFlvUrl
      : sourceMode === 'simulation'
        ? `legoagentbackend/testvideo/${simulationVideoFile}`
        : streamUrl
  const selectedImageIndex = Math.max(0, imageFiles.findIndex((filename) => filename === selectedImageFile))
  const selectedGuide = guides?.steps.find((step) => step.step_index === selectedImageIndex + 1)
  const currentLessonStepCompleted = completedStepIds.has(activitySteps[selectedImageIndex]?.id || '')
  const latestVlmPageEvent = vlmPageEvents[0]
  const previousVlmPageEvents = vlmPageEvents.slice(1)

  const pushFeedback = (item: Omit<FeedbackItem, 'id' | 'timestamp'>) => {
    setFeedbackLog((current) => [
      {
        ...item,
        id: `${Date.now()}-${current.length}`,
        timestamp: getTimestamp()
      },
      ...current
    ].slice(0, 24))
  }

  const activityStepForLessonStep = (stepIndex: number): ActivityStep => {
    const mappedIndex = Math.min(
      Math.max(0, stepIndex - 1),
      activitySteps.length - 1
    )
    return activitySteps[mappedIndex] || activitySteps[0]
  }

  const applyLocalStepSelection = useCallback((
    stepIndex: number,
    options?: {
      completedThroughStep?: number
      feedbackTitle?: string
      feedbackDetail?: string
    }
  ) => {
    const safeStepIndex = Math.min(Math.max(1, stepIndex), Math.max(1, imageFiles.length))
    const nextImage = imageFiles[safeStepIndex - 1]
    if (nextImage) {
      setSelectedImageFile(nextImage)
    }

    const mappedStep = activityStepForLessonStep(safeStepIndex)
    setSelectedStepId(mappedStep.id)
    setActiveStepId(mappedStep.id)

    if (options?.completedThroughStep) {
      const completedThroughStep = Math.min(
        Math.max(0, options.completedThroughStep),
        activitySteps.length
      )
      setCompletedStepIds((current) => {
        const next = new Set(current)
        for (let index = 1; index <= completedThroughStep; index += 1) {
          next.add(activityStepForLessonStep(index).id)
        }
        return next
      })
    }

    if (options?.feedbackTitle) {
      pushFeedback({
        tone: 'ok',
        title: options.feedbackTitle,
        detail: options.feedbackDetail || t('前端已切换到 Step {{step}}。', { step: safeStepIndex }),
        stepId: mappedStep.id
      })
    }
  }, [activitySteps, imageFiles, t])

  const applyVlmPageEvent = useCallback((event: VlmPageEvent) => {
    const eventKey = getVlmEventKey(event)
    if (appliedVlmEventKeysRef.current.has(eventKey)) return
    appliedVlmEventKeysRef.current.add(eventKey)

    const currentStepIndex = selectedImageIndex + 1
    const changepage = getVlmChangePage(event)
    const nowStep = getVlmNowStep(event)
    let applied = false
    let targetStepIndex: number | null = null

    if (changepage === 1) {
      if (currentStepIndex < imageFiles.length) {
        const completedStepIndex = currentStepIndex
        targetStepIndex = currentStepIndex + 1
        applyLocalStepSelection(targetStepIndex, {
          completedThroughStep: completedStepIndex,
          feedbackTitle: t('VLM 触发翻页到 Step {{step}}', { step: targetStepIndex }),
          feedbackDetail: [
            event.time ? `time=${event.time}` : '',
            getVlmFrameIndex(event) !== null ? `frame=${getVlmFrameIndex(event)}` : '',
            `changepage=1`,
            nowStep !== null ? `now_step=${nowStep}` : '',
            typeof event.state !== 'undefined' ? `state=${event.state}` : '',
            event.reason ? compactText(event.reason, 140) : ''
          ].filter(Boolean).join(' · ')
        })
        applied = true
      }
    }

    setVlmPageEvents((current) => [
      {
        id: `${Date.now()}-${current.length}`,
        receivedAt: getTimestamp(),
        event,
        applied,
        targetStepIndex
      },
      ...current
    ].slice(0, 20))
  }, [applyLocalStepSelection, imageFiles.length, selectedImageIndex, t])

  useEffect(() => {
    if (typeof window === 'undefined') return undefined

    window.__legoGlassApplyVlmOutput = applyVlmPageEvent
    const eventListener = (domEvent: Event) => {
      const detail = (domEvent as CustomEvent<VlmPageEvent>).detail
      if (isRecord(detail)) {
        applyVlmPageEvent(detail as VlmPageEvent)
      }
    }
    window.addEventListener('lego-vlm-output', eventListener)

    let eventSource: EventSource | null = null
    if ('EventSource' in window) {
      eventSource = new EventSource(getVlmEventUrl())
      eventSource.onmessage = (message) => {
        try {
          const parsed = JSON.parse(message.data)
          if (isRecord(parsed)) {
            applyVlmPageEvent(parsed as VlmPageEvent)
          }
        } catch {
          // Ignore malformed local controller messages.
        }
      }
    }

    return () => {
      window.removeEventListener('lego-vlm-output', eventListener)
      if (window.__legoGlassApplyVlmOutput === applyVlmPageEvent) {
        delete window.__legoGlassApplyVlmOutput
      }
      eventSource?.close()
    }
  }, [applyVlmPageEvent])

  const handleToggleConnection = () => {
    setIsConnected((connected) => {
      const nextConnected = !connected
      pushFeedback({
        tone: nextConnected ? 'ok' : 'warning',
        title: nextConnected ? t('设备链路已连接') : t('设备链路已断开'),
        detail: nextConnected
          ? t('{{source}} 已准备同步画面、语音和活动状态。', { source: sourceLabel })
          : t('已暂停设备输入，活动状态保留在本地。'),
        stepId: activeStepId
      })
      if (!nextConnected) setIsRunning(false)
      return nextConnected
    })
  }

  const handleToggleRunning = async () => {
    if (isStartingVlm) return

    if (isRunning) {
      setIsRunning(false)
      setIsStartingVlm(true)
      try {
        const status = await stopQwenVlmRun()
        setVlmRunStatus(status)
        pushFeedback({
          tone: 'warning',
          title: t('Qwen-LegoAgent 已暂停'),
          detail: t('视频播放已暂停，VLM 和 Realtime 子进程已停止。'),
          stepId: activeStepId
        })
      } catch (error) {
        const message = error instanceof Error ? error.message : t('停止 VLM 失败')
        setVlmRunStatus({ running: false, error: message })
        pushFeedback({
          tone: 'warning',
          title: t('教学服务停止请求失败'),
          detail: message,
          stepId: activeStepId
        })
      } finally {
        setIsStartingVlm(false)
      }
      return
    }

    const runSourceMode = sourceMode
    const runStreamUrl = streamUrl.trim() || defaultStreamUrl
    const runConfig = buildQwenVlmStartConfig(runSourceMode, runStreamUrl)
    const runSourceUrl =
      runSourceMode === 'glasses'
        ? runStreamUrl
        : runSourceMode === 'rtmp'
          ? publicRtmpFlvUrl
          : simulationVideoUrl

    if (runSourceMode === 'glasses' && runStreamUrl !== streamUrl) {
      setStreamUrl(runStreamUrl)
    }

    setIsConnected(true)
    setElapsedSeconds(0)
    setCompletedStepIds(new Set())
    appliedVlmEventKeysRef.current = new Set()
    setVlmPageEvents([])
    setShowVlmHistory(false)
    applyLocalStepSelection(1)
    setSimulationVideoVersion((version) => version + 1)
    setIsRunning(true)
    setIsStartingVlm(true)

    try {
      const status = await startQwenVlmRun(runConfig)
      setVlmRunStatus(status)
      pushFeedback({
        tone: 'ok',
        title: status.already_running ? t('Qwen-LegoAgent 已在运行') : t('Qwen-LegoAgent 已启动'),
        detail: [
          t('视频源：{{source}}', { source: getSourceModeLabel(runSourceMode, t) }),
          t('处理路径：{{path}}', { path: runSourceUrl }),
          t('后端源：{{source}}', { source: String(status.source_mode || runConfig.source_mode || '') }),
          t('VLM采样：{{seconds}}s', { seconds: status.default_interval_seconds ?? t('backend 默认') }),
          t('Realtime：{{status}}{{pid}}', {
            status: status.realtime?.running ? t('已启动') : t('未运行'),
            pid: status.realtime?.pid ? ` pid=${status.realtime.pid}` : ''
          }),
          status.pid ? `VLM pid=${status.pid}` : '',
          t('事件：{{url}}', { url: getVlmEventUrl() })
        ].filter(Boolean).join('\n'),
        stepId: activityStepForLessonStep(1).id
      })
    } catch (error) {
      const message = error instanceof Error ? error.message : t('启动教学服务失败')
      setIsRunning(false)
      setVlmRunStatus({ running: false, error: message })
      pushFeedback({
        tone: 'danger',
        title: t('Qwen-LegoAgent 教学服务启动失败'),
        detail: t('{{message}}\n请先运行：uv run python legoagentbackend/server.py', { message }),
        stepId: activityStepForLessonStep(1).id
      })
    } finally {
      setIsStartingVlm(false)
    }
  }

  const handleCompleteStep = () => {
    const currentStepIndex = selectedImageIndex + 1
    const nextStepIndex = Math.min(currentStepIndex + 1, imageFiles.length)
    applyLocalStepSelection(nextStepIndex, {
      completedThroughStep: currentStepIndex,
      feedbackTitle: t('手动完成 Step {{step}}', { step: currentStepIndex }),
      feedbackDetail: t('本地标记完成并切换到 Step {{step}}。', { step: nextStepIndex })
    })
  }

  const handleAddReviewFeedback = () => {
    pushFeedback({
      tone: 'warning',
      title: t('等待外部 VLM 检查'),
      detail: t('旧视觉进度接口已停用。请由新的 VLM 控制器发送 changepage 事件。'),
      stepId: activeStepId
    })
  }

  const handleVisionProgressFrame = (payload: SimulationFramePayload) => {
    const payloadSourceMode = getPayloadSourceMode(payload)
    if (!isRunning || payloadSourceMode !== sourceMode || stopLegacyAgentApi === 1) return
  }

  const handleRealtimeVideoFrame = (payload: SimulationFramePayload) => {
    const payloadSourceMode = getPayloadSourceMode(payload)
    if (!isRunning || payloadSourceMode !== sourceMode || stopLegacyAgentApi === 1) return
  }

  const handleResetSession = () => {
    void stopQwenVlmRun()
      .then((status) => setVlmRunStatus(status))
      .catch((error) => {
        const message = error instanceof Error ? error.message : t('停止 VLM 失败')
        setVlmRunStatus({ running: false, error: message })
      })
    setIsRunning(false)
    setElapsedSeconds(0)
    setSimulationVideoVersion((version) => version + 1)
    appliedVlmEventKeysRef.current = new Set()
    setVlmPageEvents([])
    setShowVlmHistory(false)
    setCompletedStepIds(new Set())
    applyLocalStepSelection(1)
    setFeedbackLog([])
    pushFeedback({
      tone: 'ok',
      title: t('本地活动已重置'),
      detail: t('旧 agent 接口未调用。VLM 事件记录和步骤横栏已清空。'),
      stepId: activityStepForLessonStep(1).id
    })
  }

  const handleSelectInstructionImage = (filename: string, index: number) => {
    const nextGuide = guides?.steps.find((step) => step.step_index === index + 1)
    applyLocalStepSelection(index + 1, {
      feedbackTitle: t('已切换到 Step {{step}}', { step: index + 1 }),
      feedbackDetail: nextGuide
        ? t('模型指导：{{text}}', { text: nextGuide.placement_instructions })
        : t('已从本地拼装指导图列表切换当前观察步骤：{{filename}}。', { filename })
    })
  }

  const handleSwitchInstructionImage = (offset: number) => {
    const nextIndex = Math.min(
      Math.max(0, selectedImageIndex + offset),
      Math.max(0, imageFiles.length - 1)
    )
    const nextFilename = imageFiles[nextIndex]
    if (!nextFilename || nextIndex === selectedImageIndex) return
    handleSelectInstructionImage(nextFilename, nextIndex)
  }

  return (
    <SafeAreaView style={styles.safeArea}>
      <StatusBar style="dark" />
      <ScrollView
        style={styles.pageScroll}
        contentContainerStyle={styles.appShell}
        keyboardShouldPersistTaps="handled"
      >
        <View style={[styles.dashboard, !isWide ? styles.dashboardStacked : null]}>
          <View
            style={[
              styles.panel,
              styles.criteriaPanel,
              dashboardPanelHeight ? { height: dashboardPanelHeight } : null,
              !isWide ? styles.fullWidthPanel : null
            ]}
          >
            <View style={styles.panelHeader}>
              <View style={styles.panelHeaderText}>
                <Text style={styles.panelTitle}>{t('拼装指导图')}</Text>
                <Text style={styles.panelMeta}>
                  Agent lesson · {agentStatus === 'ready'
                    ? t('{{count}} 张图片', { count: imageFiles.length })
                    : agentStatus === 'loading'
                      ? t('读取中')
                      : t('使用兜底列表')}
                </Text>
              </View>
              <View style={styles.headerStatus}>
                <LanguageToggle />
                <Text style={styles.scoreTotal}>Step {selectedImageIndex + 1}</Text>
              </View>
            </View>

            <InstructionImageList
              imageFiles={imageFiles}
              selectedImageFile={selectedImageFile}
              selectedImageIndex={selectedImageIndex}
              onPrevious={() => handleSwitchInstructionImage(-1)}
              onNext={() => handleSwitchInstructionImage(1)}
            />

            <View style={styles.selectedStepBlock}>
              <View style={styles.selectedStepHeader}>
                <Text style={styles.selectedStepTitle}>Step {selectedImageIndex + 1}</Text>
                <Text style={styles.selectedStepMeta}>{selectedImageFile}</Text>
              </View>
              <View style={styles.checkpointRow}>
                <View style={styles.checkpointTextGroup}>
                  <Text style={styles.checkpointLabel}>{t('所需零件')}</Text>
                  <Text numberOfLines={4} style={styles.checkpointExpectation}>
                    {selectedGuide?.parts_needed || t('暂无 agent 零件说明，可先查看指导图。')}
                  </Text>
                </View>
              </View>
              <View style={styles.checkpointRow}>
                <View style={styles.checkpointTextGroup}>
                  <Text style={styles.checkpointLabel}>{t('摆放说明')}</Text>
                  <Text numberOfLines={5} style={styles.checkpointExpectation}>
                    {selectedGuide?.placement_instructions || t('暂无 agent 摆放说明。')}
                  </Text>
                </View>
              </View>
            </View>
          </View>

          <View
            style={[
              styles.panel,
              styles.activityPanel,
              dashboardPanelHeight ? { height: dashboardPanelHeight } : null,
              !isWide ? styles.fullWidthPanel : null
            ]}
          >
            <View style={styles.panelHeader}>
              <View style={styles.panelHeaderText}>
                <Text style={styles.panelTitle}>{t('实时活动区')}</Text>
                <Text style={styles.panelMeta}>
                  {t('Session LEGO-0614 · 第 {{step}} 步 · {{time}}', {
                    step: Math.max(1, activeStepIndex + 1),
                    time: formatClock(elapsedSeconds)
                  })}
                </Text>
              </View>
              <Metric label={t('当前分')} value={currentScore} meta={`/${totalScore}`} />
            </View>

            <View style={styles.sourcePanel}>
              <View style={styles.sourceModeRow}>
                <SourceModeButton label={t('模拟实时流')} active={sourceMode === 'simulation'} onPress={() => setSourceMode('simulation')} />
                <SourceModeButton label={t('眼镜直播实时流')} active={sourceMode === 'glasses'} onPress={() => setSourceMode('glasses')} />
                <SourceModeButton label={t('RTMP 公网访问')} active={sourceMode === 'rtmp'} onPress={() => setSourceMode('rtmp')} />
              </View>

              <View style={styles.streamUrlRow}>
                <TextInput
                  value={streamInputValue}
                  editable={sourceMode === 'glasses'}
                  onChangeText={setStreamUrl}
                  autoCapitalize="none"
                  autoCorrect={false}
                  keyboardType="url"
                  placeholder={sourceMode === 'rtmp' ? publicRtmpPushUrl : defaultStreamUrl}
                  placeholderTextColor="#98a2b3"
                  style={[styles.streamUrlInput, sourceMode !== 'glasses' ? styles.streamUrlInputReadOnly : null]}
                />
                <ActionButton label={t('同步')} onPress={handleToggleConnection} variant="primary" />
              </View>
              <Text numberOfLines={1} style={styles.streamMeta}>
                {sourceMode === 'simulation'
                  ? t('使用前端模拟实时流校验流程和反馈 UI')
                  : sourceMode === 'rtmp'
                    ? t('眼镜推流地址：{{pushUrl}} · 前端 HTTP-FLV 播放：{{flvUrl}}', {
                      pushUrl: publicRtmpPushUrl,
                      flvUrl: publicRtmpFlvUrl
                    })
                    : `${sourceLabel} · ${isConnected ? t('链路可用') : t('等待连接')}`}
              </Text>
            </View>

            <View style={styles.dualStreamGrid}>
              <StreamSurface
                title={sourceLabel}
                subtitle={activeStreamSubtitle}
                active
                isRunning={isRunning}
                emphasis="large"
              >
                {sourceMode === 'simulation' ? (
                  <SimulationVideoStream
                    currentStep={activeStep}
                    isPlaying={isRunning}
                    onProgressFrame={handleVisionProgressFrame}
                    onRealtimeFrame={handleRealtimeVideoFrame}
                    restartKey={simulationVideoVersion}
                    videoUrl={simulationVideoUrl}
                  />
                ) : sourceMode === 'glasses' ? (
                  <GlassesStreamView
                    captureGlassesStreamFrame={api.captureGlassesStreamFrame}
                    currentStep={activeStep}
                    isPlaying={isRunning}
                    onProgressFrame={handleVisionProgressFrame}
                    onRealtimeFrame={handleRealtimeVideoFrame}
                    streamUrl={streamUrl}
                  />
                ) : (
                  <PublicRtmpFlvStreamView
                    currentStep={activeStep}
                    flvUrl={publicRtmpFlvUrl}
                    isPlaying={isRunning}
                    onProgressFrame={handleVisionProgressFrame}
                    onRealtimeFrame={handleRealtimeVideoFrame}
                    pushUrl={publicRtmpPushUrl}
                  />
                )}
              </StreamSurface>
            </View>

            <View style={styles.controlsRow}>
              <ActionButton
                label={isConnected ? t('断开设备') : t('连接设备')}
                onPress={handleToggleConnection}
              />
              <ActionButton
                label={isStartingVlm ? t('教学启动中') : isRunning ? t('暂停教学') : t('开始教学')}
                onPress={() => { void handleToggleRunning() }}
                variant="primary"
                disabled={isStartingVlm}
              />
              <ActionButton
                label={t('完成当前步')}
                onPress={handleCompleteStep}
                disabled={currentLessonStepCompleted}
              />
              <ActionButton label={t('重新检查')} onPress={handleAddReviewFeedback} />
              <ActionButton label={t('重置会话')} onPress={handleResetSession} variant="danger" />
            </View>

            <StepTimeline
              steps={activitySteps}
              activeStepId={activeStepId}
              completedStepIds={completedStepIds}
            />
          </View>

          <View
            style={[
              styles.panel,
              styles.vlmEventPanel,
              dashboardPanelHeight ? { height: dashboardPanelHeight } : null,
              !isWide ? styles.fullWidthPanel : null
            ]}
          >
            <View style={styles.panelHeader}>
              <View style={styles.panelHeaderText}>
                <Text style={styles.panelTitle}>{t('VLM 返回结果')}</Text>
                <Text style={styles.panelMeta}>
                  {t('接收 VLM output，并显示翻页判定')}
                </Text>
              </View>
            </View>

            <ScrollView
              style={styles.vlmEventScroll}
              nestedScrollEnabled
              contentContainerStyle={styles.vlmEventContent}
            >
              <VlmEventCard
                label={t('翻页判定')}
                value={getVlmPageDecisionLabel(latestVlmPageEvent, t)}
              />
              <VlmEventCard
                label={t('VLM 返回结果')}
                value={formatLatestVlmOutput(latestVlmPageEvent, t)}
                meta={latestVlmPageEvent ? t('最新 output · {{time}}', { time: latestVlmPageEvent.receivedAt }) : t('等待第一条 VLM output')}
              >
                {showVlmHistory && previousVlmPageEvents.length > 0 ? (
                  <Text selectable style={styles.vlmEventHistoryValue}>
                    {formatVlmOutputLog(previousVlmPageEvents, t)}
                  </Text>
                ) : null}
                <Pressable
                  accessibilityRole="button"
                  disabled={previousVlmPageEvents.length === 0}
                  onPress={() => setShowVlmHistory((visible) => !visible)}
                  style={({ pressed }) => [
                    styles.vlmHistoryButton,
                    previousVlmPageEvents.length === 0 ? styles.vlmHistoryButtonDisabled : null,
                    pressed && previousVlmPageEvents.length > 0 ? styles.vlmHistoryButtonPressed : null
                  ]}
                >
                  <Text style={styles.vlmHistoryButtonText}>
                    {showVlmHistory
                      ? t('收起之前的反馈')
                      : t('查看之前的所有反馈 ({{count}})', { count: previousVlmPageEvents.length })}
                  </Text>
                </Pressable>
              </VlmEventCard>
            </ScrollView>
          </View>
        </View>
      </ScrollView>
    </SafeAreaView>
  )
}

const styles = StyleSheet.create({
  safeArea: {
    flex: 1,
    backgroundColor: '#eef2f6'
  },
  pageScroll: {
    flex: 1
  },
  appShell: {
    flexGrow: 1,
    gap: 14,
    maxWidth: '100%',
    padding: 16,
    position: 'relative',
    width: '100%'
  },
  dropdownBackdrop: {
    ...StyleSheet.absoluteFill,
    backgroundColor: 'transparent',
    zIndex: 20
  },
  header: {
    alignItems: 'center',
    backgroundColor: '#ffffff',
    borderColor: '#d0d5dd',
    borderRadius: 8,
    borderWidth: 1,
    flexDirection: 'row',
    gap: 16,
    justifyContent: 'space-between',
    minHeight: 70,
    paddingHorizontal: 18,
    paddingVertical: 14
  },
  headerStacked: {
    alignItems: 'stretch',
    flexDirection: 'column'
  },
  headerTitleGroup: {
    flex: 1,
    minWidth: 0
  },
  appTitle: {
    color: '#101828',
    fontSize: 23,
    fontWeight: '800'
  },
  appSubtitle: {
    color: '#667085',
    fontSize: 13,
    lineHeight: 18,
    marginTop: 4
  },
  headerStatus: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 8,
    justifyContent: 'flex-end'
  },
  headerStatusStacked: {
    justifyContent: 'flex-start'
  },
  statusPill: {
    backgroundColor: '#f2f4f7',
    borderColor: '#d0d5dd',
    borderRadius: 999,
    borderWidth: 1,
    paddingHorizontal: 11,
    paddingVertical: 7
  },
  statusText: {
    color: '#475467',
    fontSize: 12,
    fontWeight: '800'
  },
  statusOnline: {
    backgroundColor: '#ecfdf3',
    borderColor: '#75e0a7'
  },
  statusOnlineText: {
    color: '#067647'
  },
  statusLive: {
    backgroundColor: '#eff8ff',
    borderColor: '#84caff'
  },
  statusLiveText: {
    color: '#175cd3'
  },
  statusWarning: {
    backgroundColor: '#fff8df',
    borderColor: '#fdb022'
  },
  statusWarningText: {
    color: '#b54708'
  },
  modelPanel: {
    backgroundColor: '#ffffff',
    borderColor: '#d0d5dd',
    borderRadius: 8,
    borderWidth: 1,
    gap: 12,
    padding: 14,
    position: 'relative',
    zIndex: 2
  },
  modelPanelRaised: {
    zIndex: 30
  },
  modelPanelHeader: {
    alignItems: 'flex-start',
    flexDirection: 'row',
    gap: 12,
    justifyContent: 'space-between'
  },
  modelPanelHeaderStacked: {
    flexDirection: 'column'
  },
  panelHeader: {
    alignItems: 'flex-start',
    flexDirection: 'row',
    gap: 12,
    justifyContent: 'space-between'
  },
  panelHeaderText: {
    flex: 1,
    minWidth: 0
  },
  panelTitle: {
    color: '#101828',
    fontSize: 17,
    fontWeight: '800'
  },
  panelMeta: {
    color: '#667085',
    fontSize: 12,
    lineHeight: 17,
    marginTop: 3
  },
  modelSummary: {
    alignSelf: 'stretch',
    color: '#175cd3',
    flexShrink: 1,
    fontSize: 12,
    fontWeight: '800',
    lineHeight: 17,
    textAlign: 'right'
  },
  modelSelectorGrid: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 12,
    zIndex: 3
  },
  modelSelectorGroup: {
    flex: 1,
    gap: 7,
    minWidth: 230,
    overflow: 'visible',
    position: 'relative'
  },
  modelSelectorGroupOpen: {
    zIndex: 100
  },
  modelCategoryLabel: {
    color: '#101828',
    fontSize: 13,
    fontWeight: '800'
  },
  modelCategoryMeta: {
    color: '#667085',
    fontSize: 11,
    lineHeight: 15
  },
  modelDropdownTrigger: {
    alignItems: 'center',
    backgroundColor: '#fcfcfd',
    borderColor: '#d0d5dd',
    borderRadius: 8,
    borderWidth: 1,
    flexDirection: 'row',
    gap: 10,
    justifyContent: 'space-between',
    minHeight: 56,
    paddingHorizontal: 12,
    paddingVertical: 9
  },
  modelDropdownTriggerOpen: {
    borderColor: '#1570ef'
  },
  modelDropdownTextGroup: {
    flex: 1,
    minWidth: 0
  },
  modelDropdownLabel: {
    color: '#101828',
    fontSize: 13,
    fontWeight: '800'
  },
  modelDropdownMeta: {
    color: '#667085',
    fontSize: 11,
    marginTop: 3
  },
  modelDropdownChevron: {
    color: '#475467',
    fontSize: 11,
    fontWeight: '800',
    textTransform: 'uppercase'
  },
  modelDropdownPopover: {
    backgroundColor: '#ffffff',
    borderColor: '#d0d5dd',
    borderRadius: 8,
    borderWidth: 1,
    elevation: 8,
    left: 0,
    maxHeight: 220,
    overflow: 'hidden',
    position: 'absolute',
    right: 0,
    top: 85,
    zIndex: 200
  },
  modelDropdownOption: {
    alignItems: 'center',
    borderBottomColor: '#eaecf0',
    borderBottomWidth: 1,
    flexDirection: 'row',
    gap: 8,
    justifyContent: 'space-between',
    minHeight: 54,
    paddingHorizontal: 12,
    paddingVertical: 9
  },
  modelDropdownOptionSelected: {
    backgroundColor: '#eff8ff'
  },
  modelDropdownOptionTextGroup: {
    flex: 1,
    minWidth: 0
  },
  modelDropdownOptionLabel: {
    color: '#344054',
    fontSize: 12,
    fontWeight: '800'
  },
  modelDropdownOptionLabelSelected: {
    color: '#175cd3'
  },
  modelDropdownOptionMeta: {
    color: '#667085',
    fontSize: 11,
    marginTop: 3
  },
  modelDropdownCheck: {
    color: '#98a2b3',
    fontSize: 10,
    fontWeight: '800',
    textTransform: 'uppercase',
    width: 56
  },
  modelDropdownCheckSelected: {
    color: '#175cd3'
  },
  dashboard: {
    alignItems: 'stretch',
    flexDirection: 'row',
    gap: 14,
    width: '100%'
  },
  dashboardStacked: {
    flexDirection: 'column'
  },
  panel: {
    backgroundColor: '#ffffff',
    borderColor: '#d0d5dd',
    borderRadius: 8,
    borderWidth: 1,
    display: 'flex',
    gap: 14,
    padding: 14
  },
  criteriaPanel: {
    flexBasis: 0,
    flexGrow: 2,
    flexShrink: 1,
    minWidth: 0
  },
  activityPanel: {
    flexBasis: 0,
    flexGrow: 1,
    flexShrink: 1,
    minWidth: 0
  },
  vlmEventPanel: {
    flexBasis: 0,
    flexGrow: 1,
    flexShrink: 1,
    minWidth: 0
  },
  fullWidthPanel: {
    alignSelf: 'stretch',
    flexBasis: 'auto',
    maxWidth: '100%',
    minWidth: 0,
    width: '100%'
  },
  scoreTotal: {
    color: '#175cd3',
    fontSize: 18,
    fontWeight: '900'
  },
  imageStepButton: {
    alignItems: 'center',
    backgroundColor: '#eff8ff',
    borderColor: '#84caff',
    borderRadius: 8,
    borderWidth: 1,
    height: 34,
    justifyContent: 'center',
    width: 34
  },
  imageStepButtonDisabled: {
    backgroundColor: '#f2f4f7',
    borderColor: '#eaecf0'
  },
  imageStepButtonText: {
    color: '#175cd3',
    fontSize: 17,
    fontWeight: '900',
    lineHeight: 19
  },
  imageStepButtonTextDisabled: {
    color: '#98a2b3'
  },
  instructionImageViewer: {
    flex: 1,
    gap: 10,
    justifyContent: 'center',
    minHeight: 0
  },
  imageStepControlRow: {
    alignItems: 'center',
    flexShrink: 0,
    justifyContent: 'center'
  },
  instructionImageSingleCard: {
    backgroundColor: '#fcfcfd',
    borderColor: '#d0d5dd',
    borderRadius: 8,
    borderWidth: 1,
    flexShrink: 1,
    overflow: 'hidden'
  },
  instructionImage: {
    aspectRatio: 842 / 595,
    backgroundColor: '#f2f4f7',
    width: '100%'
  },
  instructionImageFooter: {
    gap: 3,
    paddingHorizontal: 10,
    paddingVertical: 9
  },
  instructionImageTitle: {
    color: '#344054',
    fontSize: 13,
    fontWeight: '900'
  },
  instructionImageTitleSelected: {
    color: '#175cd3'
  },
  instructionImageMeta: {
    color: '#667085',
    fontSize: 11,
    fontWeight: '700'
  },
  stepList: {
    maxHeight: 430
  },
  stepListContent: {
    gap: 10
  },
  stepRow: {
    backgroundColor: '#fcfcfd',
    borderColor: '#eaecf0',
    borderRadius: 8,
    borderWidth: 1,
    gap: 8,
    padding: 12
  },
  stepRowSelected: {
    borderColor: '#84caff'
  },
  stepRowActive: {
    backgroundColor: '#eff8ff',
    borderColor: '#1570ef'
  },
  stepRowTop: {
    alignItems: 'center',
    flexDirection: 'row',
    gap: 8,
    justifyContent: 'space-between'
  },
  stepName: {
    color: '#344054',
    flex: 1,
    fontSize: 14,
    fontWeight: '800'
  },
  stepNameActive: {
    color: '#175cd3'
  },
  stepGoal: {
    color: '#667085',
    fontSize: 12,
    lineHeight: 17
  },
  stepMetaRow: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 8
  },
  stepMeta: {
    color: '#667085',
    fontSize: 11,
    fontWeight: '700'
  },
  stepStatusBadge: {
    backgroundColor: '#f2f4f7',
    borderColor: '#d0d5dd',
    borderRadius: 999,
    borderWidth: 1,
    paddingHorizontal: 8,
    paddingVertical: 4
  },
  stepStatusText: {
    color: '#475467',
    fontSize: 11,
    fontWeight: '800'
  },
  stepStatusActive: {
    backgroundColor: '#eff8ff',
    borderColor: '#84caff'
  },
  stepStatusActiveText: {
    color: '#175cd3'
  },
  stepStatusDone: {
    backgroundColor: '#ecfdf3',
    borderColor: '#75e0a7'
  },
  stepStatusDoneText: {
    color: '#067647'
  },
  stepStatusWarning: {
    backgroundColor: '#fff8df',
    borderColor: '#fdb022'
  },
  stepStatusWarningText: {
    color: '#b54708'
  },
  selectedStepBlock: {
    backgroundColor: '#f8fafc',
    borderColor: '#eaecf0',
    borderRadius: 8,
    borderWidth: 1,
    gap: 8,
    flexShrink: 0,
    padding: 12
  },
  selectedStepHeader: {
    alignItems: 'center',
    flexDirection: 'row',
    justifyContent: 'space-between'
  },
  selectedStepTitle: {
    color: '#101828',
    fontSize: 14,
    fontWeight: '900'
  },
  selectedStepMeta: {
    color: '#175cd3',
    fontSize: 13,
    fontWeight: '900'
  },
  checkpointRow: {
    alignItems: 'center',
    backgroundColor: '#ffffff',
    borderColor: '#eaecf0',
    borderRadius: 8,
    borderWidth: 1,
    flexDirection: 'row',
    gap: 10,
    padding: 10
  },
  checkpointTextGroup: {
    flex: 1,
    minWidth: 0
  },
  checkpointLabel: {
    color: '#344054',
    fontSize: 12,
    fontWeight: '900'
  },
  checkpointExpectation: {
    color: '#667085',
    fontSize: 11,
    lineHeight: 15,
    marginTop: 3
  },
  checkpointScore: {
    color: '#175cd3',
    fontSize: 15,
    fontWeight: '900',
    textAlign: 'right',
    width: 28
  },
  metric: {
    alignItems: 'flex-end',
    minWidth: 72
  },
  metricLabel: {
    color: '#667085',
    fontSize: 11,
    fontWeight: '800'
  },
  metricValue: {
    color: '#101828',
    fontSize: 24,
    fontWeight: '900',
    marginTop: 2
  },
  metricMeta: {
    color: '#667085',
    fontSize: 11,
    fontWeight: '800'
  },
  sourcePanel: {
    backgroundColor: '#f8fafc',
    borderColor: '#eaecf0',
    borderRadius: 8,
    borderWidth: 1,
    gap: 10,
    padding: 12
  },
  sourceModeRow: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 8
  },
  sourceModeButton: {
    backgroundColor: '#ffffff',
    borderColor: '#d0d5dd',
    borderRadius: 8,
    borderWidth: 1,
    minHeight: 36,
    paddingHorizontal: 12,
    paddingVertical: 9
  },
  sourceModeButtonActive: {
    backgroundColor: '#eff8ff',
    borderColor: '#1570ef'
  },
  sourceModeButtonText: {
    color: '#475467',
    fontSize: 12,
    fontWeight: '800'
  },
  sourceModeButtonTextActive: {
    color: '#175cd3'
  },
  streamUrlRow: {
    alignItems: 'center',
    flexDirection: 'row',
    gap: 8
  },
  streamUrlInput: {
    backgroundColor: '#ffffff',
    borderColor: '#d0d5dd',
    borderRadius: 8,
    borderWidth: 1,
    color: '#101828',
    flex: 1,
    fontSize: 13,
    minHeight: 42,
    minWidth: 0,
    paddingHorizontal: 12
  },
  streamUrlInputReadOnly: {
    backgroundColor: '#f2f4f7',
    color: '#667085'
  },
  streamMeta: {
    color: '#667085',
    fontSize: 12
  },
  dualStreamGrid: {
    alignItems: 'stretch',
    flex: 1,
    flexDirection: 'column',
    gap: 12,
    minHeight: 0
  },
  streamSurface: {
    backgroundColor: '#101828',
    borderColor: '#344054',
    borderRadius: 8,
    borderWidth: 1,
    flex: 1,
    minHeight: 0,
    overflow: 'hidden'
  },
  streamSurfaceCompact: {
    flex: 0.72,
    minHeight: 92
  },
  streamSurfaceLarge: {
    flex: 1.9,
    minHeight: 180
  },
  streamSurfaceActive: {
    borderColor: '#1570ef',
    borderWidth: 2
  },
  streamSurfaceHeader: {
    alignItems: 'center',
    backgroundColor: 'rgba(16, 24, 40, 0.9)',
    flexDirection: 'row',
    gap: 10,
    justifyContent: 'space-between',
    paddingHorizontal: 12,
    paddingVertical: 10
  },
  streamSurfaceTitleGroup: {
    flex: 1,
    minWidth: 0
  },
  streamSurfaceTitle: {
    color: '#ffffff',
    fontSize: 13,
    fontWeight: '900'
  },
  streamSurfaceSubtitle: {
    color: '#cbd5e1',
    fontSize: 11,
    fontWeight: '700',
    marginTop: 2
  },
  streamSurfaceBody: {
    flex: 1,
    minHeight: 0,
    position: 'relative',
    width: '100%'
  },
  simulationVideoFrame: {
    backgroundColor: '#0f172a',
    height: '100%',
    overflow: 'hidden',
    position: 'relative',
    width: '100%'
  },
  simulationVideo: {
    backgroundColor: '#101828',
    height: '100%',
    objectFit: 'contain',
    width: '100%'
  },
  glassesStreamFrame: {
    backgroundColor: '#101828',
    height: '100%',
    overflow: 'hidden',
    position: 'relative',
    width: '100%'
  },
  glassesStreamImage: {
    height: '100%',
    objectFit: 'cover',
    width: '100%'
  },
  publicFlvFrame: {
    backgroundColor: '#101828',
    height: '100%',
    overflow: 'hidden',
    position: 'relative',
    width: '100%'
  },
  publicFlvVideo: {
    backgroundColor: '#101828',
    height: '100%',
    objectFit: 'contain',
    width: '100%'
  },
  previewFrame: {
    aspectRatio: 16 / 9,
    backgroundColor: '#101828',
    borderRadius: 8,
    minHeight: 260,
    overflow: 'hidden',
    position: 'relative',
    width: '100%'
  },
  previewHudTop: {
    alignItems: 'center',
    backgroundColor: 'rgba(16, 24, 40, 0.78)',
    flexDirection: 'row',
    gap: 12,
    justifyContent: 'space-between',
    left: 0,
    paddingHorizontal: 14,
    paddingVertical: 10,
    position: 'absolute',
    right: 0,
    top: 0,
    zIndex: 3
  },
  previewHudText: {
    color: '#ffffff',
    flexShrink: 1,
    fontSize: 12,
    fontWeight: '800'
  },
  legoBoard: {
    backgroundColor: '#dbe4ee',
    height: '100%',
    position: 'relative',
    width: '100%'
  },
  brick: {
    borderColor: 'rgba(16, 24, 40, 0.25)',
    borderRadius: 6,
    borderWidth: 1,
    minHeight: 22,
    position: 'absolute'
  },
  brickLong: {
    height: '10%',
    width: '34%'
  },
  brickMedium: {
    height: '11%',
    width: '24%'
  },
  brickShort: {
    height: '12%',
    width: '14%'
  },
  brickHub: {
    height: '16%',
    left: '43%',
    top: '45%',
    width: '14%'
  },
  brickBlue: {
    backgroundColor: '#2e90fa'
  },
  brickYellow: {
    backgroundColor: '#fdb022'
  },
  brickGreen: {
    backgroundColor: '#12b76a'
  },
  brickRed: {
    backgroundColor: '#f04438'
  },
  wheel: {
    backgroundColor: '#344054',
    borderColor: '#101828',
    borderRadius: 999,
    borderWidth: 4,
    height: '19%',
    position: 'absolute',
    width: '12%'
  },
  previewBottomBar: {
    alignItems: 'center',
    backgroundColor: 'rgba(16, 24, 40, 0.78)',
    bottom: 0,
    flexDirection: 'row',
    gap: 8,
    left: 0,
    paddingHorizontal: 14,
    paddingVertical: 10,
    position: 'absolute',
    right: 0
  },
  liveDot: {
    backgroundColor: '#98a2b3',
    borderRadius: 999,
    height: 10,
    width: 10
  },
  liveDotOn: {
    backgroundColor: '#12b76a'
  },
  previewBottomText: {
    color: '#ffffff',
    flex: 1,
    fontSize: 12,
    fontWeight: '800'
  },
  controlsRow: {
    alignItems: 'center',
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 8
  },
  button: {
    alignItems: 'center',
    backgroundColor: '#ffffff',
    borderColor: '#d0d5dd',
    borderRadius: 8,
    borderWidth: 1,
    justifyContent: 'center',
    minHeight: 40,
    paddingHorizontal: 13,
    paddingVertical: 9
  },
  buttonPrimary: {
    backgroundColor: '#1570ef',
    borderColor: '#1570ef'
  },
  buttonDanger: {
    backgroundColor: '#fff1f3',
    borderColor: '#fecdca'
  },
  buttonDisabled: {
    backgroundColor: '#f2f4f7',
    borderColor: '#eaecf0'
  },
  buttonPressed: {
    opacity: 0.76
  },
  buttonText: {
    color: '#344054',
    fontSize: 13,
    fontWeight: '900'
  },
  buttonPrimaryText: {
    color: '#ffffff'
  },
  buttonDangerText: {
    color: '#b42318'
  },
  buttonDisabledText: {
    color: '#98a2b3'
  },
  voiceToggle: {
    alignItems: 'center',
    backgroundColor: '#f8fafc',
    borderColor: '#eaecf0',
    borderRadius: 8,
    borderWidth: 1,
    flexDirection: 'row',
    gap: 8,
    minHeight: 40,
    paddingHorizontal: 10
  },
  voiceToggleText: {
    color: '#344054',
    fontSize: 12,
    fontWeight: '800'
  },
  timelineBlock: {
    backgroundColor: '#f8fafc',
    borderColor: '#eaecf0',
    borderRadius: 8,
    borderWidth: 1,
    gap: 10,
    padding: 12
  },
  timelineHeader: {
    alignItems: 'center',
    flexDirection: 'row',
    justifyContent: 'space-between'
  },
  timelineTitle: {
    color: '#101828',
    fontSize: 14,
    fontWeight: '900'
  },
  timelineMeta: {
    color: '#667085',
    fontSize: 12,
    fontWeight: '800'
  },
  timelineTrack: {
    flexDirection: 'row',
    gap: 4,
    height: 28
  },
  timelineSegment: {
    alignItems: 'center',
    backgroundColor: '#d0d5dd',
    borderColor: '#98a2b3',
    borderRadius: 6,
    borderWidth: 1,
    justifyContent: 'center',
    minWidth: 20
  },
  timelineSegmentActive: {
    backgroundColor: '#84caff',
    borderColor: '#1570ef'
  },
  timelineSegmentDone: {
    backgroundColor: '#75e0a7',
    borderColor: '#079455'
  },
  timelineSegmentText: {
    color: '#ffffff',
    fontSize: 11,
    fontWeight: '900'
  },
  timelineLegend: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 8
  },
  timelineLegendItem: {
    alignItems: 'center',
    flexDirection: 'row',
    gap: 5,
    maxWidth: 150
  },
  timelineLegendDot: {
    backgroundColor: '#98a2b3',
    borderRadius: 999,
    height: 8,
    width: 8
  },
  timelineLegendDotActive: {
    backgroundColor: '#1570ef'
  },
  timelineLegendDotDone: {
    backgroundColor: '#079455'
  },
  timelineLegendText: {
    color: '#667085',
    flexShrink: 1,
    fontSize: 11,
    fontWeight: '700'
  },
  vlmEventMetaRow: {
    alignItems: 'center',
    backgroundColor: '#f8fafc',
    borderColor: '#eaecf0',
    borderRadius: 8,
    borderWidth: 1,
    flexDirection: 'row',
    gap: 8,
    justifyContent: 'space-between',
    paddingHorizontal: 10,
    paddingVertical: 8
  },
  vlmEventMetaText: {
    color: '#475467',
    flexShrink: 1,
    fontSize: 11,
    fontWeight: '800'
  },
  vlmEventScroll: {
    flex: 1,
    minHeight: 260
  },
  vlmEventContent: {
    gap: 10,
    paddingBottom: 4
  },
  vlmEventCard: {
    backgroundColor: '#f8fafc',
    borderColor: '#eaecf0',
    borderRadius: 8,
    borderWidth: 1,
    gap: 7,
    padding: 12
  },
  vlmEventLabel: {
    color: '#175cd3',
    fontSize: 12,
    fontWeight: '900'
  },
  vlmEventValue: {
    color: '#101828',
    fontSize: 13,
    fontWeight: '700',
    lineHeight: 19
  },
  vlmEventMeta: {
    borderTopColor: '#e2e8f0',
    borderTopWidth: 1,
    color: '#667085',
    fontSize: 11,
    fontWeight: '700',
    paddingTop: 7
  },
  vlmEventHistoryValue: {
    borderTopColor: '#e2e8f0',
    borderTopWidth: 1,
    color: '#344054',
    fontSize: 12,
    fontWeight: '700',
    lineHeight: 18,
    paddingTop: 10
  },
  vlmHistoryButton: {
    alignItems: 'center',
    backgroundColor: '#eff8ff',
    borderColor: '#b2ddff',
    borderRadius: 8,
    borderWidth: 1,
    minHeight: 36,
    justifyContent: 'center',
    paddingHorizontal: 10,
    paddingVertical: 8
  },
  vlmHistoryButtonPressed: {
    opacity: 0.72
  },
  vlmHistoryButtonDisabled: {
    backgroundColor: '#f8fafc',
    borderColor: '#eaecf0',
    opacity: 0.68
  },
  vlmHistoryButtonText: {
    color: '#175cd3',
    fontSize: 12,
    fontWeight: '900'
  },
  vlmEventStatusRow: {
    alignItems: 'center',
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 8,
    justifyContent: 'space-between'
  },
  vlmEventStatusText: {
    color: '#667085',
    fontSize: 11,
    fontWeight: '800'
  }
})

export default LegoActivityDashboard
