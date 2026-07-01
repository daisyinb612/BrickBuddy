import React, { useEffect, useMemo, useRef, useState } from 'react'
import * as DocumentPicker from 'expo-document-picker'
import { StatusBar } from 'expo-status-bar'
import {
  ActivityIndicator,
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

import { createTeachingGuideApi } from '../api/teachingGuide'
import type {
  GuidesData,
  LessonPlansData,
  ProgressStatus,
  ProjectMetadata,
  UploadFile
} from '../types/teachingGuide'
import { LanguageToggle, useI18n, type TranslationParams, type TFunction } from '../i18n'

type Tone = 'neutral' | 'ok' | 'warning' | 'danger'
type MessageState = {
  key: string
  params?: TranslationParams
  raw?: string
}

const defaultApiBaseUrl = 'http://127.0.0.1:5005'
const defaultMaterialsKey =
  '天坛是明清两代皇帝祭天、祈谷的建筑群。讲解时可结合圆形屋顶、层级结构、中心轴线和古代礼制，引导学生观察建筑结构与文化含义。'

function toUploadFile(asset: DocumentPicker.DocumentPickerAsset): UploadFile {
  return {
    uri: asset.uri,
    name: asset.name,
    type: asset.mimeType,
    file: asset.file
  }
}

function getProgressLabel(progress: ProgressStatus | null, t: TFunction): string {
  if (!progress) return t('未开始')
  if (progress.status === 'processing') return `${progress.current_step}/${progress.total_steps}`
  if (progress.status === 'completed') return t('已完成')
  if (progress.status === 'error') return t('出错')
  return t('未开始')
}

function getFileMeta(file: UploadFile | null, t: TFunction): string {
  if (!file) return t('未选择')
  return file.type ? `${file.name} · ${file.type}` : file.name
}

function ToolbarButton({
  label,
  onPress,
  disabled = false,
  variant = 'secondary'
}: {
  label: string
  onPress: () => void
  disabled?: boolean
  variant?: 'primary' | 'secondary' | 'danger'
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

function StatusBadge({ label, tone = 'neutral' }: { label: string; tone?: Tone }) {
  return (
    <View
      style={[
        styles.badge,
        tone === 'ok' ? styles.badgeOk : null,
        tone === 'warning' ? styles.badgeWarning : null,
        tone === 'danger' ? styles.badgeDanger : null
      ]}
    >
      <Text
        style={[
          styles.badgeText,
          tone === 'ok' ? styles.badgeTextOk : null,
          tone === 'warning' ? styles.badgeTextWarning : null,
          tone === 'danger' ? styles.badgeTextDanger : null
        ]}
      >
        {label}
      </Text>
    </View>
  )
}

function Section({
  title,
  meta,
  children
}: {
  title: string
  meta?: string
  children: React.ReactNode
}) {
  return (
    <View style={styles.section}>
      <View style={styles.sectionHeader}>
        <Text style={styles.sectionTitle}>{title}</Text>
        {meta ? <Text style={styles.sectionMeta}>{meta}</Text> : null}
      </View>
      {children}
    </View>
  )
}

function FileRow({
  label,
  file,
  onPick,
  optional = false
}: {
  label: string
  file: UploadFile | null
  onPick: () => void
  optional?: boolean
}) {
  const { t } = useI18n()

  return (
    <View style={styles.fileRow}>
      <View style={styles.fileInfo}>
        <Text style={styles.fileLabel}>{label}</Text>
        <Text numberOfLines={1} style={styles.fileMeta}>
          {getFileMeta(file, t)}
        </Text>
      </View>
      {optional && !file ? <StatusBadge label={t('可选')} /> : null}
      <ToolbarButton label={t('选择')} onPress={onPick} />
    </View>
  )
}

function GuidePreview({
  apiBaseUrl,
  projectId,
  guides
}: {
  apiBaseUrl: string
  projectId: string | null
  guides: GuidesData | null
}) {
  const { t } = useI18n()

  if (!projectId || !guides?.steps.length) {
    return <Text style={styles.emptyText}>{t('暂无步骤说明')}</Text>
  }

  const api = createTeachingGuideApi(apiBaseUrl)
  return (
    <View style={styles.previewList}>
      {guides.steps.slice(0, 4).map((step) => (
        <View key={step.step_index} style={styles.previewItem}>
          <Image source={{ uri: api.getImageUrl(projectId, step.image_file) }} style={styles.previewImage} />
          <View style={styles.previewText}>
            <Text style={styles.previewTitle}>Step {step.step_index}</Text>
            <Text numberOfLines={2} style={styles.previewBody}>
              {step.parts_needed}
            </Text>
            <Text numberOfLines={3} style={styles.previewBodyMuted}>
              {step.placement_instructions}
            </Text>
          </View>
        </View>
      ))}
    </View>
  )
}

function LessonPreview({ lessonPlans }: { lessonPlans: LessonPlansData | null }) {
  const { t } = useI18n()

  if (!lessonPlans?.steps.length) {
    return <Text style={styles.emptyText}>{t('暂无教案内容')}</Text>
  }

  return (
    <View style={styles.previewList}>
      {lessonPlans.steps.slice(0, 5).map((step) => (
        <View key={step.step_index} style={styles.lessonItem}>
          <Text style={styles.previewTitle}>Step {step.step_index}</Text>
          <Text style={styles.previewBody}>{step.cultural_knowledge || t('本步骤不安排文化讲解')}</Text>
          {step.teaching_notes ? <Text style={styles.previewBodyMuted}>{step.teaching_notes}</Text> : null}
        </View>
      ))}
    </View>
  )
}

export default function TeachingGuideDashboard() {
  const { t } = useI18n()
  const { width } = useWindowDimensions()
  const isWide = width >= 980
  const defaultMaterials = t(defaultMaterialsKey)
  const previousDefaultMaterialsRef = useRef(defaultMaterials)

  const [apiBaseUrl, setApiBaseUrl] = useState(defaultApiBaseUrl)
  const api = useMemo(() => createTeachingGuideApi(apiBaseUrl), [apiBaseUrl])

  const [projectId, setProjectId] = useState<string | null>(null)
  const [pdfFile, setPdfFile] = useState<UploadFile | null>(null)
  const [stepsFile, setStepsFile] = useState<UploadFile | null>(null)
  const [metadata, setMetadata] = useState<ProjectMetadata | null>(null)
  const [materials, setMaterials] = useState(() => t(defaultMaterialsKey))
  const [conversionProgress, setConversionProgress] = useState<ProgressStatus | null>(null)
  const [generationProgress, setGenerationProgress] = useState<ProgressStatus | null>(null)
  const [guides, setGuides] = useState<GuidesData | null>(null)
  const [lessonPlans, setLessonPlans] = useState<LessonPlansData | null>(null)
  const [busyLabel, setBusyLabel] = useState<string | null>(null)
  const [message, setMessage] = useState<MessageState>({ key: '后端地址已指向 5005' })
  const [healthTone, setHealthTone] = useState<Tone>('neutral')
  const messageLabel = message.raw || t(message.key, message.params)

  useEffect(() => {
    setMaterials((current) => {
      return current === previousDefaultMaterialsRef.current || current === defaultMaterialsKey
        ? defaultMaterials
        : current
    })
    previousDefaultMaterialsRef.current = defaultMaterials
  }, [defaultMaterials, t])

  const runTask = async (label: string, task: () => Promise<void>) => {
    setBusyLabel(label)
    try {
      await task()
    } catch (error) {
      const detail = error instanceof Error ? error.message : String(error)
      setMessage({ key: detail, raw: detail })
      setHealthTone('danger')
    } finally {
      setBusyLabel(null)
    }
  }

  const pickFile = async (kind: 'pdf' | 'json') => {
    const result = await DocumentPicker.getDocumentAsync({
      type: kind === 'pdf' ? 'application/pdf' : 'application/json',
      copyToCacheDirectory: true,
      multiple: false,
      base64: false
    })
    if (result.canceled || !result.assets?.[0]) return

    const file = toUploadFile(result.assets[0])
    if (kind === 'pdf') {
      setPdfFile(file)
    } else {
      setStepsFile(file)
    }
  }

  const pollConversion = async (id: string) => {
    for (let index = 0; index < 240; index += 1) {
      const status = await api.getConversionStatus(id)
      setConversionProgress(status)
      if (status.status === 'completed') {
        setGuides(await api.getGuides(id))
        setMessage({ key: '搭建步骤已生成' })
        setHealthTone('ok')
        return
      }
      if (status.status === 'error') throw new Error(status.error || t('转换失败'))
      await new Promise((resolve) => setTimeout(resolve, 1500))
    }
    throw new Error(t('转换仍在运行，请稍后刷新状态'))
  }

  const pollGeneration = async (id: string) => {
    for (let index = 0; index < 240; index += 1) {
      const status = await api.getGenerationStatus(id)
      setGenerationProgress(status)
      if (status.status === 'completed') {
        setLessonPlans(await api.getLessonPlans(id))
        setMessage({ key: '教案已生成' })
        setHealthTone('ok')
        return
      }
      if (status.status === 'error') throw new Error(status.error || t('生成失败'))
      await new Promise((resolve) => setTimeout(resolve, 1500))
    }
    throw new Error(t('生成仍在运行，请稍后刷新状态'))
  }

  const checkBackend = () =>
    runTask('检查后端', async () => {
      const health = await api.health()
      const healthMessage = `${health.service || 'backend'} · ${health.status}`
      setMessage({ key: healthMessage, raw: healthMessage })
      setHealthTone(health.status === 'ok' ? 'ok' : 'warning')
    })

  const createProject = () =>
    runTask('创建项目', async () => {
      const id = await api.createProject()
      setProjectId(id)
      setMetadata(null)
      setGuides(null)
      setLessonPlans(null)
      setConversionProgress(null)
      setGenerationProgress(null)
      setMessage({ key: '项目已创建：{{id}}', params: { id } })
      setHealthTone('ok')
    })

  const uploadFiles = () =>
    runTask('上传文件', async () => {
      if (!projectId) throw new Error(t('请先创建项目'))
      if (!pdfFile) throw new Error(t('请选择 PDF 图纸'))
      const result = await api.uploadFiles(projectId, pdfFile, stepsFile || undefined)
      setMetadata(result)
      setMessage({
        key: '上传完成：{{pages}} 页，{{steps}} 步',
        params: { pages: result.total_pages, steps: result.total_steps }
      })
      setHealthTone('ok')
    })

  const startConversion = () =>
    runTask('转换步骤', async () => {
      if (!projectId) throw new Error(t('请先创建项目'))
      await api.startConversion(projectId)
      await pollConversion(projectId)
    })

  const saveMaterials = () =>
    runTask('保存材料', async () => {
      if (!projectId) throw new Error(t('请先创建项目'))
      await api.saveMaterials(projectId, materials)
      setMessage({ key: '教学材料已保存' })
      setHealthTone('ok')
    })

  const startGeneration = () =>
    runTask('生成教案', async () => {
      if (!projectId) throw new Error(t('请先创建项目'))
      if (!guides) throw new Error(t('请先完成步骤转换'))
      await api.saveMaterials(projectId, materials)
      await api.startGeneration(projectId)
      await pollGeneration(projectId)
    })

  return (
    <SafeAreaView style={styles.safeArea}>
      <StatusBar style="dark" />
      <ScrollView contentContainerStyle={styles.page}>
        <View style={styles.header}>
          <View>
            <Text style={styles.kicker}>LegoGlass Backend</Text>
            <Text style={styles.title}>{t('教学步骤与教案生成')}</Text>
          </View>
          <View style={styles.headerStatus}>
            {busyLabel ? <ActivityIndicator color="#2563eb" /> : null}
            <LanguageToggle />
            <StatusBadge label={busyLabel ? t(busyLabel) : messageLabel} tone={healthTone} />
          </View>
        </View>

        <View style={styles.apiBar}>
          <Text style={styles.inputLabel}>Backend URL</Text>
          <TextInput
            autoCapitalize="none"
            autoCorrect={false}
            onChangeText={setApiBaseUrl}
            style={styles.urlInput}
            value={apiBaseUrl}
          />
          <ToolbarButton label={t('检查')} onPress={checkBackend} />
        </View>

        <View style={[styles.grid, isWide ? styles.gridWide : null]}>
          <View style={styles.column}>
            <Section title={t('项目')} meta={projectId || t('未创建')}>
              <View style={styles.actionRow}>
                <ToolbarButton label={t('新建项目')} onPress={createProject} variant="primary" disabled={!!busyLabel} />
                <StatusBadge label={metadata ? t('{{count}} 步', { count: metadata.total_steps }) : t('等待上传')} tone={metadata ? 'ok' : 'neutral'} />
              </View>
            </Section>

            <Section title={t('图纸上传')} meta={metadata ? t('{{count}} 页', { count: metadata.total_pages }) : undefined}>
              <FileRow label={t('PDF 图纸')} file={pdfFile} onPick={() => pickFile('pdf')} />
              <FileRow label="Step JSON" file={stepsFile} onPick={() => pickFile('json')} optional />
              <View style={styles.actionRow}>
                <ToolbarButton
                  label={t('上传')}
                  onPress={uploadFiles}
                  variant="primary"
                  disabled={!projectId || !pdfFile || !!busyLabel}
                />
                <StatusBadge label={metadata?.has_step_data ? t('含 step 数据') : t('仅 PDF')} tone={metadata ? 'ok' : 'neutral'} />
              </View>
            </Section>

            <Section title={t('转换步骤')} meta={getProgressLabel(conversionProgress, t)}>
              <View style={styles.actionRow}>
                <ToolbarButton
                  label={t('开始转换')}
                  onPress={startConversion}
                  variant="primary"
                  disabled={!metadata || !!busyLabel}
                />
                <StatusBadge
                  label={getProgressLabel(conversionProgress, t)}
                  tone={conversionProgress?.status === 'completed' ? 'ok' : 'neutral'}
                />
              </View>
            </Section>

            <Section title={t('教学材料')}>
              <TextInput
                multiline
                onChangeText={setMaterials}
                style={styles.materialInput}
                textAlignVertical="top"
                value={materials}
              />
              <View style={styles.actionRow}>
                <ToolbarButton label={t('保存材料')} onPress={saveMaterials} disabled={!projectId || !!busyLabel} />
                <ToolbarButton
                  label={t('生成教案')}
                  onPress={startGeneration}
                  variant="primary"
                  disabled={!guides || !!busyLabel}
                />
                <StatusBadge
                  label={getProgressLabel(generationProgress, t)}
                  tone={generationProgress?.status === 'completed' ? 'ok' : 'neutral'}
                />
              </View>
            </Section>
          </View>

          <View style={styles.column}>
            <Section title={t('搭建步骤预览')} meta={guides ? t('{{count}} 条', { count: guides.steps.length }) : undefined}>
              <GuidePreview apiBaseUrl={apiBaseUrl} projectId={projectId} guides={guides} />
            </Section>

            <Section title={t('教案预览')} meta={lessonPlans ? t('{{count}} 条', { count: lessonPlans.steps.length }) : undefined}>
              <LessonPreview lessonPlans={lessonPlans} />
            </Section>
          </View>
        </View>
      </ScrollView>
    </SafeAreaView>
  )
}

const styles = StyleSheet.create({
  safeArea: {
    flex: 1,
    backgroundColor: '#f6f8fb'
  },
  page: {
    padding: 20,
    gap: 18
  },
  header: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    justifyContent: 'space-between',
    gap: 16
  },
  kicker: {
    color: '#2563eb',
    fontSize: 13,
    fontWeight: '700'
  },
  title: {
    color: '#0f172a',
    fontSize: 30,
    fontWeight: '800',
    marginTop: 4
  },
  headerStatus: {
    alignItems: 'flex-end',
    flexDirection: 'row',
    gap: 10
  },
  apiBar: {
    alignItems: 'center',
    backgroundColor: '#ffffff',
    borderColor: '#d9e2ef',
    borderRadius: 8,
    borderWidth: 1,
    flexDirection: 'row',
    gap: 10,
    padding: 12
  },
  inputLabel: {
    color: '#475569',
    fontSize: 13,
    fontWeight: '700'
  },
  urlInput: {
    backgroundColor: '#f8fafc',
    borderColor: '#cbd5e1',
    borderRadius: 6,
    borderWidth: 1,
    color: '#0f172a',
    flex: 1,
    fontSize: 14,
    minHeight: 40,
    paddingHorizontal: 10
  },
  grid: {
    gap: 16
  },
  gridWide: {
    flexDirection: 'row',
    alignItems: 'flex-start'
  },
  column: {
    flex: 1,
    gap: 16
  },
  section: {
    backgroundColor: '#ffffff',
    borderColor: '#d9e2ef',
    borderRadius: 8,
    borderWidth: 1,
    padding: 16,
    gap: 12
  },
  sectionHeader: {
    alignItems: 'center',
    flexDirection: 'row',
    justifyContent: 'space-between',
    gap: 12
  },
  sectionTitle: {
    color: '#111827',
    fontSize: 18,
    fontWeight: '800'
  },
  sectionMeta: {
    color: '#64748b',
    flexShrink: 1,
    fontSize: 12,
    textAlign: 'right'
  },
  actionRow: {
    alignItems: 'center',
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 10
  },
  button: {
    alignItems: 'center',
    borderColor: '#cbd5e1',
    borderRadius: 6,
    borderWidth: 1,
    minHeight: 40,
    justifyContent: 'center',
    paddingHorizontal: 14
  },
  buttonPrimary: {
    backgroundColor: '#2563eb',
    borderColor: '#2563eb'
  },
  buttonDanger: {
    backgroundColor: '#dc2626',
    borderColor: '#dc2626'
  },
  buttonDisabled: {
    backgroundColor: '#f1f5f9',
    borderColor: '#e2e8f0'
  },
  buttonPressed: {
    opacity: 0.72
  },
  buttonText: {
    color: '#0f172a',
    fontSize: 14,
    fontWeight: '700'
  },
  buttonPrimaryText: {
    color: '#ffffff'
  },
  buttonDangerText: {
    color: '#ffffff'
  },
  buttonDisabledText: {
    color: '#94a3b8'
  },
  badge: {
    backgroundColor: '#f8fafc',
    borderColor: '#cbd5e1',
    borderRadius: 999,
    borderWidth: 1,
    maxWidth: 260,
    paddingHorizontal: 10,
    paddingVertical: 6
  },
  badgeOk: {
    backgroundColor: '#ecfdf3',
    borderColor: '#86efac'
  },
  badgeWarning: {
    backgroundColor: '#fffbeb',
    borderColor: '#fbbf24'
  },
  badgeDanger: {
    backgroundColor: '#fef2f2',
    borderColor: '#fca5a5'
  },
  badgeText: {
    color: '#475569',
    fontSize: 12,
    fontWeight: '700'
  },
  badgeTextOk: {
    color: '#166534'
  },
  badgeTextWarning: {
    color: '#92400e'
  },
  badgeTextDanger: {
    color: '#991b1b'
  },
  fileRow: {
    alignItems: 'center',
    borderColor: '#e2e8f0',
    borderRadius: 8,
    borderWidth: 1,
    flexDirection: 'row',
    gap: 10,
    padding: 10
  },
  fileInfo: {
    flex: 1,
    gap: 3
  },
  fileLabel: {
    color: '#0f172a',
    fontSize: 14,
    fontWeight: '800'
  },
  fileMeta: {
    color: '#64748b',
    fontSize: 12
  },
  materialInput: {
    backgroundColor: '#f8fafc',
    borderColor: '#cbd5e1',
    borderRadius: 8,
    borderWidth: 1,
    color: '#0f172a',
    fontSize: 14,
    lineHeight: 20,
    minHeight: 150,
    padding: 12
  },
  emptyText: {
    color: '#94a3b8',
    fontSize: 14
  },
  previewList: {
    gap: 10
  },
  previewItem: {
    borderColor: '#e2e8f0',
    borderRadius: 8,
    borderWidth: 1,
    flexDirection: 'row',
    gap: 12,
    padding: 10
  },
  previewImage: {
    backgroundColor: '#e2e8f0',
    borderRadius: 6,
    height: 96,
    width: 72
  },
  previewText: {
    flex: 1,
    gap: 5
  },
  previewTitle: {
    color: '#0f172a',
    fontSize: 15,
    fontWeight: '800'
  },
  previewBody: {
    color: '#334155',
    fontSize: 13,
    lineHeight: 19
  },
  previewBodyMuted: {
    color: '#64748b',
    fontSize: 12,
    lineHeight: 18
  },
  lessonItem: {
    borderColor: '#e2e8f0',
    borderRadius: 8,
    borderWidth: 1,
    gap: 6,
    padding: 12
  }
})
