(function registerHelperWebMcpSiteTools() {
    'use strict';

    const status = {
        supported: typeof document.modelContext?.registerTool === 'function',
        registered: false,
        registering: false,
        toolNames: [],
        error: null,
    };
    window.__helperWebMcpStatus = status;

    function emptyInputSchema() {
        return { type: 'object', properties: {}, additionalProperties: false };
    }

    function definitions(bridge) {
        return [
            {
                name: 'helper_get_workspace_state',
                description: 'Inspect the current All Time Helper workspace without reading message bodies or account identifiers.',
                inputSchema: emptyInputSchema(),
                annotations: { readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: false },
                execute: async () => bridge.getWorkspaceState(),
            },
            {
                name: 'helper_search_conversations',
                description: 'Search the signed-in user\'s conversation titles. Returns bounded title metadata only, never message bodies.',
                inputSchema: {
                    type: 'object',
                    properties: {
                        query: { type: 'string', minLength: 1, maxLength: 120, description: 'Text to match in conversation titles.' },
                        limit: { type: 'integer', minimum: 1, maximum: 10, default: 8, description: 'Maximum number of matches.' },
                    },
                    required: ['query'],
                    additionalProperties: false,
                },
                annotations: { readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: false },
                execute: async ({ query, limit } = {}) => bridge.searchConversations(query, limit),
            },
            {
                name: 'helper_open_conversation',
                description: 'Open an existing conversation in the visible All Time Helper interface.',
                inputSchema: {
                    type: 'object',
                    properties: {
                        conversationId: { type: 'string', minLength: 1, maxLength: 128, description: 'Conversation ID returned by a workspace or search tool.' },
                    },
                    required: ['conversationId'],
                    additionalProperties: false,
                },
                annotations: { readOnlyHint: false, destructiveHint: false, idempotentHint: true, openWorldHint: false },
                execute: async ({ conversationId } = {}) => bridge.openConversation(conversationId),
            },
            {
                name: 'helper_start_new_conversation',
                description: 'Move the visible interface to a fresh conversation without sending a message.',
                inputSchema: emptyInputSchema(),
                annotations: { readOnlyHint: false, destructiveHint: false, idempotentHint: false, openWorldHint: false },
                execute: async () => bridge.startNewConversation(),
            },
            {
                name: 'helper_prepare_prompt',
                description: 'Place text in the visible prompt composer for user review. This tool never sends the prompt or starts model work.',
                inputSchema: {
                    type: 'object',
                    properties: {
                        text: { type: 'string', minLength: 1, maxLength: 6000, description: 'Prompt text to prepare in the composer.' },
                        mode: { type: 'string', enum: ['replace', 'append'], default: 'replace', description: 'Replace the composer or append on a new line.' },
                    },
                    required: ['text'],
                    additionalProperties: false,
                },
                annotations: { readOnlyHint: false, destructiveHint: false, idempotentHint: false, openWorldHint: false },
                execute: async ({ text, mode } = {}) => bridge.preparePrompt(text, mode),
            },
            {
                name: 'helper_set_theme',
                description: 'Set the visible interface theme preference.',
                inputSchema: {
                    type: 'object',
                    properties: {
                        theme: { type: 'string', enum: ['light', 'dark', 'system'], description: 'Theme preference to apply.' },
                    },
                    required: ['theme'],
                    additionalProperties: false,
                },
                annotations: { readOnlyHint: false, destructiveHint: false, idempotentHint: true, openWorldHint: false },
                execute: async ({ theme } = {}) => bridge.setTheme(theme),
            },
            {
                name: 'helper_set_assistant_route',
                description: 'Select one of the assistant routes currently available in the visible model menu without sending a message.',
                inputSchema: {
                    type: 'object',
                    properties: {
                        routeId: { type: 'string', minLength: 1, maxLength: 120, description: 'Route ID returned by helper_get_workspace_state.' },
                    },
                    required: ['routeId'],
                    additionalProperties: false,
                },
                annotations: { readOnlyHint: false, destructiveHint: false, idempotentHint: true, openWorldHint: false },
                execute: async ({ routeId } = {}) => bridge.setAssistantRoute(routeId),
            },
        ];
    }

    async function register() {
        if (status.registered || status.registering) return;
        const modelContext = document.modelContext;
        const bridge = window.HelperSiteTools;
        status.supported = typeof modelContext?.registerTool === 'function';
        if (!status.supported || !bridge) return;

        status.registering = true;
        const controller = new AbortController();
        try {
            const tools = definitions(bridge);
            for (const tool of tools) {
                await modelContext.registerTool(tool, { signal: controller.signal });
            }
            window.__helperWebMcpController = controller;
            status.registered = true;
            status.toolNames = tools.map(tool => tool.name);
        } catch (_) {
            controller.abort();
            status.error = 'registration_failed';
            status.toolNames = [];
        } finally {
            status.registering = false;
        }
    }

    window.addEventListener('helper:app-bridge-ready', register);
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', register, { once: true });
    } else {
        register();
    }
})();
