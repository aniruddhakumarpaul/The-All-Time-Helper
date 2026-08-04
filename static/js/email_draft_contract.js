// Shared browser-side email-draft normalization and safe boundary serializers.
(function () {
    const SCHEMA_VERSION = 1;
    const MIME_RE = /^[a-z0-9][a-z0-9!#$&^_.+-]*\/[a-z0-9][a-z0-9!#$&^_.+-]*$/i;

    class EmailDraftVersionError extends Error {
        constructor(message, code) {
            super(message);
            this.name = this.constructor.name;
            this.code = code;
        }
    }

    class InvalidEmailDraftVersion extends EmailDraftVersionError {
        constructor(message = 'Email draft schema_version must be a non-negative integer.') {
            super(message, 'invalid_email_draft_version');
        }
    }

    class UnsupportedEmailDraftVersion extends EmailDraftVersionError {
        constructor(message = 'Email draft schema version is newer than supported.') {
            super(message, 'unsupported_email_draft_version');
        }
    }

    function detectEmailDraftVersion(raw) {
        if (!Object.prototype.hasOwnProperty.call(raw, 'schema_version')) return 0;
        const version = raw.schema_version;
        if (typeof version !== 'number' || !Number.isInteger(version) || version < 0) {
            throw new InvalidEmailDraftVersion();
        }
        if (version > SCHEMA_VERSION) throw new UnsupportedEmailDraftVersion();
        return version;
    }

    function migrateLegacyEmailDraft(raw) {
        return { ...raw, schema_version: SCHEMA_VERSION };
    }

    function migrateEmailDraft(raw) {
        const version = detectEmailDraftVersion(raw);
        if (version === 0) return migrateLegacyEmailDraft(raw);
        if (version === SCHEMA_VERSION) return { ...raw };
        throw new UnsupportedEmailDraftVersion('Email draft schema version is unsupported.');
    }

    function text(value, fallback = '') {
        return String(value ?? fallback).trim();
    }

    function filename(value, fallback = 'attachment.bin') {
        const clean = text(value, fallback).replace(/\\/g, '/').split('/').pop().replace(/^[ .]+|[ .]+$/g, '');
        return (clean || fallback).slice(0, 160);
    }

    function safeRemoteUrl(value) {
        try {
            const url = new URL(text(value), window.location.origin);
            return url.protocol === 'http:' || url.protocol === 'https:' ? url.href.slice(0, 2000) : '';
        } catch (_) {
            return '';
        }
    }
    function displayFilename(value, item, index, mimeType) {
        const clean = filename(value, `attachment-${index + 1}.bin`);
        const source = text(item?.source).toLowerCase();
        const stem = clean.replace(/\.[a-z0-9]{2,8}$/i, '');
        const words = stem.match(/[A-Za-z0-9]+/g) || [];
        const promptLike = source === 'generated' && (
            clean.length > 64
            || words.length > 7
            || /^(edit|change|update|add|include|make|create|draft)\b/i.test(stem)
        );
        if (!promptLike) return clean;
        const slug = (words.slice(0, 6).join('-').toLowerCase() || 'generated-image').slice(0, 72);
        const extension = ({
            'image/png': 'png', 'image/jpeg': 'jpg', 'image/webp': 'webp', 'image/gif': 'gif',
        })[mimeType] || 'bin';
        return slug + '.' + extension;
    }
    function attachment(raw, index, legacyContent) {
        const item = raw && typeof raw === 'object' ? raw : { content: raw };
        const content = item.content || item.data || (index === 0 ? legacyContent : null);
        const id = text(item.id) || null;
        const mimeType = text(item.mime_type || item.content_type || item.type, 'application/octet-stream').toLowerCase();
        const available = item.available == null ? ((id || content) ? true : undefined) : Boolean(item.available);
        const rawSource = item.source == null ? null : text(item.source);
        const derivedSource = content && !id ? 'generated' : id ? 'upload' : 'unknown';
        const source = rawSource == null
            ? derivedSource
            : ['upload', 'generated', 'legacy', 'remote', 'reference', 'unknown'].includes(rawSource)
                ? rawSource : 'unknown';
        const result = {

            filename: displayFilename(item.filename || item.name, item, index, MIME_RE.test(mimeType) ? mimeType : 'application/octet-stream'),
            name: displayFilename(item.filename || item.name, item, index, MIME_RE.test(mimeType) ? mimeType : 'application/octet-stream'),
            mime_type: MIME_RE.test(mimeType) ? mimeType : 'application/octet-stream',
            type: MIME_RE.test(mimeType) ? mimeType : 'application/octet-stream',
            content_type: MIME_RE.test(mimeType) ? mimeType : 'application/octet-stream',
            size: Number.isInteger(Number(item.size)) && Number(item.size) >= 0 ? Number(item.size) : undefined,
            sha256: /^[0-9a-f]{64}$/i.test(text(item.sha256 || item.sha_256)) ? text(item.sha256 || item.sha_256).toLowerCase() : undefined,
            available: available == null ? undefined : Boolean(available),
            source,
            description: text(item.description).slice(0, 320) || undefined,
        };
        if (id != null) result.id = id;
        const safeUrl = safeRemoteUrl(item.url || (typeof content === 'string' && /^https?:\/\//i.test(content) ? content : ''));
        if (safeUrl) result.url = safeUrl;
        if (content != null) result.content = String(content);
        return result;
    }

    function meaningfulAttachment(item) {
        if (item.id || item.content || item.available != null || item.size != null || item.sha256) return true;
        if (item.mime_type !== 'application/octet-stream') return true;
        const name = text(item.filename).toLowerCase();
        return Boolean(name && !['attachment.bin', 'attachment-1.bin'].includes(name));
    }

    function normalize(raw) {
        if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null;
        const source = migrateEmailDraft(raw);
        const rawAttachments = Array.isArray(source.attachments) ? source.attachments : [];
        const legacyContent = source.attachment_content ?? null;
        const attachments = rawAttachments
            .map((item, index) => attachment(item, index, source.attachment_filename ? null : legacyContent))
            .filter(meaningfulAttachment);
        if (!attachments.length && (legacyContent != null || source.attachment_filename)) {
            const legacyAttachment = attachment({
                content: legacyContent,
                filename: source.attachment_filename,
                type: source.attachment_type || source.content_type || source.type,
                source: 'legacy',
            }, 0, legacyContent);
            if (meaningfulAttachment(legacyAttachment)) attachments.push(legacyAttachment);
        }
        if (attachments.length && legacyContent != null) {
            const legacyTarget = attachments.find(item => source.attachment_filename && item.filename === filename(source.attachment_filename, "")) || attachments[0];
            if (!legacyTarget.content) legacyTarget.content = String(legacyContent);
        }
        let primary = attachments[0] || {};
        if (attachments.length && (legacyContent != null || source.attachment_filename)) {
            primary = attachments.find(item => (
                (legacyContent != null && item.content === String(legacyContent))
                || (source.attachment_filename && item.filename === filename(source.attachment_filename, ""))
            )) || primary;
        }
        return {
            schema_version: SCHEMA_VERSION,
            recipient: text(source.recipient || source.to),
            subject: text(source.subject),
            body: String(source.body || '').replace(/\r\n/g, '\n').replace(/\r/g, '\n'),
            tone: ['formal', 'informal', 'modern'].includes(text(source.tone, 'modern')) ? text(source.tone, 'modern') : 'modern',
            attachment_content: primary.content ?? null,
            attachment_filename: attachments.length ? primary.filename : '',
            attachment_type: attachments.length ? primary.mime_type : null,
            attachment_description: source.attachment_description ? text(source.attachment_description) : null,
            attachments,
            has_attachment_content: Boolean(primary.content || attachments.some(item => item.content)),
        };
    }

    function stripContent(item) {
        const next = { ...item };
        delete next.content;
        delete next.data;
        delete next.bytes;
        delete next.attachment_content;
        return next;
    }

    function serializeFullTransient(raw) {
        const draft = normalize(raw);
        if (!draft) return null;
        return { ...draft, attachments: draft.attachments.map(item => ({ ...item })) };
    }

    function serializePromptContext(raw) {
        const draft = normalize(raw);
        if (!draft) return null;
        const result = { ...draft, attachments: draft.attachments.map(stripContent) };
        delete result.attachment_content;
        delete result.has_attachment_content;
        return result;
    }

    function serializePersistable(raw) {
        const result = serializePromptContext(raw);
        if (!result) return null;
        delete result.attachment_content;
        delete result.attachment_filename;
        delete result.attachment_type;
        return result;
    }

    function serializeDelivery(raw) {
        return serializeFullTransient(raw);
    }

    window.helperEmailDraftContract = {
        SCHEMA_VERSION,
        EmailDraftVersionError,
        InvalidEmailDraftVersion,
        UnsupportedEmailDraftVersion,
        detectEmailDraftVersion,
        migrateEmailDraft,
        normalize,
        serializeFullTransient,
        serializePromptContext,
        serializePersistable,
        serializeDelivery,
    };
})();
