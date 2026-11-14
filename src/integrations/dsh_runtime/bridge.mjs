import { createHash } from 'node:crypto'
import { readFile, realpath } from 'node:fs/promises'
import { dirname, join, resolve } from 'node:path'

export const name = 'gxworks-bridge'
export const inject = ['tools']

const stringOutput = {
  schema: { type: 'string' },
  render(_args, value) { return [{ type: 'text', text: value }] },
}

// The DSH spill policy gives the model a file path when it stores a large
// result. Make that output retrievable without exposing project files.
const spillReader = {
  name: 'read',
  description: 'Read a DSH spilled tool result from this session using the path in the spill notice.',
  parameters: {
    type: 'object',
    properties: {
      file_path: { type: 'string' },
      offset: { type: 'integer', minimum: 0, description: 'Starting character offset, zero based.' },
      limit: { type: 'integer', minimum: 1, maximum: 16000, description: 'Maximum characters to read.' },
    },
    required: ['file_path'],
    additionalProperties: false,
  },
  output: stringOutput,
  async execute({ file_path, offset = 0, limit = 8000 }, exec) {
    if (!exec.agent?.id) throw new Error('A DSH session is required to read a spilled result')
    if (!Number.isSafeInteger(offset) || offset < 0 || !Number.isSafeInteger(limit) || limit < 1 || limit > 16000) {
      throw new Error('Invalid spill read offset or limit')
    }
    const sessionHash = createHash('sha256').update(exec.agent.id).digest('hex').slice(0, 12)
    const sessionDir = await realpath(join(process.env.DSH_HOME, 'spill', `session-${sessionHash}`))
    const requested = resolve(file_path)
    if (dirname(requested) !== sessionDir || !requested.endsWith('.txt') || await realpath(requested) !== requested) {
      throw new Error('The file is outside this DSH session’s spilled results')
    }
    const contents = await readFile(requested, { encoding: 'utf8', signal: exec.signal })
    const end = offset + limit
    const excerpt = contents.slice(offset, end)
    return end < contents.length ? `${excerpt}\n[Continue reading at offset ${end}]` : excerpt
  },
}

function providerUsage(raw) {
  if (!raw) return undefined
  const input = raw.input_tokens ?? raw.prompt_tokens
  const output = raw.output_tokens ?? raw.completion_tokens
  if (!Number.isSafeInteger(input) || input < 0 || !Number.isSafeInteger(output) || output < 0) return undefined
  const cached = [raw.prompt_cache_hit_tokens, raw.prompt_tokens_details?.cached_tokens,
    raw.input_tokens_details?.cached_tokens, raw.cache_read_input_tokens]
    .find(count => Number.isSafeInteger(count) && count >= 0 && count <= input)
  // DSH reports input excluding cache buckets. The Web usage receipt counts
  // the full provider input, with cache reads reported separately when known.
  return { inputTokens: input - (cached ?? 0), outputTokens: output,
    ...(cached === undefined ? {} : { cacheReadTokens: cached }) }
}

function observeUsage(response, attempt) {
  if (!response.ok || !response.body || !response.headers.get('content-type')?.toLowerCase().includes('text/event-stream')) return response
  const decoder = new TextDecoder()
  let pending = ''
  function inspect(text, flush = false) {
    const events = (pending + text).split(/\r?\n\r?\n/)
    pending = flush ? '' : events.pop()
    for (const event of events) {
      if (!event.includes('"usage"')) continue
      const data = event.split(/\r?\n/).filter(line => line.startsWith('data:')).map(line => line.slice(5)).join('\n')
      try {
        const value = JSON.parse(data)
        const usage = providerUsage(value?.usage ?? value?.response?.usage)
        if (usage !== undefined) attempt.usage = usage
      } catch {
        // Let the SDK handle malformed SSE. The meter only observes it.
      }
    }
  }
  const body = response.body.pipeThrough(new TransformStream({
    transform(chunk, controller) {
      inspect(decoder.decode(chunk, { stream: true }))
      controller.enqueue(chunk)
    },
    flush() { inspect(decoder.decode(), true) },
  }))
  return new Response(body, { status: response.status, statusText: response.statusText, headers: response.headers })
}

function requestReceipt(payload, modelUrl, attemptId) {
  const api = modelUrl.pathname.endsWith('/responses') ? 'openai-responses' : 'openai-completions'
  const format = payload.response_format?.type ?? payload.text?.format?.type
  const effort = payload.reasoning?.effort ?? payload.reasoning_effort
  const messages = payload.messages ?? payload.input
  return { type: 'request', attemptId, model: payload.model, api,
    tools: Array.isArray(payload.tools) && payload.tools.length > 0, streaming: payload.stream === true,
    structured_output: format === 'json_object' || format === 'json_schema',
    images: Array.isArray(messages) && messages.some(message => Array.isArray(message?.content)
      && message.content.some(item => item?.type === 'image_url' || item?.type === 'input_image')),
    ...(['none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max', 'ultra'].includes(effort)
      ? { reasoning_effort: effort } : {}) }
}

// pi-ai builds the model messages and tools. Apply the selected provider's
// wire options only to requests sent to that provider's model endpoint.
function installRequestOptions(ctx, originalFetch, getAttempt, queueFrame) {
  const modelUrl = new URL(process.env.GXWORKS_DSH_MODEL_URL)
  const responses = modelUrl.pathname.endsWith('/responses')
  const options = JSON.parse(process.env.GXWORKS_DSH_REQUEST_PARAMETERS)
  const customHeaders = new Headers(JSON.parse(process.env.GXWORKS_DSH_REQUEST_HEADERS))
  const overrideRequest = Object.keys(options).length > 0 || [...customHeaders].length > 0
  const capNames = ['max_output_tokens', 'max_completion_tokens', 'max_tokens']
  const fetchWithOptions = async (input, init) => {
    const url = new URL(input instanceof Request ? input.url : String(input))
    const method = init?.method ?? (input instanceof Request ? input.method : 'GET')
    if (method.toUpperCase() !== 'POST' || url.origin !== modelUrl.origin || url.pathname !== modelUrl.pathname) {
      return originalFetch(input, init)
    }

    const attempt = getAttempt()
    const body = init?.body ?? (input instanceof Request ? await input.clone().text() : undefined)
    if (typeof body !== 'string') throw new Error('DSH model request does not contain a JSON body')
    const payload = JSON.parse(body)
    const normalizeTools = responses && Array.isArray(payload.tools)
      && payload.tools.some(tool => tool?.type === 'function' && tool.strict !== false)
    if (overrideRequest || normalizeTools || capNames.some(key => Object.hasOwn(payload, key))) {
      const headers = new Headers(init?.headers ?? (input instanceof Request ? input.headers : undefined))
      for (const [key, value] of customHeaders) {
        if (key !== 'content-length' && key !== 'authorization') headers.set(key, value)
      }
      headers.delete('content-length')
      // pi-ai may insert a default output cap even when the application sent
      // none. Only explicitly resolved request options may impose that cap;
      // otherwise let the provider use its own model limit. Restore any user
      // setting below, in the selected API's native field.
      for (const key of capNames) delete payload[key]
      Object.assign(payload, options)
      // Responses treats an unspecified strict flag as permission to normalize
      // schemas into strict form, making optional properties mandatory. GXWorks
      // checks the original schema locally, including nested optional fields.
      if (normalizeTools) {
        payload.tools = payload.tools.map(tool => tool?.type === 'function' ? { ...tool, strict: false } : tool)
      }
      init = { ...init, headers, body: JSON.stringify(payload) }
    }
    if (attempt !== undefined) queueFrame(attempt.agent, requestReceipt(payload, modelUrl, attempt.id))
    const response = await originalFetch(input, init)
    return attempt === undefined ? response : observeUsage(response, attempt)
  }
  ctx.effect(() => {
    globalThis.fetch = fetchWithOptions
    return () => {
      if (globalThis.fetch === fetchWithOptions) globalThis.fetch = originalFetch
    }
  })
}

export async function apply(ctx) {
  const originalFetch = globalThis.fetch
  const baseUrl = new URL(process.env.GXWORKS_DSH_BRIDGE_URL)
  if (baseUrl.protocol !== 'http:' || !['127.0.0.1', '[::1]', 'localhost'].includes(baseUrl.hostname)) {
    throw new Error('GXWorks DSH bridge requires a loopback HTTP URL')
  }
  const token = process.env.GXWORKS_DSH_BRIDGE_TOKEN
  if (!token) throw new Error('GXWorks DSH bridge token is missing')

  async function request(path, payload, signal) {
    const response = await originalFetch(new URL(baseUrl.href.replace(/\/$/, '') + path), {
      method: payload === undefined ? 'GET' : 'POST',
      headers: {
        authorization: `Bearer ${token}`,
        ...(payload === undefined ? {} : { 'content-type': 'application/json' }),
      },
      body: payload === undefined ? undefined : JSON.stringify(payload),
      redirect: 'error',
      signal,
    })
    if (!response.ok) throw new Error(`GXWorks DSH bridge ${path} returned HTTP ${response.status}`)
    const body = await response.text()
    return body ? JSON.parse(body) : null
  }

  const advertised = await request('/tools', undefined, AbortSignal.timeout(5000))
  const tools = Array.isArray(advertised) ? advertised : advertised?.tools
  if (!Array.isArray(tools)) throw new Error('GXWorks DSH bridge returned an invalid tool list')

  const names = new Set([spillReader.name])
  for (const { type, function: tool } of tools) {
    if (type !== 'function' || !tool?.name || !tool.parameters || names.has(tool.name)) {
      throw new Error('GXWorks DSH bridge returned an invalid tool declaration')
    }
    names.add(tool.name)
    ctx.effect(() => ctx.tools.register({
      name: tool.name,
      description: tool.description ?? '',
      parameters: tool.parameters,
      output: stringOutput,
      async execute(args, exec) {
        const result = await request('/invoke', {
          name: tool.name,
          arguments: args,
          call_id: exec.callId,
          session_id: exec.agent?.id,
        }, exec.signal)
        if (result?.cancelled) {
          exec.agent?.cancel({ kind: 'user' })
          return ''
        }
        return JSON.stringify(result)
      },
    }))
  }
  ctx.effect(() => ctx.tools.register(spillReader))
  ctx.effect(() => ctx.tools.guard(exec => names.has(exec.name) ? undefined : 'Tool is outside the GXWorks bridge'))
  // The host already budgets and assembles engineering reads. Preserve that
  // value through DSH's generic tool-output retention.
  const hostReads = new Set(['get_design_context', 'get_generation_context',
    'search_plc_manual', 'get_fbd_catalog', 'gxworks_read_context_unit'])
  ctx.on('tools/post-execute', async (exec, result, next) => {
    const decision = await next()
    if (decision.kind !== 'accept' || decision.content !== undefined
        || Object.hasOwn(decision, 'value') || !hostReads.has(exec.name)
        || typeof result.value !== 'string') return decision
    return { ...decision, value: result.value }
  })

  const eventsBySession = new Map()
  function sendEvents(agent, state) {
    clearTimeout(state.timer)
    state.timer = undefined
    const frames = state.frames.splice(0)
    if (!frames.length) return state.pending
    const payload = { session_id: agent.id, ...(frames.length === 1 ? { frame: frames[0] } : { frames }) }
    state.pending = state.pending.then(async () => {
      const result = await request('/event', payload, AbortSignal.timeout(5000))
      if (result?.cancelled) agent.cancel({ kind: 'user' })
    }).catch(error => ctx.logger.warn(`GXWorks DSH stream forwarding failed: ${error.message}`))
    return state.pending
  }
  function queueFrame(agent, frame) {
    let state = eventsBySession.get(agent.id)
    if (state === undefined) {
      state = { pending: Promise.resolve(), frames: [], timer: undefined }
      eventsBySession.set(agent.id, state)
    }
    state.frames.push(frame)
    if (frame.type === 'start' || frame.type === 'end' || state.frames.length >= 64) {
      sendEvents(agent, state)
    } else if (state.timer === undefined) {
      state.timer = setTimeout(() => sendEvents(agent, state), 75)
    }
  }
  // A GXWorks runtime process handles one bound Web session and one model
  // attempt at a time, including the subsequent attempts after tool calls.
  let activeAttempt
  ctx.on('agent/assistant-stream', ({ agent, frame }) => {
    if (frame.type === 'start') activeAttempt = { agent, id: frame.attemptId }
    const attempt = activeAttempt?.id === frame.attemptId && activeAttempt?.agent.id === agent.id ? activeAttempt : undefined
    if (frame.type === 'chunk' && frame.chunk.type === 'usage') {
      const usage = attempt?.usage ?? frame.chunk.usage
      // The adapter emits a zeroed placeholder on failed requests without
      // a provider usage report. It contributes no metered tokens.
      if (!attempt?.usage && !usage.inputTokens && !usage.outputTokens && !usage.cacheReadTokens && !usage.cacheWriteTokens) return
      frame = { ...frame, chunk: { ...frame.chunk, usage } }
    }
    if (frame.type === 'end' && attempt !== undefined) activeAttempt = undefined
    queueFrame(agent, frame)
  })
  ctx.on('agent/turn-stopping', async ({ agent }) => {
    const state = eventsBySession.get(agent.id)
    if (state === undefined) return
    await sendEvents(agent, state)
    eventsBySession.delete(agent.id)
  })
  ctx.effect(() => () => {
    for (const state of eventsBySession.values()) clearTimeout(state.timer)
  })
  installRequestOptions(ctx, originalFetch, () => activeAttempt, queueFrame)
  ctx.provide('gxworksBridge', true)
}
