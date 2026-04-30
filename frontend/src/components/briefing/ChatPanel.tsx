'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import {
  chatStream, clearChatSession, getSuggestedQuestions,
  getChatSummary, submitChatFeedback,
} from '@/lib/api'
import type { ChatDoneEvent, ChatMessage, Patient, Reading } from '@/lib/types'
import {
  Send, Bot, Sparkles, RotateCcw, Copy, Check,
  ThumbsUp, ThumbsDown, FileText, AlertTriangle,
} from 'lucide-react'

interface ChatPanelProps {
  patientId: string
  patient: Patient
  readings: Reading[]
}

const TOOL_LABELS: Record<string, string> = {
  get_patient_readings:  'Reviewing BP readings',
  get_medication_history: 'Checking medication history',
  get_adherence_summary: 'Analysing adherence data',
  get_clinical_context:  'Loading clinical context',
  get_briefing:          'Reading pre-visit briefing',
  get_patient_alerts:    'Checking active alerts',
}

const CONFIDENCE_STYLES: Record<string, string> = {
  high:    'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400 border border-green-200 dark:border-green-800',
  medium:  'bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400 border border-amber-200 dark:border-amber-800',
  low:     'bg-orange-100 text-orange-700 dark:bg-orange-900/30 dark:text-orange-400 border border-orange-200 dark:border-orange-800',
  no_data: 'bg-gray-100 text-gray-500 dark:bg-gray-800 dark:text-gray-400 border border-gray-200 dark:border-gray-700',
  blocked: 'bg-red-100 text-red-600 dark:bg-red-900/30 dark:text-red-400 border border-red-200 dark:border-red-800',
}

const RISK_TIER_STYLES: Record<string, string> = {
  high:   'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400',
  medium: 'bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400',
  low:    'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400',
}

function formatTime(d: Date) {
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

function TypingDots() {
  return (
    <div className="flex items-center gap-1 px-1 py-0.5">
      {[0, 1, 2].map(i => (
        <span key={i}
          className="w-1.5 h-1.5 rounded-full bg-gray-400 dark:bg-gray-500 animate-bounce"
          style={{ animationDelay: `${i * 0.15}s`, animationDuration: '0.8s' }}
        />
      ))}
    </div>
  )
}

function ThinkingChip({ tool }: { tool: string }) {
  const label = TOOL_LABELS[tool] ?? tool.replace('get_', '').replace(/_/g, ' ')
  return (
    <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px]
                     bg-teal-50 dark:bg-teal-900/30 text-teal-600 dark:text-teal-400
                     border border-teal-200 dark:border-teal-800">
      <span className="w-1.5 h-1.5 rounded-full bg-teal-500 animate-pulse" />
      {label}…
    </span>
  )
}

interface BubbleProps {
  msg: ChatMessage
  index: number
  isLast: boolean
  onCopy: (index: number, text: string) => void
  onFeedback: (index: number, rating: 'up' | 'down') => void
  copied: boolean
  onFollowUp: (q: string) => void
}

function MessageBubble({ msg, index, isLast, onCopy, onFeedback, copied, onFollowUp }: BubbleProps) {
  const isUser = msg.role === 'user'
  const [showEvidence, setShowEvidence] = useState(false)
  const hasEvidence = (msg.evidence?.length ?? 0) > 0

  return (
    <div className={`flex flex-col ${isUser ? 'items-end' : 'items-start'} gap-1 animate-fadeSlideUp`}>
      <div className={`flex ${isUser ? 'justify-end' : 'justify-start'} w-full gap-2`}>
        {!isUser && (
          <div className="w-6 h-6 rounded-full bg-gradient-to-br from-teal-400 to-teal-600
                          flex items-center justify-center flex-shrink-0 mt-0.5 shadow-sm">
            <Bot size={11} className="text-white" strokeWidth={2.5} />
          </div>
        )}
        <div className="max-w-[85%] flex flex-col gap-1">
          {/* Bubble */}
          <div className={`px-3.5 py-2.5 rounded-2xl shadow-sm ${
            isUser
              ? 'bg-gradient-to-br from-teal-500 to-teal-700 text-white rounded-tr-sm'
              : 'bg-white dark:bg-[#1a2235] text-gray-800 dark:text-gray-100 rounded-tl-sm border border-gray-100 dark:border-[#2a3548]'
          }`}>
            <p className={`text-[13px] leading-relaxed ${msg.blocked ? 'italic opacity-70' : ''}`}>
              {msg.content}
            </p>
          </div>

          {/* Assistant meta row */}
          {!isUser && !msg.blocked && (
            <div className="flex items-center gap-2 flex-wrap px-0.5">
              {/* Confidence badge */}
              {msg.confidence && msg.confidence !== 'blocked' && (
                <span className={`text-[9px] font-bold uppercase tracking-wide px-1.5 py-0.5 rounded-full ${CONFIDENCE_STYLES[msg.confidence] ?? CONFIDENCE_STYLES.medium}`}>
                  {msg.confidence}
                </span>
              )}

              {/* Evidence toggle */}
              {hasEvidence && (
                <button
                  onClick={() => setShowEvidence(v => !v)}
                  className="text-[10px] text-teal-600 dark:text-teal-400 hover:underline"
                >
                  {showEvidence ? 'hide sources' : `${msg.evidence!.length} source${msg.evidence!.length > 1 ? 's' : ''}`}
                </button>
              )}

              <div className="flex-1" />

              {/* Timestamp */}
              {msg.timestamp && (
                <span className="text-[10px] text-gray-300 dark:text-gray-600">
                  {formatTime(msg.timestamp)}
                </span>
              )}

              {/* Copy */}
              <button
                onClick={() => onCopy(index, msg.content)}
                title="Copy answer"
                className="text-gray-300 dark:text-gray-600 hover:text-gray-500 dark:hover:text-gray-400 transition-colors"
              >
                {copied ? <Check size={11} /> : <Copy size={11} />}
              </button>

              {/* Thumbs */}
              <button
                onClick={() => onFeedback(index, 'up')}
                title="Helpful"
                className={`transition-colors ${msg.feedback === 'up' ? 'text-green-500' : 'text-gray-300 dark:text-gray-600 hover:text-green-500'}`}
              >
                <ThumbsUp size={11} />
              </button>
              <button
                onClick={() => onFeedback(index, 'down')}
                title="Not helpful"
                className={`transition-colors ${msg.feedback === 'down' ? 'text-red-400' : 'text-gray-300 dark:text-gray-600 hover:text-red-400'}`}
              >
                <ThumbsDown size={11} />
              </button>
            </div>
          )}

          {/* Timestamp for user messages */}
          {isUser && msg.timestamp && (
            <span className="text-[10px] text-gray-300 dark:text-gray-600 text-right pr-0.5">
              {formatTime(msg.timestamp)}
            </span>
          )}

          {/* Evidence list */}
          {!isUser && showEvidence && hasEvidence && (
            <div className="bg-gray-50 dark:bg-[#111827] rounded-xl px-3 py-2 border border-gray-100 dark:border-[#2a3548] space-y-1">
              {msg.evidence!.map((e, i) => (
                <p key={i} className="text-[11px] text-gray-500 dark:text-gray-400 leading-snug">
                  <span className="font-semibold text-teal-600 dark:text-teal-400 mr-1">[{i + 1}]</span>
                  {e}
                </p>
              ))}
              {(msg.data_gaps?.length ?? 0) > 0 && (
                <div className="pt-1 border-t border-gray-100 dark:border-[#2a3548]">
                  {msg.data_gaps!.map((g, i) => (
                    <p key={i} className="text-[11px] text-orange-400 dark:text-orange-500">{g}</p>
                  ))}
                </div>
              )}
            </div>
          )}

          {/* Follow-up chips — only on last assistant message */}
          {!isUser && isLast && (msg.follow_up_questions?.length ?? 0) > 0 && (
            <div className="flex flex-wrap gap-1.5 pt-1">
              {msg.follow_up_questions!.map((q, i) => (
                <button
                  key={i}
                  onClick={() => onFollowUp(q)}
                  className="text-[11px] px-2.5 py-1 rounded-full border
                             border-teal-200 dark:border-teal-800
                             text-teal-600 dark:text-teal-400
                             bg-teal-50 dark:bg-teal-900/20
                             hover:bg-teal-100 dark:hover:bg-teal-900/40
                             transition-colors"
                >
                  {q}
                </button>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

export default function ChatPanel({ patientId, patient, readings }: ChatPanelProps) {
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [input, setInput] = useState('')
  const [streaming, setStreaming] = useState(false)
  const [streamingText, setStreamingText] = useState('')
  const [thinkingTools, setThinkingTools] = useState<string[]>([])
  const [suggested, setSuggested] = useState<{ questions: string[]; proactive: string | null }>({ questions: [], proactive: null })
  const [suggestionsUsed, setSuggestionsUsed] = useState(false)
  const [summary, setSummary] = useState<string | null>(null)
  const [summaryLoading, setSummaryLoading] = useState(false)
  const [copiedIndex, setCopiedIndex] = useState<number | null>(null)
  const bottomRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const msgIdRef = useRef(0)

  // Data freshness
  const sortedReadings = [...readings].sort(
    (a, b) => new Date(b.effective_datetime).getTime() - new Date(a.effective_datetime).getTime()
  )
  const lastReadingDate = sortedReadings[0] ? new Date(sortedReadings[0].effective_datetime) : null
  const daysSinceReading = lastReadingDate
    ? Math.floor((Date.now() - lastReadingDate.getTime()) / (1000 * 60 * 60 * 24))
    : null
  const isDataStale = daysSinceReading !== null && daysSinceReading > 7

  useEffect(() => {
    getSuggestedQuestions(patientId).then(setSuggested)
  }, [patientId])

  useEffect(() => {
    return () => { clearChatSession(patientId).catch(() => {}) }
  }, [patientId])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, streamingText, thinkingTools])

  const handleCopy = useCallback((index: number, text: string) => {
    navigator.clipboard.writeText(text).then(() => {
      setCopiedIndex(index)
      setTimeout(() => setCopiedIndex(null), 2000)
    })
  }, [])

  const handleFeedback = useCallback((index: number, rating: 'up' | 'down') => {
    setMessages(prev => prev.map((m, i) => i === index ? { ...m, feedback: rating } : m))
    submitChatFeedback(patientId, index, rating).catch(() => {})
  }, [patientId])

  const handleSummary = useCallback(async () => {
    if (summaryLoading || messages.length === 0) return
    setSummaryLoading(true)
    const result = await getChatSummary(patientId).catch(() => ({ summary: null }))
    setSummary(result.summary)
    setSummaryLoading(false)
  }, [patientId, summaryLoading, messages.length])

  const sendMessage = useCallback(async (question: string) => {
    if (!question.trim() || streaming) return

    setSuggestionsUsed(true)
    setSummary(null)
    const id = ++msgIdRef.current
    setMessages(prev => [...prev, { role: 'user', content: question, timestamp: new Date(), id }])
    setInput('')
    setStreaming(true)
    setStreamingText('')
    setThinkingTools([])

    try {
      const response = await chatStream(patientId, question)
      if (!response.body) throw new Error('No response body')

      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      let accumulatedText = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop() ?? ''

        for (const line of lines) {
          if (line.startsWith('data: ')) {
            try {
              const payload = JSON.parse(line.slice(6))
              if (payload.tool) {
                setThinkingTools(prev => [...prev, payload.tool as string])
              } else if (payload.token !== undefined) {
                accumulatedText += payload.token as string
                setStreamingText(accumulatedText)
              } else if (payload.answer !== undefined) {
                const doneEvent = payload as ChatDoneEvent
                const assistantId = ++msgIdRef.current
                setMessages(prev => [...prev, {
                  role: 'assistant',
                  content: doneEvent.answer,
                  evidence: doneEvent.evidence,
                  confidence: doneEvent.confidence,
                  data_gaps: doneEvent.data_gaps,
                  tools_used: doneEvent.tools_used,
                  blocked: doneEvent.blocked,
                  follow_up_questions: doneEvent.follow_up_questions,
                  timestamp: new Date(),
                  id: assistantId,
                  feedback: null,
                }])
                setStreamingText('')
                setThinkingTools([])
              }
            } catch { /* skip malformed line */ }
          }
        }
      }
    } catch {
      setMessages(prev => [...prev, {
        role: 'assistant',
        content: 'Something went wrong. Please try again.',
        confidence: 'low',
        blocked: false,
        timestamp: new Date(),
      }])
    } finally {
      setStreaming(false)
      setStreamingText('')
      setThinkingTools([])
    }
  }, [patientId, streaming])

  function handleKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(input) }
  }

  function handleReset() {
    setMessages([])
    setSuggestionsUsed(false)
    setSummary(null)
    clearChatSession(patientId).catch(() => {})
  }

  const lastAssistantIndex = messages.reduce((acc, m, i) => m.role === 'assistant' ? i : acc, -1)

  return (
    <div className="flex flex-col h-full rounded-xl overflow-hidden shadow-lg
                    border border-gray-100 dark:border-[#1F2937]
                    bg-gray-50 dark:bg-[#0d1626]">

      {/* Header */}
      <div className="flex items-center gap-3 px-4 py-3 flex-shrink-0
                      bg-white dark:bg-[#111827]
                      border-b border-gray-100 dark:border-[#1F2937]">
        <div className="w-8 h-8 rounded-xl bg-gradient-to-br from-teal-400 to-teal-600
                        flex items-center justify-center shadow-sm flex-shrink-0">
          <Sparkles size={14} className="text-white" strokeWidth={2} />
        </div>
        <div className="flex-1 min-w-0">
          <p className="text-[13px] font-semibold text-gray-800 dark:text-gray-100 leading-tight">
            Ask ARIA
          </p>
          {/* Patient context */}
          <div className="flex items-center gap-1.5 mt-0.5 flex-wrap">
            <span className={`text-[9px] font-bold uppercase tracking-wide px-1.5 py-0.5 rounded-full ${RISK_TIER_STYLES[patient.risk_tier] ?? ''}`}>
              {patient.risk_tier} risk
            </span>
            {patient.next_appointment && (
              <span className="text-[10px] text-gray-400 dark:text-gray-500">
                Appt: {new Date(patient.next_appointment).toLocaleDateString([], { month: 'short', day: 'numeric' })}
              </span>
            )}
          </div>
        </div>

        {/* Summary button */}
        {messages.length > 0 && (
          <button
            onClick={handleSummary}
            disabled={summaryLoading}
            title="Summarise conversation"
            className="w-7 h-7 flex items-center justify-center rounded-lg text-gray-400
                       hover:text-gray-600 dark:hover:text-gray-300
                       hover:bg-gray-100 dark:hover:bg-[#1F2937] transition-colors
                       disabled:opacity-40"
          >
            {summaryLoading
              ? <span className="w-3 h-3 border-2 border-gray-400 border-t-transparent rounded-full animate-spin" />
              : <FileText size={13} strokeWidth={2} />
            }
          </button>
        )}

        {/* Reset button */}
        {messages.length > 0 && (
          <button
            onClick={handleReset}
            title="Clear conversation"
            className="w-7 h-7 flex items-center justify-center rounded-lg text-gray-400
                       hover:text-gray-600 dark:hover:text-gray-300
                       hover:bg-gray-100 dark:hover:bg-[#1F2937] transition-colors"
          >
            <RotateCcw size={13} strokeWidth={2} />
          </button>
        )}
      </div>

      {/* Data freshness warning */}
      {isDataStale && (
        <div className="flex items-center gap-2 px-4 py-2 bg-amber-50 dark:bg-amber-900/20
                        border-b border-amber-100 dark:border-amber-800 flex-shrink-0">
          <AlertTriangle size={12} className="text-amber-500 flex-shrink-0" strokeWidth={2} />
          <p className="text-[11px] text-amber-600 dark:text-amber-400">
            Last reading was {daysSinceReading} days ago — data may not reflect current status
          </p>
        </div>
      )}

      {/* Summary block */}
      {summary && (
        <div className="px-4 py-3 bg-blue-50 dark:bg-blue-900/20
                        border-b border-blue-100 dark:border-blue-800 flex-shrink-0">
          <p className="text-[11px] font-semibold text-blue-600 dark:text-blue-400 mb-1">Conversation Summary</p>
          <p className="text-[12px] text-blue-700 dark:text-blue-300 leading-relaxed whitespace-pre-line">{summary}</p>
          <button onClick={() => setSummary(null)} className="text-[10px] text-blue-400 mt-1 hover:underline">dismiss</button>
        </div>
      )}

      {/* Messages */}
      <div className="flex-1 overflow-y-auto px-4 py-4 space-y-3
                      scrollbar-thin scrollbar-thumb-gray-200 dark:scrollbar-thumb-gray-700">

        {/* Welcome state */}
        {messages.length === 0 && !streaming && (
          <div className="space-y-3 animate-fadeSlideUp">
            <div className="flex justify-start">
              <div className="flex gap-2">
                <div className="w-6 h-6 rounded-full bg-gradient-to-br from-teal-400 to-teal-600
                                flex items-center justify-center flex-shrink-0 mt-0.5 shadow-sm">
                  <Bot size={11} className="text-white" strokeWidth={2.5} />
                </div>
                <div className="max-w-[85%] px-3.5 py-2.5 rounded-2xl rounded-tl-sm shadow-sm
                                bg-white dark:bg-[#1a2235] border border-gray-100 dark:border-[#2a3548]">
                  <p className="text-[13px] text-gray-600 dark:text-gray-300 leading-relaxed">
                    Hi! I can answer questions about this patient — BP trends, medications, adherence, alerts, and clinical history.
                  </p>
                </div>
              </div>
            </div>
            {/* Suggested questions */}
            {!suggestionsUsed && (suggested.questions.length > 0 || suggested.proactive) && (
              <div className="space-y-2 pl-8">
                {suggested.proactive && (
                  <button
                    onClick={() => sendMessage(suggested.proactive!)}
                    className="w-full text-left text-[12px] px-3 py-2 rounded-xl
                               bg-teal-50 dark:bg-teal-900/20
                               border border-teal-200 dark:border-teal-800
                               text-teal-700 dark:text-teal-300
                               hover:bg-teal-100 dark:hover:bg-teal-900/40 transition-colors"
                  >
                    💡 {suggested.proactive}
                  </button>
                )}
                <div className="flex flex-wrap gap-1.5">
                  {suggested.questions.map((q, i) => (
                    <button
                      key={i}
                      onClick={() => sendMessage(q)}
                      className="text-[11px] px-2.5 py-1.5 rounded-full
                                 bg-white dark:bg-[#1a2235]
                                 border border-gray-200 dark:border-[#2a3548]
                                 text-gray-600 dark:text-gray-300
                                 hover:border-teal-400 hover:text-teal-600
                                 dark:hover:border-teal-600 dark:hover:text-teal-400
                                 transition-colors"
                    >
                      {q}
                    </button>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}

        {/* Message list */}
        {messages.map((msg, i) => (
          <MessageBubble
            key={msg.id ?? i}
            msg={msg}
            index={i}
            isLast={i === lastAssistantIndex}
            onCopy={handleCopy}
            onFeedback={handleFeedback}
            copied={copiedIndex === i}
            onFollowUp={sendMessage}
          />
        ))}

        {/* Thinking chips */}
        {thinkingTools.length > 0 && (
          <div className="flex justify-start animate-fadeSlideUp">
            <div className="flex gap-2">
              <div className="w-6 h-6 rounded-full bg-gradient-to-br from-teal-400 to-teal-600
                              flex items-center justify-center flex-shrink-0 mt-0.5 shadow-sm">
                <Bot size={11} className="text-white" strokeWidth={2.5} />
              </div>
              <div className="px-3.5 py-2.5 rounded-2xl rounded-tl-sm shadow-sm
                              bg-white dark:bg-[#1a2235] border border-gray-100 dark:border-[#2a3548]
                              flex flex-col gap-1.5">
                <div className="flex flex-wrap gap-1.5">
                  {thinkingTools.map((t, i) => <ThinkingChip key={i} tool={t} />)}
                </div>
              </div>
            </div>
          </div>
        )}

        {/* Streaming bubble */}
        {streamingText && (
          <div className="flex justify-start animate-fadeSlideUp">
            <div className="flex gap-2">
              <div className="w-6 h-6 rounded-full bg-gradient-to-br from-teal-400 to-teal-600
                              flex items-center justify-center flex-shrink-0 mt-0.5 shadow-sm">
                <Bot size={11} className="text-white" strokeWidth={2.5} />
              </div>
              <div className="max-w-[85%] px-3.5 py-2.5 rounded-2xl rounded-tl-sm shadow-sm
                              bg-white dark:bg-[#1a2235] text-gray-800 dark:text-gray-100
                              border border-gray-100 dark:border-[#2a3548]">
                <p className="text-[13px] leading-relaxed">{streamingText}
                  <span className="inline-block w-0.5 h-3.5 bg-teal-500 ml-0.5 animate-pulse align-middle" />
                </p>
              </div>
            </div>
          </div>
        )}

        {/* Typing dots */}
        {streaming && !streamingText && thinkingTools.length === 0 && (
          <div className="flex justify-start animate-fadeSlideUp">
            <div className="flex gap-2">
              <div className="w-6 h-6 rounded-full bg-gradient-to-br from-teal-400 to-teal-600
                              flex items-center justify-center flex-shrink-0 mt-0.5 shadow-sm">
                <Bot size={11} className="text-white" strokeWidth={2.5} />
              </div>
              <div className="px-3.5 py-2.5 rounded-2xl rounded-tl-sm shadow-sm
                              bg-white dark:bg-[#1a2235] border border-gray-100 dark:border-[#2a3548]">
                <TypingDots />
              </div>
            </div>
          </div>
        )}

        <div ref={bottomRef} />
      </div>

      {/* Input bar */}
      <div className="flex-shrink-0 px-3 pb-3 pt-2
                      bg-white dark:bg-[#111827]
                      border-t border-gray-100 dark:border-[#1F2937]">
        <div className="flex items-center gap-2 bg-gray-50 dark:bg-[#1a2235]
                        rounded-xl px-3 py-2 border border-gray-200 dark:border-[#2a3548]
                        focus-within:border-teal-400 dark:focus-within:border-teal-600
                        transition-colors shadow-sm">
          <input
            ref={inputRef}
            type="text"
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Ask about this patient…"
            disabled={streaming}
            className="flex-1 bg-transparent text-[13px] text-gray-800 dark:text-gray-100
                       placeholder:text-gray-400 dark:placeholder:text-gray-500
                       focus:outline-none disabled:opacity-50"
          />
          <button
            onClick={() => sendMessage(input)}
            disabled={streaming || !input.trim()}
            className="w-7 h-7 flex items-center justify-center rounded-lg flex-shrink-0
                       bg-gradient-to-br from-teal-500 to-teal-700 text-white shadow-sm
                       hover:from-teal-400 hover:to-teal-600
                       disabled:opacity-30 disabled:cursor-not-allowed
                       transition-all duration-150 active:scale-95"
          >
            <Send size={12} strokeWidth={2.5} />
          </button>
        </div>
        <p className="text-[10px] text-gray-300 dark:text-gray-600 text-center mt-1.5">
          Decision support only · Clinician makes all clinical decisions
        </p>
      </div>
    </div>
  )
}
