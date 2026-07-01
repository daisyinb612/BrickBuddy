import React, { createContext, useContext, useMemo, useState } from 'react'
import { Pressable, StyleSheet, Text, View } from 'react-native'

export type Language = 'zh' | 'en'
export type TranslationParams = Record<string, string | number | null | undefined>
export type TFunction = (key: string, params?: TranslationParams) => string

const languageStorageKey = 'lego-glass-language'

const enTranslations: Record<string, string> = {
  '语言': 'Language',
  '中文': '中文',
  'English': 'English',
  '第 {{step}} 步': 'Step {{step}}',
  '等待 agent lesson plan 加载，当前对应 {{filename}}。': 'Waiting for the agent lesson plan. Current fallback image: {{filename}}.',
  '所需零件': 'Required parts',
  '摆放动作': 'Placement',
  '完成确认': 'Completion check',
  '等待 agent 返回当前步骤零件。': 'Waiting for the agent to return parts for this step.',
  '等待 agent 返回当前步骤摆放说明。': 'Waiting for the agent to return placement instructions.',
  '等待视觉检查或学生确认。': 'Waiting for a vision check or student confirmation.',
  '完成第 {{step}} 步拼装。': 'Complete Step {{step}} assembly.',
  '确认当前步骤零件已准备好。': 'Confirm the parts for this step are ready.',
  '按照当前指导图完成摆放。': 'Place the bricks according to the current guide image.',
  '教学提示': 'Teaching note',
  '完成后等待 agent 复核。': 'Wait for agent review after completion.',
  '未选择': 'Not selected',
  '模拟实时流': 'Simulation stream',
  'RTMP 公网访问': 'Public RTMP',
  '眼镜实时流': 'Glasses stream',
  '等待 VLM output。': 'Waiting for VLM output.',
  '等待 VLM output': 'Waiting for VLM output',
  '翻页': 'Page turn',
  '不翻页': 'Stay',
  '请选择模型': 'Select a model',
  '已完成': 'Completed',
  '进行中': 'In progress',
  '复核': 'Review',
  '待开始': 'Pending',
  'AI 正在抽帧识别结构与手部操作': 'AI is sampling frames to identify structure and hand actions',
  '等待开始活动评测': 'Waiting to start activity review',
  '拼装步骤时间轴': 'Assembly timeline',
  '{{done}}/{{total}} 步完成': '{{done}}/{{total}} steps complete',
  '切换到上一张拼装指导图': 'Switch to the previous assembly guide image',
  '切换到下一张拼装指导图': 'Switch to the next assembly guide image',
  '模拟实时传输': 'Simulation stream',
  '等待直播画面': 'Waiting for live video',
  '浏览器截帧': 'Browser frame capture',
  '浏览器截帧受限': 'Browser frame capture blocked',
  '后端截帧': 'Backend frame capture',
  '后端截帧失败': 'Backend frame capture failed',
  '等待 HTTP-FLV 直播流': 'Waiting for HTTP-FLV live stream',
  '当前浏览器不支持 HTTP-FLV': 'This browser does not support HTTP-FLV',
  '正在连接 HTTP-FLV': 'Connecting to HTTP-FLV',
  'HTTP-FLV 已就绪': 'HTTP-FLV ready',
  '点击视频开始播放': 'Click the video to start playback',
  'HTTP-FLV 播放中': 'HTTP-FLV playing',
  '等待直播数据': 'Waiting for live data',
  'FLV 播放异常': 'FLV playback error',
  'FLV 播放异常：{{detail}}': 'FLV playback error: {{detail}}',
  'HTTP-FLV 播放中 · 浏览器截帧': 'HTTP-FLV playing · Browser frame capture',
  'HTTP-FLV 播放中 · 跨域限制截帧': 'HTTP-FLV playing · Cross-origin frame capture blocked',
  '推流：{{pushUrl}} · 播放：{{flvUrl}} · {{stepName}} · {{status}}': 'Push: {{pushUrl}} · Playback: {{flvUrl}} · {{stepName}} · {{status}}',
  '活动驾驶舱已就绪': 'Activity cockpit is ready',
  '左侧加载拼装指导图，中间显示模拟流、眼镜局域网流或 RTMP 公网流，右侧等待 VLM 翻页事件。': 'The left panel loads assembly guide images, the center shows simulation, glasses LAN, or public RTMP streams, and the right panel waits for VLM page-turn events.',
  '前端已切换到 Step {{step}}。': 'The UI switched to Step {{step}}.',
  'VLM 触发翻页到 Step {{step}}': 'VLM advanced to Step {{step}}',
  '设备链路已连接': 'Device link connected',
  '设备链路已断开': 'Device link disconnected',
  '{{source}} 已准备同步画面、语音和活动状态。': '{{source}} is ready to sync video, audio, and activity status.',
  '已暂停设备输入，活动状态保留在本地。': 'Device input is paused; activity state remains local.',
  'Qwen-LegoAgent 已暂停': 'Qwen-LegoAgent paused',
  '视频播放已暂停，VLM 和 Realtime 子进程已停止。': 'Video playback is paused; VLM and Realtime subprocesses have stopped.',
  '停止 VLM 失败': 'Failed to stop VLM',
  '教学服务停止请求失败': 'Failed to request teaching service stop',
  'Qwen-LegoAgent 已在运行': 'Qwen-LegoAgent is already running',
  'Qwen-LegoAgent 已启动': 'Qwen-LegoAgent started',
  '视频源：{{source}}': 'Video source: {{source}}',
  '处理路径：{{path}}': 'Processing path: {{path}}',
  '后端源：{{source}}': 'Backend source: {{source}}',
  'VLM采样：{{seconds}}s': 'VLM sampling: {{seconds}}s',
  'backend 默认': 'backend default',
  'Realtime：{{status}}{{pid}}': 'Realtime: {{status}}{{pid}}',
  '已启动': 'Started',
  '未运行': 'Not running',
  '事件：{{url}}': 'Events: {{url}}',
  '启动教学服务失败': 'Failed to start teaching service',
  'Qwen-LegoAgent 教学服务启动失败': 'Failed to start Qwen-LegoAgent teaching service',
  '{{message}}\n请先运行：uv run python legoagentbackend/server.py': '{{message}}\nRun this first: uv run python legoagentbackend/server.py',
  '手动完成 Step {{step}}': 'Manually completed Step {{step}}',
  '本地标记完成并切换到 Step {{step}}。': 'Marked complete locally and switched to Step {{step}}.',
  '等待外部 VLM 检查': 'Waiting for external VLM check',
  '旧视觉进度接口已停用。请由新的 VLM 控制器发送 changepage 事件。': 'The legacy vision progress API is disabled. Send changepage events from the new VLM controller.',
  '本地活动已重置': 'Local activity reset',
  '旧 agent 接口未调用。VLM 事件记录和步骤横栏已清空。': 'The legacy agent API was not called. VLM event records and the step bar were cleared.',
  '已切换到 Step {{step}}': 'Switched to Step {{step}}',
  '模型指导：{{text}}': 'Model guidance: {{text}}',
  '已从本地拼装指导图列表切换当前观察步骤：{{filename}}。': 'Switched the current observation step from the local guide image list: {{filename}}.',
  '拼装指导图': 'Assembly guide',
  '{{count}} 张图片': '{{count}} images',
  '读取中': 'Loading',
  '使用兜底列表': 'Using fallback list',
  '暂无 agent 零件说明，可先查看指导图。': 'No agent part notes yet. Check the guide image first.',
  '摆放说明': 'Placement notes',
  '暂无 agent 摆放说明。': 'No agent placement notes yet.',
  '实时活动区': 'Live activity',
  'Session LEGO-0614 · 第 {{step}} 步 · {{time}}': 'Session LEGO-0614 · Step {{step}} · {{time}}',
  '当前分': 'Score',
  '眼镜直播实时流': 'Glasses live stream',
  '同步': 'Sync',
  '使用前端模拟实时流校验流程和反馈 UI': 'Use the frontend simulation stream to validate flow and feedback UI',
  '眼镜推流地址：{{pushUrl}} · 前端 HTTP-FLV 播放：{{flvUrl}}': 'Glasses push URL: {{pushUrl}} · Frontend HTTP-FLV playback: {{flvUrl}}',
  '链路可用': 'Link available',
  '等待连接': 'Waiting to connect',
  '断开设备': 'Disconnect device',
  '连接设备': 'Connect device',
  '教学启动中': 'Starting teaching',
  '暂停教学': 'Pause teaching',
  '开始教学': 'Start teaching',
  '完成当前步': 'Complete current step',
  '重新检查': 'Recheck',
  '重置会话': 'Reset session',
  'VLM 返回结果': 'VLM output',
  '接收 VLM output，并显示翻页判定': 'Receives VLM output and displays the page-turn decision',
  '翻页判定': 'Page-turn decision',
  '最新 output · {{time}}': 'Latest output · {{time}}',
  '等待第一条 VLM output': 'Waiting for the first VLM output',
  '收起之前的反馈': 'Hide previous feedback',
  '查看之前的所有反馈 ({{count}})': 'View all previous feedback ({{count}})',
  '天坛是明清两代皇帝祭天、祈谷的建筑群。讲解时可结合圆形屋顶、层级结构、中心轴线和古代礼制，引导学生观察建筑结构与文化含义。': 'The Temple of Heaven was an architectural complex where Ming and Qing emperors worshiped Heaven and prayed for harvests. During instruction, connect the round roof, tiered structure, central axis, and ritual system to help students observe architectural structure and cultural meaning.',
  '未开始': 'Not started',
  '出错': 'Error',
  '可选': 'Optional',
  '选择': 'Choose',
  '暂无步骤说明': 'No step instructions yet',
  '暂无教案内容': 'No lesson plan content yet',
  '本步骤不安排文化讲解': 'No cultural explanation for this step',
  '后端地址已指向 5005': 'Backend URL points to 5005',
  '搭建步骤已生成': 'Assembly steps generated',
  '转换失败': 'Conversion failed',
  '转换仍在运行，请稍后刷新状态': 'Conversion is still running. Refresh the status later.',
  '教案已生成': 'Lesson plan generated',
  '生成失败': 'Generation failed',
  '生成仍在运行，请稍后刷新状态': 'Generation is still running. Refresh the status later.',
  '检查后端': 'Check backend',
  '创建项目': 'Create project',
  '项目已创建：{{id}}': 'Project created: {{id}}',
  '上传文件': 'Upload files',
  '请先创建项目': 'Create a project first',
  '请选择 PDF 图纸': 'Choose a PDF drawing first',
  '上传完成：{{pages}} 页，{{steps}} 步': 'Upload complete: {{pages}} pages, {{steps}} steps',
  '转换步骤': 'Convert steps',
  '保存材料': 'Save materials',
  '教学材料已保存': 'Teaching materials saved',
  '生成教案': 'Generate lesson plan',
  '请先完成步骤转换': 'Complete step conversion first',
  '教学步骤与教案生成': 'Teaching Steps and Lesson Plan Generation',
  '检查': 'Check',
  '项目': 'Project',
  '未创建': 'Not created',
  '新建项目': 'New project',
  '{{count}} 步': '{{count}} steps',
  '等待上传': 'Waiting for upload',
  '图纸上传': 'Drawing upload',
  '{{count}} 页': '{{count}} pages',
  'PDF 图纸': 'PDF drawing',
  '上传': 'Upload',
  '含 step 数据': 'Includes step data',
  '仅 PDF': 'PDF only',
  '开始转换': 'Start conversion',
  '教学材料': 'Teaching materials',
  '搭建步骤预览': 'Assembly step preview',
  '{{count}} 条': '{{count}} items',
  '教案预览': 'Lesson plan preview'
}

interface I18nContextValue {
  language: Language
  setLanguage: (language: Language) => void
  t: TFunction
}

const I18nContext = createContext<I18nContextValue | null>(null)

function isLanguage(value: unknown): value is Language {
  return value === 'zh' || value === 'en'
}

function readInitialLanguage(): Language {
  if (typeof window !== 'undefined') {
    try {
      const storedLanguage = window.localStorage.getItem(languageStorageKey)
      if (isLanguage(storedLanguage)) return storedLanguage
    } catch {
      // Ignore storage access errors in restricted runtimes.
    }
  }
  return 'zh'
}

function interpolate(template: string, params?: TranslationParams): string {
  if (!params) return template
  return template.replace(/\{\{(\w+)\}\}/g, (match, key: string) => {
    const value = params[key]
    if (value === null || typeof value === 'undefined') return ''
    return String(value)
  })
}

export function I18nProvider({ children }: { children: React.ReactNode }) {
  const [language, setLanguageState] = useState<Language>(readInitialLanguage)

  const setLanguage = (nextLanguage: Language) => {
    setLanguageState(nextLanguage)
    if (typeof window !== 'undefined') {
      try {
        window.localStorage.setItem(languageStorageKey, nextLanguage)
      } catch {
        // Ignore storage access errors in restricted runtimes.
      }
    }
  }

  const value = useMemo<I18nContextValue>(() => {
    const t: TFunction = (key, params) => {
      const translated = language === 'en' ? enTranslations[key] || key : key
      return interpolate(translated, params)
    }

    return { language, setLanguage, t }
  }, [language])

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>
}

export function useI18n(): I18nContextValue {
  const context = useContext(I18nContext)
  if (!context) {
    throw new Error('useI18n must be used within I18nProvider')
  }
  return context
}

export function LanguageToggle() {
  const { language, setLanguage, t } = useI18n()

  return (
    <View accessibilityLabel={t('语言')} style={styles.languageToggle}>
      <Pressable
        accessibilityRole="button"
        accessibilityState={{ selected: language === 'zh' }}
        onPress={() => setLanguage('zh')}
        style={({ pressed }) => [
          styles.languageOption,
          language === 'zh' ? styles.languageOptionActive : null,
          pressed ? styles.languageOptionPressed : null
        ]}
      >
        <Text style={[styles.languageOptionText, language === 'zh' ? styles.languageOptionTextActive : null]}>
          {t('中文')}
        </Text>
      </Pressable>
      <Pressable
        accessibilityRole="button"
        accessibilityState={{ selected: language === 'en' }}
        onPress={() => setLanguage('en')}
        style={({ pressed }) => [
          styles.languageOption,
          language === 'en' ? styles.languageOptionActive : null,
          pressed ? styles.languageOptionPressed : null
        ]}
      >
        <Text style={[styles.languageOptionText, language === 'en' ? styles.languageOptionTextActive : null]}>
          {t('English')}
        </Text>
      </Pressable>
    </View>
  )
}

const styles = StyleSheet.create({
  languageToggle: {
    alignItems: 'center',
    backgroundColor: '#f8fafc',
    borderColor: '#cbd5e1',
    borderRadius: 8,
    borderWidth: 1,
    flexDirection: 'row',
    gap: 4,
    padding: 3
  },
  languageOption: {
    alignItems: 'center',
    borderRadius: 6,
    minHeight: 32,
    justifyContent: 'center',
    paddingHorizontal: 10
  },
  languageOptionActive: {
    backgroundColor: '#1570ef'
  },
  languageOptionPressed: {
    opacity: 0.72
  },
  languageOptionText: {
    color: '#475467',
    fontSize: 12,
    fontWeight: '800'
  },
  languageOptionTextActive: {
    color: '#ffffff'
  }
})
