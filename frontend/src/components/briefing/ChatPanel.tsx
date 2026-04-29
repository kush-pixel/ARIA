'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import { chatStream, clearChatSession, getSuggestedQuestions } from '@/lib/api'
import type { ChatDoneEvent, ChatMessage } from '@/lib/types'
import EvidenceCard from './EvidenceCard'
import SuggestedQuestions from './SuggestedQuestions'
import { Send, Bot, Sparkles, RotateCcw } from 'lucide-react'

interface ChatPanelProps {
  patientId: string
}

function TypingDots() {
  return (
    <div className="flex items-center gap-1 px-1 py-0.5">
      {[0, 1, 2].map(i => (
        <span
          key={i}
          className="w-1.5 h-1.5 rounded-full bg-gray-400 dark:bg-gray-500 animate-bounce"
          style={{ animationDelay: `${i * 0.15}s`, animationDuration: '0.8s' }}
        />
      ))}
    </div>
  )
}

function ThinkingChip({ tool }: { tool: string }) {
  const label = tool.replace('get_', '').replace(/_/g, ' ')
  return (
    <span
      className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px]
                 bg-teal-50 dark:bg-teal-900/30 text-teal-600 dark:text-teal-400
                 border border-teal-200 dark:border-teal-800 animate-pulse"
    >
      <span className="w-1 h-1 rounded-full bg-teal-500" />
      {label}
    </span>
  )
}

interface BubbleProps {
  msg: ChatMessage
  index: number
}

function MessageBubble({ msg, index }: BubbleProps) {
  const isUser = msg.role === 'user'
  return (
    <div
      className={`flex ${isUser ? 'justify-end' : 'justify-start'} animate-fadeSlideUp`}
      style={{ animationDelay: `${index * 0.03}s` }}
    >
      {!isUser && (
        <div className="w-6 h-6 rounded-full bg-gradient-to-br from-teal-400 to-teal-600
                        flex items-center justify-center flex-shrink-0 mr-2 mt-0.5 shadow-sm">
          <Bot size={11} className="text-white" strokeWidth={2.5} />
        </div>
      )}
      <div className={`max-w-[85%] ${isUser ? 'items-end' : 'items-start'} flex flex-col gap-1`}>
        <div className={`px-3.5 py-2.5 rounded-2xl shadow-sm ${
          isUser
            ? 'bg-gradient-to-br from-teal-500 to-teal-700 text-white rounded-tr-sm'
            : 'bg-white dark:bg-[#1a2235] text-gray-800 dark:text-gray-100 rounded-tl-sm border border-gray-100 dark:border-[#2a3548]'
        }`}>
          <p className={`text-[13px] leading-relaxed ${msg.blocked ? 'italic opacity-70' : ''}`}>
            {msg.content}
          </p>
          {!isUser && !msg.blocked && (
            <EvidenceCard
              evidence={msg.evidence ?? []}
              confidence={msg.confidence ?? 'medium'}
              dataGaps={msg.data_gaps ?? []}
              toolsUsed={msg.tools_used ?? []}
            />
          )}
        </div>
      </div>
    </div>
  )
}

export default function ChatPanel({ patientId }: ChatPanelProps) {
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [input, setInput] = useState('')
  const [streaming, setStreaming] = useState(false)
  const [streamingText, setStreamingText] = useState('')
  const [thinkingTools, setThinkingTools] = useState<string[]>([])
  const [suggested, setSuggested] = useState<{ questions: string[]; proactive: string | null }>({
    questions: [],
    proactive: null,
  })
  const [suggestionsUsed, setSuggestionsUsed] = useState(false)
  const bottomRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    getSuggestedQuestions(patientId).then(setSuggested)
  }, [patientId])

  useEffect(() => {
    return () => { clearChatSession(patientId).catch(() => {}) }
  }, [patientId])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, streamingText, thinkingTools])

  const sendMessage = useCallback(async (question: string) => {
    if (!question.trim() || streaming) return

    setSuggestionsUsed(true)
    setMessages(prev => [...prev, { role: 'user', content: question }])
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
                const done = payload as ChatDoneEvent
                setMessages(prev => [...prev, {
                  role: 'assistant',
                  content: done.answer,
                  evidence: done.evidence,
                  confidence: done.confidence,
                  data_gaps: done.data_gaps,
                  tools_used: done.tools_used,
                  blocked: done.blocked,
                }])
                setStreamingText('')
                setThinkingTools([])
              }
            } catch { /* skip */ }
          }
        }
      }
    } catch {
      setMessages(prev => [...prev, {
        role: 'assistant', content: 'Something went wrong. Please try again.',
        confidence: 'low', blocked: false,
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
    clearChatSession(patientId).catch(() => {})
  }

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
          <div className="flex items-center gap-1.5 mt-0.5">
            <span className="w-1.5 h-1.5 rounded-full bg-teal-500 animate-pulse" />
            <span className="text-[10px] text-gray-400 dark:text-gray-500">
              Clinical decision support
            </span>
          </div>
        </div>
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
            <SuggestedQuestions
              questions={suggestionsUsed ? [] : suggested.questions}
              proactive={suggestionsUsed ? null : suggested.proactive}
              onSelect={sendMessage}
            />
          </div>
        )}

        {/* Messages */}
        {messages.map((msg, i) => (
          <MessageBubble key={i} msg={msg} index={i} />
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
                <p className="text-[11px] text-gray-400 dark:text-gray-500">Retrieving data…</p>
                <div className="flex flex-wrap gap-1">
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

        {/* Typing dots when waiting for first token */}
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
