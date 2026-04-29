'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import { chatStream, clearChatSession, getSuggestedQuestions } from '@/lib/api'
import type { ChatDoneEvent, ChatMessage } from '@/lib/types'
import EvidenceCard from './EvidenceCard'
import SuggestedQuestions from './SuggestedQuestions'

interface ChatPanelProps {
  patientId: string
}

export default function ChatPanel({ patientId }: ChatPanelProps) {
  const [open, setOpen] = useState(false)
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

  // Load suggested questions when panel opens
  useEffect(() => {
    if (open && suggested.questions.length === 0) {
      getSuggestedQuestions(patientId).then(setSuggested)
    }
  }, [open, patientId, suggested.questions.length])

  // Clear session on unmount
  useEffect(() => {
    return () => {
      clearChatSession(patientId).catch(() => {})
    }
  }, [patientId])

  // Auto-scroll to bottom
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
          if (line.startsWith('event: ')) {
            // event type handled with next data line
          } else if (line.startsWith('data: ')) {
            try {
              const payload = JSON.parse(line.slice(6))

              if (payload.tool) {
                // thinking event
                setThinkingTools(prev => [...prev, payload.tool as string])
              } else if (payload.token !== undefined) {
                // token event
                accumulatedText += payload.token as string
                setStreamingText(accumulatedText)
              } else if (payload.answer !== undefined) {
                // done event
                const done = payload as ChatDoneEvent
                setMessages(prev => [
                  ...prev,
                  {
                    role: 'assistant',
                    content: done.answer,
                    evidence: done.evidence,
                    confidence: done.confidence,
                    data_gaps: done.data_gaps,
                    tools_used: done.tools_used,
                    blocked: done.blocked,
                  },
                ])
                setStreamingText('')
                setThinkingTools([])
              }
            } catch {
              // malformed JSON line — skip
            }
          }
        }
      }
    } catch (err) {
      setMessages(prev => [
        ...prev,
        {
          role: 'assistant',
          content: 'Something went wrong. Please try again.',
          confidence: 'low',
          blocked: false,
        },
      ])
    } finally {
      setStreaming(false)
      setStreamingText('')
      setThinkingTools([])
    }
  }, [patientId, streaming])

  function handleKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      sendMessage(input)
    }
  }

  return (
    <div className="mt-6 border border-slate-200 dark:border-slate-700 rounded-xl overflow-hidden">
      {/* Header / toggle */}
      <button
        onClick={() => setOpen(o => !o)}
        className="w-full flex items-center justify-between px-5 py-3
                   bg-slate-50 dark:bg-slate-800 hover:bg-slate-100 dark:hover:bg-slate-750
                   transition-colors text-left"
      >
        <div className="flex items-center gap-2">
          <span className="text-[14px] font-medium text-slate-700 dark:text-slate-200">
            Ask ARIA
          </span>
          <span className="text-[12px] text-slate-400">
            Ask questions about this patient
          </span>
        </div>
        <span className="text-slate-400 text-[12px]">{open ? '▲' : '▼'}</span>
      </button>

      {open && (
        <div className="flex flex-col bg-white dark:bg-slate-900" style={{ maxHeight: '480px' }}>
          {/* Messages */}
          <div className="flex-1 overflow-y-auto px-5 py-4 space-y-4 min-h-0">
            {messages.length === 0 && !streaming && (
              <SuggestedQuestions
                questions={suggestionsUsed ? [] : suggested.questions}
                proactive={suggestionsUsed ? null : suggested.proactive}
                onSelect={q => { setInput(q); sendMessage(q) }}
              />
            )}

            {messages.map((msg, i) => (
              <div key={i} className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
                <div className={`max-w-[85%] ${msg.role === 'user'
                  ? 'bg-teal-600 text-white rounded-2xl rounded-tr-sm px-4 py-2.5'
                  : 'bg-slate-100 dark:bg-slate-800 text-slate-800 dark:text-slate-100 rounded-2xl rounded-tl-sm px-4 py-2.5'
                }`}>
                  <p className={`text-[14px] leading-relaxed ${msg.blocked ? 'italic text-slate-500 dark:text-slate-400' : ''}`}>
                    {msg.content}
                  </p>
                  {msg.role === 'assistant' && !msg.blocked && (
                    <EvidenceCard
                      evidence={msg.evidence ?? []}
                      confidence={msg.confidence ?? 'medium'}
                      dataGaps={msg.data_gaps ?? []}
                      toolsUsed={msg.tools_used ?? []}
                    />
                  )}
                </div>
              </div>
            ))}

            {/* Thinking indicator */}
            {thinkingTools.length > 0 && (
              <div className="flex justify-start">
                <div className="bg-slate-100 dark:bg-slate-800 rounded-2xl rounded-tl-sm px-4 py-2.5">
                  <p className="text-[12px] text-slate-400 italic">
                    Querying: {thinkingTools.join(', ').replace(/get_/g, '').replace(/_/g, ' ')}…
                  </p>
                </div>
              </div>
            )}

            {/* Streaming text */}
            {streamingText && (
              <div className="flex justify-start">
                <div className="max-w-[85%] bg-slate-100 dark:bg-slate-800 text-slate-800
                                dark:text-slate-100 rounded-2xl rounded-tl-sm px-4 py-2.5">
                  <p className="text-[14px] leading-relaxed">{streamingText}</p>
                </div>
              </div>
            )}

            <div ref={bottomRef} />
          </div>

          {/* Input */}
          <div className="border-t border-slate-200 dark:border-slate-700 px-4 py-3 flex gap-2">
            <input
              type="text"
              value={input}
              onChange={e => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="Ask a question about this patient…"
              disabled={streaming}
              className="flex-1 px-3 py-2 rounded-lg border border-slate-300 dark:border-slate-600
                         bg-white dark:bg-slate-800 text-slate-800 dark:text-slate-100
                         text-[13px] placeholder:text-slate-400 focus:outline-none
                         focus:ring-2 focus:ring-teal-500 disabled:opacity-50"
            />
            <button
              onClick={() => sendMessage(input)}
              disabled={streaming || !input.trim()}
              className="px-4 py-2 rounded-lg bg-teal-600 hover:bg-teal-700 disabled:opacity-40
                         text-white text-[13px] font-medium transition-colors"
            >
              {streaming ? '…' : 'Send'}
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
